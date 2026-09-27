# AI entity resolver: resolver.
# Maps patient language to clinic/doctor/generic records without guessing UUIDs.
from typing import Dict, Any
from app.entities.base import BaseEntityAdapter, EntityContext
from app.entities.clinic import ClinicEntityAdapter
from app.entities.doctor import DoctorEntityAdapter
from app.entities.generic import GenericEntityAdapter


# Entity resolver.
class EntityResolver:
    # Initialize instance.
    def __init__(self):
        self._adapters: Dict[str, BaseEntityAdapter] = {
            "CLINIC": ClinicEntityAdapter(),
            "DOCTOR": DoctorEntityAdapter(),
        }
        self._generic_adapter = GenericEntityAdapter()

    # Register adapter.
    def register_adapter(self, entity_type: str, adapter: BaseEntityAdapter):
        self._adapters[entity_type.upper()] = adapter

    # Resolve.
    async def resolve(
        self,
        entity_type: str,
        entity_id: str,
        context: Dict[str, Any]
    ) -> EntityContext:
        adapter = self._adapters.get(entity_type.upper(), self._generic_adapter)
        return await adapter.resolve(entity_id, context)


entity_resolver = EntityResolver()
