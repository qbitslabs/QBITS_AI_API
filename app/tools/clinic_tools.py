# AI tool implementation: clinic tools.
# Model-callable clinic action; results stay internal, never dumped to WhatsApp.
import time
from typing import Dict, Any, Optional, Tuple
from app.core.config import settings
from app.core.http_client import get_http_client
from app.tools.base import BaseTool

# TTL cache for rarely-changing clinic reads (doctors/services)
_READ_CACHE: Dict[str, Tuple[float, Any]] = {}
_READ_CACHE_TTL_SEC = 120.0


# Cache get.
def _cache_get(key: str) -> Optional[Any]:
    hit = _READ_CACHE.get(key)
    if not hit:
        return None
    expires, value = hit
    if time.monotonic() > expires:
        _READ_CACHE.pop(key, None)
        return None
    return value


# Cache set.
def _cache_set(key: str, value: Any) -> Any:
    _READ_CACHE[key] = (time.monotonic() + _READ_CACHE_TTL_SEC, value)
    return value


# Drop doctors/services TTL entries. If clinic_id is None, clear all.
def invalidate_read_cache(clinic_id: Optional[str] = None) -> int:
    if not clinic_id:
        n = len(_READ_CACHE)
        _READ_CACHE.clear()
        return n
    keys = [k for k in list(_READ_CACHE.keys()) if k.endswith(f":{clinic_id}")]
    for k in keys:
        _READ_CACHE.pop(k, None)
    return len(keys)


# Base cgs tool.
class BaseCgsTool(BaseTool):
    capability = "clinic"

    # Get headers.
    def _get_headers(self, context: Dict[str, Any]) -> Dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Internal-Service-Key": settings.CGS_INTERNAL_SERVICE_KEY,
            "X-Request-ID": context.get("request_id", "ai_req_internal"),
        }

    # Clinic id.
    def _clinic_id(self, context: Dict[str, Any]) -> str:
        entity = context.get("entity") or {}
        live = entity.get("live_business_context") or {}
        clinic = live.get("clinic") or {}
        metadata = entity.get("metadata") or {}
        return (
            context.get("clinic_id")
            or clinic.get("id")
            or metadata.get("clinic_id")
            or context.get("entity_id")
        )


# Get patient tool.
class GetPatientTool(BaseCgsTool):
    name = "get_patient"
    description = (
        "Look up a patient by phone/ID including confirmed upcoming appointments. "
        "If hasConfirmedBooking is true or upcomingAppointments is non-empty, the patient ALREADY has a booking — "
        "acknowledge it (service/date/time/doctor) and do NOT say it is unbooked. "
        "If age/gender missing, you may still ask to update the profile, but do not rebook the same slot."
    )
    parameters = {
        "type": "object",
        "properties": {
            "phone": {"type": "string", "description": "Patient WhatsApp or phone number."},
            "patientId": {"type": "string", "description": "Unique UUID of the patient if known."},
        },
        "required": [],
    }

    # Execute.
    async def execute(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        clinic_id = self._clinic_id(context)
        phone = arguments.get("phone") or context.get("participant_id")
        patient_id = arguments.get("patientId")

        url = f"{settings.CGS_INTERNAL_URL}/cgs_api/v1/internal/ai/patient"
        client = get_http_client()
        res = await client.post(
            url,
            json={
                k: v
                for k, v in {
                    "clinicId": clinic_id,
                    "phone": phone,
                    "patientId": patient_id,
                }.items()
                if v is not None and v != ""
            },
            headers=self._get_headers(context),
            timeout=10,
        )
        if res.status_code == 200:
            return res.json().get("data")
        return {"error": f"Failed to fetch patient: {res.text}"}


# Get doctors tool.
class GetDoctorsTool(BaseCgsTool):
    name = "get_doctors"
    description = "Retrieve list of doctors, their specialties, consultation fees, and available days."
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    # Execute.
    async def execute(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        clinic_id = self._clinic_id(context)
        cache_key = f"doctors:{clinic_id}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        # Prefer live context already fetched for this request (avoids another CGS hop)
        entity = context.get("entity") or {}
        live = entity.get("live_business_context") or {}
        doctors = live.get("doctors")
        if isinstance(doctors, list) and doctors:
            return _cache_set(cache_key, doctors)

        url = f"{settings.CGS_INTERNAL_URL}/cgs_api/v1/internal/ai/doctors?clinicId={clinic_id}"
        client = get_http_client()
        res = await client.get(url, headers=self._get_headers(context), timeout=10)
        if res.status_code == 200:
            return _cache_set(cache_key, res.json().get("data"))
        return {"error": f"Failed to fetch doctors: {res.text}"}


# Get services tool.
class GetServicesTool(BaseCgsTool):
    name = "get_services"
    description = "Retrieve list of procedures and treatments offered by the clinic along with pricing and duration."
    parameters = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    # Execute.
    async def execute(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        clinic_id = self._clinic_id(context)
        cache_key = f"services:{clinic_id}"
        cached = _cache_get(cache_key)
        if cached is not None:
            return cached

        entity = context.get("entity") or {}
        live = entity.get("live_business_context") or {}
        services = live.get("services")
        if isinstance(services, list) and services:
            return _cache_set(cache_key, services)

        url = f"{settings.CGS_INTERNAL_URL}/cgs_api/v1/internal/ai/services?clinicId={clinic_id}"
        client = get_http_client()
        res = await client.get(url, headers=self._get_headers(context), timeout=10)
        if res.status_code == 200:
            return _cache_set(cache_key, res.json().get("data"))
        return {"error": f"Failed to fetch services: {res.text}"}


# Check availability tool.
class CheckAvailabilityTool(BaseCgsTool):
    name = "check_availability"
    description = "Check real-time open appointment slots for a specific date and optional doctor."
    parameters = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "Date in YYYY-MM-DD format (e.g. 2026-08-24)."},
            "doctorId": {"type": "string", "description": "Optional UUID of the doctor."},
        },
        "required": ["date"],
    }

    # Execute.
    async def execute(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        clinic_id = self._clinic_id(context)
        doctor_id = arguments.get("doctorId") or context.get("doctor_id")
        url = f"{settings.CGS_INTERNAL_URL}/cgs_api/v1/internal/ai/availability"
        client = get_http_client()
        res = await client.post(
            url,
            json={
                k: v
                for k, v in {
                    "clinicId": clinic_id,
                    "date": arguments["date"],
                    "doctorId": doctor_id,
                }.items()
                if v is not None and v != ""
            },
            headers=self._get_headers(context),
            timeout=10,
        )
        if res.status_code == 200:
            return res.json().get("data")
        return {"error": f"Failed to check availability: {res.text}"}


# Book appointment tool.
class BookAppointmentTool(BaseCgsTool):
    name = "book_appointment"
    description = (
        "Book ONLY when patient clearly wants to book and check_availability shows the slot open. "
        "Not for info-only chat. Pass age/gender/allergies/medicalHistoryNotes when known. "
        "After success, reply with full confirmation (service, doctor, date, time IST)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "date": {"type": "string", "description": "Appointment date in YYYY-MM-DD format."},
            "time": {"type": "string", "description": "Appointment time e.g. '10:30 AM' or '17:00'."},
            "service": {"type": "string", "description": "Name of the procedure or consultation."},
            "doctorId": {"type": "string", "description": "Optional UUID of doctor."},
            "patientName": {"type": "string", "description": "Name of the patient."},
            "age": {"type": "integer", "description": "Patient age in years (required once collected)."},
            "gender": {
                "type": "string",
                "description": "Patient gender: Male, Female, or Other (required once collected).",
            },
            "allergies": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Known allergies. Use ['None'] if patient reports none.",
            },
            "medicalHistoryNotes": {
                "type": "string",
                "description": (
                    "Clinical screening summary to save on the patient record, e.g. "
                    "'BP: normal; Sugar/diabetes: no; Other: none'."
                ),
            },
            "notes": {"type": "string", "description": "Optional visit reason or preferences."},
        },
        "required": ["date", "time"],
    }

    # Execute.
    async def execute(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        clinic_id = self._clinic_id(context)
        phone = context.get("participant_id")
        patient_name = arguments.get("patientName") or context.get("participant_name") or "Patient"
        doctor_id = arguments.get("doctorId") or context.get("doctor_id")

        allergies = arguments.get("allergies")
        if isinstance(allergies, str) and allergies.strip():
            allergies = [a.strip() for a in allergies.split(",") if a.strip()]

        url = f"{settings.CGS_INTERNAL_URL}/cgs_api/v1/internal/ai/appointments"
        client = get_http_client()
        res = await client.post(
            url,
            json={
                k: v
                for k, v in {
                    "clinicId": clinic_id,
                    "patientPhone": phone,
                    "patientName": patient_name,
                    "doctorId": doctor_id,
                    "service": arguments.get("service") or "General Consultation",
                    "date": arguments["date"],
                    "time": arguments["time"],
                    "notes": arguments.get("notes"),
                    "age": arguments.get("age"),
                    "gender": arguments.get("gender"),
                    "allergies": allergies,
                    "medicalHistoryNotes": arguments.get("medicalHistoryNotes"),
                }.items()
                if v is not None and v != ""
            },
            headers=self._get_headers(context),
            timeout=15,
        )
        if res.status_code in (200, 201):
            data = res.json().get("data") or {}
            data["replyInstruction"] = (
                "SUCCESS: Appointment is booked. Your next message to the patient MUST clearly state: "
                "confirmed + service + doctor + date + time (IST). "
                "Do not reply with only Ok/Done/Booked. Do not skip the details."
            )
            return data
        return {
            "error": f"Failed to book appointment: {res.text}",
            "replyInstruction": (
                "BOOKING FAILED. Tell the patient it was NOT confirmed and offer another available slot. "
                "Do not say the appointment is booked."
            ),
        }


# Request human handoff tool.
class RequestHumanHandoffTool(BaseCgsTool):
    name = "request_human_handoff"
    description = (
        "Escalate this chat to clinic staff ONLY for true human-request or emergency. "
        "Do NOT use this for normal appointment booking with a preferred doctor. "
        "Sets Human Handoff Required (Critical Situation when severity=critical)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "reason": {
                "type": "string",
                "description": "Short reason shown to staff (e.g. 'Patient requested human' or 'Possible emergency - chest pain').",
            },
            "severity": {
                "type": "string",
                "enum": ["normal", "critical"],
                "description": "critical = Critical Situation badge; normal = Human Handoff Required.",
            },
        },
        "required": ["reason"],
    }

    # Execute.
    async def execute(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        clinic_id = self._clinic_id(context)
        conversation_id = (
            context.get("conversation_id")
            or ((context.get("entity") or {}).get("metadata") or {}).get("conversationId")
        )
        if not conversation_id:
            return {"error": "conversation_id missing — cannot escalate"}

        severity = (arguments.get("severity") or "normal").lower()
        if severity not in ("normal", "critical"):
            severity = "normal"

        url = f"{settings.CGS_INTERNAL_URL}/cgs_api/v1/internal/ai/handoff"
        client = get_http_client()
        res = await client.post(
            url,
            json={
                "clinicId": clinic_id,
                "conversationId": conversation_id,
                "reason": arguments.get("reason") or "Human assistance requested",
                "severity": severity,
            },
            headers=self._get_headers(context),
            timeout=10,
        )
        if res.status_code in (200, 201):
            return res.json().get("data")
        return {"error": f"Failed to request handoff: {res.text}"}


# Get lead tool.
class GetLeadTool(BaseCgsTool):
    name = "get_lead"
    description = "Look up or capture a lead by phone so the clinic CRM stays up to date."
    parameters = {
        "type": "object",
        "properties": {
            "phone": {"type": "string", "description": "Lead phone number."},
            "name": {"type": "string", "description": "Lead or patient name if known."},
            "interestedService": {"type": "string", "description": "Treatment the lead asked about."},
            "intent": {"type": "string", "description": "HIGH, MEDIUM, or LOW."},
        },
        "required": [],
    }

    # Execute.
    async def execute(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        clinic_id = self._clinic_id(context)
        url = f"{settings.CGS_INTERNAL_URL}/cgs_api/v1/internal/ai/lead"
        client = get_http_client()
        res = await client.post(
            url,
            json={
                k: v
                for k, v in {
                    "clinicId": clinic_id,
                    "phone": arguments.get("phone") or context.get("participant_id"),
                    "name": arguments.get("name")
                    or context.get("participant_name")
                    or "WhatsApp Lead",
                    "interestedService": arguments.get("interestedService"),
                    "intent": arguments.get("intent") or "MEDIUM",
                }.items()
                if v is not None and v != ""
            },
            headers=self._get_headers(context),
            timeout=10,
        )
        if res.status_code in (200, 201):
            return res.json().get("data")
        return {"error": f"Failed to fetch or capture lead: {res.text}"}


# Move an existing appointment to a new date/time. Required: date, time.
class RescheduleAppointmentTool(BaseCgsTool):
    name = "reschedule_appointment"
    description = (
        "Reschedule an existing appointment to a new date and time. "
        "Required: date (YYYY-MM-DD) and time (e.g. 10:30 AM). "
        "appointmentId is optional — omit to use the conversation's last appointment. "
        "Call check_availability first. Do not book a new appointment for a move."
    )
    parameters = {
        "type": "object",
        "properties": {
            "appointmentId": {
                "type": "string",
                "description": "CGS appointment UUID. Omit to use the saved last appointment.",
            },
            "date": {"type": "string", "description": "New date in YYYY-MM-DD format."},
            "time": {"type": "string", "description": "New time e.g. '10:30 AM' or '17:00'."},
            "doctorId": {"type": "string", "description": "Optional UUID of doctor. Omit unless switching."},
            "notes": {"type": "string", "description": "Optional reason for the move."},
        },
        "required": ["date", "time"],
    }

    async def execute(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        clinic_id = self._clinic_id(context)
        apt_id = (
            arguments.get("appointmentId")
            or context.get("last_appointment_id")
        )
        if not apt_id:
            return {"error": "No appointment id to reschedule."}
        if not arguments.get("date") or not arguments.get("time"):
            return {"error": "date and time are required to reschedule."}
        url = (
            f"{settings.CGS_INTERNAL_URL}/cgs_api/v1/internal/ai/appointments/"
            f"{apt_id}/reschedule"
        )
        client = get_http_client()
        res = await client.post(
            url,
            json={
                k: v
                for k, v in {
                    "clinicId": clinic_id,
                    "date": arguments["date"],
                    "time": arguments["time"],
                    "doctorId": arguments.get("doctorId") or context.get("doctor_id"),
                    "notes": arguments.get("notes"),
                }.items()
                if v is not None and v != ""
            },
            headers=self._get_headers(context),
            timeout=15,
        )
        if res.status_code in (200, 201):
            data = res.json().get("data") or {}
            data["replyInstruction"] = (
                "SUCCESS: Appointment was moved. Tell the patient the new service, "
                "doctor, date and time (IST). Do not say a new booking was created."
            )
            return data
        return {
            "error": f"Failed to reschedule appointment: {res.text}",
            "replyInstruction": (
                "RESCHEDULE FAILED. Tell the patient it was NOT moved and offer another slot. "
                "Do not say the appointment is confirmed on the new time."
            ),
        }


# Look up one booked appointment by CGS id (from conversation.last_appointment_id).
class FetchAppointmentTool(BaseCgsTool):
    name = "fetch_appointment"
    description = (
        "Fetch a booked appointment by id. Use lastAppointmentId from context when the "
        "patient asks about their existing booking (time, doctor, status). "
        "Do not start a new booking wizard for this."
    )
    parameters = {
        "type": "object",
        "properties": {
            "appointmentId": {
                "type": "string",
                "description": "CGS appointment UUID. Omit to use the conversation's last appointment.",
            },
        },
        "required": [],
    }

    async def execute(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        clinic_id = self._clinic_id(context)
        apt_id = (
            arguments.get("appointmentId")
            or context.get("last_appointment_id")
        )
        if not apt_id:
            return {"error": "No appointment id on this conversation yet."}
        url = (
            f"{settings.CGS_INTERNAL_URL}/cgs_api/v1/internal/ai/appointments/{apt_id}"
            f"?clinicId={clinic_id}"
        )
        client = get_http_client()
        res = await client.get(url, headers=self._get_headers(context), timeout=10)
        if res.status_code == 200:
            return res.json().get("data")
        return {"error": f"Failed to fetch appointment: {res.text}"}
