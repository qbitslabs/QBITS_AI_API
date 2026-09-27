# AI entity resolver: generic.
# Maps patient language to clinic/doctor/generic records without guessing UUIDs.
from typing import Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.entities.base import BaseEntityAdapter, EntityContext
from app.db.models import EntityModel


# Generic entity adapter.
class GenericEntityAdapter(BaseEntityAdapter):
    # Resolve.
    async def resolve(
        self,
        entity_id: str,
        session_or_context: Dict[str, Any]
    ) -> EntityContext:
        db_session: AsyncSession = session_or_context.get("db")
        entity_record: EntityModel = None

        if db_session:
            stmt = select(EntityModel).where(
                (EntityModel.id == entity_id) | (EntityModel.external_id == entity_id)
            )
            result = await db_session.execute(stmt)
            entity_record = result.scalars().first()

        name = entity_record.name if entity_record else entity_id
        entity_type = entity_record.type if entity_record else "GENERIC"
        config = entity_record.configuration if entity_record else {}

        system_prompt = (
            entity_record.system_prompt
            if entity_record and entity_record.system_prompt
            else f"You are {name}, a helpful and professional AI assistant. Assist users politely, accurately, and concisely."
        )

        return EntityContext(
            id=entity_id,
            type=entity_type,
            name=name,
            system_prompt=system_prompt,
            capabilities=["generic"],
            live_business_context={"configuration": config},
            metadata={"source": "ai_database"},
        )
