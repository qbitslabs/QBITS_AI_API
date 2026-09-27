# AI entity resolver: doctor.
# Maps patient language to clinic/doctor/generic records without guessing UUIDs.
from typing import Dict, Any
from app.core.config import settings
from app.core.logging import logger
from app.core.http_client import get_http_client
from app.entities.base import BaseEntityAdapter, EntityContext


# Doctor entity adapter.
class DoctorEntityAdapter(BaseEntityAdapter):
    # Resolve.
    async def resolve(
        self,
        entity_id: str,
        session_or_context: Dict[str, Any]
    ) -> EntityContext:
        url = f"{settings.CGS_INTERNAL_URL}/cgs_api/v1/internal/ai/context"
        headers = {
            "Content-Type": "application/json",
            "X-Internal-Service-Key": settings.CGS_INTERNAL_SERVICE_KEY,
            "X-Request-ID": session_or_context.get("request_id", "ai_req_internal"),
        }

        doctor_name = "Lead Doctor"
        live_data = {}

        try:
            client = get_http_client()
            res = await client.post(
                url,
                json={"doctorId": entity_id},
                headers=headers,
                timeout=10,
            )
            if res.status_code == 200:
                payload = res.json().get("data", {})
                live_data = payload
                doc = payload.get("doctor")
                if doc:
                    doctor_name = doc.get("name", doctor_name)
        except Exception as err:
            logger.warning(f"Could not connect to CGS internal API for doctor {entity_id}: {err}")

        system_prompt = (
            f"You are the executive clinical assistant for {doctor_name}.\n"
            f"Assist patients with scheduling consultations, inquiries regarding treatment plans, consultation fees, and clinic hours.\n"
            f"Always use the provided clinic tools to check slot availability and schedule visits.\n"
            f"Maintain strict medical ethics: do not diagnose or prescribe medications."
        )

        clinic = live_data.get("clinic") or {}
        clinic_id = clinic.get("id")
        slim_context = {
            "clinic": {
                "id": clinic_id,
                "name": clinic.get("name"),
                "workingHours": clinic.get("workingHours"),
                "phone": clinic.get("phone"),
            },
            "doctor": live_data.get("doctor"),
            "doctors": live_data.get("doctors") or [],
            "services": (live_data.get("services") or [])[:12],
        }

        return EntityContext(
            id=entity_id,
            type="DOCTOR",
            name=doctor_name,
            system_prompt=system_prompt,
            capabilities=["clinic", "generic"],
            live_business_context=slim_context,
            metadata={"source": "cgs_backend", "clinic_id": clinic_id, "doctor_id": entity_id},
        )
