# Conversation memory: memory engine.
# Stores or summarizes WhatsApp turns in the dedicated AI Postgres.
import asyncio
from typing import Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.logging import logger
from app.db.session import AsyncSessionLocal
from app.memory.recent_memory import RecentMemoryManager
from app.memory.summary_memory import SummaryMemoryManager
from app.memory.addons import AddonMemoryManager


# Memory context.
class MemoryContext:
    # Initialize instance.
    def __init__(
        self,
        recent_messages: List[Dict[str, Any]],
        summary: Dict[str, Any] = None,
        pending_addons: List[Dict[str, Any]] = None,
        live_business_context: Dict[str, Any] = None,
    ):
        self.recent_messages = recent_messages
        self.summary = summary
        self.pending_addons = pending_addons or []
        self.live_business_context = live_business_context or {}


# Memory engine.
class MemoryEngine:
    # Initialize instance.
    def __init__(self):
        self.recent_mgr = RecentMemoryManager()
        self.summary_mgr = SummaryMemoryManager()
        self.addon_mgr = AddonMemoryManager()

    # Assemble memory.
    async def assemble_memory(
        self,
        db: AsyncSession,
        conversation_id: str,
        live_business_context: Dict[str, Any] = None
    ) -> MemoryContext:
        # Three independent reads. Each uses its own session — one AsyncSession
        # cannot run queries at the same time.
        async def recent_task():
            async with AsyncSessionLocal() as session:
                return await self.recent_mgr.get_recent_messages(session, conversation_id, limit=6)

        async def summary_task():
            async with AsyncSessionLocal() as session:
                return await self.summary_mgr.get_summary(session, conversation_id)

        async def addon_task():
            async with AsyncSessionLocal() as session:
                return await self.addon_mgr.get_pending_addons(session, conversation_id)

        try:
            recent_msgs, summary, pending_addons = await asyncio.gather(
                recent_task(),
                summary_task(),
                addon_task(),
            )
        except Exception as err:
            # A pool timeout must not drop the reply. Read on the request session.
            logger.warning(f"Parallel memory read failed, using one session: {err}")
            recent_msgs = await self.recent_mgr.get_recent_messages(db, conversation_id, limit=6)
            summary = await self.summary_mgr.get_summary(db, conversation_id)
            pending_addons = await self.addon_mgr.get_pending_addons(db, conversation_id)

        # Layer 4: Live Business Context
        live_context = live_business_context or {}

        return MemoryContext(
            recent_messages=recent_msgs,
            summary=summary,
            pending_addons=pending_addons,
            live_business_context=live_context,
        )


memory_engine = MemoryEngine()
