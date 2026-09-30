# Turns an inbound patient message into tools + a patient-safe reply.
# Loads clinic card, booking draft, and LLM; reply_guard sanitizes output.
import time
import json
import uuid
import asyncio
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.config import settings
from app.core.logging import logger
from app.api.schemas import GenerateRequest, GenerateResponse, TokenUsage, ToolCallInfo
from app.db.models import ConversationModel, MessageModel, AiUsageLogModel, EntityModel
from app.entities.resolver import entity_resolver
from app.memory.memory_engine import memory_engine
from app.tools.registry import tool_registry
from app.services.prompt_builder import prompt_builder
from app.providers.openrouter import OpenRouterProvider
from app.memory.addons import AddonMemoryManager
from app.memory.addon_extractor import extract_addons
from app.utils.phone import canonicalize_participant_id, participant_id_variants
from app.services import booking_flow
from app.services.reply_guard import authenticate_patient_reply
from app.services.clinic_card import clinic_card_reply, looks_like_clinic_faq
from datetime import datetime


# Aiorchestrator.
class AIOrchestrator:
    # Initialize instance.
    def __init__(self, provider: OpenRouterProvider = None):
        self.provider = provider or OpenRouterProvider()

    # Tool packs — only the schemas we need for the turn (full set burns ~2–4k tokens/call).
    _TOOLS_CHAT = ["request_human_handoff"]
    _TOOLS_INFO = [
        "get_doctors",
        "get_services",
        "faq_lookup",
        "knowledge_base_search",
        "get_entity_info",
        "request_human_handoff",
    ]
    _TOOLS_STATUS = [
        "get_patient",
        "get_doctors",
        "check_availability",
        "fetch_appointment",
        "request_human_handoff",
    ]
    _TOOLS_BOOKING = [
        "get_patient",
        "get_lead",
        "get_doctors",
        "get_services",
        "check_availability",
        "book_appointment",
        "reschedule_appointment",
        "fetch_appointment",
        "request_human_handoff",
    ]

    # Return (tool_names, max_tool_turns, pack). pack: greet|info|status|booking.
    # max_tool_turns=0 means hard early-exit (no LLM).
    def _select_tool_names(
        self,
        user_message: str,
        recent_messages: Optional[List[Dict[str, Any]]] = None,
    ) -> Tuple[List[str], int, str]:
        msg = (user_message or "").strip().lower()
        recent = " ".join(
            (m.get("content") or "")[:100].lower()
            for m in (recent_messages or [])[-6:]
            if isinstance(m, dict)
        )
        blob = f"{msg} {recent}"

        # Status ("last booking") before book — "appointment booking" is not "book".
        if booking_flow.is_status_ask(user_message):
            return list(self._TOOLS_STATUS), 2, "status"
        if booking_flow.is_book_entry(user_message) or booking_flow.is_reschedule(user_message):
            return list(self._TOOLS_BOOKING), 3, "booking"
        if looks_like_clinic_faq(user_message):
            return list(self._TOOLS_INFO), 2, "info"

        if booking_flow.is_greet(user_message):
            return list(self._TOOLS_CHAT), 0, "greet"

        if booking_flow.is_thanks_or_ack(user_message):
            if booking_flow.last_ai_confirmed_appointment(recent_messages):
                return list(self._TOOLS_CHAT), 0, "outro"
            if booking_flow.is_soft_ack_only(user_message) or msg in {
                "now", "then", "hmm", "hm",
            }:
                return list(self._TOOLS_CHAT), 0, "ack"
            # thanks / bye without a confirm — polite close, not welcome
            return list(self._TOOLS_CHAT), 0, "close"

        if any(
            k in msg
            for k in (
                "have you booked", "my appointment", "already booked",
                "booking status", "appointment status", "did you book",
                "confirm ho", "booked or not",
            )
        ):
            return list(self._TOOLS_STATUS), 2, "status"

        if any(
            k in msg
            for k in (
                "book", "appoint", "slot", "schedule", "reschedule", "available",
                "availability", "tomorrow", "today", "monday", "tuesday",
                "wednesday", "thursday", "friday", "saturday", " am", " pm",
                "age", "gender", "routine", "cleaning", "checkup", "check-up",
            )
        ):
            return list(self._TOOLS_BOOKING), 3, "booking"

        if any(
            k in blob
            for k in (
                "have you booked", "my appointment", "already booked",
                "booking status", "appointment status", "did you book",
                "confirm ho", "booked or not",
            )
        ):
            return list(self._TOOLS_STATUS), 2, "status"

        if any(
            k in msg
            for k in (
                "fee", "price", "cost", "doctor", "service", "timing", "hours",
                "address", "offer", "what do you", "clinic", "treatment",
            )
        ):
            return list(self._TOOLS_INFO), 2, "info"

        # Ambiguous follow-ups (e.g. "yes", "11 am") — keep booking tools if recent was booking.
        return list(self._TOOLS_BOOKING), 3, "booking"

    # Max tokens for pack.
    def _max_tokens_for_pack(self, pack: str, tools_enabled: bool) -> int:
        if pack in ("greet", "outro", "ack", "close"):
            return 120
        if pack == "info":
            return 250
        if pack == "status":
            return 350
        # booking
        return 500 if tools_enabled else 300

    # Only booking/status packs or an explicit slot ask may list open slots.
    @staticmethod
    def _allow_availability_narration(pack: str, message: str) -> bool:
        if pack in ("booking", "status"):
            return True
        return booking_flow.user_asked_slots(message)

    # All tools failed.
    @staticmethod
    def _all_tools_failed(tool_calls: List[ToolCallInfo]) -> bool:
        if not tool_calls:
            return False
        for tc in tool_calls:
            if not isinstance(tc.result, dict):
                return False
            if not tc.result.get("error"):
                return False
        return True

    _REPLY_TOOLS_FAILED = (
        "Sorry — I lost that for a second. Try once more, or say staff and I'll get someone."
    )
    _REPLY_GENERIC_EMPTY = (
        "I didn't quite catch that. Mind sending it again, or say staff if you'd rather talk to someone?"
    )

    # Fast greeting reply.
    def _fast_greeting_reply(
        self,
        memory_ctx,
        request_metadata: Optional[Dict[str, Any]],
        clinic_name: str,
    ) -> str:
        meta = request_metadata or {}
        name = (meta.get("participantName") or "").strip() or "there"
        lang = None
        for a in memory_ctx.pending_addons or []:
            if (a.get("type") or "").upper() == "LANGUAGE_PREFERENCE":
                content = (a.get("content") or "").lower()
                if "hindi" in content or "hinglish" in content:
                    lang = "hinglish"
                elif "english" in content:
                    lang = "english"
        if lang is None:
            return (
                f"Hi {name} — {clinic_name} this side. Booking, fees, doctors, jo bhi chahiye pooch lijiye.\n"
                f"Hinglish theek rahegi ya English?"
            )
        if lang == "hinglish":
            return (
                f"Namaste {name}! {clinic_name} se bol rahi hoon. "
                f"Appointment book karni hai, fees/doctors poochne hain, ya kuch aur?"
            )
        return (
            f"Hi {name} — this is {clinic_name}. "
            f"I can book a visit, or just tell you about fees, doctors, and timings. What do you need?"
        )

    # After a confirmed booking: thanks / okay / fine.
    @staticmethod
    def _fast_outro_reply(memory_ctx, clinic_name: str) -> str:
        lang = "english"
        for a in memory_ctx.pending_addons or []:
            if (a.get("type") or "").upper() == "LANGUAGE_PREFERENCE":
                content = (a.get("content") or "").lower()
                if "hindi" in content or "hinglish" in content:
                    lang = "hinglish"
        if lang == "hinglish":
            return (
                f"Shukriya {clinic_name} ki taraf se — clinic pe milte hain. "
                "Kuch aur ho to yahin likh dena."
            )
        return (
            f"You're welcome — see you at {clinic_name}. "
            "I'm here if anything else comes up."
        )

    # Mid-chat fine/okay — not a welcome, not "see you at the appointment".
    @staticmethod
    def _fast_ack_reply(memory_ctx) -> str:
        lang = "english"
        for a in memory_ctx.pending_addons or []:
            if (a.get("type") or "").upper() == "LANGUAGE_PREFERENCE":
                content = (a.get("content") or "").lower()
                if "hindi" in content or "hinglish" in content:
                    lang = "hinglish"
        if lang == "hinglish":
            return "Theek hai. Aur kuch chahiye ho to bata dena."
        return "Sure — anything else I can help with?"

    # Thanks / bye with no booking confirm in the last AI turn.
    @staticmethod
    def _fast_close_reply(memory_ctx, clinic_name: str) -> str:
        lang = "english"
        for a in memory_ctx.pending_addons or []:
            if (a.get("type") or "").upper() == "LANGUAGE_PREFERENCE":
                content = (a.get("content") or "").lower()
                if "hindi" in content or "hinglish" in content:
                    lang = "hinglish"
        if lang == "hinglish":
            return f"Shukriya. {clinic_name} pe kabhi bhi likh sakte ho."
        return f"Anytime. {clinic_name} is just a message away."

    # Pull saved appointment + patient age/gender from CGS.
    @staticmethod
    async def _cgs_appointment_facts(
        appointment_id: Optional[str],
        clinic_id: Optional[str],
        request_id: str,
    ) -> Dict[str, Any]:
        if not appointment_id or not clinic_id:
            return {}
        tool = tool_registry.get_tool("fetch_appointment")
        if not tool:
            return {}
        try:
            data = await tool.execute(
                {"appointmentId": appointment_id},
                {
                    "clinic_id": clinic_id,
                    "entity_id": clinic_id,
                    "last_appointment_id": appointment_id,
                    "request_id": request_id,
                },
            )
            if isinstance(data, dict) and not data.get("error"):
                return data
        except Exception as err:
            logger.warning(f"[{request_id}] CGS appointment fetch failed: {err}")
        return {}

    # Shrink tool JSON before re-feeding the LLM (cuts next-turn prompt size).
    def _compact_tool_result(self, tool_name: str, result: Any) -> Any:
        if result is None:
            return result
        if tool_name == "check_availability" and isinstance(result, dict):
            slots = result.get("availableSlots") or []
            return {
                "date": result.get("date"),
                "doctorId": result.get("doctorId"),
                "doctorName": result.get("doctorName"),
                "availabilityDays": result.get("availabilityDays"),
                "availabilityHours": result.get("availabilityHours"),
                "availableSlots": slots[:12],
                "isDoctorOnLeave": result.get("isDoctorOnLeave"),
                "isDoctorOffDuty": result.get("isDoctorOffDuty"),
                "error": result.get("error"),
                "replyInstruction": result.get("replyInstruction"),
            }
        if tool_name == "get_doctors" and isinstance(result, list):
            return [
                {
                    "id": d.get("id"),
                    "name": d.get("name"),
                    "specialization": d.get("specialization"),
                    "consultationFee": d.get("consultationFee") or d.get("fee"),
                    "availabilityDays": d.get("availabilityDays"),
                    "availabilityHours": d.get("availabilityHours"),
                }
                for d in result[:8]
                if isinstance(d, dict)
            ]
        if tool_name == "get_services" and isinstance(result, list):
            return [
                {"name": s.get("name"), "price": s.get("price"), "duration": s.get("duration")}
                for s in result[:10]
                if isinstance(s, dict)
            ]
        if tool_name in ("book_appointment", "reschedule_appointment") and isinstance(result, dict):
            return {
                k: result.get(k)
                for k in (
                    "appointmentId", "service", "date", "time", "status",
                    "patientName", "doctorName", "previousDate", "previousTime",
                    "error", "replyInstruction",
                )
                if result.get(k) is not None
            }
        if tool_name == "fetch_appointment" and isinstance(result, dict):
            return {
                k: result.get(k)
                for k in (
                    "appointmentId", "service", "date", "time", "status",
                    "patientName", "doctorName", "error",
                )
                if result.get(k) is not None
            }
        if tool_name == "get_patient" and isinstance(result, dict):
            upcoming = result.get("upcomingAppointments") or []
            return {
                "name": result.get("name"),
                "age": result.get("age"),
                "gender": result.get("gender"),
                "hasConfirmedBooking": result.get("hasConfirmedBooking"),
                "upcomingAppointments": upcoming[:3],
                "error": result.get("error"),
            }
        raw = json.dumps(result, default=str)
        if len(raw) > 1500:
            return {"truncated": True, "preview": raw[:1400]}
        return result

    # POST usage to CGS. Intended for FastAPI BackgroundTasks after the reply is sent.
    async def forward_usage_to_cgs(self, payload: Dict[str, Any]) -> None:
        if not settings.CGS_INTERNAL_URL or not payload:
            return
        try:
            from app.core.http_client import get_http_client

            url = f"{settings.CGS_INTERNAL_URL}/cgs_api/v1/internal/ai/usage"
            client = get_http_client()
            res = await client.post(
                url,
                json=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-Internal-Service-Key": settings.CGS_INTERNAL_SERVICE_KEY,
                },
                timeout=8,
            )
            if res.status_code >= 400:
                logger.warning(f"CGS usage forward HTTP {res.status_code}: {res.text[:300]}")
            else:
                logger.info(
                    f"Forwarded AI usage to CGS request_id={payload.get('requestId')} "
                    f"tokens={payload.get('totalTokens')}"
                )
        except Exception as usage_err:
            logger.warning(f"Could not forward usage to CGS backend: {usage_err}")

    # Memory continuity is keyed by (entity_id, participant_id).
    # CGS may send a new conversation_id after cleanup — prefer the stable
    # participant phone thread so history is not lost.
    async def _get_or_create_conversation(
        self,
        db: AsyncSession,
        entity_id: str,
        entity_type: str,
        participant_id: str,
        channel: str,
        conversation_id: str = None,
    ) -> ConversationModel:
        canonical_participant = canonicalize_participant_id(participant_id)
        variants = participant_id_variants(participant_id)
        conv = None

        # 1) Participant-first (stable WhatsApp memory key)
        if variants:
            stmt = (
                select(ConversationModel)
                .where(
                    ConversationModel.entity_id == entity_id,
                    ConversationModel.participant_id.in_(variants),
                )
                .order_by(ConversationModel.last_activity.desc())
            )
            res = await db.execute(stmt)
            conv = res.scalars().first()
            if conv and conversation_id and conv.id != conversation_id:
                logger.info(
                    f"AI memory reuse: CGS conversation_id={conversation_id} not bound; "
                    f"using existing AI thread={conv.id} for participant={canonical_participant}"
                )

        # 2) Exact CGS conversation id (only if no participant thread yet)
        if not conv and conversation_id:
            stmt = select(ConversationModel).where(ConversationModel.id == conversation_id)
            res = await db.execute(stmt)
            conv = res.scalars().first()
            if conv and canonical_participant and conv.participant_id != canonical_participant:
                # Normalize stored phone if we landed on this row by id
                conv.participant_id = canonical_participant

        if not conv:
            # Check if entity exists in AI DB; if not (e.g. clinic or new generic), create entity record
            stmt_ent = select(EntityModel).where(EntityModel.id == entity_id)
            res_ent = await db.execute(stmt_ent)
            ent_record = res_ent.scalars().first()

            if not ent_record:
                ent_record = EntityModel(
                    id=entity_id,
                    type=entity_type.upper(),
                    name=f"{entity_type} {entity_id[:8]}",
                    external_id=entity_id,
                )
                db.add(ent_record)
                await db.flush()

            conv_kwargs = {
                "entity_id": entity_id,
                "participant_id": canonical_participant or participant_id,
                "channel": channel or "WHATSAPP",
                "state": "AI_ACTIVE",
            }
            if conversation_id:
                conv_kwargs["id"] = conversation_id
            conv = ConversationModel(**conv_kwargs)
            db.add(conv)
            await db.flush()
            logger.info(
                f"Created AI conversation id={conv.id} participant={conv.participant_id} "
                f"entity={entity_id}"
            )
        elif canonical_participant and conv.participant_id != canonical_participant:
            # Keep stored key in canonical 91… form for future lookups
            conv.participant_id = canonical_participant

        conv.last_activity = datetime.utcnow()
        return conv

    # Generate response.
    async def generate_response(
        self,
        db: AsyncSession,
        request: GenerateRequest
    ) -> Tuple[GenerateResponse, Optional[Dict[str, Any]]]:
        start_time = time.time()
        request_id = f"ai_req_{uuid.uuid4().hex[:12]}"
        participant_id = canonicalize_participant_id(request.participant_id) or request.participant_id
        cgs_conversation_id = request.conversation_id or (request.metadata or {}).get("conversationId")

        logger.info(
            f"Processing AI Request: [{request_id}] for Entity: {request.entity_type} ({request.entity_id}), "
            f"Participant: {participant_id} (raw={request.participant_id}), "
            f"CGS conversation_id={cgs_conversation_id}"
        )

        # 1. Resolve Entity Context
        context_meta = {
            "db": db,
            "request_id": request_id,
            "entity_id": request.entity_id,
            "entity_type": request.entity_type,
            "participant_id": participant_id,
        }
        entity_ctx = await entity_resolver.resolve(request.entity_type, request.entity_id, context_meta)

        # 2. Resolve / Create Conversation Record in AI DB
        conversation = await self._get_or_create_conversation(
            db=db,
            entity_id=request.entity_id,
            entity_type=request.entity_type,
            participant_id=participant_id,
            channel=request.channel or "WHATSAPP",
            conversation_id=cgs_conversation_id,
        )
        if cgs_conversation_id and getattr(conversation, "cgs_conversation_id", None) != cgs_conversation_id:
            conversation.cgs_conversation_id = cgs_conversation_id
        logger.info(
            f"[{request_id}] AI memory thread={conversation.id} participant_id={conversation.participant_id}"
            f" cgs_conversation_id={getattr(conversation, 'cgs_conversation_id', None)}"
        )

        # 3. Assemble 4-Layer Memory
        memory_ctx = await memory_engine.assemble_memory(
            db=db,
            conversation_id=conversation.id,
            live_business_context=entity_ctx.live_business_context,
        )

        # 4. Lazy tools — only schemas needed for this turn (full set burns quota fast)
        tool_names, max_tool_turns, pack = self._select_tool_names(
            request.message,
            memory_ctx.recent_messages,
        )
        tool_schemas = tool_registry.get_openrouter_schemas(
            entity_ctx.capabilities,
            tool_names=tool_names,
        )
        logger.info(
            f"[{request_id}] Tool pack={pack} ({len(tool_names)}): {tool_names} "
            f"max_turns={max_tool_turns}"
        )

        executed_tool_calls: List[ToolCallInfo] = []
        total_prompt_tokens = 0
        total_completion_tokens = 0
        estimated_cost = 0.0
        final_text = ""
        used_model = request.model_override or settings.DEFAULT_MODEL
        used_static_booking = False
        run_llm = False
        booking_resume_suffix: Optional[str] = None

        live = entity_ctx.live_business_context or {}
        clinic_id = (
            (live.get("clinic") or {}).get("id")
            or entity_ctx.metadata.get("clinic_id")
            or (request.metadata or {}).get("clinicId")
            or (request.entity_id if request.entity_type.upper() == "CLINIC" else None)
        )
        tool_context = {
            "request_id": request_id,
            "clinic_id": clinic_id,
            "entity_id": request.entity_id,
            "participant_id": participant_id,
            "participant_name": (request.metadata or {}).get("participantName"),
            "conversation_id": cgs_conversation_id or conversation.id,
            "ai_conversation_id": conversation.id,
            "doctor_id": request.doctor_id
            or (request.metadata or {}).get("doctorId"),
            "entity": entity_ctx.model_dump(),
        }
        meta = request.metadata or {}
        addon_mgr = AddonMemoryManager()
        draft = booking_flow.load_draft(memory_ctx.pending_addons)
        force_wizard = False
        skip_wizard = False
        is_clinic = request.entity_type.upper() in ("CLINIC", "DOCTOR")
        apt_id = getattr(conversation, "last_appointment_id", None)
        apt_date = getattr(conversation, "last_appointment_date", None)
        verb_book = booking_flow.is_book_entry(request.message)
        want_reschedule = booking_flow.is_reschedule(request.message)
        tool_context["last_appointment_id"] = apt_id
        cgs_facts: Dict[str, Any] = {}

        if is_clinic and getattr(conversation, "awaiting_rebook", False) and (
            booking_flow.is_yes(request.message)
            or (verb_book and not want_reschedule)
        ):
            conversation.awaiting_rebook = False
            force_wizard = True
            logger.info(f"[{request_id}] Rebook confirmed — starting wizard")

        if is_clinic and apt_id and (
            not apt_date
            or want_reschedule
            or force_wizard
            or booking_flow.is_status_ask(request.message)
        ):
            cgs_facts = await self._cgs_appointment_facts(apt_id, clinic_id, request_id)
            cgs_date = str(cgs_facts.get("date") or "").strip()[:10]
            if cgs_date:
                apt_date = cgs_date
                conversation.last_appointment_date = cgs_date
        upcoming_apt = booking_flow.appointment_is_upcoming(
            apt_date, cgs_facts.get("status") if cgs_facts else None
        )

        if is_clinic and not force_wizard and (
            booking_flow.is_status_ask(request.message)
            or booking_flow.is_thanks_or_ack(request.message)
        ):
            skip_wizard = True

        if (
            is_clinic
            and upcoming_apt
            and not verb_book
            and not force_wizard
            and not want_reschedule
        ):
            if draft and draft.get("active"):
                draft = booking_flow.mark_draft_done(draft)
                await addon_mgr.upsert_addon(
                    db, conversation.id, booking_flow.BOOKING_DRAFT_TYPE, json.dumps(draft)
                )
                draft = None
            skip_wizard = True
            logger.info(
                f"[{request_id}] Skip wizard — upcoming appointment {apt_date} id={apt_id}"
            )

        # Static booking intake (language → service → age → gender → screen)
        # then AI owns date/time/slots/book (NEED_AI_SLOTS)
        booking_ai_handoff = False
        if (
            request.entity_type.upper() in ("CLINIC", "DOCTOR")
            and draft
            and draft.get("active")
            and booking_flow.draft_is_stale(draft, memory_ctx.pending_addons)
            and not booking_flow.is_book_intent(request.message)
            and not booking_flow.is_cancel(request.message, draft.get("step"))
        ):
            draft = booking_flow.mark_draft_done(draft)
            await addon_mgr.upsert_addon(
                db, conversation.id, booking_flow.BOOKING_DRAFT_TYPE, json.dumps(draft)
            )
            logger.info(f"[{request_id}] Stale booking draft closed (idle > 48h)")
            draft = None
        if request.entity_type.upper() in ("CLINIC", "DOCTOR") and booking_flow.is_cancel(
            request.message, (draft or {}).get("step")
        ) and draft and draft.get("active"):
            used_static_booking = True
            lang = booking_flow._lang(draft, memory_ctx.pending_addons)
            draft = booking_flow.mark_draft_done(draft)
            await addon_mgr.upsert_addon(
                db, conversation.id, booking_flow.BOOKING_DRAFT_TYPE, json.dumps(draft)
            )
            final_text = (
                "Theek hai, booking cancel kar di. Aur kuch chahiye toh bataiye."
                if lang == "hinglish"
                else "Okay, I've cancelled the booking flow. How else can I help?"
            )
            logger.info(f"[{request_id}] Static booking cancelled")
        elif (
            is_clinic
            and want_reschedule
            and not force_wizard
            and not upcoming_apt
        ):
            conversation.awaiting_rebook = True
            if draft and draft.get("active"):
                draft = booking_flow.mark_draft_done(draft)
                await addon_mgr.upsert_addon(
                    db, conversation.id, booking_flow.BOOKING_DRAFT_TYPE, json.dumps(draft)
                )
                draft = None
            used_static_booking = True
            lang = booking_flow._lang({}, memory_ctx.pending_addons)
            last_bit = f" Last visit was {apt_date}." if apt_date else ""
            final_text = (
                f"Abhi koi ongoing appointment nahi hai.{last_bit} "
                "Nayi booking karni hai? Yes ya No likhein."
                if lang == "hinglish"
                else (
                    f"There is no ongoing appointment.{last_bit} "
                    "Would you like to book a new one? Reply Yes or No."
                )
            )
            logger.info(f"[{request_id}] Reschedule — no upcoming apt, offered new book")
        elif (
            is_clinic
            and booking_flow.is_status_ask(request.message)
            and not force_wizard
        ):
            lang = booking_flow._lang(draft or {}, memory_ctx.pending_addons)
            facts = cgs_facts
            if apt_id and not facts:
                facts = await self._cgs_appointment_facts(
                    apt_id, clinic_id, request_id
                )
            if facts:
                executed_tool_calls.append(
                    ToolCallInfo(
                        tool_name="fetch_appointment",
                        arguments={"appointmentId": apt_id} if apt_id else {},
                        result=facts,
                    )
                )
                if facts.get("date"):
                    conversation.last_appointment_date = str(facts["date"])[:10]
            conversation.awaiting_rebook = True
            used_static_booking = True
            final_text = booking_flow.format_appointment_status(facts, lang)
            logger.info(
                f"[{request_id}] Status card fetch_appointment "
                f"apt={apt_id} has_facts={bool(facts)}"
            )
        elif (
            is_clinic
            and getattr(conversation, "awaiting_rebook", False)
            and not force_wizard
            and not want_reschedule
        ):
            if booking_flow.is_cancel(request.message, "NEED_AI_SLOTS"):
                conversation.awaiting_rebook = False
                used_static_booking = True
                final_text = (
                    "Theek hai. Booking nahi karenge. Aur kuch chahiye toh bataiye."
                    if booking_flow._lang(draft or {}, memory_ctx.pending_addons) == "hinglish"
                    else "Okay, I won't start a new booking. How else can I help?"
                )
                logger.info(f"[{request_id}] Rebook declined")
            else:
                used_static_booking = True
                final_text = (
                    "Nayi appointment book karni hai? Yes ya No likhein."
                    if booking_flow._lang(draft or {}, memory_ctx.pending_addons) == "hinglish"
                    else "Would you like to book a new appointment? Reply Yes or No."
                )
        elif (
            is_clinic
            and apt_date
            and not upcoming_apt
            and not verb_book
            and not skip_wizard
            and getattr(conversation, "rebook_asked_date", None) != apt_date
        ):
            conversation.awaiting_rebook = True
            conversation.rebook_asked_date = apt_date
            if draft and draft.get("active"):
                draft = booking_flow.mark_draft_done(draft)
                await addon_mgr.upsert_addon(
                    db, conversation.id, booking_flow.BOOKING_DRAFT_TYPE, json.dumps(draft)
                )
                draft = None
            used_static_booking = True
            lang = booking_flow._lang(draft or {}, memory_ctx.pending_addons)
            final_text = (
                f"Aapki last appointment {apt_date} ko thi. Nayi book karni hai? "
                "Yes likhein wizard ke liye, No se skip."
                if lang == "hinglish"
                else (
                    f"Your last appointment was on {apt_date}. "
                    "Would you like to book a new one? Reply Yes to start, or No to skip."
                )
            )
            logger.info(f"[{request_id}] Asked rebook after appointment date {apt_date}")
        elif (
            is_clinic
            and not skip_wizard
            and (
                force_wizard
                or (want_reschedule and upcoming_apt)
                or booking_flow.should_enter_booking(
                    request.message, draft, pack, memory_ctx.pending_addons
                )
            )
        ):
            was_active = bool(draft and draft.get("active"))
            step_before = (draft or {}).get("step") if was_active else None
            before_snap = dict(draft) if was_active and draft else {}

            if want_reschedule and upcoming_apt:
                draft = booking_flow.seed_reschedule_draft(
                    memory_ctx.pending_addons,
                    meta,
                    apt_id,
                    apt_date,
                )
                draft = booking_flow.apply_message_to_draft(
                    draft, request.message, meta
                )
                logger.info(
                    f"[{request_id}] Reschedule upcoming {apt_date} → "
                    f"date={draft.get('date') or 'ask'}"
                )
            elif force_wizard and apt_id:
                facts = cgs_facts or await self._cgs_appointment_facts(
                    apt_id, clinic_id, request_id
                )
                if facts.get("service"):
                    meta = {**meta, "serviceInterested": facts.get("service")}
                draft = booking_flow.seed_returning_book_draft(
                    memory_ctx.pending_addons, meta, facts
                )
                logger.info(
                    f"[{request_id}] Returning book — CGS facts age={facts.get('patientAge')} "
                    f"→ NEED_SCREEN only"
                )
            elif not was_active:
                draft = booking_flow.seed_draft_from_context(
                    request.message, memory_ctx.pending_addons, meta
                )
                logger.info(f"[{request_id}] Static booking started step={draft.get('step')}")
            elif step_before == "NEED_AI_SLOTS":
                if (draft.get("mode") or "") == "reschedule":
                    draft = booking_flow.apply_message_to_draft(
                        draft, request.message, meta
                    )
                else:
                    draft["step"] = "NEED_AI_SLOTS"
                logger.info(
                    f"[{request_id}] Booking AI-slots turn "
                    f"mode={draft.get('mode')} date={draft.get('date')}"
                )
            else:
                draft = booking_flow.apply_message_to_draft(draft, request.message, meta)
                logger.info(
                    f"[{request_id}] Static booking {step_before} → {draft.get('step')} "
                    f"lang={draft.get('language')} service={draft.get('service')} "
                    f"age={draft.get('age')} gender={draft.get('gender')}"
                )

            draft = await booking_flow.hydrate_booking_options(draft, tool_context)
            if draft.get("doctorId"):
                tool_context["doctor_id"] = draft["doctorId"]
            draft["step"] = booking_flow.next_missing_step(draft, meta)
            step = draft.get("step")

            if draft.get("language"):
                await addon_mgr.upsert_addon(
                    db,
                    conversation.id,
                    "LANGUAGE_PREFERENCE",
                    str(draft["language"]),
                )

            lang = booking_flow._lang(draft, memory_ctx.pending_addons)
            step = draft.get("step")
            await addon_mgr.upsert_addon(
                db, conversation.id, booking_flow.BOOKING_DRAFT_TYPE, json.dumps(draft)
            )
            screening = booking_flow.screening_addon_content(draft)
            if screening:
                await addon_mgr.upsert_addon(
                    db,
                    conversation.id,
                    booking_flow.PATIENT_SCREENING_TYPE,
                    screening,
                )

            if (
                was_active
                and step_before
                and step_before not in ("NEED_AI_SLOTS", "NEED_DOCTOR", "NEED_SERVICE")
                and booking_flow.is_booking_side_question(
                    step_before, request.message, before_snap, draft
                )
            ):
                booking_resume_suffix = booking_flow.resume_booking_line(step_before, lang)
                run_llm = True
                logger.info(
                    f"[{request_id}] Booking side-question at {step_before} — LLM then resume"
                )
            elif step == "NEED_AI_SLOTS":
                # Hand off to LLM for natural dates + real slot listing + book
                booking_ai_handoff = True
                run_llm = True
                pack = "booking"
                tool_names = list(self._TOOLS_BOOKING)
                max_tool_turns = 3
                tool_schemas = tool_registry.get_openrouter_schemas(
                    entity_ctx.capabilities,
                    tool_names=tool_names,
                )
                logger.info(f"[{request_id}] Booking → AI slots/book handoff")
            elif step == "DONE":
                used_static_booking = True
                final_text = (
                    "Yeh visit already file pe hai. Aur kuch chahiye?"
                    if lang == "hinglish"
                    else "That visit is already on file. Anything else I can help with?"
                )
            else:
                used_static_booking = True
                if (
                    step == "NEED_LANGUAGE"
                    and booking_flow.parse_language(request.message) is None
                    and draft.get("language") is None
                    and not booking_flow.is_book_intent(request.message)
                ):
                    final_text = (
                        "Hinglish theek rahegi ya English? Jo comfortable ho woh likh dena.\n"
                        "Hinglish or English — whichever is easier."
                    )
                elif (
                    step == "NEED_AGE"
                    and booking_flow.parse_age(request.message) is None
                    and draft.get("age") is None
                    and not booking_flow.is_book_intent(request.message)
                ):
                    final_text = (
                        "Age sirf number mein bhej dena, jaise 23 — phir aage badhte hain."
                        if lang == "hinglish"
                        else "Just send the age as a number, like 23, and I'll keep going."
                    )
                elif (
                    step == "NEED_GENDER"
                    and booking_flow.parse_gender(request.message) is None
                    and draft.get("gender") is None
                    and not booking_flow.is_book_intent(request.message)
                ):
                    final_text = (
                        "Gender Male, Female ya Other mein likh dena — jo bhi apply hota hai."
                        if lang == "hinglish"
                        else "You can reply Male, Female, or Other — whichever applies."
                    )
                else:
                    final_text = booking_flow.prompt_for_step(step, lang, draft)

        # Hard early-exit: greet / post-booking outro / mid-chat ack — no OpenRouter
        elif max_tool_turns == 0 and pack in ("greet", "outro", "ack", "close"):
            used_static_booking = True
            clinic_name = entity_ctx.name or "our clinic"
            if pack == "outro":
                final_text = self._fast_outro_reply(memory_ctx, clinic_name)
            elif pack == "ack":
                final_text = self._fast_ack_reply(memory_ctx)
            elif pack == "close":
                final_text = self._fast_close_reply(memory_ctx, clinic_name)
            else:
                final_text = self._fast_greeting_reply(
                    memory_ctx,
                    request.metadata or {},
                    clinic_name,
                )
            logger.info(f"[{request_id}] Fast {pack} path (no LLM): {final_text[:80]}")
        else:
            run_llm = True

        if run_llm:
            # 5. Build Messages Array
            messages = prompt_builder.build_prompt_messages(
                entity_ctx=entity_ctx,
                memory_ctx=memory_ctx,
                current_user_message=request.message,
                request_metadata=request.metadata or {},
            )
            if apt_id:
                messages.append({
                    "role": "system",
                    "content": (
                        f"This patient already has lastAppointmentId={apt_id} "
                        f"on {apt_date or 'unknown date'}. "
                        "If they ask about that visit, call fetch_appointment with that id "
                        "(or omit id to use the saved one). "
                        "Do not start a new booking unless they use the verb book "
                        "(e.g. book appointment / book karo)."
                    ),
                })
            if booking_ai_handoff and draft:
                handoff = booking_flow.ai_slot_handoff_system(
                    draft, booking_flow._lang(draft, memory_ctx.pending_addons)
                )
                if (draft.get("mode") or "") == "returning":
                    slots = await booking_flow.prefetch_open_slots(tool_context)
                    open_list = slots.get("availableSlots") or []
                    if open_list:
                        draft["date"] = slots.get("date")
                        await addon_mgr.upsert_addon(
                            db,
                            conversation.id,
                            booking_flow.BOOKING_DRAFT_TYPE,
                            json.dumps(draft),
                        )
                        shown = ", ".join(str(s) for s in open_list[:12])
                        handoff += (
                            f"\nCGS already returned open slots for {slots.get('date')}: {shown}. "
                            "Do NOT ask for a date. List these slots and wait for a time."
                        )
                        logger.info(
                            f"[{request_id}] Returning book — prefetched "
                            f"{len(open_list)} slots on {slots.get('date')}"
                        )
                messages.append({
                    "role": "system",
                    "content": handoff,
                })
            if booking_resume_suffix:
                # Mid-wizard FAQ: info tools only — never check/book from a side question
                pack = "info"
                tool_names = list(self._TOOLS_INFO)
                max_tool_turns = 2
                tool_schemas = tool_registry.get_openrouter_schemas(
                    entity_ctx.capabilities,
                    tool_names=tool_names,
                )
                messages.append({
                    "role": "system",
                    "content": (
                        "The patient is mid appointment booking and asked a side question. "
                        "Answer ONLY that question in 2-4 short sentences using clinic context/tools if needed. "
                        "Do NOT call check_availability or book_appointment. "
                        "Do NOT collect booking fields (age, gender, date, service). "
                        "Do NOT list open appointment slots. "
                        "Do NOT end by asking them to continue booking — a follow-up line will be appended."
                    ),
                })
            elif pack == "info" and not booking_ai_handoff:
                messages.append({
                    "role": "system",
                    "content": (
                        "This is an INFO turn. Answer fees, doctors, timings, address, or services. "
                        "Do NOT list specific open appointment slots. "
                        "Do NOT call check_availability. If they want a slot, ask them to say they want to book."
                    ),
                })

            # 6. Multi-Step OpenRouter Tool Execution Loop
            turn = 0

            while turn < max_tool_turns:
                turn += 1
                max_tokens = self._max_tokens_for_pack(pack, bool(tool_schemas))
                try:
                    llm_res = await self.provider.generate(
                        messages=messages,
                        tools=tool_schemas if tool_schemas else None,
                        model=used_model,
                        temperature=request.temperature or 0.3,
                        max_tokens=max_tokens,
                    )
                except Exception as llm_err:
                    logger.error(f"[{request_id}] All LLM models failed: {llm_err}")
                    card = clinic_card_reply(
                        request.message,
                        entity_ctx.live_business_context or {},
                        entity_ctx.name or "our clinic",
                    )
                    final_text = card or self._REPLY_GENERIC_EMPTY
                    if booking_resume_suffix and card:
                        final_text = f"{final_text}\n\n{booking_resume_suffix}"
                    break

                total_prompt_tokens += llm_res.prompt_tokens
                total_completion_tokens += llm_res.completion_tokens
                estimated_cost += llm_res.estimated_cost
                used_model = llm_res.model

                if llm_res.tool_calls:
                    messages.append({
                        "role": "assistant",
                        "content": llm_res.content or "",
                        "tool_calls": llm_res.tool_calls,
                    })

                    # Run one.
                    async def _run_one(tc: Dict[str, Any]) -> Tuple[Dict[str, Any], str, Dict, Any]:
                        fn = tc.get("function", {})
                        tool_name = fn.get("name") or "unknown"
                        try:
                            args = json.loads(fn.get("arguments", "{}"))
                        except Exception:
                            args = {}
                        logger.info(f"Executing tool [{tool_name}] with args: {args}")
                        tool = tool_registry.get_tool(tool_name)
                        if not tool:
                            return tc, tool_name, args, {"error": f"Tool {tool_name} is not available."}
                        try:
                            result = await tool.execute(arguments=args, context=tool_context)
                            return tc, tool_name, args, result
                        except Exception as tool_err:
                            logger.error(f"Error executing tool {tool_name}: {tool_err}")
                            return tc, tool_name, args, {"error": str(tool_err)}

                    # Parallel tool execution when the model requests several at once
                    results = await asyncio.gather(
                        *[_run_one(tc) for tc in llm_res.tool_calls]
                    )
                    for tc, tool_name, args, tool_result in results:
                        compact = self._compact_tool_result(tool_name, tool_result)
                        executed_tool_calls.append(
                            ToolCallInfo(tool_name=tool_name, arguments=args, result=tool_result)
                        )
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.get("id", f"call_{uuid.uuid4().hex[:6]}"),
                            "name": tool_name,
                            "content": json.dumps(compact, default=str),
                        })

                    booked_ok = any(
                        tc.tool_name in ("book_appointment", "reschedule_appointment")
                        and isinstance(tc.result, dict)
                        and not tc.result.get("error")
                        for tc in executed_tool_calls
                    )
                    if booked_ok:
                        tool_schemas = None
                        book_res = next(
                            (
                                tc.result
                                for tc in reversed(executed_tool_calls)
                                if tc.tool_name in (
                                    "book_appointment",
                                    "reschedule_appointment",
                                )
                                and isinstance(tc.result, dict)
                                and not tc.result.get("error")
                            ),
                            {},
                        )
                        aid = book_res.get("appointmentId")
                        adate = book_res.get("date")
                        if aid:
                            conversation.last_appointment_id = str(aid)
                            conversation.last_appointment_date = (
                                str(adate)[:10] if adate else conversation.last_appointment_date
                            )
                            conversation.awaiting_rebook = False
                            conversation.rebook_asked_date = None
                            logger.info(
                                f"[{request_id}] Saved last appointment {aid} date={adate}"
                            )
                        if draft and draft.get("active"):
                            screening = booking_flow.screening_addon_content(draft)
                            if screening:
                                await addon_mgr.upsert_addon(
                                    db,
                                    conversation.id,
                                    booking_flow.PATIENT_SCREENING_TYPE,
                                    screening,
                                )
                            draft = booking_flow.mark_draft_done(draft)
                            await addon_mgr.upsert_addon(
                                db,
                                conversation.id,
                                booking_flow.BOOKING_DRAFT_TYPE,
                                json.dumps(draft),
                            )
                    elif turn >= 2 and not any(
                        tc.tool_name in ("book_appointment", "reschedule_appointment")
                        for tc in executed_tool_calls
                    ):
                        if any(tc.tool_name == "check_availability" for tc in executed_tool_calls):
                            confirm_tool = (
                                "reschedule_appointment"
                                if draft and (draft.get("mode") or "") == "reschedule"
                                else "book_appointment"
                            )
                            tool_schemas = tool_registry.get_openrouter_schemas(
                                entity_ctx.capabilities,
                                tool_names=[
                                    confirm_tool,
                                    "check_availability",
                                    "request_human_handoff",
                                ],
                            )
                        else:
                            tool_schemas = None
                else:
                    final_text = (llm_res.content or "").strip()

                    if final_text and llm_res.finish_reason == "length":
                        logger.warning(
                            f"[{request_id}] Truncated reply on turn {turn} "
                            f"({llm_res.completion_tokens} tokens): {final_text[-60:]!r}"
                        )
                        if turn < max_tool_turns:
                            messages.append({"role": "assistant", "content": final_text})
                            messages.append({
                                "role": "user",
                                "content": (
                                    "Your reply was cut off. Send the complete reply again from the "
                                    "start, under 120 words. If you still need to call a tool to "
                                    "answer, call it now instead of narrating it."
                                ),
                            })
                            final_text = ""
                            continue

                    if final_text:
                        break

                    if turn < max_tool_turns:
                        logger.warning(
                            f"[{request_id}] Empty model reply on turn {turn} — retrying text-only "
                            f"(tool_calls so far: {len(executed_tool_calls)})"
                        )
                        messages.append({
                            "role": "user",
                            "content": (
                                "Your previous reply was empty. Reply to the patient NOW in plain text, "
                                "in their language, using the tool results above. "
                                "If an appointment was booked, state service, doctor, date and time (IST). "
                                + (
                                    "If slots were checked, list the available times. "
                                    if self._allow_availability_narration(pack, request.message)
                                    else "Do not invent or list appointment slots. "
                                )
                                + "Do not call any tool. "
                                  "Never mention tool names, UUID, doctorId, or JSON to the patient."
                            ),
                        })
                        tool_schemas = None
                        continue
                    break

            if booking_resume_suffix and (final_text or "").strip():
                final_text = final_text.rstrip() + "\n\n" + booking_resume_suffix
            elif booking_resume_suffix and not (final_text or "").strip():
                # LLM failed — still keep them on the booking step
                final_text = booking_resume_suffix

        lang_for_guard = "english"
        if draft:
            lang_for_guard = booking_flow._lang(draft, memory_ctx.pending_addons)
        booking_facts = booking_flow.confirmed_booking_facts(
            executed_tool_calls, draft
        )

        if (final_text or "").strip():
            final_text, leaked = authenticate_patient_reply(
                final_text, pack, lang_for_guard, request_id
            )
            if leaked:
                logger.warning(f"[{request_id}] Booking/info reply replaced by reply_guard")
                if booking_facts:
                    final_text = booking_flow.format_confirmed_appointment(
                        service=booking_facts["service"],
                        doctor=booking_facts["doctor"],
                        date=booking_facts["date"],
                        time=booking_facts["time"],
                        lang=lang_for_guard,
                        rescheduled=booking_facts["rescheduled"],
                    )

        if not (final_text or "").strip():
            # Last resort: answer from tool results instead of a generic line that also
            # poisons conversation memory on the next turn.
            availability = next(
                (
                    tc.result
                    for tc in reversed(executed_tool_calls)
                    if tc.tool_name == "check_availability" and isinstance(tc.result, dict)
                ),
                None,
            )

            if booking_facts:
                final_text = booking_flow.format_confirmed_appointment(
                    service=booking_facts["service"],
                    doctor=booking_facts["doctor"],
                    date=booking_facts["date"],
                    time=booking_facts["time"],
                    lang=lang_for_guard,
                    rescheduled=booking_facts["rescheduled"],
                )
            elif (
                availability
                and not (isinstance(availability, dict) and availability.get("error"))
                and self._allow_availability_narration(pack, request.message)
            ):
                if availability.get("availableSlots"):
                    slots = ", ".join(availability["availableSlots"][:6])
                    final_text = (
                        f"On {availability.get('date')} I still have {slots}. "
                        "Which of those works for you?"
                    )
                else:
                    final_text = (
                        f"Nothing free on {availability.get('date')} from what I can see. "
                        "Want me to check another day?"
                    )
            elif self._all_tools_failed(executed_tool_calls):
                final_text = self._REPLY_TOOLS_FAILED
            elif (
                want_reschedule
                or (draft and (draft.get("mode") or "") == "reschedule")
            ):
                lang_fb = booking_flow._lang(draft or {}, memory_ctx.pending_addons)
                if not draft or (draft.get("mode") or "") != "reschedule":
                    draft = booking_flow.seed_reschedule_draft(
                        memory_ctx.pending_addons, meta, apt_id, apt_date
                    )
                draft = booking_flow.apply_message_to_draft(
                    draft, request.message, meta
                )
                msg, tools, draft = await booking_flow.static_reschedule_reply(
                    draft, tool_context, lang_fb
                )
                executed_tool_calls.extend(tools)
                await addon_mgr.upsert_addon(
                    db,
                    conversation.id,
                    booking_flow.BOOKING_DRAFT_TYPE,
                    json.dumps(draft),
                )
                booked_ok = any(
                    getattr(tc, "tool_name", None)
                    in ("book_appointment", "reschedule_appointment")
                    and isinstance(getattr(tc, "result", None), dict)
                    and not tc.result.get("error")
                    for tc in tools
                )
                if booked_ok:
                    book_res = next(
                        tc.result
                        for tc in reversed(tools)
                        if getattr(tc, "tool_name", None)
                        in ("book_appointment", "reschedule_appointment")
                    )
                    aid = book_res.get("appointmentId")
                    adate = book_res.get("date") or draft.get("date")
                    if aid:
                        conversation.last_appointment_id = str(aid)
                        conversation.last_appointment_date = (
                            str(adate)[:10] if adate else conversation.last_appointment_date
                        )
                        conversation.awaiting_rebook = False
                final_text = msg
                logger.info(
                    f"[{request_id}] Reschedule LLM-fail fallback "
                    f"date={draft.get('date')} time={draft.get('time')}"
                )
            else:
                card = clinic_card_reply(
                    request.message,
                    entity_ctx.live_business_context or {},
                    entity_ctx.name or "our clinic",
                )
                final_text = card or self._REPLY_GENERIC_EMPTY
                if booking_resume_suffix and card:
                    final_text = f"{final_text}\n\n{booking_resume_suffix}"
            logger.warning(f"[{request_id}] Built fallback reply from tool results: {final_text[:80]}")
        else:
            final_text = final_text.strip()

        lang_for_guard = "english"
        if draft:
            lang_for_guard = booking_flow._lang(draft, memory_ctx.pending_addons)
        final_text, leaked = authenticate_patient_reply(
            final_text, pack, lang_for_guard, request_id
        )
        if leaked:
            logger.warning(f"[{request_id}] Final reply blocked by reply_guard")
            booking_facts = booking_facts or booking_flow.confirmed_booking_facts(
                executed_tool_calls, draft
            )
            if booking_facts:
                final_text = booking_flow.format_confirmed_appointment(
                    service=booking_facts["service"],
                    doctor=booking_facts["doctor"],
                    date=booking_facts["date"],
                    time=booking_facts["time"],
                    lang=lang_for_guard,
                    rescheduled=booking_facts["rescheduled"],
                )

        duration_ms = int((time.time() - start_time) * 1000)

        # 7. Persist Messages in AI Database
        user_msg = MessageModel(
            conversation_id=conversation.id,
            sender_type="USER",
            content=request.message,
            tokens=len(request.message) // 4,
        )
        ai_msg = MessageModel(
            conversation_id=conversation.id,
            sender_type="AI",
            content=final_text,
            tokens=total_completion_tokens,
            msg_metadata={"tool_calls": [tc.model_dump() for tc in executed_tool_calls]},
        )
        db.add(user_msg)
        db.add(ai_msg)

        addon_mgr_persist = addon_mgr
        for addon_type, addon_content in extract_addons(request.message, request.entity_type):
            await addon_mgr_persist.add_addon(db, conversation.id, addon_type, addon_content)

        # 8. Record AI Usage Log in AI Database
        usage_log = AiUsageLogModel(
            entity_id=request.entity_id,
            conversation_id=conversation.id,
            request_id=request_id,
            model=used_model,
            prompt_tokens=total_prompt_tokens,
            completion_tokens=total_completion_tokens,
            total_tokens=total_prompt_tokens + total_completion_tokens,
            estimated_cost=estimated_cost,
            duration_ms=duration_ms,
            success=True,
            usage_metadata={
                "channel": request.channel,
                "tool_calls_count": len(executed_tool_calls),
            },
        )
        db.add(usage_log)
        await db.flush()

        # Usage payload for CGS — forwarded by FastAPI BackgroundTasks after reply is returned
        usage_payload: Optional[Dict[str, Any]] = None
        if settings.CGS_INTERNAL_URL:
            doc_id = request.doctor_id or (
                request.metadata.get("doctorId") or request.metadata.get("doctor_id")
                if request.metadata
                else None
            )
            usage_payload = {
                "clinicId": request.entity_id
                if request.entity_type.upper() == "CLINIC"
                else (request.metadata or {}).get("clinicId"),
                "doctorId": doc_id,
                "conversationId": request.conversation_id or conversation.id,
                "requestId": request_id,
                "entityType": request.entity_type.upper(),
                "entityId": request.entity_id,
                "operationType": "CONVERSATION_RESPONSE",
                "provider": "groq" if "groq.com" in getattr(self.provider, "base_url", "") else "openrouter",
                "model": used_model,
                "inputTokens": total_prompt_tokens,
                "outputTokens": total_completion_tokens,
                "totalTokens": total_prompt_tokens + total_completion_tokens,
                "inputCost": float(estimated_cost * 0.5),
                "outputCost": float(estimated_cost * 0.5),
                "totalCost": float(estimated_cost),
                "currency": "INR",
                "durationMs": duration_ms,
                "success": True,
            }

        return (
            GenerateResponse(
                request_id=request_id,
                entity_id=request.entity_id,
                entity_type=request.entity_type,
                conversation_id=conversation.id,
                response=final_text,
                tool_calls=executed_tool_calls,
                usage=TokenUsage(
                    prompt_tokens=total_prompt_tokens,
                    completion_tokens=total_completion_tokens,
                    total_tokens=total_prompt_tokens + total_completion_tokens,
                    estimated_cost=estimated_cost,
                ),
                model=used_model,
                duration_ms=duration_ms,
            ),
            usage_payload,
        )


ai_orchestrator = AIOrchestrator()
