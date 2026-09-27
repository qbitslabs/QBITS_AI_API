# AI tool implementation: base.
# Model-callable clinic action; results stay internal, never dumped to WhatsApp.
from abc import ABC, abstractmethod
from typing import Dict, Any


# Abstract tool the LLM can call (execute + OpenRouter schema).
class BaseTool(ABC):
    name: str
    description: str
    parameters: Dict[str, Any]
    capability: str  # e.g. "clinic", "generic", "ecommerce", "restaurant"

    # Execute the tool logic with given arguments and request context.
    @abstractmethod
    async def execute(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        pass

    # Convert to OpenAI/OpenRouter function schema.
    def to_openrouter_tool(self) -> Dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }
