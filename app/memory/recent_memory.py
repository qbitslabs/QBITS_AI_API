# Conversation memory: recent memory.
# Stores or summarizes WhatsApp turns in the dedicated AI Postgres.
from typing import List, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import MessageModel


# Recent memory manager.
class RecentMemoryManager:
    # Get recent messages.
    async def get_recent_messages(
        self,
        db: AsyncSession,
        conversation_id: str,
        limit: int = 8
    ) -> List[Dict[str, Any]]:
        stmt = (
            select(MessageModel)
            .where(MessageModel.conversation_id == conversation_id)
            .order_by(MessageModel.created_at.desc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        records = list(reversed(result.scalars().all()))

        messages = []
        for msg in records:
            role = "assistant" if msg.sender_type in ("AI", "assistant") else "user"
            messages.append({"role": role, "content": msg.content})

        return messages
