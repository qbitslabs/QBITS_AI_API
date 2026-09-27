# AI service core: security.
# Config, logging, HTTP, or cache used by WhatsApp orchestration.
from fastapi import Header, HTTPException, status
from app.core.config import settings


# Verify internal api key.
async def verify_internal_api_key(
    x_internal_service_key: str = Header(None, alias="X-Internal-Service-Key"),
    x_service_key: str = Header(None, alias="X-Service-Key")
):
    expected = (settings.CGS_INTERNAL_SERVICE_KEY or "").strip()
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Internal service key is not configured on AI service.",
        )

    key = (x_internal_service_key or x_service_key or "").strip()
    if not key or key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing internal service key.",
        )
    return True
