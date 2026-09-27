# LLM provider adapter: openrouter.
# Talks to the model host; patient text still goes through reply_guard.
import json
import re
import httpx
from typing import List, Dict, Any, Optional
from app.core.config import settings
from app.core.logging import logger
from app.providers.base import BaseLLMProvider, LLMResponse


# OpenRouter 402 often says: can only afford N.
def _parse_affordable_max_tokens(error_text: str) -> Optional[int]:
    m = re.search(r"can only afford\s+(\d+)", error_text or "", re.I)
    if not m:
        return None
    try:
        n = int(m.group(1))
        return max(32, min(n - 5, 400)) if n > 40 else max(16, n - 2)
    except ValueError:
        return None


# Open router provider.
class OpenRouterProvider(BaseLLMProvider):
    # Initialize instance.
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
        fallback_model: Optional[str] = None,
    ):
        self.api_key = api_key or settings.OPENROUTER_API_KEY
        self.fallback_api_key = (settings.OPENROUTER_FALLBACK_API_KEY or "").strip() or self.api_key
        self.base_url = (base_url or settings.OPENROUTER_BASE_URL).rstrip("/")
        self.default_model = default_model or settings.DEFAULT_MODEL
        self.fallback_model = fallback_model or settings.FALLBACK_MODEL
        self.fallback_model_2 = (settings.FALLBACK_MODEL_2 or "").strip() or None
        # Models currently answering another chat. Free tiers rate-limit per model,
        # so a second concurrent chat is routed to the next free model instead of queueing.
        self._in_flight: set[str] = set()

    # Headers.
    def _headers(self, api_key: Optional[str] = None) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key or self.api_key}",
            "HTTP-Referer": "https://clinicgrowth.com",
            "X-Title": "Clinic Growth System AI Engine",
            "Content-Type": "application/json",
        }

    # Make request.
    async def _make_request(
        self,
        client: httpx.AsyncClient,
        payload: Dict[str, Any],
        model_name: str,
        api_key: Optional[str] = None,
    ) -> LLMResponse:
        url = f"{self.base_url}/chat/completions"
        req_payload = {**payload, "model": model_name}
        # Free-tier endpoints: shorter timeout so quota/rate errors fail over faster
        timeout = (
            settings.FREE_MODEL_TIMEOUT
            if ":free" in (model_name or "")
            else settings.REQUEST_TIMEOUT
        )

        response = await client.post(
            url,
            headers=self._headers(api_key),
            json=req_payload,
            timeout=timeout,
        )

        if response.status_code != 200:
            error_body = response.text
            raise RuntimeError(f"OpenRouter API Error [{response.status_code}]: {error_body}")

        data = response.json()
        choice = data.get("choices", [{}])[0]
        message = choice.get("message", {})
        usage = data.get("usage", {})

        prompt_tokens = usage.get("prompt_tokens", 0)
        completion_tokens = usage.get("completion_tokens", 0)
        total_tokens = usage.get("total_tokens", prompt_tokens + completion_tokens)

        # Google: Gemma 4 31B on OpenRouter (~$0.08/1M input, $0.35/1M output)
        estimated_cost = (prompt_tokens * 0.00000008) + (completion_tokens * 0.00000035)

        tool_calls = message.get("tool_calls", []) or []

        return LLMResponse(
            content=message.get("content"),
            tool_calls=tool_calls,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            model=model_name,
            estimated_cost=estimated_cost,
            finish_reason=choice.get("finish_reason"),
        )

    # Retry once on 402 with a lower max_tokens if OpenRouter says we can only afford N.
    async def _generate_with_retries(
        self,
        client: httpx.AsyncClient,
        payload: Dict[str, Any],
        model_name: str,
        api_key: Optional[str] = None,
    ) -> LLMResponse:
        try:
            return await self._make_request(client, payload, model_name, api_key=api_key)
        except RuntimeError as err:
            err_text = str(err)
            if "[402]" not in err_text:
                raise
            affordable = _parse_affordable_max_tokens(err_text)
            current = int(payload.get("max_tokens") or 400)
            if affordable and affordable < current:
                logger.warning(
                    f"OpenRouter credits low — retrying {model_name} with max_tokens={affordable} (was {current})"
                )
                retry_payload = {**payload, "max_tokens": affordable}
                return await self._make_request(client, retry_payload, model_name, api_key=api_key)
            raise

    # Generate.
    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: float = 0.3,
        # 400 was truncating tool calls mid-generation (finish_reason=length), which
        # sent half-sentences like "Let me check the" and silently lost the booking.
        max_tokens: int = 1000,
    ) -> LLMResponse:
        target_model = model or self.default_model

        payload: Dict[str, Any] = {
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools and len(tools) > 0:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        from app.core.http_client import get_http_client

        client = get_http_client()

        # Model chain: primary then fallbacks (deduped, order preserved)
        chain: List[str] = []
        for m in (target_model, self.fallback_model, self.fallback_model_2):
            if m and m not in chain:
                chain.append(m)

        # Concurrency routing: prefer a model that is not already answering another chat.
        # Two simultaneous chats then use different models instead of both hitting one
        # rate-limited free endpoint (which produced empty / duplicate replies).
        free_first = [m for m in chain if m not in self._in_flight]
        busy_last = [m for m in chain if m in self._in_flight]
        attempt_order = free_first + busy_last
        if free_first and free_first[0] != chain[0]:
            logger.info(
                f"Model [{chain[0]}] busy with another chat — routing to [{free_first[0]}]"
            )

        last_err: Optional[Exception] = None
        skip_free = False
        for model_name in attempt_order:
            if skip_free and ":free" in (model_name or ""):
                logger.info(f"Skipping free model [{model_name}] — daily quota already hit")
                continue

            # The key follows the model itself, not the attempt order — the fallback key
            # exists because the primary key is the one that runs out of credits.
            is_fallback = model_name in (self.fallback_model, self.fallback_model_2)
            key = self.fallback_api_key if is_fallback else self.api_key

            if last_err is None:
                logger.info(f"Dispatching LLM generation to OpenRouter model: {model_name}")
            else:
                logger.warning(f"Retrying on [{model_name}] after failure: {last_err}")

            self._in_flight.add(model_name)
            try:
                return await self._generate_with_retries(
                    client, payload, model_name, api_key=key
                )
            except Exception as err:
                err_text = str(err).strip() or type(err).__name__
                logger.error(f"Model [{model_name}] failed: {err_text}")
                last_err = RuntimeError(err_text) if not str(err).strip() else err
                # Account-wide free daily quota — other :free models share the same bucket.
                # Skip remaining free models; continue to paid/BYOK if present in the chain.
                if "[404]" in err_text and "unavailable for free" in err_text.lower():
                    logger.warning(
                        f"Skipping dead free slug [{model_name}] — use the paid slug in .env"
                    )
                if (
                    "free-models-per-day" in err_text
                    or "openrouter_free_tier_daily" in err_text
                    or "X-RateLimit-Remaining\":\"0\"" in err_text
                ) and ":free" in (model_name or ""):
                    skip_free = True
                    logger.warning(
                        "OpenRouter free-tier daily quota exhausted — "
                        "skipping remaining free models (paid models still tried if configured)"
                    )
            finally:
                self._in_flight.discard(model_name)

        raise last_err or RuntimeError("No OpenRouter model available")
