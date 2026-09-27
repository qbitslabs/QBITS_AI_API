# AI service source: phone.
# WhatsApp receptionist backend on dedicated Postgres.
from __future__ import annotations

from typing import List


# Prefer 91XXXXXXXXXX for Indian mobiles; otherwise digits-only.
def canonicalize_participant_id(raw: str | None) -> str:
    if not raw:
        return ""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if not digits:
        return raw.strip()
    if len(digits) == 10:
        return f"91{digits}"
    if digits.startswith("0") and len(digits) == 11:
        return f"91{digits[1:]}"
    if digits.startswith("91") and len(digits) == 12:
        return digits
    return digits


# All common forms so 10-digit and 91… map to the same AI thread.
def participant_id_variants(raw: str | None) -> List[str]:
    if not raw:
        return []
    digits = "".join(ch for ch in raw if ch.isdigit())
    variants: set[str] = set()
    if raw.strip():
        variants.add(raw.strip())
    if digits:
        variants.add(digits)
        canonical = canonicalize_participant_id(digits)
        if canonical:
            variants.add(canonical)
        if len(digits) == 10:
            variants.add(f"91{digits}")
        if digits.startswith("91") and len(digits) == 12:
            variants.add(digits[2:])
        if digits.startswith("0") and len(digits) == 11:
            variants.add(digits[1:])
            variants.add(f"91{digits[1:]}")
    return [v for v in variants if v]
