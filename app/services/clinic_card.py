# Builds the clinic context card (doctors, services, hours) for the model.
# Doctors include assigned procedures; consultations are clinic-wide.
from typing import Any, Dict, List, Optional


def _clinic_block(live: Dict[str, Any]) -> Dict[str, Any]:
    return (live or {}).get("clinic") or {}


def _doctors(live: Dict[str, Any]) -> List[Dict[str, Any]]:
    docs = (live or {}).get("doctors") or []
    one = (live or {}).get("doctor")
    if one and isinstance(one, dict) and one.get("name"):
        ids = {d.get("id") for d in docs if isinstance(d, dict)}
        if one.get("id") not in ids:
            docs = [one] + list(docs)
    return [d for d in docs if isinstance(d, dict) and d.get("name")]


def _services(live: Dict[str, Any]) -> List[Dict[str, Any]]:
    return [s for s in ((live or {}).get("services") or []) if isinstance(s, dict)]


def _join_days(days: Any) -> str:
    if not days:
        return ""
    if isinstance(days, str):
        return days.strip()
    names = [str(x).strip() for x in days if x]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _money(fee: Any) -> str:
    if fee in (None, ""):
        return ""
    try:
        n = int(float(fee))
        return f"₹{n}"
    except (TypeError, ValueError):
        return f"₹{fee}"


def _doctor_paragraph(d: Dict[str, Any], clinic_name: str) -> str:
    name = d.get("name") or "our doctor"
    spec = (d.get("specialization") or "").strip()
    days = _join_days(d.get("availabilityDays"))
    hours = (d.get("availabilityHours") or "").strip()
    fee = _money(d.get("consultationFee"))

    lead = f"{name} consults at {clinic_name}"
    if spec:
        lead += f" for {spec}"
    lead += "."

    when = ""
    if days and hours:
        when = f" You can usually see {name} on {days}, {hours}."
    elif days:
        when = f" {name} is available on {days}."
    elif hours:
        when = f" Clinic hours with {name} are {hours}."

    cost = f" Consultation fee is {fee}." if fee else ""
    return f"{lead}{when}{cost}".strip()


def _clinic_overview(
    name: str,
    clinic: Dict[str, Any],
    doctors: List[Dict[str, Any]],
    services: List[Dict[str, Any]],
) -> str:
    hours = (clinic.get("workingHours") or "").strip() or "09:00 AM - 08:00 PM (Mon-Sat)"
    addr = ", ".join(x for x in (clinic.get("address"), clinic.get("city")) if x)
    phone = (clinic.get("phone") or "").strip()

    lines = [
        f"This is {name} — happy to help here on WhatsApp.",
    ]
    if addr:
        lines.append(f"You'll find us at {addr}.")
    lines.append(f"We're usually open {hours}.")
    if phone:
        lines.append(f"You can also call {phone} if that's easier.")

    if doctors:
        if len(doctors) == 1:
            lines.append(_doctor_paragraph(doctors[0], name))
        else:
            lines.append("You can see:")
            for d in doctors[:5]:
                spec = (d.get("specialization") or "").strip()
                fee = _money(d.get("consultationFee"))
                extra = []
                if spec:
                    extra.append(spec)
                if fee:
                    extra.append(f"consult {fee}")
                label = d.get("name") or "our doctor"
                if extra:
                    label += f" ({', '.join(extra)})"
                lines.append(f"{label}")

    svc_names = [s.get("name") for s in services[:6] if s.get("name")]
    if svc_names:
        lines.append("We commonly do " + ", ".join(svc_names) + ".")

    lines.append("Want fees, a doctor's timings, or shall I book you in?")
    return "\n".join(lines)


def _doctors_reply(doctors: List[Dict[str, Any]], clinic_name: str) -> str:
    if len(doctors) == 1:
        d = doctors[0]
        return (
            _doctor_paragraph(d, clinic_name)
            + f"\nIf you'd like to come in with {d.get('name') or 'this doctor'}, just say you want to book."
        )
    parts = [
        f"At {clinic_name} you can see:",
    ]
    for d in doctors[:5]:
        parts.append(_doctor_paragraph(d, clinic_name))
    parts.append("Tell me who you prefer, or just say you want to book.")
    return "\n".join(parts)


def _fees_reply(doctors: List[Dict[str, Any]], clinic_name: str) -> Optional[str]:
    rows = []
    for d in doctors[:5]:
        fee = _money(d.get("consultationFee"))
        if not fee:
            continue
        spec = (d.get("specialization") or "").strip()
        who = d.get("name") or "Doctor"
        if spec:
            rows.append(f"• {who} ({spec}) — {fee}")
        else:
            rows.append(f"• {who} — {fee}")
    if not rows:
        return None
    return (
        f"Consultation at {clinic_name} is:\n"
        + "\n".join(rows)
        + "\nThat's the OPD consult — treatments are quoted separately. "
        "I can book you whenever you're ready."
    )


def _services_reply(services: List[Dict[str, Any]], clinic_name: str) -> Optional[str]:
    if not services:
        return None
    lines = [f"At {clinic_name} we commonly do:"]
    for s in services[:8]:
        n = s.get("name")
        if not n:
            continue
        desc = (s.get("description") or "").strip()
        price = s.get("price")
        extra = []
        if desc:
            extra.append(desc.rstrip("."))
        if price not in (None, ""):
            extra.append(_money(price) or "")
        extra = [e for e in extra if e]
        if extra:
            lines.append(f"• {n} — {'; '.join(extra)}")
        else:
            lines.append(f"• {n}")
    lines.append("Which one would you like to know more about?")
    return "\n".join(lines)


# Clinic-card *entry* only — info words. Do not use booking-wizard words
# (book / slot / appoint / schedule / reschedule).
FAQ_ENTRY = (
    "tell me about", "about you", "about the", "about your",
    "about him", "about her", "about them",
    "doctor", "doctors", "dr ", "dentist", "specialist",
    "clinic", "hospital",
    "timing", "timings", "hours",
    "are you open", "you open", "open today", "open now", "opening",
    "address", "location", "where are you",
    "fee", "fees", "price", "charge", "charges", "cost",
    "what services", "which services", "your services", "services",
    "treatment", "treatments",
    "phone", "contact",
)


# True if the message is a clinic / doctor / timings / fees FAQ (not a book start).
def looks_like_clinic_faq(message: str) -> bool:
    lower = (message or "").strip().lower()
    if not lower or lower in {"now", "then", "ok", "okay", "hmm"}:
        return False
    if not any(k in lower for k in FAQ_ENTRY):
        return False
    from app.services.booking_flow import is_book_entry
    return not is_book_entry(message)


# Short reply from the clinic card, or None if we cannot answer.
def clinic_card_reply(
    message: str,
    live: Optional[Dict[str, Any]],
    clinic_name: str = "our clinic",
) -> Optional[str]:
    live = live or {}
    lower = (message or "").strip().lower()
    clinic = _clinic_block(live)
    name = clinic.get("name") or clinic_name or "our clinic"
    doctors = _doctors(live)
    services = _services(live)

    want_doc = any(
        k in lower
        for k in (
            "doctor", "doctors", "dr ", "dentist", "specialist",
            "about him", "about her", "about them",
            "tell me about him", "tell me about her",
        )
    ) or lower in {"him", "her", "them"}
    want_time = any(k in lower for k in ("timing", "hours", "open", "close"))
    want_fee = any(k in lower for k in ("fee", "price", "charge", "cost"))
    want_svc = any(k in lower for k in ("service", "treatment"))
    want_addr = any(k in lower for k in ("address", "where", "location"))
    want_clinic = any(
        k in lower
        for k in ("clinic", "hospital", "about you", "about the clinic", "about your clinic")
    )

    if want_doc and doctors:
        return _doctors_reply(doctors, name)

    if want_fee:
        fees = _fees_reply(doctors, name)
        if fees:
            return fees

    if want_svc:
        svc = _services_reply(services, name)
        if svc:
            return svc

    if want_time or want_addr or want_clinic:
        return _clinic_overview(name, clinic, doctors, services)

    if looks_like_clinic_faq(message) and (doctors or clinic.get("name")):
        if doctors and any(k in lower for k in ("him", "her", "them", "tell me about")):
            return _doctors_reply(doctors, name)
        return _clinic_overview(name, clinic, doctors, services)

    return None
