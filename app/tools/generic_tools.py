# AI tool implementation: generic tools.
# Model-callable clinic action; results stay internal, never dumped to WhatsApp.
from typing import Dict, Any
from app.tools.base import BaseTool


# Config lives on live_business_context.configuration for GENERIC entities.
def _entity_config(entity: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(entity, dict):
        return {}
    cfg = entity.get("configuration")
    if isinstance(cfg, dict) and cfg:
        return cfg
    live = entity.get("live_business_context") or {}
    nested = live.get("configuration") if isinstance(live, dict) else None
    return nested if isinstance(nested, dict) else {}


# Faq lookup tool.
class FaqLookupTool(BaseTool):
    name = "faq_lookup"
    description = "Lookup answers to frequently asked questions."
    parameters = {
        "type": "object",
        "properties": {
            "topic": {"type": "string", "description": "Topic or category e.g. 'pricing', 'hours', 'refunds', 'location'."}
        },
        "required": ["topic"],
    }
    capability = "generic"

    # Execute.
    async def execute(self, arguments: Dict[str, Any], context: Dict[str, Any]) -> Any:
        topic = (arguments.get("topic") or "").lower()
        entity = context.get("entity", {}) or {}
        faqs = _entity_config(entity).get("faqs", [])
        if not isinstance(faqs, list):
            faqs = []
        matched = [
            f for f in faqs
            if isinstance(f, dict) and (
                topic in (f.get("topic") or "").lower()
                or topic in (f.get("q") or "").lower()
                or topic in (f.get("question") or "").lower()
            )
        ]
        return {"faqs": matched if matched else faqs[:3]}
