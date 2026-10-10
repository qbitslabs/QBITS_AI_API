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


def _instructions_only(text: str) -> str:
    kept = []
    for line in (text or "").splitlines():
        low = line.strip().lower()
        if low.startswith("receptionist name:") or low.startswith("greeting:"):
            continue
        kept.append(line)
    return "\n".join(kept).strip()


def _clip(text: str, limit: int) -> str:
    body = (text or "").strip()
    if len(body) <= limit:
        return body
    return body[:limit].rstrip() + "…"


_ROSTER_HEADINGS = (
    "Doctors:",
    "Consultation Details:",
    "Timings:",
    "Services & Treatments:",
)


def _drop_roster_sections(text: str) -> str:
    kept = []
    for block in (text or "").split("\n\n"):
        head = block.strip().split("\n", 1)[0].strip()
        if head in _ROSTER_HEADINGS:
            continue
        if block.strip():
            kept.append(block.strip())
    return "\n\n".join(kept)


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
        raw_instructions = meta.get("conversationInstructions") or _instructions_only(
            ai.get("customInstructions") or ""
        )
        instructions = _clip(
            raw_instructions
            or "Welcome the patient, listen carefully, and help them with the clinic's doctors, visits, and bookings.",
            900,
        )
        knowledge = _clip(_drop_roster_sections((ai.get("systemPrompt") or "").strip()), 1200)
        if not knowledge:
            knowledge_bits = [
                meta.get("clinicInformation") and f"About the clinic:\n{meta.get('clinicInformation')}",
                meta.get("faqs") and f"FAQs:\n{meta.get('faqs')}",
            ]
            knowledge = _clip("\n\n".join([b for b in knowledge_bits if b]), 1200)

        sections: List[str] = [
            f"You are {receptionist}, the WhatsApp receptionist for {clinic_name}. "
            f"Your tone is {tone}. Language setting: {language}.",
            "Manner:\n"
            "Be polite, patient, and genuinely helpful. Sound like a real receptionist, not a script.\n"
            "Acknowledge what the patient just said, then help with the next useful step.\n"
            "Use simple words. Ask one clear question at a time.\n"
            "If you cannot do something, say so kindly and offer to connect them with the clinic staff.\n"
            "Once they choose Hinglish or English, stay in that language unless they switch.",
            f"Clinic instructions:\n{instructions}",
            "Accuracy:\n"
            "- Continue this same conversation. Do not restart it or invent a new topic.\n"
            "- Doctors, working days, and hours come only from the live clinic card.\n"
            "- Fees, visit length, and what a service includes come only from the services on that card.\n"
            "- Never invent a doctor, a fee, a working hour, or an open appointment time.\n"
            "- When they ask what is free, use check_availability and mention only the times it returns.\n"
            "- Say a visit is booked only after book_appointment returns an appointmentId. "
            "Then repeat the service, the doctor, the date, and the time.\n"
            "- If that time was just taken, apologize briefly and offer the times that are still open.\n"
            "- Do not diagnose or give medical advice. Invite them to discuss that with the doctor.\n"
            "- Never show tool names, UUIDs, or JSON.\n"
            "- Offer a person when they ask for staff, seem stuck, or describe an emergency.",
        ]

        if knowledge:
            sections.append(
                "About this clinic and its FAQs. If this wording disagrees with the live clinic card, "
                "trust the card for doctors, hours, fees, and services:\n"
                + knowledge
            )

        if ai.get("isAiEnabled") is False:
            sections.append(
                "NOTE: AI receptionist is marked disabled for this clinic. Keep replies minimal and offer human staff contact."
            )

        return "\n\n".join(sections)
