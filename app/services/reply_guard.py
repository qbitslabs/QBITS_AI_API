# Last-line filter so WhatsApp never shows tools, UUIDs, JSON, or chain-of-thought.
# authenticate_patient_reply must run before persist/send; do not weaken it.
from __future__ import annotations

import re
from typing import Optional, Tuple

from app.core.logging import logger

# If any of these appear in the patient-facing reply, replace it.
LEAK_PATTERNS = (
    r"\bcheck_availability\b",
    r"\bbook_appointment\b",
    r"\breschedule_appointment\b",
    r"\bfetch_appointment\b",
    r"\bget_doctors\b",
    r"\bget_patient\b",
    r"\bget_lead\b",
    r"\bget_services\b",
    r"\bfaq_lookup\b",
    r"\brequest_human_handoff\b",
    r"\btool_calls?\b",
    r"\bfunction call\b",
    r"\bdoctorid\b",
    r"\bdoctor_id\b",
    r"\buuid\b",
    r"\bplaceholder\b",
    r"dummy uuid",
    r"clinic card",
    r"let me think",
    r"let me try to call",
    r"i need to call",
    r"i will call the tool",
    r"call the tool",
    r"the tool requires",
    r"tool description",
    r"openrouter",
    r"\bjson\b",
    r"\barguments\b",
    r"availableSlots",
    r"\bYYYY-MM-DD\b",
    r"valid uuid",
    r"doctor'?s uuid",
    r"pre-configured",
    r"string \(uuid\)",
)

LEAK_RE = re.compile("|".join(LEAK_PATTERNS), re.IGNORECASE)

SAFE_BOOKING_EN = (
    "I can check what's free that day. "
    "Send the date again — tomorrow, 25 Sept, or coming Monday all work."
)
SAFE_BOOKING_HI = (
    "Us din ke khali times dekh leti hoon. "
    "Date ek baar aur bhej dena — kal, 25 Sept, ya aane wala Monday."
)
SAFE_GENERIC_EN = "Sorry — I lost that. What did you need help with?"
SAFE_GENERIC_HI = "Maaf karna, woh cut ho gaya. Kis cheez mein help chahiye?"


# True if the draft looks like leaked internals / tool narration.
def is_leaked_reply(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return False
    if LEAK_RE.search(t):
        return True
    # Long self-talk even without a tagged tool name
    if len(t) > 420 and t.count("?") >= 3 and any(
        x in t.lower() for x in ("however", "perhaps", "i could", "let me")
    ):
        return True
    return False


# Patient-safe replacement (no tool names, no UUIDs).
def safe_fallback(pack: str, lang: str) -> str:
    hinglish = (lang or "").lower().startswith("hing")
    if pack in ("booking", "status"):
        return SAFE_BOOKING_HI if hinglish else SAFE_BOOKING_EN
    return SAFE_GENERIC_HI if hinglish else SAFE_GENERIC_EN


# Returns (text, replaced). Logs when a leak is blocked.
def authenticate_patient_reply(
    text: str,
    pack: str,
    lang: str = "english",
    request_id: Optional[str] = None,
) -> Tuple[str, bool]:
    body = (text or "").strip()
    if not is_leaked_reply(body):
        return body, False
    replacement = safe_fallback(pack, lang)
    logger.warning(
        f"[{request_id or '-'}] Reply guard blocked leaked {pack} draft "
        f"({len(body)} chars) → safe fallback"
    )
    return replacement, True
