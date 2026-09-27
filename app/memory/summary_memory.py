# Conversation memory: summary memory.
# Stores or summarizes WhatsApp turns in the dedicated AI Postgres.
from typing import Optional, Dict, Any
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.db.models import ConversationSummaryModel


# Summary memory manager.
class SummaryMemoryManager:
    # Get summary.
    async def get_summary(
        self,
        db: AsyncSession,
        conversation_id: str
    ) -> Optional[Dict[str, Any]]:
        stmt = select(ConversationSummaryModel).where(
            ConversationSummaryModel.conversation_id == conversation_id
        )
        result = await db.execute(stmt)
        summary = result.scalars().first()

        if not summary:
            return None

        return {
            "version": summary.version,
            "text": summary.summary_text,
            "facts": summary.summary_json.get("facts", []),
            "preferences": summary.summary_json.get("preferences", []),
            "pending_topics": summary.summary_json.get("pending_topics", []),
        }
