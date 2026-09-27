# AI service core: http client.
# Config, logging, HTTP, or cache used by WhatsApp orchestration.
from __future__ import annotations

import httpx

_client: httpx.AsyncClient | None = None


# Get http client.
def get_http_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=10.0))
    return _client


# Close http client.
async def close_http_client() -> None:
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None
