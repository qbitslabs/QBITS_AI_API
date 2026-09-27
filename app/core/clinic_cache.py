# AI service core: clinic cache.
# Config, logging, HTTP, or cache used by WhatsApp orchestration.
from __future__ import annotations

from typing import Any, Dict, Optional

from app.entities.clinic import invalidate_context_cache
from app.tools.clinic_tools import invalidate_read_cache


# Invalidate clinic caches.
def invalidate_clinic_caches(clinic_id: Optional[str] = None) -> Dict[str, Any]:
    read_n = invalidate_read_cache(clinic_id)
    ctx_n = invalidate_context_cache(clinic_id)
    return {
        "clinicId": clinic_id,
        "readCacheCleared": read_n,
        "contextCacheCleared": ctx_n,
    }
