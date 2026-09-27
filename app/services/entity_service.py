# AI domain service: entity service.
# Booking, clinic card, or orchestration for WhatsApp replies.
from typing import Optional, List
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import EntityModel
from app.api.schemas import EntityCreate, EntityUpdate


# Entity service.
class EntityService:
    # Create entity.
    async def create_entity(self, db: AsyncSession, data: EntityCreate) -> EntityModel:
        entity = EntityModel(
            type=data.type.upper(),
            name=data.name,
            external_id=data.external_id,
            system_prompt=data.system_prompt,
            configuration=data.configuration or {},
            status=data.status or "ACTIVE",
        )
        db.add(entity)
        await db.flush()
        return entity

    # Get entity.
    async def get_entity(self, db: AsyncSession, entity_id: str) -> Optional[EntityModel]:
        stmt = select(EntityModel).where(
            (EntityModel.id == entity_id) | (EntityModel.external_id == entity_id)
        )
        res = await db.execute(stmt)
        return res.scalars().first()

    # List entities.
    async def list_entities(
        self,
        db: AsyncSession,
        entity_type: Optional[str] = None,
        limit: int = 50
    ) -> List[EntityModel]:
        stmt = select(EntityModel)
        if entity_type:
            stmt = stmt.where(EntityModel.type == entity_type.upper())
        stmt = stmt.limit(limit)
        res = await db.execute(stmt)
        return res.scalars().all()

    # Update entity.
    async def update_entity(
        self,
        db: AsyncSession,
        entity_id: str,
        data: EntityUpdate
    ) -> Optional[EntityModel]:
        entity = await self.get_entity(db, entity_id)
        if not entity:
            return None

        if data.name is not None:
            entity.name = data.name
        if data.system_prompt is not None:
            entity.system_prompt = data.system_prompt
        if data.configuration is not None:
            entity.configuration = data.configuration
        if data.status is not None:
            entity.status = data.status

        await db.flush()
        return entity

    # Delete entity.
    async def delete_entity(self, db: AsyncSession, entity_id: str) -> bool:
        entity = await self.get_entity(db, entity_id)
        if not entity:
            return False
        await db.delete(entity)
        await db.flush()
        return True


entity_service = EntityService()
