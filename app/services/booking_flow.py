# Hybrid WhatsApp booking wizard: language, doctor, numbered service, age, gender, screen.
# Then the model owns slots and book_appointment; never invents doctor UUIDs.
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.core.logging import logger

BOOKING_DRAFT_TYPE = "BOOKING_DRAFT"
# Mergeable clinical screen (summary worker skips BOOKING_DRAFT)
PATIENT_SCREENING_TYPE = "PATIENT_SCREENING"

# Static intake ends at medical screen; AI owns date/time/slots/book.
STEPS = (
    "NEED_LANGUAGE",
    "NEED_DOCTOR",
    "NEED_SERVICE",
    "NEED_AGE",
    "NEED_GENDER",
    "NEED_SCREEN",
    "NEED_AI_SLOTS",
    "DONE",
)

DEFAULT_SERVICE = "General Consultation"

# Wizard *entry* only — action phrases. Do not use clinic-card words
# (doctor / clinic / fee / hours / address / services) or bare
# "appointment" / "booking" / "schedule" (those appear in FAQs).
BOOK_INTENT = (
    "book appointment", "book an appointment", "book the appointment",
    "book my appointment", "book a slot", "book slot", "book a time",
    "book consult", "book consultation", "book checkup", "book check up",
    "book karo", "book kar", "book kardo", "book kar do", "book karwao",
    "book karwa do", "book karao", "book kara do", "book karwa dena",
    "appointment book kar", "appointment book karo", "appointment book karni",
    "appointment book kardo", "appointment lena", "appointment lo",
    "appointment lena hai", "appointment leni hai", "appointment chahiye",
    "appointment fix", "appointment set", "appointment karwao",
    "slot book", "slot lena", "slot chahiye", "slot do", "slot mil",
    "schedule appointment", "schedule a visit", "schedule my visit",
    "make appointment", "make an appointment", "fix appointment",
    "fix a slot", "fix kardo", "fix kar do",
    "need appointment", "want appointment", "want an appointment",
    "i want to book", "i need to book", "please book", "pls book", "plz book",
    "can you book", "could you book", "mujhe book",
    "mujhe appointment lena", "mujhe appointment chahiye",
    "time book", "date book", "visit book", "consult book",
    "mulaqat", "mulakat", "check karwana", "check karwao",
    "need a slot", "want a slot", "need a booking", "want a booking",
    "i need a booking", "i want a booking",
    "book another", "book a new", "book new",
    "nayi booking", "nayi appointment", "dusri booking",
    "book with", "book for", "book me",
    "mujhe dikhana hai", "consult karwana", "consult karwao",
    "opd book", "token lena",
    # Reschedule — action only (not bare "schedule" / "change")
    "reschedule", "re schedule", "re-schedule", "reschedule appointment",
    "reschedule my appointment", "reschedule karo", "reschedule kar do",
    "reschedule karwao", "rechedule", "rescedule", "reshedule",
    "reschdule", "rescedual", "rescdule",
    "change appointment", "change my appointment", "change the appointment",
    "change the date", "change my date", "change the time", "change my time",
    "change my slot", "change the slot", "change slot",
    "move my appointment", "shift my appointment", "shift appointment",
    "postpone", "postpone appointment", "postpone my appointment",
    "push my appointment", "another date", "another time", "another slot",
    "different date", "different time", "different slot",
    "new date", "new time", "new slot", "nayi date", "naya time", "naya slot",
    "date change", "date badlo", "date badal", "date badal do", "date hatao",
    "time change", "time badlo", "time badal", "time badal do",
    "slot change", "slot badlo", "slot badal",
    "appointment change", "appointment badlo", "appointment shift",
    "appointment aage", "appointment peeche",
    "dusri date", "doosri date", "dusra time", "doosra time",
    "tareekh badlo", "tareekh badal", "samay badlo", "samay badal",
    "mulaqat badlo", "mulakat badlo",
    "can we change the date", "can we change the time",
)

# Whole-message exit (bare "no" is cancel except on NEED_SCREEN).
CANCEL_EXACT = frozenset({
    "no", "n", "nahi", "nahin", "nahi.", "nah", "nope", "naa", "na",
    "stop", "leave", "later", "enough", "cancel", "drop", "skip",
    "abort", "exit", "quit", "nevermind", "never mind", "nvm",
    "not now", "leave it", "forget it", "drop it", "skip it", "skip this",
    "no thanks", "no thank you", "no thankyou", "no need", "no more",
    "dont", "don't", "do not", "dont want", "don't want",
    "nahi chahiye", "mat karo", "band karo", "band kar do", "rok do", "rok",
    "rehne do", "rehne de", "chor do", "chhod do", "chhodo", "chodo",
    "nahi book", "don't book", "dont book", "not interested",
    "no booking", "stop booking", "cancel booking", "cancel it",
    "stop it", "that's it", "thats it", "bas", "bas kar", "bas karo",
    "baad mein", "baad me", "kal", "not today", "maybe later",
    "changed my mind", "i changed my mind", "don't want to",
    "dont want to", "nahi karna", "nahi karungi", "nahi karunga",
    "mat book", "book nahi", "appointment nahi",
    "cancel karo", "cancel kardo", "cancel kar do",
    "booking cancel", "mat book karo", "abhi nahi",
    "chhod do", "chhod dete", "rehne dete hain",
})

# Substring exit phrases (do not use bare "no" here — matches "none" / "know").
CANCEL_PHRASES = (
    "cancel booking", "cancel the booking", "cancel my booking",
    "cancel appointment", "cancel the appointment", "cancel my appointment",
    "cancel karo", "cancel kardo", "cancel kar do", "cancel karwao",
    "booking cancel kar", "appointment cancel kar",
    "stop booking", "stop the booking", "stop this booking", "stop this",
    "don't want to book", "dont want to book", "do not want to book",
    "don't book", "dont book", "do not book", "dont book my",
    "don't book my", "nahi book", "book mat", "mat book", "mat book karo",
    "leave booking", "abort booking", "no more booking", "not booking",
    "not interested", "booking cancel", "booking band", "booking rok",
    "appointment cancel", "appointment mat", "appointment nahi",
    "i don't want", "i dont want", "changed my mind", "change my mind",
    "forget booking", "skip booking", "no need to book",
    "press no", "type no", "reply no",
    "baad mein", "baad me book", "abhi nahi", "abhi mat",
    "rehne do booking", "band karo booking", "rok do booking",
    "chhod do booking", "chhodo booking", "booking rehne do",
    "nahi lena", "nahi leni", "appointment nahi lena",
)

# Status / existing booking — never start the wizard (not "appointment book").
STATUS_ASK = (
    "my appointment", "my booking", "last booking", "last appointment",
    "current appointment", "current booking", "existing appointment",
    "existing booking", "already booked", "booking status",
    "appointment status", "have you booked", "did you book",
    "tell me about my appointment", "tell me my last", "tell me my current",
    "tell my appointment", "tell me my appointment", "tell appointment",
    "show my appointment", "show appointment",
    "appoint btao", "appoint batao", "appoint bataiye",
    "appointment btao", "appointment batao", "appointment bataiye",
    "appointment dikhao", "appoint dikhao", "booking dikhao",
    "meri appointment", "mera appointment", "meri booking", "mera booking",
    "appointment kya hai", "booking kya hai", "appointment details",
    "booking details", "kab hai appointment", "appointment kab",
    "mera last", "meri last", "pichla appointment", "pichli booking",
    "last wala", "pehle wala appointment", "confirm ho", "booked or not",
    "kya book hua", "booking kya hui",
)


def is_status_ask(message: str) -> bool:
    lower = (message or "").strip().lower()
    if not lower:
        return False
    if is_book_verb(message) and not any(
        k in lower
        for k in (
            "last", "current", "existing", "status", "already",
            "btao", "batao", "bataiye", "dikhao", "tell", "show",
        )
    ):
        return False
    if any(k in lower for k in STATUS_ASK):
        return True
    return bool(
        re.search(
            r"\b(appoint(?:ment)?|booking)\b.{0,24}"
            r"\b(btao|batao|bataiye|dikhao|dikha|details|status|kya)\b"
            r"|\b(btao|batao|bataiye|tell|show|dikhao|meri|mera|my)\b.{0,24}"
            r"\b(appoint(?:ment)?|booking)\b",
            lower,
        )
    )


# Intro greetings only (thanks / bye are outro, not welcome).
GREET_EXACT = frozenset({
    "hi", "hii", "hiii", "hello", "helo", "hellow", "hey", "heyy", "yo",
    "namaste", "namaskar", "namastey", "hola",
    "good morning", "good afternoon", "good evening",
    "suprabhat", "shubh prabhat", "shubh din",
    "kaise ho", "kaise hain", "kya haal", "kya haal hai",
    "radhe radhe", "ram ram", "sat sri akal",
})
GREET_PREFIX = (
    "hi", "hii", "hello", "helo", "hey", "heyy", "yo",
    "namaste", "namaskar", "hola",
    "good morning", "good afternoon", "good evening",
    "suprabhat", "shubh prabhat", "kaise ho", "kya haal",
    "radhe radhe", "ram ram",
)

# Close / thanks. Outro ("see you at appointment") only if last AI confirmed a booking.
OUTRO_EXACT = frozenset({
    "thanks", "thank you", "thank u", "thankyou", "thx", "ty", "tysm",
    "ok thanks", "okay thanks", "thanks a lot", "thank you so much",
    "thanks alot", "many thanks", "thank you very much",
    "shukriya", "shukriyaa", "sukriya", "dhanyavad", "dhanyavaad",
    "dhanyawaad", "shukria",
    "bye", "goodbye", "good bye", "good night", "gn", "tc", "take care",
    "that's all", "thats all", "that is all", "bas itna hi", "bas itna",
    "alvida", "phir milte", "milte hain",
})
# Soft ack: outro only after confirm; otherwise a short "theek hai" (never welcome).
SOFT_ACK_EXACT = frozenset({
    "ok", "okay", "okk", "k", "fine", "ok fine", "okay fine",
    "fine okay", "done", "great", "perfect", "cool", "nice",
    "theek", "theek hai", "thik", "thik hai", "theekh",
    "acha", "accha", "achha", "acchi", "sahi", "sahi hai",
    "ji", "haan theek", "okji", "ok ji", "okay ji",
    "now", "then", "hmm", "hm",
})
_OUTRO_WORD = frozenset({
    "fine", "ok", "okay", "okk", "thanks", "thank", "you", "u", "thx", "ty",
    "shukriya", "sukriya", "dhanyavad", "dhanyavaad", "bye", "goodbye",
    "good", "night", "theek", "hai", "done", "great", "perfect", "cool",
    "nice", "ji", "acha", "accha", "achha", "sahi", "alot", "a", "lot",
    "so", "much", "very", "take", "care", "gn", "tc",
})
_CONFIRM_HINTS = (
    "appointment is confirmed", "appointment confirmed",
    "your appointment is confirmed", "booking is confirmed",
    "appointment is already noted", "appointment already noted",
    "book ho gaya", "book ho gya", "booking confirm",
    "please arrive on time", "please arrive",
    "appointment confirm", "confirm ho gaya",
)

# Idle draft: coming back after this many hours does not resume the wizard.
DRAFT_STALE_HOURS = 48

SERVICE_MAP = [
    ("root canal", "Root Canal"),
    ("rct", "Root Canal"),
    ("cleaning", "Dental Cleaning"),
    ("whitening", "Teeth Whitening"),
    ("aligner", "Aligners"),
    ("braces", "Braces"),
    ("implant", "Dental Implant"),
    ("extraction", "Tooth Extraction"),
    ("filling", "Dental Filling"),
    ("crown", "Crown"),
    ("checkup", "Routine Health Check-up"),
    ("check-up", "Routine Health Check-up"),
    ("check up", "Routine Health Check-up"),
    ("routine", "Routine Health Check-up"),
    ("tooth", "General Consultation"),
    ("daant", "General Consultation"),
    ("dard", "General Consultation"),
    ("pain", "General Consultation"),
    ("consultation", "General Consultation"),
]


# Now ist.
def _now_ist() -> datetime:
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("Asia/Kolkata"))
    except Exception:
        return datetime.now(timezone(timedelta(hours=5, minutes=30)))


# Lang.
def _lang(draft: Dict[str, Any], addons: List[Dict[str, Any]]) -> str:
    dl = (draft.get("language") or "").lower()
    if "hing" in dl or "hindi" in dl:
        return "hinglish"
    if "eng" in dl:
        return "english"
    for a in reversed(addons or []):
        if (a.get("type") or "").upper() == "LANGUAGE_PREFERENCE":
            c = (a.get("content") or "").lower()
            if "hindi" in c or "hinglish" in c:
                return "hinglish"
            if "english" in c:
                return "english"
    return "english"


# T.
def _t(hinglish: str, english: str, lang: str) -> str:
    return hinglish if lang == "hinglish" else english


def format_display_date(date_str: Optional[str]) -> str:
    raw = (date_str or "").strip()
    if not raw:
        return "—"
    parsed = None
    for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            parsed = datetime.strptime(raw[:32], fmt)
            break
        except ValueError:
            continue
    if parsed is None:
        return raw
    pretty = parsed.strftime("%d %B %Y")
    today = _now_ist().date()
    day = parsed.date()
    if day == today:
        pretty = f"{pretty} (today)"
    elif day == today + timedelta(days=1):
        pretty = f"{pretty} (tomorrow)"
    return pretty


def format_display_time(time_str: Optional[str]) -> str:
    raw = (time_str or "").strip()
    if not raw:
        return "—"
    cleaned = re.sub(r"(?i)\s*IST\s*$", "", raw).strip()
    ampm_match = re.search(
        r"(?i)^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*(AM|PM)\s*$",
        cleaned,
    )
    if ampm_match:
        hour = int(ampm_match.group(1))
        mins = ampm_match.group(2)
        period = ampm_match.group(3).upper()
        hour12 = hour % 12 or 12
        return f"{hour12:02d}:{mins} {period} IST"
    hhmm = re.match(r"^\s*(\d{1,2}):(\d{2})(?::\d{2})?\s*$", cleaned)
    if hhmm:
        hour = int(hhmm.group(1))
        mins = hhmm.group(2)
        period = "AM" if hour < 12 else "PM"
        hour12 = hour % 12 or 12
        return f"{hour12:02d}:{mins} {period} IST"
    return f"{cleaned} IST"


def format_doctor_display(name: Optional[str]) -> str:
    n = (name or "").strip()
    if not n:
        return "the assigned doctor"
    if re.match(r"(?i)^dr\.?\s+", n):
        return n
    return f"Dr. {n}"


def format_confirmed_appointment(
    *,
    service: Optional[str] = None,
    doctor: Optional[str] = None,
    date: Optional[str] = None,
    time: Optional[str] = None,
    lang: str = "english",
    rescheduled: bool = False,
) -> str:
    svc = ((service or "").strip() or "Consultation")
    doc = format_doctor_display(doctor)
    day = format_display_date(date)
    slot = format_display_time(time)
    if lang == "hinglish":
        if rescheduled:
            lead = f"Ho gaya — appointment shift kar di hai. {svc} {doc} ke saath, {day} ko {slot}."
        else:
            lead = f"Ho gaya — booking confirm hai. {svc} {doc} ke saath, {day} ko {slot}."
        footer = "Thoda pehle aa jana. Agar date/time badalni ho to mujhe yahin likh dena."
    else:
        if rescheduled:
            lead = f"All set — I moved your visit. {svc} with {doc} on {day} at {slot}."
        else:
            lead = f"All set — you're booked. {svc} with {doc} on {day} at {slot}."
        footer = "Come a few minutes early, and just write here if you need to move it."
    return f"{lead}\n{footer}"


def confirmed_booking_facts(
    executed_tool_calls: Optional[List[Any]] = None,
    draft: Optional[Dict[str, Any]] = None,
    extra_booking: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    calls = list(executed_tool_calls or [])
    booking = extra_booking if isinstance(extra_booking, dict) and not extra_booking.get("error") else None
    if booking is None:
        for tc in reversed(calls):
            name = getattr(tc, "tool_name", None)
            result = getattr(tc, "result", None)
            if (
                name in ("book_appointment", "reschedule_appointment")
                and isinstance(result, dict)
                and not result.get("error")
            ):
                booking = result
                break
    if not booking:
        return None
    availability = None
    for tc in reversed(calls):
        if getattr(tc, "tool_name", None) == "check_availability" and isinstance(getattr(tc, "result", None), dict):
            availability = tc.result
            break
    draft = draft or {}
    tool_name = ""
    for tc in reversed(calls):
        result = getattr(tc, "result", None)
        if result is booking:
            tool_name = getattr(tc, "tool_name", "") or ""
            break
    return {
        "service": booking.get("service") or draft.get("service") or "Consultation",
        "doctor": (
            booking.get("doctorName")
            or (availability or {}).get("doctorName")
            or draft.get("doctorName")
            or ""
        ),
        "date": booking.get("date") or draft.get("date"),
        "time": booking.get("time") or draft.get("time"),
        "rescheduled": tool_name == "reschedule_appointment" or (draft.get("mode") or "") == "reschedule",
    }


# Empty draft.
def empty_draft() -> Dict[str, Any]:
    return {
        "step": "NEED_LANGUAGE",
        "language": None,
        "doctorId": None,
        "doctorName": None,
        "doctorOptions": [],
        "serviceOptions": [],
        "service": None,
        "age": None,
        "gender": None,
        "date": None,
        "time": None,
        "bp": None,
        "sugar": None,
        "allergies": None,
        "medicalHistoryNotes": None,
        "active": True,
        "updated_at": datetime.utcnow().isoformat(),
    }


# Text for a mergeable PATIENT_SCREENING addon, or None if screen not filled.
def screening_addon_content(draft: Optional[Dict[str, Any]]) -> Optional[str]:
    if not draft:
        return None
    notes = draft.get("medicalHistoryNotes")
    if notes:
        return str(notes).strip() or None
    bp = draft.get("bp")
    sugar = draft.get("sugar")
    allergies = draft.get("allergies")
    if bp is None and sugar is None and not allergies:
        return None
    if isinstance(allergies, list):
        other = ", ".join(str(a) for a in allergies if a) or "none"
    else:
        other = str(allergies) if allergies else "none"
    return (
        f"BP: {bp or 'unknown'}; "
        f"Sugar/diabetes: {sugar or 'unknown'}; "
        f"Other: {other}"
    )


# Load draft.
def load_draft(addons: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for a in reversed(addons or []):
        if (a.get("type") or "").upper() == BOOKING_DRAFT_TYPE:
            try:
                data = json.loads(a.get("content") or "{}")
                if isinstance(data, dict) and data.get("active"):
                    return data
            except Exception:
                continue
    return None


# Is book intent.
def is_book_intent(message: str) -> bool:
    lower = (message or "").strip().lower()
    return any(k in lower for k in BOOK_INTENT)


# New wizard start only. Cancel phrases never open booking (even if they contain "book").
def is_book_entry(message: str) -> bool:
    if is_cancel(message) or is_status_ask(message):
        return False
    return is_book_verb(message) or is_book_intent(message)


# Move an existing appointment — not a new intake.
_RESCHEDULE_MARKERS = (
    "reschedule", "re-schedule", "re schedule", "rechedule",
    "rescedule", "reshedule", "reschdule", "rescedual", "rescdule",
    "date change", "date badlo", "date badal", "time change", "time badlo",
    "slot change", "slot badlo", "appointment change", "appointment badlo",
    "appointment shift", "change my appointment", "change the date",
    "change the time", "move my appointment", "shift my appointment",
    "postpone", "dusri date", "doosri date", "nayi date", "naya time",
    "tareekh badlo", "samay badlo", "mulaqat badlo",
    "baad ki appointment", "baad ki booking", "later date",
    "later appointment", "another day", "different day",
)


def is_reschedule(message: str) -> bool:
    if is_cancel(message) or is_status_ask(message):
        return False
    lower = (message or "").strip().lower()
    if any(k in lower for k in _RESCHEDULE_MARKERS):
        return True
    return bool(re.search(r"\bre-?s[ce]h?[a-z]{0,8}d", lower))


# Verb "book" (book appointment / book karo) — not the noun booking/appointment/slot.
def is_book_verb(message: str) -> bool:
    lower = (message or "").strip().lower()
    if re.fullmatch(r"book+[!?.]*", lower):
        return True
    return bool(
        re.search(
            r"\b(book|booked)\s+(an?\s+|my\s+|the\s+)?(appointment|slot|consult|consultation|checkup|visit|time|booking)\b"
            r"|\bbook\s+kar"
            r"|\b(please|pls|plz|can you|could you|want to|wanna|like to|need to|i want|i need)\s+book\b"
            r"|\b(appointment|slot|consult)\s+(book|lena|lo|chahiye|fix|set)\b"
            r"|\b(mujhe|mera|meri)\s+(appointment|slot|book)\s+(lena|lo|chahiye|kar)"
            r"|\b(schedule|reschedule|re-schedule|make|fix)\s+(an?\s+|my\s+|the\s+)?(appointment|visit|slot)\b"
            r"|\b(reschedule|re schedule|re-schedule)\b"
            r"|\b(date|time|slot|appointment|tareekh|samay)\s+(change|badlo|badal)"
            r"|\b(change|move|shift|postpone)\s+(my\s+|the\s+)?(appointment|slot|date|time)\b",
            lower,
        )
    )


# Opening hello — not thanks / bye.
def is_greet(message: str) -> bool:
    lower = (message or "").strip().lower().strip("!.?, ")
    if not lower or len(lower) > 40:
        return False
    if is_book_entry(message) or is_cancel(message):
        return False
    if lower in GREET_EXACT:
        return True
    return any(
        lower == g or lower.startswith(g + " ") or lower.startswith(g + "!")
        for g in GREET_PREFIX
    )


# Thanks / fine / okay (possibly several of these in one bubble).
def is_thanks_or_ack(message: str) -> bool:
    lower = (message or "").strip().lower().strip("!.?, ")
    if not lower or len(lower) > 60:
        return False
    if is_book_entry(message) or is_cancel(message) or is_greet(message):
        return False
    if lower in OUTRO_EXACT or lower in SOFT_ACK_EXACT:
        return True
    if re.fullmatch(r"tha+n+k+[a-z]*", lower):
        return True
    words = [w for w in re.split(r"\s+", lower) if w]
    if not words:
        return False
    return all(w.strip("!.?,") in _OUTRO_WORD for w in words)


def is_soft_ack_only(message: str) -> bool:
    lower = (message or "").strip().lower().strip("!.?, ")
    if lower in SOFT_ACK_EXACT:
        return True
    if lower in OUTRO_EXACT:
        return False
    words = [w.strip("!.?,") for w in re.split(r"\s+", lower) if w]
    thanks = {"thanks", "thank", "thx", "ty", "shukriya", "dhanyavad", "dhanyavaad", "bye"}
    return bool(words) and all(w in _OUTRO_WORD for w in words) and not any(
        w in thanks for w in words
    )


# Last AI turn was a booking-confirm bubble (not an old appointment id).
def last_ai_confirmed_appointment(recent_messages: Optional[List[Dict[str, Any]]]) -> bool:
    for m in reversed(recent_messages or []):
        if not isinstance(m, dict):
            continue
        role = (m.get("sender_type") or m.get("role") or "").upper()
        if role not in ("AI", "ASSISTANT"):
            continue
        text = (m.get("content") or "").lower()
        return any(h in text for h in _CONFIRM_HINTS)
    return False


# Affirmative short reply (rebook confirm).
def is_yes(message: str) -> bool:
    bare = (message or "").strip().lower().strip("!.?, ")
    return bare in {
        "yes", "y", "yeah", "yep", "yup", "ya", "yas", "yes please", "yes pls",
        "sure", "ok", "okay", "okk", "ok please", "alright", "right",
        "haan", "han", "ha", "ji", "haaji", "haanji", "theek", "thik",
        "theek hai", "thik hai", "haan book", "yes book", "book karo",
        "karo", "kardo", "go ahead", "do it", "start", "start booking",
    }


# Today in IST as YYYY-MM-DD.
def today_ist_str() -> str:
    return _now_ist().strftime("%Y-%m-%d")


# True if last appointment date is today or in the future (and not closed).
_DEAD_APPOINTMENT_STATUS = frozenset({
    "COMPLETED", "CANCELLED", "NO_SHOW", "RESCHEDULED",
})


def appointment_is_upcoming(
    date_str: Optional[str],
    status: Optional[str] = None,
) -> bool:
    if status and str(status).strip().upper() in _DEAD_APPOINTMENT_STATUS:
        return False
    raw = (date_str or "").strip()[:10]
    if not raw:
        return False
    try:
        d = datetime.strptime(raw, "%Y-%m-%d").date()
    except ValueError:
        return False
    today = _now_ist().date()
    return d >= today


# Fixed WhatsApp card after fetch_appointment. Never includes ids/tools.
def format_appointment_status(
    facts: Optional[Dict[str, Any]],
    lang: str = "english",
) -> str:
    if not facts or facts.get("error"):
        return _t(
            "File pe abhi koi upcoming visit nahi dikh rahi. Nayi booking karni ho to bas book likh dena.",
            "I don't see an upcoming visit on file yet. If you want a new one, just say you want to book.",
            lang,
        )
    service = (facts.get("service") or "Consultation").strip()
    doctor = (facts.get("doctorName") or "").strip()
    date = format_display_date(facts.get("date"))
    time = format_display_time(facts.get("time"))
    status = str(facts.get("status") or "").replace("_", " ").title()
    with_doc = f" {doctor} ke saath" if doctor else ""
    with_doc_en = f" with {doctor}" if doctor else ""
    extra = f" ({status})" if status and status not in ("—", "Confirmed") else ""
    body = _t(
        f"Aapki visit {service} hai{with_doc}, {date} ko {time}{extra}.",
        f"You have {service}{with_doc_en} on {date} at {time}{extra}.",
        lang,
    )
    tail = _t(
        "Ise shift karna ho ya nayi booking karni ho to bata dena.",
        "Want to move this, or book another visit? Just tell me.",
        lang,
    )
    return f"{body}\n{tail}"


# Stamp last wizard activity (used for stale-draft expiry).
def touch_draft(draft: Dict[str, Any]) -> Dict[str, Any]:
    draft["updated_at"] = datetime.utcnow().isoformat()
    return draft


# Parse iso / datetime from draft or addon.
def _as_naive_utc(raw: Any) -> Optional[datetime]:
    if raw is None:
        return None
    if isinstance(raw, datetime):
        dt = raw
    else:
        try:
            dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except Exception:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


# True if the booking draft has been idle too long (e.g. patient texts after 2 days).
def draft_is_stale(
    draft: Optional[Dict[str, Any]],
    addons: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    if not draft or not draft.get("active"):
        return False
    ts = _as_naive_utc(draft.get("updated_at") or draft.get("started_at"))
    if ts is None:
        for a in reversed(addons or []):
            if (a.get("type") or "").upper() == BOOKING_DRAFT_TYPE:
                ts = _as_naive_utc(a.get("created_at"))
                if ts:
                    break
    if ts is None:
        return False
    return datetime.utcnow() - ts > timedelta(hours=DRAFT_STALE_HOURS)


# Is cancel. Bare "no"/"nahi" exits the wizard except on the medical screen.
def is_cancel(message: str, step: Optional[str] = None) -> bool:
    lower = (message or "").strip().lower()
    bare = lower.strip("!.?, ")
    if bare in CANCEL_EXACT:
        if step == "NEED_SCREEN" and bare in {
            "no", "n", "nahi", "nahin", "nah", "naa",
        }:
            return False
        return True
    return any(p in lower for p in CANCEL_PHRASES)


_STEP_FIELDS = {
    "NEED_LANGUAGE": ("language",),
    "NEED_DOCTOR": ("doctorId",),
    "NEED_SERVICE": ("service",),
    "NEED_AGE": ("age",),
    "NEED_GENDER": ("gender",),
    "NEED_SCREEN": ("medicalHistoryNotes",),
}

_SIDE_Q_STARTS = (
    "how", "what", "where", "when", "why", "who", "which",
    "can you", "could you", "do you", "does", "is there", "are there",
    "tell me", "please tell", "i want to know", "want to know",
    "kya", "kaise", "kaisa", "kaisi", "kitna", "kitne", "kitni",
    "kab", "kahan", "kahaan", "konsa", "kaun", "batao", "bataiye", "btana",
)

_SIDE_FAQ_HINTS = (
    "doctor", "doctors", "dr ", "dr.", "dentist", "clinic", "hospital",
    "fee", "fees", "price", "cost", "charges",
    "timing", "timings", "hours", "address", "location", "where",
    "experience", "qualification", "review", "rating", "parking",
    "insurance", "payment", "whatsapp", "phone", "contact",
    "about you", "about the", "about your", "services",
    "last booking", "last appointment", "current appointment",
    "current booking", "my booking", "my appointment",
)

# Short acks / noise — never treat as mid-booking FAQ (avoid wasted LLM)
_NOT_SIDE = frozenset({
    "ok", "okay", "okk", "k", "haan", "han", "ha", "yes", "y", "no", "nahi",
    "na", "n", "hmm", "hm", "ji", "acha", "accha", "theek", "thik", "sure",
    "none", "n/a", "naa", "ok.", "okay.",
})


# True if this message filled the current step or advanced the wizard.
def draft_progressed(before: Dict[str, Any], after: Dict[str, Any], step_before: str) -> bool:
    if (after.get("step") or "") != (step_before or ""):
        return True
    for key in _STEP_FIELDS.get(step_before or "", ()):
        if not before.get(key) and after.get(key):
            return True
    return False


# Heuristic: patient asked a clinic FAQ / question instead of the step answer.
def looks_like_side_question(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    lower = text.lower().strip("!.? ")
    if lower in _NOT_SIDE:
        return False
    # Short noise / failed step answers → static re-ask, not LLM
    if len(text) < 8 and "?" not in text:
        if not any(h in text.lower() for h in _SIDE_FAQ_HINTS):
            return False
    raw = text.lower()
    if "?" in text:
        return True
    if any(raw.startswith(s) or raw.startswith(s + " ") for s in _SIDE_Q_STARTS):
        return True
    if any(h in raw for h in _SIDE_FAQ_HINTS):
        return True
    return False


# Mid-wizard message that did not advance booking and looks off-topic.
def is_booking_side_question(
    step_before: str,
    message: str,
    before: Dict[str, Any],
    after: Dict[str, Any],
) -> bool:
    if not step_before or step_before in ("NEED_AI_SLOTS", "DONE", "READY"):
        return False
    if is_cancel(message, step_before):
        return False
    if draft_progressed(before, after, step_before):
        return False
    return looks_like_side_question(message)


# True when the patient is asking for open slots / to book a time.
def user_asked_slots(message: str) -> bool:
    lower = (message or "").strip().lower()
    if not lower:
        return False
    if any(
        k in lower
        for k in (
            "slot", "slots", "available", "availability", "khali",
            "free hai", "free time", "book", "appoint", "schedule", "reschedule",
        )
    ):
        return True
    # Explicit time-check phrasing (not bare "am"/"today")
    if re.search(r"\b\d{1,2}(?::\d{2})?\s*(am|pm)\b", lower) and any(
        k in lower for k in ("check", "can", "free", "available", "book", "hai kya", "milega")
    ):
        return True
    return False


# Closing line that re-asks the field they drifted from.
def resume_booking_line(step: str, lang: str) -> str:
    if step == "NEED_LANGUAGE":
        return (
            "Booking aage badhane ke liye — Hinglish theek rahegi ya English?\n"
            "Want to keep going? Hinglish or English is fine."
        )
    if step == "NEED_DOCTOR":
        return _t(
            "Doctor choose karne ke liye upar wala number bhej dena — 1, 2…",
            "Whenever you're ready, just send the doctor number from the list — 1, 2…",
            lang,
        )
    if step == "NEED_SERVICE":
        return _t(
            "Service ke liye list mein se number likh dena, jaise 1.",
            "For the treatment, just send the number from that list, like 1.",
            lang,
        )
    if step == "NEED_AGE":
        return _t(
            "Patient ki age number mein bata dena, jaise 23.",
            "And the patient's age as a number is enough — like 23.",
            lang,
        )
    if step == "NEED_GENDER":
        return _t(
            "Gender Male, Female ya Other mein likh dena.",
            "You can reply Male, Female, or Other for gender.",
            lang,
        )
    if step == "NEED_SCREEN":
        return _t(
            "BP, sugar/diabetes ya koi allergy hai? Sab theek ho to None likh dena.",
            "Any BP, sugar/diabetes, or allergy I should note? If all is fine, just say none.",
            lang,
        )
    return _t(
        "Jo last cheez poochi thi, woh bhej dena — main booking complete kar deti hoon.",
        "Just send what I asked last and I'll finish the booking for you.",
        lang,
    )


# Parse age.
def parse_age(message: str) -> Optional[int]:
    text = (message or "").strip().lower()
    # Don't treat clock times or "24 sept" as age
    text = re.sub(r"\b\d{1,2}(?::\d{2})?\s*(am|pm)\b", " ", text)
    text = re.sub(r"\b([01]?\d|2[0-3]):[0-5]\d\b", " ", text)
    text = re.sub(
        r"\b\d{1,2}\s*(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
        " ",
        text,
    )
    m = re.search(r"\b([1-9][0-9]?|1[01][0-9]|120)\b", text)
    if not m:
        return None
    age = int(m.group(1))
    if 1 <= age <= 120:
        return age
    return None


# Parse gender.
def parse_gender(message: str) -> Optional[str]:
    lower = (message or "").strip().lower()
    if re.search(r"\b(male|m|boy|purush|ladka)\b", lower):
        return "Male"
    if re.search(r"\b(female|f|girl|mahila|ladki|woman)\b", lower):
        return "Female"
    if re.search(r"\b(other|others|non[- ]?binary)\b", lower):
        return "Other"
    return None


def parse_numbered_choice(message: str, options: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    if not options:
        return None
    text = (message or "").strip()
    if not text:
        return None
    m = re.match(
        r"^(?:option|number|no\.?|choice|#)?\s*(\d{1,2})\s*[.)\-]?\s*(.*)$",
        text,
        re.I,
    )
    if not m:
        return None
    n = int(m.group(1))
    for opt in options:
        if int(opt.get("n") or 0) == n:
            return opt
    return None


def parse_named_choice(message: str, options: Optional[List[Dict[str, Any]]]) -> Optional[Dict[str, Any]]:
    if not options:
        return None
    lower = (message or "").strip().lower()
    if not lower or len(lower) < 2:
        return None
    compact = re.sub(r"[^a-z0-9]+", "", lower)
    for opt in options:
        name = (opt.get("name") or "").strip()
        if not name:
            continue
        nl = name.lower()
        if nl in lower or lower in nl:
            return opt
        nc = re.sub(r"[^a-z0-9]+", "", nl)
        if nc and (nc in compact or compact in nc):
            return opt
        # "Dr. Aman Rajput" ↔ "aman"
        parts = [p for p in re.split(r"\s+", nl.replace("dr.", "").replace("dr ", "")) if p]
        if any(p in lower.split() for p in parts if len(p) > 2):
            return opt
    return None


def parse_doctor_choice(
    message: str,
    options: Optional[List[Dict[str, Any]]] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    if meta:
        mid = meta.get("doctorId") or meta.get("doctor_id")
        if mid:
            hit = next((o for o in (options or []) if o.get("id") == mid), None)
            if hit:
                return hit
            return {"id": mid, "name": meta.get("doctorName") or meta.get("preferredDoctor")}
        pname = (meta.get("doctorName") or meta.get("preferredDoctor") or "").strip()
        if pname:
            named = parse_named_choice(pname, options)
            if named:
                return named
    return parse_numbered_choice(message, options) or parse_named_choice(message, options)


# Parse service.
def parse_service(
    message: str,
    meta: Optional[Dict[str, Any]] = None,
    options: Optional[List[Dict[str, Any]]] = None,
) -> Optional[str]:
    if meta and meta.get("serviceInterested"):
        return str(meta["serviceInterested"]).strip()
    numbered = parse_numbered_choice(message, options)
    if numbered and numbered.get("name"):
        return str(numbered["name"]).strip()
    named = parse_named_choice(message, options)
    if named and named.get("name"):
        return str(named["name"]).strip()
    lower = (message or "").strip().lower()
    for hint, label in SERVICE_MAP:
        if hint in lower:
            if options:
                match = parse_named_choice(label, options)
                if match:
                    return str(match.get("name") or label).strip()
                continue
            return label
    return None


# Parse time.
def parse_time(message: str) -> Optional[str]:
    lower = (message or "").strip().lower()
    m = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", lower)
    if m:
        h = int(m.group(1))
        mi = int(m.group(2) or 0)
        ap = m.group(3).upper()
        if h == 0 or h > 12:
            return None
        return f"{h}:{mi:02d} {ap}"
    m2 = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", lower)
    if m2:
        h = int(m2.group(1))
        mi = int(m2.group(2))
        ap = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        return f"{h12}:{mi:02d} {ap}"
    return None


# Return YYYY-MM-DD in IST when possible.
def parse_date(message: str) -> Optional[str]:
    lower = (message or "").strip().lower()
    now = _now_ist()
    if "today" in lower or "aaj" in lower:
        return now.strftime("%Y-%m-%d")
    if "tomorrow" in lower or re.search(r"\bkal\b", lower):
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")

    days = {
        "monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3,
        "friday": 4, "saturday": 5, "sunday": 6,
        "somwar": 0, "mangal": 1, "budh": 2, "guru": 3, "shukra": 4, "shanivar": 5, "ravivar": 6,
    }
    for name, wd in days.items():
        if name in lower:
            delta = (wd - now.weekday()) % 7
            if delta == 0:
                delta = 7
            return (now + timedelta(days=delta)).strftime("%Y-%m-%d")

    m = re.search(r"\b(20\d{2})-(\d{2})-(\d{2})\b", lower)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"

    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d{2})\b", lower)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return datetime(y, mo, d).strftime("%Y-%m-%d")
        except ValueError:
            try:
                return datetime(y, d, mo).strftime("%Y-%m-%d")
            except ValueError:
                return None

    months = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    m = re.search(
        r"\b(\d{1,2})\s*(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
        lower,
    )
    if m:
        day = int(m.group(1))
        mo_key = m.group(2)
        mo = 9 if mo_key.startswith("sept") else months.get(mo_key[:3], months.get(mo_key))
        year = now.year
        try:
            dt = datetime(year, mo, day)
            if dt.date() < now.date():
                dt = datetime(year + 1, mo, day)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


# Parse screen.
def parse_screen(message: str) -> Optional[Dict[str, Any]]:
    lower = (message or "").strip().lower()
    if not lower:
        return None

    # Has.
    def _has(*words: str) -> bool:
        return any(re.search(rf"\b{re.escape(w)}\b", lower) for w in words)

    allergies = ["None"]
    bp = "normal"
    sugar = "no"

    if _has("none", "nil", "nothing") or lower in (
        "no", "nahi", "na", "sab theek", "all good", "no problem",
    ):
        return {
            "bp": "normal",
            "sugar": "no",
            "allergies": ["None"],
            "medicalHistoryNotes": "BP: normal; Sugar/diabetes: no; Other: none",
        }

    am = re.search(r"(allerg(?:y|ies)?\s*(?:to|se|:)?\s*)([a-zA-Z ,]+)", lower)
    if am:
        raw = am.group(2).strip(" .,")
        if raw and not _has("none", "no", "nahi"):
            allergies = [a.strip() for a in re.split(r"[,/]| and ", raw) if a.strip()]
    elif "allerg" in lower:
        if not any(n in lower for n in ("no allerg", "allergy nahi", "no any")):
            m2 = re.search(r"([a-z]+)\s+se\s+allerg", lower)
            if m2:
                allergies = [m2.group(1)]

    if any(x in lower for x in ("no bp", "bp normal", "bp nahi", "no blood pressure", "bp ok")):
        bp = "normal"
    elif any(x in lower for x in ("high bp", "bp high", "blood pressure", "bp problem")):
        bp = "issue reported"

    if any(x in lower for x in ("no sugar", "sugar nahi", "no diabetes", "diabetes nahi")):
        sugar = "no"
    elif _has("sugar", "diabetes", "diabetic"):
        sugar = "yes"

    if allergies != ["None"] or "bp" in lower or _has("sugar", "diabetes", "diabetic") or "allerg" in lower:
        notes = f"BP: {bp}; Sugar/diabetes: {sugar}; Other: {', '.join(allergies)}"
        return {"bp": bp, "sugar": sugar, "allergies": allergies, "medicalHistoryNotes": notes}

    return None


# Return 'Hinglish' or 'English' if the patient chose a language.
def parse_language(message: str) -> Optional[str]:
    from app.memory.addon_extractor import _detect_language_preference

    lower = (message or "").strip().lower()
    hit = _detect_language_preference(lower)
    if hit:
        return hit
    # Short answers on the language step (avoid "hi" — greeting conflict)
    if lower in ("e", "eng", "en"):
        return "English"
    if lower in ("h", "hin", "hind"):
        return "Hinglish"
    return None


# Prompt for step.
def prompt_for_step(step: str, lang: str, draft: Dict[str, Any]) -> str:
    if step == "NEED_LANGUAGE":
        return (
            "Bilkul — booking start karte hain. Hinglish theek rahegi ya English mein baat karein?\n"
            "Happy to book you in. Shall we talk in Hinglish or English?"
        )
    if step == "NEED_DOCTOR":
        return format_doctor_menu(draft, lang)
    if step == "NEED_SERVICE":
        return format_service_menu(draft, lang)
    if step == "NEED_AGE":
        return _t(
            "Theek hai. Patient ki age kya hai? Sirf number kaafi hai, jaise 23.",
            "Got it. How old is the patient? Just the number is enough — like 23.",
            lang,
        )
    if step == "NEED_GENDER":
        return _t(
            "Aur gender? Male, Female ya Other — jo bhi apply hota hai likh dena.",
            "And gender — you can just say Male, Female, or Other.",
            lang,
        )
    if step == "NEED_SCREEN":
        if (draft.get("mode") or "") == "returning":
            return _t(
                "Aapki pehli visit ki details mere paas hain. Bas yeh bata dena — "
                "BP, sugar/diabetes ya koi allergy? Sab normal ho to None likh dena.",
                "I already have your last visit on file. Just a quick check — "
                "any BP, sugar/diabetes, or allergies? If you're all clear, say none.",
                lang,
            )
        return _t(
            "Ek chhoti si check — BP ki dikkat, sugar/diabetes, ya koi allergy? "
            "Agar sab theek hai to None likh dena, phir main slots nikal leti hoon.",
            "Quick check before I look at slots — any BP issues, sugar/diabetes, or allergies? "
            "If everything's fine, just say none.",
            lang,
        )
    if step == "NEED_AI_SLOTS":
        if (draft.get("mode") or "") == "reschedule":
            old = draft.get("rescheduleFromDate") or ""
            old_bit = f" {old} wali" if old else ""
            return _t(
                f"Aapki{old_bit} appointment shift karni hai. Nayi date kya rakhun — "
                f"jaise aane wala Monday, 28 Sept, ya kal? Rokna ho to No likh dena.",
                f"I can move your visit{(' on ' + old) if old else ''}. "
                f"What date works instead — coming Monday, 28 Sept, or tomorrow? "
                f"Say No if you'd rather leave it.",
                lang,
            )
        if (draft.get("mode") or "") == "returning":
            return _t(
                "Note ho gaya. Main aaj ke khali slots dekh rahi hoon — jo time theek lage woh likh dena. "
                "Rokna ho to No.",
                "Noted. I'm pulling today's open times — send whichever slot you like. "
                "Or say No if you want to stop.",
                lang,
            )
        return _t(
            "Theek, yeh note hai. Kis din aana hai — aane wala Monday, yeh Friday, ya 28 Sept? "
            "Main us din ke open slots bataungi. Band karna ho to No likh dena.",
            "Perfect, noted. Which day works — coming Monday, this Friday, or something like 28 Sept? "
            "I'll show what's still open. Say No anytime to stop.",
            lang,
        )
    return _t("Ek second, dekh rahi hoon…", "One second, let me check…", lang)


def _format_numbered_lines(options: List[Dict[str, Any]], extra_key: str = "") -> str:
    lines = []
    for opt in options:
        n = opt.get("n")
        name = opt.get("name") or "Option"
        extra = (opt.get(extra_key) or "").strip() if extra_key else ""
        suffix = f" — {extra}" if extra else ""
        lines.append(f"{n}. {name}{suffix}")
    return "\n".join(lines)


def format_doctor_menu(draft: Dict[str, Any], lang: str) -> str:
    options = draft.get("doctorOptions") or []
    if options:
        header = _t(
            "Kaunse doctor ke saath book karun? Jo number theek lage woh bhej dena:",
            "Who would you like to see? Just send the number that fits:",
            lang,
        )
        footer = _t(
            "Jaise 1 likhoge to pehla doctor select ho jayega.",
            "For example, 1 books you with the first doctor.",
            lang,
        )
        return f"{header}\n\n{_format_numbered_lines(options, 'specialization')}\n\n{footer}"
    return _t(
        "Kaunse doctor ke saath aana hai? Naam likh dena, main unke slots dekh leti hoon.",
        "Who should I book you with? Share the doctor's name and I'll take it from there.",
        lang,
    )


def format_service_menu(draft: Dict[str, Any], lang: str) -> str:
    options = draft.get("serviceOptions") or []
    doc = draft.get("doctorName") or ("doctor" if lang != "hinglish" else "doctor")
    if options:
        header = _t(
            f"{doc} ke saath yeh options hain. Jo chahiye uska number bhej dena:",
            f"With {doc}, here's what I can book. Send the number for the one you want:",
            lang,
        )
        footer = _t(
            "Example: 1 likhne se pehli service select ho jayegi.",
            "Example: send 1 and I'll lock the first one.",
            lang,
        )
        return f"{header}\n\n{_format_numbered_lines(options)}\n\n{footer}"
    return _t(
        f"{doc} ke liye kaunsi service rakhun? Naam ya number dono chalenge.",
        f"What should I book with {doc}? The name or a number both work.",
        lang,
    )


def _is_consultation_item(item: Dict[str, Any]) -> bool:
    st = (item.get("serviceType") or "").strip().lower()
    if st == "consultation":
        return True
    if st == "procedure":
        return False
    cat = (item.get("category") or "").strip().lower()
    if cat in ("consultation", "consult"):
        return True
    name = (item.get("name") or "").strip().lower()
    return name in ("consultation", "consult") or "normal consultation" in name


# Load clinic doctors + that doctor's services onto the draft for numbered menus.
async def hydrate_booking_options(
    draft: Dict[str, Any],
    tool_context: Dict[str, Any],
) -> Dict[str, Any]:
    from app.tools.registry import tool_registry

    docs_tool = tool_registry.get_tool("get_doctors")
    svc_tool = tool_registry.get_tool("get_services")
    docs: List[Dict[str, Any]] = []
    all_svcs: List[Dict[str, Any]] = []
    if docs_tool:
        raw = await docs_tool.execute({}, tool_context)
        if isinstance(raw, list):
            docs = [d for d in raw if isinstance(d, dict) and d.get("id")]
    if svc_tool:
        raw = await svc_tool.execute({}, tool_context)
        if isinstance(raw, list):
            all_svcs = [s for s in raw if isinstance(s, dict) and s.get("name")]

    draft["doctorOptions"] = [
        {
            "n": i + 1,
            "id": d.get("id"),
            "name": d.get("name"),
            "specialization": d.get("specialization") or "",
            "services": d.get("services") or [],
        }
        for i, d in enumerate(docs)
    ]
    if len(draft["doctorOptions"]) == 1 and not draft.get("doctorId"):
        only = draft["doctorOptions"][0]
        draft["doctorId"] = only["id"]
        draft["doctorName"] = only.get("name")

    if draft.get("doctorId") and not draft.get("doctorName"):
        hit = next((d for d in draft["doctorOptions"] if d.get("id") == draft["doctorId"]), None)
        if hit:
            draft["doctorName"] = hit.get("name")

    svc_source: List[Dict[str, Any]] = []
    if draft.get("doctorId"):
        match = next((d for d in draft["doctorOptions"] if d.get("id") == draft["doctorId"]), None)
        assigned = [s for s in (match or {}).get("services") or [] if isinstance(s, dict)]
        consults = [s for s in all_svcs if _is_consultation_item(s)]
        extra = [] if assigned else all_svcs
        seen = set()
        for s in consults + assigned + extra:
            key = (s.get("name") or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            svc_source.append(s)
    draft["serviceOptions"] = [
        {"n": i + 1, "id": s.get("id"), "name": s.get("name")}
        for i, s in enumerate(svc_source)
    ]
    return touch_draft(draft)


# Landing serviceInterested wins; else keep parsed. Do not invent a default on new bookings.
def resolve_service(draft: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> None:
    if meta and meta.get("serviceInterested"):
        draft["service"] = str(meta["serviceInterested"]).strip()
        return
    if draft.get("service"):
        return
    if (draft.get("mode") or "") in ("reschedule", "returning"):
        draft["service"] = DEFAULT_SERVICE


def resolve_doctor(draft: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> None:
    if draft.get("doctorId"):
        return
    choice = parse_doctor_choice("", draft.get("doctorOptions") or [], meta)
    if choice and choice.get("id"):
        draft["doctorId"] = choice["id"]
        draft["doctorName"] = choice.get("name") or draft.get("doctorName")


# Next missing step.
def next_missing_step(draft: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> str:
    if (draft.get("mode") or "") == "reschedule":
        if not draft.get("language"):
            draft["language"] = "english"
        resolve_service(draft, meta)
        return "NEED_AI_SLOTS"
    if (draft.get("mode") or "") == "returning":
        if not draft.get("language"):
            draft["language"] = "english"
        resolve_service(draft, meta)
        if not draft.get("medicalHistoryNotes"):
            return "NEED_SCREEN"
        return "NEED_AI_SLOTS"
    if not draft.get("language"):
        return "NEED_LANGUAGE"
    resolve_doctor(draft, meta)
    if not draft.get("doctorId"):
        return "NEED_DOCTOR"
    resolve_service(draft, meta)
    if not draft.get("service"):
        return "NEED_SERVICE"
    if not draft.get("age"):
        return "NEED_AGE"
    if not draft.get("gender"):
        return "NEED_GENDER"
    if not draft.get("medicalHistoryNotes"):
        return "NEED_SCREEN"
    return "NEED_AI_SLOTS"


# Fill static fields: language → service → age → gender → screen.
def apply_message_to_draft(
    draft: Dict[str, Any],
    message: str,
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if (draft.get("mode") or "") == "reschedule":
        if not draft.get("language"):
            draft["language"] = "english"
        resolve_service(draft, meta)
        parsed_date = parse_date(message)
        if parsed_date:
            draft["date"] = parsed_date
            draft["time"] = None
        parsed_time = parse_time(message)
        if parsed_time:
            draft["time"] = parsed_time
        draft["step"] = "NEED_AI_SLOTS"
        return touch_draft(draft)
    if (draft.get("mode") or "") == "returning":
        if not draft.get("medicalHistoryNotes"):
            sc = parse_screen(message)
            if sc:
                draft.update(sc)
        draft["step"] = next_missing_step(draft, meta)
        return touch_draft(draft)
    msg = (message or "").strip()

    if not draft.get("language"):
        lang = parse_language(msg)
        if lang:
            draft["language"] = lang

    resolve_doctor(draft, meta)
    if draft.get("language") and not draft.get("doctorId"):
        doc = parse_doctor_choice(msg, draft.get("doctorOptions") or [], None)
        if doc and doc.get("id"):
            prev = draft.get("doctorId")
            draft["doctorId"] = doc["id"]
            draft["doctorName"] = doc.get("name") or draft.get("doctorName")
            if prev and prev != doc["id"]:
                draft["service"] = None
                draft["serviceOptions"] = []

    if draft.get("doctorId") and not draft.get("service"):
        s = parse_service(msg, None, draft.get("serviceOptions") or [])
        if s:
            draft["service"] = s

    if draft.get("service") and not draft.get("age"):
        a = parse_age(msg)
        if a:
            draft["age"] = a
    if draft.get("service") and not draft.get("gender"):
        g = parse_gender(msg)
        if g:
            draft["gender"] = g

    resolve_service(draft, meta)
    if (
        draft.get("language")
        and draft.get("doctorId")
        and draft.get("service")
        and draft.get("age")
        and draft.get("gender")
        and not draft.get("medicalHistoryNotes")
    ):
        sc = parse_screen(msg)
        if sc:
            draft.update(sc)

    draft["step"] = next_missing_step(draft, meta)
    return touch_draft(draft)


# System directive when static intake is done and AI owns slots/booking.
def ai_slot_handoff_system(draft: Dict[str, Any], lang: str) -> str:
    notes = draft.get("medicalHistoryNotes") or "none"
    if (draft.get("mode") or "") == "reschedule":
        old_id = draft.get("rescheduleAppointmentId") or "saved"
        old_date = draft.get("rescheduleFromDate") or "the current date"
        return (
            "RESCHEDULE — do not collect age, gender, language, service, or screening.\n"
            f"Existing appointment id={old_id} on {old_date}. "
            "Ask only for the new preferred date (coming Monday / 28 Sept / tomorrow).\n"
            "Call check_availability with date only (YYYY-MM-DD). doctorId is OPTIONAL — omit it. "
            "Never guess UUIDs. Never mention tools, UUID, or JSON to the patient.\n"
            "List real open slots, then reschedule_appointment with date and time "
            "(appointmentId optional — use the saved one). Do not call book_appointment.\n"
            "Say we are moving the existing visit — do not start a full new intake.\n"
            "If they say No / stop / cancel, stop. When asking for a date: Reply No to stop."
        )
    if (draft.get("mode") or "") == "returning":
        today = today_ist_str()
        last_visit = draft.get("lastVisitDate") or "the last visit"
        return (
            "RETURNING PATIENT BOOKING — last visit date, age, gender, and service "
            f"are already on file from CGS (last visit {last_visit}).\n"
            "Do NOT ask for date, age, gender, language, or service. "
            "Only BP/sugar/allergies were collected.\n"
            f"Collected: language={draft.get('language')}; service={draft.get('service')}; "
            f"age={draft.get('age')}; gender={draft.get('gender')}; screening={notes}.\n"
            f"Immediately call check_availability with date={today} only (today IST). "
            "If no slots, try the next calendar day, then the day after. "
            "Then list real open slots and wait for them to pick a time.\n"
            "doctorId is OPTIONAL — omit it. Never guess UUIDs. "
            "Never mention tools, UUID, or JSON to the patient.\n"
            "Then book_appointment with collected age, gender, allergies, and medicalHistoryNotes.\n"
            "If they say No / stop / cancel, stop."
        )
    return (
        "STATIC BOOKING INTAKE IS COMPLETE. You now own date/time/slots/booking.\n"
        f"Collected: language={draft.get('language')}; service={draft.get('service')}; "
        f"age={draft.get('age')}; gender={draft.get('gender')}; screening={notes}.\n"
        "Reply in the patient's language. "
        f"Doctor already chosen: {draft.get('doctorName') or draft.get('doctorId') or 'assigned'}. "
        "Ask for preferred date (accept phrases like coming Monday / this Friday / 28 Sept). "
        "Call check_availability with date only (YYYY-MM-DD). "
        "Use the chosen doctor when booking; doctorId is OPTIONAL if already in context. "
        "Never guess UUIDs. Never mention tool names, UUID, doctorId, or JSON to the patient. "
        "Patient reply: 1–3 short sentences listing real open slots, or ask the date. "
        "If they say No / stop / cancel / later, stop booking — do not keep asking for a date. "
        "Then book_appointment with collected age, gender, allergies, and medicalHistoryNotes. "
        "Never invent slots. Never re-ask language/service/age/gender/screening unless missing. "
        "When asking for a date, add: Reply No to stop booking."
    )


# Mark draft done.
def mark_draft_done(draft: Dict[str, Any]) -> Dict[str, Any]:
    draft["active"] = False
    draft["step"] = "DONE"
    return touch_draft(draft)


# Should enter booking.
def should_enter_booking(
    message: str,
    draft: Optional[Dict[str, Any]],
    pack: str,
    addons: Optional[List[Dict[str, Any]]] = None,
) -> bool:
    if (
        draft
        and draft.get("active")
        and draft.get("step") not in ("DONE", None)
        and not draft_is_stale(draft, addons)
    ):
        return True
    # pack is LLM-tool routing only — never start the wizard from "today"/"doctor"/"age".
    return is_book_entry(message) or is_reschedule(message)


# Seed draft from context.
def seed_draft_from_context(
    message: str,
    addons: List[Dict[str, Any]],
    meta: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    draft = empty_draft()
    lang_pref = None
    for a in reversed(addons or []):
        if (a.get("type") or "").upper() == "LANGUAGE_PREFERENCE":
            lang_pref = a.get("content")
            break
    if lang_pref:
        draft["language"] = lang_pref
    if meta and meta.get("serviceInterested"):
        draft["service"] = str(meta["serviceInterested"]).strip()
    if meta and (meta.get("doctorId") or meta.get("doctor_id")):
        draft["doctorId"] = meta.get("doctorId") or meta.get("doctor_id")
        draft["doctorName"] = meta.get("doctorName") or meta.get("preferredDoctor")
    apply_message_to_draft(draft, message, meta)
    draft["step"] = next_missing_step(draft, meta)
    return draft


# Existing patient moving a booked visit — skip age/gender/screen.
def seed_reschedule_draft(
    addons: List[Dict[str, Any]],
    meta: Optional[Dict[str, Any]],
    appointment_id: Optional[str],
    appointment_date: Optional[str],
) -> Dict[str, Any]:
    draft = empty_draft()
    draft["mode"] = "reschedule"
    draft["rescheduleAppointmentId"] = appointment_id
    draft["rescheduleFromDate"] = appointment_date
    for a in reversed(addons or []):
        if (a.get("type") or "").upper() == "LANGUAGE_PREFERENCE":
            draft["language"] = a.get("content") or "english"
            break
    if not draft.get("language"):
        draft["language"] = "english"
    if meta and meta.get("serviceInterested"):
        draft["service"] = str(meta["serviceInterested"]).strip()
    resolve_service(draft, meta)
    draft["step"] = "NEED_AI_SLOTS"
    return touch_draft(draft)


# Past visit on file — reuse CGS age/gender/service; only collect BP/sugar.
def seed_returning_book_draft(
    addons: List[Dict[str, Any]],
    meta: Optional[Dict[str, Any]],
    facts: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    facts = facts or {}
    draft = empty_draft()
    draft["mode"] = "returning"
    for a in reversed(addons or []):
        if (a.get("type") or "").upper() == "LANGUAGE_PREFERENCE":
            draft["language"] = a.get("content") or "english"
            break
    if not draft.get("language"):
        draft["language"] = "english"
    svc = facts.get("service") or (meta or {}).get("serviceInterested")
    if svc:
        draft["service"] = str(svc).strip()
    resolve_service(draft, meta)
    age = facts.get("patientAge") or facts.get("age")
    if age not in (None, ""):
        try:
            draft["age"] = int(age)
        except (TypeError, ValueError):
            pass
    gender = facts.get("patientGender") or facts.get("gender")
    if gender:
        g = str(gender).strip().lower()
        if g in ("male", "m"):
            draft["gender"] = "Male"
        elif g in ("female", "f"):
            draft["gender"] = "Female"
        else:
            draft["gender"] = "Other"
    last_date = facts.get("date")
    if last_date:
        draft["lastVisitDate"] = str(last_date).strip()[:10]
    if not draft.get("age"):
        draft["age"] = 0
    if not draft.get("gender"):
        draft["gender"] = "Other"
    draft["step"] = "NEED_SCREEN"
    return touch_draft(draft)


# Fixed slot list after check_availability — no LLM.
def format_open_slots(date_str: str, slots: List[str], lang: str) -> str:
    pretty = format_display_date(date_str)
    if not slots:
        return _t(
            f"{pretty} ko koi khali slot nahi mil raha. Koi aur din try karein?",
            f"I couldn't find an open slot on {pretty}. Want to try another day?",
            lang,
        )
    shown = ", ".join(str(s) for s in slots[:12])
    return _t(
        f"{pretty} pe yeh times free hain: {shown}. Jo theek lage woh time likh dena, ya doosri date bhejo.",
        f"On {pretty} I still have {shown}. Send the time you want, or another date if none of these work.",
        lang,
    )


# Slots for one YYYY-MM-DD from CGS.
async def check_slots_on_date(
    tool_context: Dict[str, Any],
    date_str: str,
) -> Dict[str, Any]:
    from app.tools.registry import tool_registry

    avail_tool = tool_registry.get_tool("check_availability")
    if not avail_tool or not date_str:
        return {}
    try:
        result = await avail_tool.execute({"date": date_str}, tool_context)
    except Exception as err:
        logger.warning(f"check_slots_on_date {date_str} failed: {err}")
        return {"date": date_str, "error": str(err)}
    if isinstance(result, dict):
        return {"date": date_str, **result}
    return {"date": date_str}


# LLM-fail fallback: ask date → list CGS slots → book. Returns (text, tool_calls, draft).
async def static_reschedule_reply(
    draft: Dict[str, Any],
    tool_context: Dict[str, Any],
    lang: str,
) -> Tuple[str, List[Any], Dict[str, Any]]:
    from app.api.schemas import ToolCallInfo

    tools: List[Any] = []
    if draft.get("date") and draft.get("time"):
        msg, tools, draft = await execute_booking_tools(draft, tool_context, lang)
        if (draft.get("step") or "") == "NEED_DATETIME":
            draft["step"] = "NEED_AI_SLOTS"
        return msg, tools, draft
    if draft.get("date"):
        avail = await check_slots_on_date(tool_context, str(draft["date"]))
        if avail:
            tools.append(
                ToolCallInfo(
                    tool_name="check_availability",
                    arguments={"date": draft["date"]},
                    result=avail,
                )
            )
        slots = (avail or {}).get("availableSlots") or []
        return format_open_slots(str(draft["date"]), slots, lang), tools, draft
    return prompt_for_step("NEED_AI_SLOTS", lang, draft), tools, draft


# First day (today IST, then next 2) that CGS still has open slots.
async def prefetch_open_slots(
    tool_context: Dict[str, Any],
    days: int = 3,
) -> Dict[str, Any]:
    from app.tools.registry import tool_registry

    avail_tool = tool_registry.get_tool("check_availability")
    if not avail_tool:
        return {}
    start = _now_ist().date()
    last: Dict[str, Any] = {}
    for i in range(max(1, days)):
        day = (start + timedelta(days=i)).strftime("%Y-%m-%d")
        try:
            result = await avail_tool.execute({"date": day}, tool_context)
        except Exception as err:
            logger.warning(f"prefetch_open_slots {day} failed: {err}")
            continue
        if not isinstance(result, dict) or result.get("error"):
            continue
        last = {"date": day, **result}
        slots = result.get("availableSlots") or []
        if slots and not result.get("isDoctorOnLeave") and not result.get("isDoctorOffDuty"):
            return last
    return last


# Normalize slot.
def normalize_slot(slot: str) -> str:
    s = (slot or "").strip().upper().replace(".", "")
    s = re.sub(r"\s+", " ", s)
    m = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", s)
    if not m:
        return s
    return f"{int(m.group(1))}:{m.group(2)} {m.group(3)}"


# Pick slot.
def pick_slot(requested: str, available: List[str]) -> Optional[str]:
    want = normalize_slot(requested)
    for s in available or []:
        if normalize_slot(s) == want:
            return s
    # allow 11 AM vs 11:00 AM
    wm = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", want)
    if not wm:
        return None
    for s in available or []:
        sm = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", normalize_slot(s))
        if sm and sm.group(1) == wm.group(1) and sm.group(2) == wm.group(2) and sm.group(3) == wm.group(3):
            return s
    return None


# Returns (reply_text, tool_call_dicts, updated_draft).
async def execute_booking_tools(
    draft: Dict[str, Any],
    tool_context: Dict[str, Any],
    lang: str,
) -> Tuple[str, List[Dict[str, Any]], Dict[str, Any]]:
    from app.tools.registry import tool_registry
    from app.api.schemas import ToolCallInfo

    executed: List[Any] = []
    date = draft["date"]
    time_req = draft["time"]
    service = draft.get("service") or "General Consultation"

    doctors_tool = tool_registry.get_tool("get_doctors")
    avail_tool = tool_registry.get_tool("check_availability")
    is_reschedule_mode = (draft.get("mode") or "") == "reschedule"
    action_tool = tool_registry.get_tool(
        "reschedule_appointment" if is_reschedule_mode else "book_appointment"
    )

    doctor_id = draft.get("doctorId") or tool_context.get("doctor_id")
    doctor_name = draft.get("doctorName")
    if doctors_tool:
        docs = await doctors_tool.execute({}, tool_context)
        executed.append(ToolCallInfo(tool_name="get_doctors", arguments={}, result=docs))
        if isinstance(docs, list) and docs:
            if not doctor_id:
                doctor_id = docs[0].get("id")
            for d in docs:
                if d.get("id") == doctor_id:
                    doctor_name = d.get("name") or doctor_name
                    break
            if not doctor_name:
                doctor_name = docs[0].get("name")
                doctor_id = doctor_id or docs[0].get("id")

    avail = None
    if avail_tool:
        avail = await avail_tool.execute(
            {"date": date, "doctorId": doctor_id},
            {**tool_context, "doctor_id": doctor_id},
        )
        executed.append(
            ToolCallInfo(
                tool_name="check_availability",
                arguments={"date": date, "doctorId": doctor_id},
                result=avail,
            )
        )

    if not isinstance(avail, dict) or avail.get("error"):
        draft["step"] = "NEED_DATETIME"
        draft["date"] = None
        draft["time"] = None
        msg = _t(
            "Us din ke slots abhi nikal nahi paaye. Koi aur date/time try karein?",
            "I couldn't pull slots for that day just now. Want to try another date or time?",
            lang,
        )
        return msg, executed, draft

    if avail.get("isDoctorOffDuty") or avail.get("isDoctorOnLeave"):
        draft["step"] = "NEED_DATETIME"
        draft["date"] = None
        draft["time"] = None
        msg = _t(
            f"Us din doctor clinic mein nahi hain ({avail.get('availabilityDays') or 'off'}). "
            f"Koi aur din sochte hain?",
            f"The doctor isn't in that day ({avail.get('availabilityDays') or 'off'}). "
            f"Want to pick another date?",
            lang,
        )
        return msg, executed, draft

    slots = avail.get("availableSlots") or []
    matched = pick_slot(time_req, slots)
    if not matched:
        draft["step"] = "NEED_DATETIME"
        draft["time"] = None
        slot_txt = ", ".join(slots[:8]) if slots else "none"
        msg = _t(
            f"{format_display_date(date)} ko {time_req} nahi mil raha. "
            f"Abhi yeh times khali hain: {slot_txt}. Inme se koi chalega?",
            f"{time_req} is already taken on {format_display_date(date)}. "
            f"I still have {slot_txt} — which of those works?",
            lang,
        )
        return msg, executed, draft

    if not action_tool:
        return _t(
            "Abhi booking lock nahi ho pa rahi. Thodi der baad try karein, ya staff likh dena.",
            "I can't lock that visit just now. Try another time, or say staff and I'll get someone.",
            lang,
        ), executed, draft

    if is_reschedule_mode:
        book_args = {
            "appointmentId": draft.get("rescheduleAppointmentId")
            or tool_context.get("last_appointment_id"),
            "date": date,
            "time": matched,
            "doctorId": doctor_id,
        }
        action_name = "reschedule_appointment"
    else:
        book_args = {
            "date": date,
            "time": matched,
            "service": service,
            "doctorId": doctor_id,
            "patientName": tool_context.get("participant_name") or "Patient",
            "age": draft.get("age"),
            "gender": draft.get("gender"),
            "allergies": draft.get("allergies") or ["None"],
            "medicalHistoryNotes": draft.get("medicalHistoryNotes"),
        }
        action_name = "book_appointment"
    book_res = await action_tool.execute(book_args, {**tool_context, "doctor_id": doctor_id})
    executed.append(ToolCallInfo(tool_name=action_name, arguments=book_args, result=book_res))

    if isinstance(book_res, dict) and not book_res.get("error"):
        draft["step"] = "DONE"
        draft["active"] = False
        draft["time"] = matched
        doc = doctor_name or avail.get("doctorName") or ""
        booked_service = (
            (book_res.get("service") if isinstance(book_res, dict) else None)
            or service
        )
        booked_date = (
            (book_res.get("date") if isinstance(book_res, dict) else None)
            or date
        )
        booked_time = (
            (book_res.get("time") if isinstance(book_res, dict) else None)
            or matched
        )
        booked_doc = (
            (book_res.get("doctorName") if isinstance(book_res, dict) else None)
            or doc
        )
        msg = format_confirmed_appointment(
            service=booked_service,
            doctor=booked_doc,
            date=booked_date,
            time=booked_time,
            lang=lang,
            rescheduled=is_reschedule_mode,
        )
        return msg, executed, draft

    err = (book_res or {}).get("error") if isinstance(book_res, dict) else "unknown"
    draft["step"] = "NEED_DATETIME"
    logger.warning(f"Static booking failed: {err}")
    msg = _t(
        "Yeh slot lock nahi ho paya. Koi aur time try karein, ya staff likh dena main unse connect kar deti hoon.",
        "That slot didn't lock. Want to try another time, or say staff and I'll connect you?",
        lang,
    )
    return msg, executed, draft
