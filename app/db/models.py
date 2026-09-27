# AI Postgres access: models.
# SQLAlchemy models/session for the dedicated AI database only.
import uuid
from datetime import datetime
from sqlalchemy import (
    Column,
    String,
    Text,
    Integer,
    Numeric,
    DateTime,
    ForeignKey,
    Index,
    Boolean,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from app.db.base import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


class EntityModel(Base):
    __tablename__ = "entities"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    type = Column(String(50), nullable=False, index=True)
    name = Column(String(255), nullable=False)
    external_id = Column(String(255), nullable=True, index=True)
    system_prompt = Column(Text, nullable=True)
    configuration = Column(JSONB, default=dict)
    status = Column(String(50), default="ACTIVE")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    conversations = relationship("ConversationModel", back_populates="entity", cascade="all, delete-orphan")
    usage_logs = relationship("AiUsageLogModel", back_populates="entity", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_entity_type_ext", "type", "external_id"),
    )


class ConversationModel(Base):
    __tablename__ = "conversations"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    entity_id = Column(String(36), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False, index=True)
    participant_id = Column(String(255), nullable=False, index=True)
    channel = Column(String(50), default="WHATSAPP")
    state = Column(String(50), default="AI_ACTIVE")
    last_appointment_id = Column(String(36), nullable=True)
    last_appointment_date = Column(String(10), nullable=True)
    cgs_conversation_id = Column(String(36), nullable=True)
    awaiting_rebook = Column(Boolean, default=False)
    rebook_asked_date = Column(String(10), nullable=True)
    last_activity = Column(DateTime, default=datetime.utcnow, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    entity = relationship("EntityModel", back_populates="conversations")
    messages = relationship(
        "MessageModel",
        back_populates="conversation",
        cascade="all, delete-orphan",
        order_by="MessageModel.created_at",
    )
    summary = relationship(
        "ConversationSummaryModel",
        back_populates="conversation",
        uselist=False,
        cascade="all, delete-orphan",
    )
    addons = relationship("SummaryAddonModel", back_populates="conversation", cascade="all, delete-orphan")
    usage_logs = relationship("AiUsageLogModel", back_populates="conversation", cascade="all, delete-orphan")

    __table_args__ = (
        Index("idx_conv_entity_part", "entity_id", "participant_id"),
        Index("idx_conv_last_activity", "last_activity"),
    )


class MessageModel(Base):
    __tablename__ = "messages"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    sender_type = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    tokens = Column(Integer, default=0)
    msg_metadata = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    conversation = relationship("ConversationModel", back_populates="messages")


class ConversationSummaryModel(Base):
    __tablename__ = "conversation_summaries"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), unique=True, nullable=False)
    version = Column(Integer, default=1)
    summary_text = Column(Text, nullable=False)
    summary_json = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    conversation = relationship("ConversationModel", back_populates="summary")


class SummaryAddonModel(Base):
    __tablename__ = "summary_addons"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False, index=True)
    type = Column(String(50), nullable=False)
    content = Column(Text, nullable=False)
    status = Column(String(50), default="PENDING", index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    conversation = relationship("ConversationModel", back_populates="addons")


class AiUsageLogModel(Base):
    __tablename__ = "ai_usage_logs"

    id = Column(String(36), primary_key=True, default=generate_uuid)
    entity_id = Column(String(36), ForeignKey("entities.id", ondelete="CASCADE"), nullable=True, index=True)
    conversation_id = Column(String(36), ForeignKey("conversations.id", ondelete="SET NULL"), nullable=True, index=True)
    request_id = Column(String(255), nullable=False, index=True)
    model = Column(String(255), nullable=False)
    prompt_tokens = Column(Integer, default=0)
    completion_tokens = Column(Integer, default=0)
    total_tokens = Column(Integer, default=0)
    estimated_cost = Column(Numeric(14, 8), default=0.0)
    duration_ms = Column(Integer, default=0)
    success = Column(Boolean, default=True)
    error_message = Column(Text, nullable=True)
    usage_metadata = Column(JSONB, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False, index=True)

    entity = relationship("EntityModel", back_populates="usage_logs")
    conversation = relationship("ConversationModel", back_populates="usage_logs")

    __table_args__ = (
        Index("idx_usage_entity_created", "entity_id", "created_at"),
    )
