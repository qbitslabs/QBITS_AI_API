# AI entity resolver: base.
# Maps patient language to clinic/doctor/generic records without guessing UUIDs.
from abc import ABC, abstractmethod
from typing import Dict, Any, List
from pydantic import BaseModel


# Resolved entity payload passed into prompts and tools.
class EntityContext(BaseModel):

    id: str
    type: str
    name: str
    system_prompt: str
    capabilities: List[str]
    live_business_context: Dict[str, Any] = {}
    metadata: Dict[str, Any] = {}


# Adapter that loads an entity and returns EntityContext.
class BaseEntityAdapter(ABC):

    # Resolve entity data and return standard EntityContext.
    @abstractmethod
    async def resolve(
        self,
        entity_id: str,
        session_or_context: Dict[str, Any]
    ) -> EntityContext:
        pass
