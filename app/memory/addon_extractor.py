# Conversation memory: addon extractor.
# Stores or summarizes WhatsApp turns in the dedicated AI Postgres.
from typing import List, Tuple, Optional
import re


SERVICE_HINTS = (
    "implant", "aligner", "braces", "root canal", "rct", "whitening",
    "cleaning", "crown", "bridge", "extraction", "filling", "consultation",
)
TIME_HINTS = ("morning", "afternoon", "evening", "night", "am", "pm", "tomorrow", "kal", "aaj")
CONCERN_HINTS = ("pain", "dard", "bleeding", "swelling", "sensitive", "sensitivity", "infection")


# Detect explicit Hinglish vs English preference from the patient.
def _detect_language_preference(lower: str) -> Optional[str]:
    # English preferred
    if any(
        p in lower
        for p in (
            "english",
            "in english",
            "talk in english",
            "speak english",
            "english mein",
            "english me",
            "only english",
        )
    ) and "hinglish" not in lower and "hindi" not in lower:
        return "English"
    # Hinglish / Hindi preferred
    if any(
        p in lower
        for p in (
            "hinglish",
            "hindi",
            "hindi mein",
            "hindi me",
            "hinglish mein",
            "hinglish me",
            "in hindi",
            "in hinglish",
        )
    ):
        return "Hinglish"
    return None


# Extract addons.
def extract_addons(message: str, entity_type: str = "GENERIC") -> List[Tuple[str, str]]:
    text = (message or "").strip()
    if not text:
        return []

    lower = text.lower()
    addons: List[Tuple[str, str]] = []
    is_clinical = (entity_type or "").upper() in ["CLINIC", "DOCTOR"]

    lang = _detect_language_preference(lower)
    if lang:
        addons.append(("LANGUAGE_PREFERENCE", lang))

    if is_clinical:
        for hint in SERVICE_HINTS:
            if hint in lower:
                addons.append(("SERVICE_INTEREST", f"Patient mentioned {hint}"))
                break

        if any(h in lower for h in TIME_HINTS) or re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)?\b", lower):
            addons.append(("APPOINTMENT_PREFERENCE", text[:180]))

        if any(h in lower for h in CONCERN_HINTS):
            addons.append(("PATIENT_CONCERN", text[:180]))

        if "book" in lower or "appointment" in lower or "slot" in lower:
            addons.append(("BOOKING_CONTEXT", text[:180]))
    else:
        # Generic non-clinic entities (Chatbot, Restaurant, Ecommerce, General)
        if any(h in lower for h in TIME_HINTS) or re.search(r"\b\d{1,2}(:\d{2})?\s*(am|pm)?\b", lower):
            addons.append(("TIME_PREFERENCE", text[:180]))

        if any(h in lower for h in CONCERN_HINTS) or "issue" in lower or "problem" in lower or "help" in lower:
            addons.append(("USER_INQUIRY", text[:180]))

        if "prefer" in lower or "want" in lower or "like" in lower:
            addons.append(("USER_PREFERENCE", text[:180]))

        if "reserve" in lower or "order" in lower or "request" in lower:
            addons.append(("ACTION_REQUEST", text[:180]))

    return addons[:3]
