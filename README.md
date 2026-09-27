# CGS AI Service — Folder & File Guide

Python **FastAPI** intelligence engine used by Clinic Growth (WhatsApp receptionist) and by **GENERIC** landing chatbots.

For the end-to-end message pipeline, see [`../../CGS_AI_FLOW.md`](../../CGS_AI_FLOW.md).

---

## Top-level layout

```text
ai_service/
├── main.py              # App entry: lifespan, CORS, /health, mounts /ai/v1
├── requirements.txt     # Python dependencies
├── .env / .env.example  # Secrets & model config (do not commit .env)
├── alembic.ini          # Alembic config for PostgreSQL migrations
├── alembic/             # DB migration scripts
├── tests/               # Pytest suite
├── tmp_purge_canned.py  # One-off ops script (not runtime)
└── app/                 # Application package
```

| Path | Role |
|------|------|
| `main.py` | Creates FastAPI app, starts summary worker on lifespan, mounts routes |
| `requirements.txt` | pip packages |
| `.env` | `DATABASE_URL` (PostgreSQL), `OPENROUTER_*`, `CGS_*`, models, timeouts |
| `app/db/models.py` | PostgreSQL ORM models (entities, conversations, messages, summaries, addons, usage) |
| `alembic/` | Schema migrations for the dedicated AI PostgreSQL database |
| `tests/` | Health, entities, tools, memory tests |
| `tmp_purge_canned.py` | Manual DB cleanup helper — not part of the server |

---

## `app/` package map

```text
app/
├── api/         # HTTP surface
├── core/        # Config, auth, HTTP client, logging, cache bust
├── db/          # ORM models + async sessions
├── entities/    # CLINIC / DOCTOR / GENERIC adapters
├── memory/      # 4-layer memory + summary worker
├── providers/   # OpenRouter LLM client
├── services/    # Orchestrator, booking, prompts, entities CRUD
├── tools/       # LLM-callable tools (clinic + generic)
└── utils/       # Phone canonicalization, etc.
```

---

### `app/api/` — HTTP API

| File | Role |
|------|------|
| `routes.py` | Endpoints: `/generate`, entities, usage, conversations, `/cache/invalidate`, `/summary/merge` |
| `schemas.py` | Pydantic request/response models |

---

### `app/core/` — Cross-cutting infrastructure

| File | Role |
|------|------|
| `config.py` | Settings from env (models, CGS URL, timeouts, summary worker) |
| `security.py` | Validates `X-Internal-Service-Key` on AI routes |
| `http_client.py` | Shared `httpx.AsyncClient` for OpenRouter + CGS |
| `clinic_cache.py` | Busts doctors/services/context TTL caches |
| `logging.py` | Logger setup |
| `__init__.py` | Core package marker |

---

### `app/db/` — AI database

| File | Role |
|------|------|
| `base.py` | SQLAlchemy declarative `Base` |
| `models.py` | Tables: Entity, Conversation, Message, Summary, Addon, UsageLog |
| `session.py` | Async engine, `AsyncSessionLocal`, `init_db`, `get_db` |

---

### `app/entities/` — Entity resolution

Maps `entity_type` → live context + system prompt.

| File | Role |
|------|------|
| `base.py` | `EntityContext` model + `BaseEntityAdapter` |
| `resolver.py` | Dispatches CLINIC / DOCTOR / GENERIC |
| `clinic.py` | Fetches CGS `/ai/context`, builds receptionist prompt, caches card |
| `doctor.py` | Doctor-scoped context from CGS |
| `generic.py` | Landing chatbot: prompt + `configuration` from AI DB |

---

### `app/memory/` — Four-layer conversation memory

| File | Role |
|------|------|
| `memory_engine.py` | Assembles recent + summary + addons + live card |
| `recent_memory.py` | Last N message turns for the prompt |
| `summary_memory.py` | Loads consolidated summary layer |
| `addons.py` | Add / upsert pending `SummaryAddon` rows |
| `addon_extractor.py` | Heuristics that create addons from user text |
| `summary_processor.py` | Merges pending fact addons → new summary (LLM) |
| `summary_worker.py` | Background loop: idle chats → merge (skips `BOOKING_DRAFT` / language) |

---

### `app/providers/` — LLM backends

| File | Role |
|------|------|
| `base.py` | `LLMResponse` + abstract `BaseLLMProvider` |
| `openrouter.py` | Chat/completions, model chain, free-tier 429 handling, timeouts |

---

### `app/services/` — Business orchestration

| File | Role |
|------|------|
| `ai_orchestrator.py` | **Main turn:** tool packs, static booking, LLM loop, persist, usage |
| `booking_flow.py` | Hybrid wizard: language→service→age→gender→screen → AI slots/book |
| `prompt_builder.py` | Builds system + history + user messages for OpenRouter |
| `entity_service.py` | CRUD for AI-DB entities (GENERIC chatbots) |

---

### `app/tools/` — LLM tools

| File | Role |
|------|------|
| `base.py` | `BaseTool` + OpenRouter function schema |
| `registry.py` | Registers tools; filters by capability / pack |
| `clinic_tools.py` | CGS tools: doctors, services, availability, book, patient, lead, handoff |
| `generic_tools.py` | Entity info, FAQ, knowledge base (from `configuration`) |

---

### `app/utils/` — Helpers

| File | Role |
|------|------|
| `phone.py` | Canonicalize WhatsApp participant IDs for memory continuity |
| `__init__.py` | Utils package |

---

## Request path (mental model)

```text
CGS  →  POST /ai/v1/generate
              │
              ▼
        ai_orchestrator
              │
    ┌─────────┼─────────┬──────────────┐
    ▼         ▼         ▼              ▼
 entities  memory    booking_flow   providers
 (clinic)  (4 layer) (static/AI)    (OpenRouter)
    │                   │
    └─────── tools ─────┘
         (CGS HTTP)
```

| Concern | Where it lives |
|---------|----------------|
| “Who is this clinic?” | `entities/` |
| “What did they say before?” | `memory/` |
| “Book appointment wizard” | `services/booking_flow.py` + orchestrator |
| “Call CGS / list slots” | `tools/clinic_tools.py` |
| “Talk to the model” | `providers/openrouter.py` |
| “HTTP contract” | `api/routes.py` + `schemas.py` |

---

## Quick start

```bash
cd backend/ai_service
pip install -r requirements.txt
# copy .env.example → .env and set OPENROUTER_API_KEY + CGS_INTERNAL_*
python main.py
```

- Health: `GET /health`  
- Docs: `http://localhost:8000/docs`  
- Generate: `POST /ai/v1/generate` (requires internal service key)

---

## Related docs

| Doc | Purpose |
|-----|---------|
| [`CGS_AI_FLOW.md`](../../CGS_AI_FLOW.md) | Interactive end-to-end WhatsApp → AI flow |
| [`AAA_report.txt`](../../AAA_report.txt) | Shipped work / deferred paid-model notes |
| [`AI_PRD.md`](../../AI_PRD.md) | Product requirements & contracts |
