# AI entity resolver: clinic.
# Maps patient language to clinic/doctor/generic records without guessing UUIDs.
import time
from typing import Dict, Any, List, Optional, Tuple
from app.core.config import settings
from app.core.logging import logger
from app.core.http_client import get_http_client
from app.entities.base import BaseEntityAdapter, EntityContext

# Clinic context changes rarely — cache to skip CGS on every WhatsApp message
_CONTEXT_CACHE: Dict[str, Tuple[float, Dict[str, Any], str]] = {}
_CONTEXT_TTL_SEC = 120.0


# Drop live clinic-card TTL entries. If clinic_id is None, clear all.
def invalidate_context_cache(clinic_id: Optional[str] = None) -> int:
    if not clinic_id:
        n = len(_CONTEXT_CACHE)
        _CONTEXT_CACHE.clear()
        return n
    if clinic_id in _CONTEXT_CACHE:
        _CONTEXT_CACHE.pop(clinic_id, None)
        return 1
    return 0


# Clinic entity adapter.
class ClinicEntityAdapter(BaseEntityAdapter):
    # Resolve.
    async def resolve(
        self,
        entity_id: str,
        session_or_context: Dict[str, Any]
    ) -> EntityContext:
        clinic_name = "Clinic Growth Center"
        live_data: Dict[str, Any] = {}

        cached = _CONTEXT_CACHE.get(entity_id)
        if cached and time.monotonic() <= cached[0]:
            live_data, clinic_name = cached[1], cached[2]
            logger.info(f"Clinic context cache hit for {entity_id}")
        else:
            url = f"{settings.CGS_INTERNAL_URL}/cgs_api/v1/internal/ai/context"
            headers = {
                "Content-Type": "application/json",
                "X-Internal-Service-Key": settings.CGS_INTERNAL_SERVICE_KEY,
                "X-Request-ID": session_or_context.get("request_id", "ai_req_internal"),
            }
            try:
                client = get_http_client()
                res = await client.post(
                    url,
                    json={"clinicId": entity_id},
                    headers=headers,
                    timeout=10,
                )
                if res.status_code == 200:
                    payload = res.json().get("data", {})
                    live_data = payload
                    clinic_name = payload.get("clinic", {}).get("name", clinic_name)
                    _CONTEXT_CACHE[entity_id] = (
                        time.monotonic() + _CONTEXT_TTL_SEC,
                        live_data,
                        clinic_name,
                    )
            except Exception as err:
                logger.warning(
                    f"Could not connect to CGS internal API: {err}. Using fallback clinic context."
                )

        system_prompt = self._build_system_prompt(clinic_name, live_data)

        return EntityContext(
            id=entity_id,
            type="CLINIC",
            name=clinic_name,
            system_prompt=system_prompt,
            capabilities=["clinic", "generic"],
            live_business_context=live_data,
            metadata={"source": "cgs_backend"},
        )

    # Build system prompt.
    def _build_system_prompt(self, clinic_name: str, live_data: Dict[str, Any]) -> str:
        ai = live_data.get("aiConfig") or {}
        meta = ai.get("metadata") if isinstance(ai.get("metadata"), dict) else {}

        receptionist = (
            meta.get("receptionistName")
            or ai.get("receptionistName")
            or "Asha"
        )
        tone = (ai.get("tone") or meta.get("tone") or "friendly").lower()
        language = (
            meta.get("language")
            or ai.get("language")
            or "auto"
        )
        greeting = (
            meta.get("greetingMessage")
            or ai.get("greetingMessage")
            or f"Namaste! {clinic_name} me aapka swagat hai. Main kaise madad kar sakti hoon?"
        )
        instructions = (
            meta.get("conversationInstructions")
            or ai.get("customInstructions")
            or "Be warm and natural like a WhatsApp receptionist. Prefer Hindi/Hinglish when the patient does; keep replies short."
        )
        knowledge = (ai.get("systemPrompt") or "").strip()
        if not knowledge:
            knowledge_bits = [
                meta.get("clinicInformation") and f"Clinic Information:\n{meta.get('clinicInformation')}",
                meta.get("servicesTreatments") and f"Services & Treatments:\n{meta.get('servicesTreatments')}",
                meta.get("doctorsInfo") and f"Doctors:\n{meta.get('doctorsInfo')}",
                meta.get("consultationDetails") and f"Consultation Details:\n{meta.get('consultationDetails')}",
                meta.get("timings") and f"Timings:\n{meta.get('timings')}",
                meta.get("faqs") and f"FAQs:\n{meta.get('faqs')}",
            ]
            knowledge = "\n\n".join([b for b in knowledge_bits if b])

        # Qualification + handoff are fixed (not configurable) to keep clinic setup simple.
        # Keep this compact — every token here is re-sent on every OpenRouter turn.
        sections: List[str] = [
            f"You are {receptionist}, AI receptionist for {clinic_name}. Tone: {tone}. "
            f"Default language setting: {language} (auto = ask once Hinglish or English, then stick to it).",
            f"Greeting when starting:\n\"{greeting}\"",
            f"Instructions:\n{instructions}",
            "RULES:\n"
            "- Read recent chat + lead facts; continue the same thread. Do not restart or invent topics.\n"
            "- Ask Hinglish vs English once if unknown; if LANGUAGE_PREFERENCE exists, do not ask again.\n"
            "- Short replies (1–3 sentences). Ask 1–2 things at a time.\n"
            "- INFO (fees/timings/services/doctors): answer with tools/knowledge. Do NOT book.\n"
            "- BOOK only when patient clearly wants to book. Ask doctor first if several, then numbered services; patient replies 1, 2, …\n"
            "- Out of scope: decline politely; offer clinic help or staff. Do NOT book.\n"
            "- Collect name, age, gender before/while booking. NEVER invent age/gender.\n"
            "- If landing lead already has name/service/doctor, do not re-ask.\n"
            "- Before NEW book: brief BP/sugar/allergies screen. Pass age, gender, allergies, medicalHistoryNotes.\n"
            "- After doctor+service are chosen, check slots with a date only; never guess UUIDs.\n"
            "- Patient text: never mention tools, UUID, doctorId, JSON, or internal reasoning.\n"
            "- Empty slots / leave / off-duty → say unavailable; do NOT book.\n"
            "- Confirm booking status via get_patient first; never say unbooked if tool shows CONFIRMED.\n"
            "- NEVER say confirmed unless book_appointment succeeded. Then include service, doctor, date, time IST.\n"
            "- Preferring a doctor is NOT a handoff. Handoff only for human/staff request or emergency (severity=critical).\n"
            "- Resolve today/tomorrow from CURRENT DATE block. Never invent hours or medical advice.",
        ]

        if knowledge:
            sections.append(f"Clinic knowledge base:\n{knowledge}")

        if ai.get("isAiEnabled") is False:
            sections.append(
                "NOTE: AI receptionist is marked disabled for this clinic. Keep replies minimal and offer human staff contact."
            )

        return "\n\n".join(sections)
