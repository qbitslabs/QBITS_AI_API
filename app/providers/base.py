# LLM provider adapter: base.
# Talks to the model host; patient text still goes through reply_guard.
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from pydantic import BaseModel


# Normalized response from an LLM provider call.
class LLMResponse(BaseModel):

    content: Optional[str] = None
    tool_calls: List[Dict[str, Any]] = []
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    model: str = ""
    estimated_cost: float = 0.0
    # "stop" = complete, "length" = hit max_tokens (reply/tool call cut off mid-way)
    finish_reason: Optional[str] = None


# Interface for chat+tools LLM backends (e.g. OpenRouter).
class BaseLLMProvider(ABC):

    # Generate a model reply (and optional tool calls).
    @abstractmethod
    async def generate(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        model: Optional[str] = None,
        temperature: float = 0.3,
        max_tokens: int = 1000,
    ) -> LLMResponse:
        pass
