# FastAPI entry for the CGS AI service: lifespan, CORS, health, /ai/v1.
# Uses dedicated AI Postgres, not the CGS clinic database.
import time
import uuid
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.logging import logger
from app.db.session import init_db
from app.api.routes import router as ai_router
from app.core.http_client import close_http_client
from app.memory.summary_worker import summary_worker_loop


# Lifespan.
@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing CGS AI Service database...")
    await init_db()
    stop_event = asyncio.Event()
    worker_task = asyncio.create_task(summary_worker_loop(stop_event))
    logger.info("CGS AI Service initialized successfully.")
    yield
    stop_event.set()
    try:
        await asyncio.wait_for(worker_task, timeout=5)
    except (asyncio.TimeoutError, Exception):
        worker_task.cancel()
    await close_http_client()
    logger.info("Shutting down CGS AI Service.")


app = FastAPI(
    title="CGS AI Intelligence Service",
    version="1.0.0",
    description="Decoupled, multi-entity AI intelligence engine powered by OpenRouter & 4-layer memory architecture.",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# CORS — prefer configured origins; never pair allow_origins=* with credentials=True
_cors_origins = [
    o.strip()
    for o in settings.CORS_ORIGINS.split(",")
    if o.strip()
]
_is_dev = settings.APP_ENV.lower() in {"development", "dev", "local"}
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins or ["http://localhost:4000"],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1)(:\d+)?" if _is_dev else None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Response-Time-Ms"],
)


# Correlation id middleware.
@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    req_id = request.headers.get("X-Request-ID") or f"ai_req_{uuid.uuid4().hex[:12]}"
    start_time = time.time()

    response = await call_next(request)

    duration = int((time.time() - start_time) * 1000)
    response.headers["X-Request-ID"] = req_id
    response.headers["X-Response-Time-Ms"] = str(duration)
    return response


# Health check endpoint confirming API and LLM provider readiness.
@app.get("/health", tags=["Health"])
async def health_check():
    provider = "groq" if "groq.com" in settings.llm_base_url else "openrouter"
    return {
        "status": "ok",
        "service": "cgs-ai-service",
        "environment": settings.APP_ENV,
        "provider": provider,
        "base_url": settings.llm_base_url,
        "default_model": settings.DEFAULT_MODEL,
        "fallback_model": settings.FALLBACK_MODEL,
        "fallback_model_2": settings.FALLBACK_MODEL_2,
        "llm_configured": bool(settings.api_key),
        "openrouter_configured": bool(settings.api_key),
    }


# Mount Universal AI Routes under /ai/v1
app.include_router(ai_router, prefix="/ai/v1", tags=["AI Engine"])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=settings.APP_HOST,
        port=settings.APP_PORT,
        reload=settings.DEBUG,
    )
