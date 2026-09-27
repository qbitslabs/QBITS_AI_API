# Conversation memory: summary worker.
# Stores or summarizes WhatsApp turns in the dedicated AI Postgres.
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import select

from app.core.config import settings
from app.core.logging import logger
from app.db.session import AsyncSessionLocal
from app.db.models import ConversationModel, SummaryAddonModel
from app.memory.summary_processor import SummaryProcessor

# Keep live booking/language state out of summary merges
_SKIP_MERGE_TYPES = frozenset({"BOOKING_DRAFT", "LANGUAGE_PREFERENCE"})


# Merge conversations idle long enough with pending fact addons. Returns count merged.
async def run_summary_merge_once(inactivity_hours: Optional[float] = None) -> int:
    hours = inactivity_hours if inactivity_hours is not None else settings.SUMMARY_INACTIVITY_HOURS
    cutoff = datetime.utcnow() - timedelta(hours=hours)
    processor = SummaryProcessor()
    merged = 0

    async with AsyncSessionLocal() as db:
        # Conversations with pending addons (excluding booking/language state)
        stmt = (
            select(SummaryAddonModel.conversation_id)
            .where(
                SummaryAddonModel.status == "PENDING",
                ~SummaryAddonModel.type.in_(list(_SKIP_MERGE_TYPES)),
            )
            .group_by(SummaryAddonModel.conversation_id)
        )
        res = await db.execute(stmt)
        conv_ids = [row[0] for row in res.all()]
        if not conv_ids:
            return 0

        stmt_c = (
            select(ConversationModel)
            .where(
                ConversationModel.id.in_(conv_ids),
                ConversationModel.last_activity <= cutoff,
            )
            .limit(20)
        )
        res_c = await db.execute(stmt_c)
        conversations = list(res_c.scalars().all())

        for conv in conversations:
            try:
                await processor.merge_summary(db, conv.id)
                await db.commit()
                merged += 1
                logger.info(f"[SummaryWorker] Merged conversation {conv.id}")
            except Exception as err:
                await db.rollback()
                logger.warning(f"[SummaryWorker] Merge failed for {conv.id}: {err}")

    return merged


# Poll every SUMMARY_WORKER_INTERVAL_SEC until stop_event is set.
async def summary_worker_loop(stop_event: asyncio.Event) -> None:
    interval = max(30, int(settings.SUMMARY_WORKER_INTERVAL_SEC))
    logger.info(
        f"[SummaryWorker] Started (interval={interval}s, "
        f"inactivity={settings.SUMMARY_INACTIVITY_HOURS}h)"
    )
    while not stop_event.is_set():
        try:
            n = await run_summary_merge_once()
            if n:
                logger.info(f"[SummaryWorker] Merged {n} conversation(s)")
        except Exception as err:
            logger.error(f"[SummaryWorker] Loop error: {err}")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            continue
    logger.info("[SummaryWorker] Stopped")
