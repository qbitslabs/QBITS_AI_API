# Conversation memory: addons.
# Stores or summarizes WhatsApp turns in the dedicated AI Postgres.
from typing import List, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import SummaryAddonModel


# Addon memory manager.
class AddonMemoryManager:
    # Get pending addons.
    async def get_pending_addons(
        self,
        db: AsyncSession,
        conversation_id: str
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(SummaryAddonModel)
            .where(
                SummaryAddonModel.conversation_id == conversation_id,
                SummaryAddonModel.status == "PENDING",
            )
            .order_by(SummaryAddonModel.created_at.asc())
        )
        result = await db.execute(stmt)
        records = result.scalars().all()

        return [
            {
                "id": a.id,
                "type": a.type,
                "content": a.content,
                "created_at": a.created_at,
            }
            for a in records
        ]

    # Add addon.
    async def add_addon(
        self,
        db: AsyncSession,
        conversation_id: str,
        addon_type: str,
        content: str
    ) -> SummaryAddonModel:
        addon = SummaryAddonModel(
            conversation_id=conversation_id,
            type=addon_type,
            content=content,
            status="PENDING",
        )
        db.add(addon)
        await db.flush()
        return addon

    # Update latest pending addon of this type, or create one.
    async def upsert_addon(
        self,
        db: AsyncSession,
        conversation_id: str,
        addon_type: str,
        content: str,
    ) -> SummaryAddonModel:
        stmt = (
            select(SummaryAddonModel)
            .where(
                SummaryAddonModel.conversation_id == conversation_id,
                SummaryAddonModel.type == addon_type,
                SummaryAddonModel.status == "PENDING",
            )
            .order_by(SummaryAddonModel.created_at.desc())
            .limit(1)
        )
        result = await db.execute(stmt)
        existing = result.scalar_one_or_none()
        if existing:
            existing.content = content
            await db.flush()
            return existing
        return await self.add_addon(db, conversation_id, addon_type, content)
