# AI HTTP API: routes.
# Inbound from CGS/WhatsApp workers; not the clinic Prisma schema.
from fastapi import APIRouter, Depends, HTTPException, status, BackgroundTasks, Body
from app.core.security import verify_internal_api_key
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta

from app.db.session import get_db
from app.api.schemas import (
    EntityCreate,
    EntityUpdate,
    EntityResponse,
    GenerateRequest,
    GenerateResponse,
    SummaryMergeRequest,
    SummaryMergeResponse,
)
from app.db.models import AiUsageLogModel, ConversationModel
from app.services.entity_service import entity_service
from app.services.ai_orchestrator import ai_orchestrator
from app.memory.summary_processor import SummaryProcessor
from app.core.clinic_cache import invalidate_clinic_caches

router = APIRouter(dependencies=[Depends(verify_internal_api_key)])
summary_processor = SummaryProcessor()


# Bust AI TTL caches for doctors/services/clinic context.
# Body: { "clinicId": "<uuid>" } or {} / omitted to clear all.
@router.post("/cache/invalidate")
async def invalidate_clinic_cache(
    payload: Optional[Dict[str, Any]] = Body(default=None),
):
    clinic_id = None
    if isinstance(payload, dict):
        clinic_id = payload.get("clinicId") or payload.get("clinic_id")
    result = invalidate_clinic_caches(clinic_id)
    return {"success": True, "data": result}


# Universal generation endpoint for any entity type (CLINIC, DOCTOR, CHATBOT, RESTAURANT, ECOMMERCE, etc.)
@router.post("/generate", response_model=GenerateResponse)
async def generate_response(
    request: GenerateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    try:
        result, usage_payload = await ai_orchestrator.generate_response(db, request)
        # Forward usage to CGS after the HTTP response is sent (no deadlock with inbound handler)
        if usage_payload:
            background_tasks.add_task(ai_orchestrator.forward_usage_to_cgs, usage_payload)
        return result
    except Exception as err:
        err_text = str(err)
        # OpenRouter out of credits — surface clearly (not a generic 500)
        if "[402]" in err_text or "more credits" in err_text.lower() or "can only afford" in err_text.lower():
            raise HTTPException(
                status_code=status.HTTP_402_PAYMENT_REQUIRED,
                detail=(
                    "OpenRouter credits exhausted. Add credits at https://openrouter.ai/settings/credits "
                    "or switch DEFAULT_MODEL to a :free model in ai_service/.env"
                ),
            )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"AI generation failed: {err_text}"
        )


# Consolidate conversation summary + pending add-ons into a new versioned summary.
@router.post("/summary/merge", response_model=SummaryMergeResponse)
async def merge_summary(
    request: SummaryMergeRequest,
    db: AsyncSession = Depends(get_db)
):
    try:
        result = await summary_processor.merge_summary(db, request.conversation_id)
        return SummaryMergeResponse(**result)
    except Exception as err:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Summary merge failed: {str(err)}"
        )


# --- Generic Entity Management Endpoints ---

# Create a new generic entity (CHATBOT, RESTAURANT, ECOMMERCE, etc.) in the AI database.
@router.post("/entities", response_model=EntityResponse, status_code=status.HTTP_201_CREATED)
async def create_entity(
    data: EntityCreate,
    db: AsyncSession = Depends(get_db)
):
    entity = await entity_service.create_entity(db, data)
    return entity


# List registered entities.
@router.get("/entities", response_model=List[EntityResponse])
async def list_entities(
    entity_type: Optional[str] = None,
    limit: int = 100,
    db: AsyncSession = Depends(get_db)
):
    return await entity_service.list_entities(db, entity_type, limit)


# Get entity details.
@router.get("/entities/{entity_id}", response_model=EntityResponse)
async def get_entity(
    entity_id: str,
    db: AsyncSession = Depends(get_db)
):
    entity = await entity_service.get_entity(db, entity_id)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


# Update entity configuration or prompt.
@router.patch("/entities/{entity_id}", response_model=EntityResponse)
async def update_entity(
    entity_id: str,
    data: EntityUpdate,
    db: AsyncSession = Depends(get_db)
):
    entity = await entity_service.update_entity(db, entity_id, data)
    if not entity:
        raise HTTPException(status_code=404, detail="Entity not found")
    return entity


# Delete an entity.
@router.delete("/entities/{entity_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_entity(
    entity_id: str,
    db: AsyncSession = Depends(get_db)
):
    deleted = await entity_service.delete_entity(db, entity_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Entity not found")


# --- AI Usage & Cost Analytics Endpoints ---

# Overall aggregate AI usage, tokens, and cost breakdown.
@router.get("/usage/summary")
async def get_overall_usage_summary(
    days: int = 30,
    db: AsyncSession = Depends(get_db)
):
    since = datetime.utcnow() - timedelta(days=days)
    today_start = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = datetime.utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)

    # 1. Total in period
    stmt = select(
        func.count(AiUsageLogModel.id).label("total_requests"),
        func.coalesce(func.sum(AiUsageLogModel.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(AiUsageLogModel.completion_tokens), 0).label("completion_tokens"),
        func.coalesce(func.sum(AiUsageLogModel.total_tokens), 0).label("total_tokens"),
        func.coalesce(func.sum(AiUsageLogModel.estimated_cost), 0.0).label("estimated_cost"),
    ).where(AiUsageLogModel.created_at >= since)
    res = await db.execute(stmt)
    overall = res.first()

    # 2. Today's Cost
    stmt_today = select(
        func.coalesce(func.sum(AiUsageLogModel.estimated_cost), 0.0)
    ).where(AiUsageLogModel.created_at >= today_start)
    res_today = await db.execute(stmt_today)
    today_cost = res_today.scalar() or 0.0

    # 3. This Month's Cost
    stmt_month = select(
        func.coalesce(func.sum(AiUsageLogModel.estimated_cost), 0.0)
    ).where(AiUsageLogModel.created_at >= month_start)
    res_month = await db.execute(stmt_month)
    month_cost = res_month.scalar() or 0.0

    total_reqs = overall.total_requests or 0
    total_cost = float(overall.estimated_cost or 0.0)
    avg_cost = (total_cost / total_reqs) if total_reqs > 0 else 0.0

    return {
        "period_days": days,
        "total_requests": total_reqs,
        "prompt_tokens": overall.prompt_tokens or 0,
        "completion_tokens": overall.completion_tokens or 0,
        "total_tokens": overall.total_tokens or 0,
        "estimated_cost": total_cost,
        "today_cost": float(today_cost),
        "month_cost": float(month_cost),
        "avg_cost_per_request": avg_cost,
    }


# Breakdown of requests, tokens, and cost grouped by LLM model.
@router.get("/usage/models")
async def get_usage_by_models(
    days: int = 30,
    db: AsyncSession = Depends(get_db)
):
    since = datetime.utcnow() - timedelta(days=days)
    stmt = (
        select(
            AiUsageLogModel.model,
            func.count(AiUsageLogModel.id).label("requests"),
            func.sum(AiUsageLogModel.total_tokens).label("total_tokens"),
            func.sum(AiUsageLogModel.estimated_cost).label("cost"),
        )
        .where(AiUsageLogModel.created_at >= since)
        .group_by(AiUsageLogModel.model)
        .order_by(desc("cost"))
    )
    res = await db.execute(stmt)
    rows = res.all()

    total_cost = sum(float(r.cost or 0) for r in rows) or 1.0

    return [
        {
            "model": r.model,
            "requests": r.requests,
            "total_tokens": r.total_tokens or 0,
            "cost": float(r.cost or 0),
            "percentage": round((float(r.cost or 0) / total_cost) * 100, 2),
        }
        for r in rows
    ]


# Breakdown of usage grouped by entity.
@router.get("/usage/entities")
async def get_usage_by_entities(
    days: int = 30,
    db: AsyncSession = Depends(get_db)
):
    since = datetime.utcnow() - timedelta(days=days)
    stmt = (
        select(
            AiUsageLogModel.entity_id,
            func.count(AiUsageLogModel.id).label("requests"),
            func.sum(AiUsageLogModel.total_tokens).label("total_tokens"),
            func.sum(AiUsageLogModel.estimated_cost).label("cost"),
        )
        .where(AiUsageLogModel.created_at >= since)
        .group_by(AiUsageLogModel.entity_id)
        .order_by(desc("cost"))
    )
    res = await db.execute(stmt)
    rows = res.all()

    entities_data = []
    for r in rows:
        ent = await entity_service.get_entity(db, r.entity_id) if r.entity_id else None
        entities_data.append({
            "entity_id": r.entity_id or "unknown",
            "name": ent.name if ent else f"Entity ({r.entity_id[:8] if r.entity_id else 'N/A'})",
            "type": ent.type if ent else "CLINIC",
            "requests": r.requests,
            "total_tokens": r.total_tokens or 0,
            "cost": float(r.cost or 0),
        })

    return entities_data


# Daily time-series usage breakdown for charts.
@router.get("/usage/daily")
async def get_daily_usage(
    days: int = 14,
    db: AsyncSession = Depends(get_db)
):
    since = datetime.utcnow() - timedelta(days=days)
    stmt = (
        select(
            func.date(AiUsageLogModel.created_at).label("day"),
            func.count(AiUsageLogModel.id).label("requests"),
            func.sum(AiUsageLogModel.total_tokens).label("total_tokens"),
            func.sum(AiUsageLogModel.estimated_cost).label("cost"),
        )
        .where(AiUsageLogModel.created_at >= since)
        .group_by(func.date(AiUsageLogModel.created_at))
        .order_by("day")
    )
    res = await db.execute(stmt)
    rows = res.all()

    return [
        {
            "date": str(r.day),
            "requests": r.requests,
            "total_tokens": r.total_tokens or 0,
            "cost": float(r.cost or 0),
        }
        for r in rows
    ]


# --- AI Conversations & Inspection Endpoints ---

# List conversations stored in the AI Database.
@router.get("/conversations")
async def list_conversations(
    limit: int = 50,
    entity_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(ConversationModel)
        .options(selectinload(ConversationModel.messages), selectinload(ConversationModel.entity))
        .order_by(desc(ConversationModel.last_activity))
        .limit(limit)
    )
    if entity_id:
        stmt = stmt.where(ConversationModel.entity_id == entity_id)

    res = await db.execute(stmt)
    convs = res.scalars().all()

    return [
        {
            "id": c.id,
            "entity_id": c.entity_id,
            "entity_name": c.entity.name if c.entity else "Clinic Entity",
            "entity_type": c.entity.type if c.entity else "CLINIC",
            "participant_id": c.participant_id,
            "channel": c.channel,
            "state": c.state,
            "message_count": len(c.messages),
            "last_activity": c.last_activity.isoformat() if c.last_activity else None,
        }
        for c in convs
    ]


# Get complete conversation transcript with tool calls and metadata.
@router.get("/conversations/{conversation_id}")
async def get_conversation_details(
    conversation_id: str,
    db: AsyncSession = Depends(get_db)
):
    stmt = (
        select(ConversationModel)
        .options(
            selectinload(ConversationModel.messages),
            selectinload(ConversationModel.entity),
            selectinload(ConversationModel.summary),
        )
        .where(ConversationModel.id == conversation_id)
    )
    res = await db.execute(stmt)
    conv = res.scalars().first()

    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    return {
        "id": conv.id,
        "entity_id": conv.entity_id,
        "entity_name": conv.entity.name if conv.entity else "Clinic Entity",
        "entity_type": conv.entity.type if conv.entity else "CLINIC",
        "participant_id": conv.participant_id,
        "channel": conv.channel,
        "state": conv.state,
        "last_activity": conv.last_activity.isoformat() if conv.last_activity else None,
        "summary": conv.summary.summary_text if conv.summary else None,
        "messages": [
            {
                "id": m.id,
                "sender_type": m.sender_type,
                "content": m.content,
                "tokens": m.tokens,
                "metadata": m.msg_metadata,
                "created_at": m.created_at.isoformat(),
            }
            for m in conv.messages
        ],
    }
