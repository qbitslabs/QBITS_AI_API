# AI HTTP API: schemas.
# Inbound from CGS/WhatsApp workers; not the clinic Prisma schema.
from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime


# Entity create.
class EntityCreate(BaseModel):
    type: Optional[str] = Field("GENERIC", description="CLINIC, DOCTOR, CHATBOT, RESTAURANT, ECOMMERCE, GENERIC, etc.")
    name: Optional[str] = Field("AI Assistant", description="Name of the entity or bot")
    external_id: Optional[str] = None
    system_prompt: Optional[str] = None
    configuration: Optional[Dict[str, Any]] = Field(default_factory=dict)
    status: Optional[str] = "ACTIVE"


# Entity update.
class EntityUpdate(BaseModel):
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    configuration: Optional[Dict[str, Any]] = None
    status: Optional[str] = None


# Entity response.
class EntityResponse(BaseModel):
    id: str
    type: str
    name: str
    external_id: Optional[str] = None
    system_prompt: Optional[str] = None
    configuration: Dict[str, Any]
    status: str
    created_at: datetime
    updated_at: datetime

    # Config.
    class Config:
        from_attributes = True


# Generate request.
class GenerateRequest(BaseModel):
    entity_id: str
    entity_type: Optional[str] = "GENERIC"
    participant_id: str
    message: str
    channel: Optional[str] = "WEB"
    doctor_id: Optional[str] = None
    conversation_id: Optional[str] = None
    metadata: Optional[Dict[str, Any]] = Field(default_factory=dict)
    model_override: Optional[str] = None
    temperature: Optional[float] = 0.3


# Tool call info.
class ToolCallInfo(BaseModel):
    tool_name: str
    arguments: Dict[str, Any]
    result: Any


# Token usage.
class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated_cost: float = 0.0


# Generate response.
class GenerateResponse(BaseModel):
    request_id: str
    entity_id: str
    entity_type: str
    conversation_id: str
    response: str
    tool_calls: List[ToolCallInfo] = Field(default_factory=list)
    usage: TokenUsage
    model: str
    duration_ms: int


# Summary merge request.
class SummaryMergeRequest(BaseModel):
    conversation_id: str
    entity_id: Optional[str] = None
    entity_type: Optional[str] = "CLINIC"


# Summary merge response.
class SummaryMergeResponse(BaseModel):
    conversation_id: str
    version: int
    summary_text: str
    summary_json: Dict[str, Any]
    merged_addons_count: int


# Usage stats response.
class UsageStatsResponse(BaseModel):
    entity_id: str
    total_requests: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    estimated_cost: float
