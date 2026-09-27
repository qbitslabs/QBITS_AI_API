-- Dedicated AI database: tables in public (visible in Supabase Table Editor)

CREATE TABLE IF NOT EXISTS public.entities (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    type VARCHAR(50) NOT NULL,
    name VARCHAR(255) NOT NULL,
    external_id VARCHAR(255),
    system_prompt TEXT,
    configuration JSONB DEFAULT '{}'::jsonb,
    status VARCHAR(50) DEFAULT 'ACTIVE',
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
);

CREATE INDEX IF NOT EXISTS ix_entities_type ON public.entities (type);
CREATE INDEX IF NOT EXISTS ix_entities_external_id ON public.entities (external_id);
CREATE INDEX IF NOT EXISTS idx_entity_type_ext ON public.entities (type, external_id);

CREATE TABLE IF NOT EXISTS public.conversations (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    entity_id VARCHAR(36) NOT NULL,
    participant_id VARCHAR(255) NOT NULL,
    channel VARCHAR(50) DEFAULT 'WHATSAPP',
    state VARCHAR(50) DEFAULT 'AI_ACTIVE',
    last_appointment_id VARCHAR(36),
    last_appointment_date VARCHAR(10),
    cgs_conversation_id VARCHAR(36),
    awaiting_rebook BOOLEAN DEFAULT FALSE,
    rebook_asked_date VARCHAR(10),
    last_activity TIMESTAMP WITHOUT TIME ZONE DEFAULT (NOW() AT TIME ZONE 'utc'),
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    CONSTRAINT fk_conv_entity FOREIGN KEY (entity_id) REFERENCES public.entities (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_conversations_entity_id ON public.conversations (entity_id);
CREATE INDEX IF NOT EXISTS ix_conversations_participant_id ON public.conversations (participant_id);
CREATE INDEX IF NOT EXISTS idx_conv_entity_part ON public.conversations (entity_id, participant_id);
CREATE INDEX IF NOT EXISTS idx_conv_last_activity ON public.conversations (last_activity);

CREATE TABLE IF NOT EXISTS public.messages (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    conversation_id VARCHAR(36) NOT NULL,
    sender_type VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    tokens INTEGER DEFAULT 0,
    msg_metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    CONSTRAINT fk_msg_conv FOREIGN KEY (conversation_id) REFERENCES public.conversations (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_messages_conversation_id ON public.messages (conversation_id);
CREATE INDEX IF NOT EXISTS ix_messages_created_at ON public.messages (created_at);

CREATE TABLE IF NOT EXISTS public.conversation_summaries (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    conversation_id VARCHAR(36) NOT NULL UNIQUE,
    version INTEGER DEFAULT 1,
    summary_text TEXT NOT NULL,
    summary_json JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    CONSTRAINT fk_sum_conv FOREIGN KEY (conversation_id) REFERENCES public.conversations (id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS public.summary_addons (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    conversation_id VARCHAR(36) NOT NULL,
    type VARCHAR(50) NOT NULL,
    content TEXT NOT NULL,
    status VARCHAR(50) DEFAULT 'PENDING',
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    CONSTRAINT fk_addon_conv FOREIGN KEY (conversation_id) REFERENCES public.conversations (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_summary_addons_conversation_id ON public.summary_addons (conversation_id);
CREATE INDEX IF NOT EXISTS ix_summary_addons_status ON public.summary_addons (status);

CREATE TABLE IF NOT EXISTS public.ai_usage_logs (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    entity_id VARCHAR(36),
    conversation_id VARCHAR(36),
    request_id VARCHAR(255) NOT NULL,
    model VARCHAR(255) NOT NULL,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    total_tokens INTEGER DEFAULT 0,
    estimated_cost NUMERIC(14, 8) DEFAULT 0.0,
    duration_ms INTEGER DEFAULT 0,
    success BOOLEAN DEFAULT TRUE,
    error_message TEXT,
    usage_metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
    CONSTRAINT fk_usage_entity FOREIGN KEY (entity_id) REFERENCES public.entities (id) ON DELETE CASCADE,
    CONSTRAINT fk_usage_conv FOREIGN KEY (conversation_id) REFERENCES public.conversations (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS ix_usage_entity_id ON public.ai_usage_logs (entity_id);
CREATE INDEX IF NOT EXISTS ix_usage_conversation_id ON public.ai_usage_logs (conversation_id);
CREATE INDEX IF NOT EXISTS ix_usage_request_id ON public.ai_usage_logs (request_id);
CREATE INDEX IF NOT EXISTS idx_usage_entity_created ON public.ai_usage_logs (entity_id, created_at);
