"""Application entry point.

Run locally:   uvicorn app.main:app --reload
API docs:      http://localhost:8000/docs
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from app.config import PROJECT_ROOT, Settings, get_settings
from app.database import Base, create_db_engine
from app.dependencies import RateLimiter
from app.pipeline.llm_client import LLMClient, build_llm_client
from app.pipeline.preprocessing import QueryValidationError, SpellingDetector
from app.routers import queries, users
from app.schemas import HealthOut

STATIC_DIR = PROJECT_ROOT / "static"
VOCABULARY_FILE = Path(__file__).parent / "data" / "domain_vocabulary.txt"
_USE_DEFAULT = object()

logger = logging.getLogger("app")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # The OpenAI SDK's debug logs can include request bodies; keep them quiet.
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _first_error(exc: RequestValidationError) -> str:
    errors = exc.errors()
    if not errors:
        return "The request was not valid."
    first = errors[0]
    msg = str(first.get("msg", "The request was not valid.")).removeprefix("Value error, ")
    # loc looks like ("body", "user_id"); name the field when it isn't a model-level error.
    fields = [str(part) for part in first.get("loc", ()) if part not in ("body", "query", "path")]
    return f"{fields[-1]}: {msg}" if fields and not msg.endswith(".") else msg


def create_app(settings: Settings | None = None, llm_client: LLMClient | None | object = _USE_DEFAULT) -> FastAPI:
    """App factory. Tests pass their own settings and a fake LLM."""
    settings = settings or get_settings()
    _configure_logging(settings.log_level)

    engine = create_db_engine(settings.database_url)
    session_factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        # Creates tables on first run. For schema changes in production use Alembic (see README).
        Base.metadata.create_all(engine)
        logger.info("Started %s (AI %s, model %s)", settings.app_name,
                    "enabled" if _app.state.llm else "disabled", settings.openai_model)
        yield
        engine.dispose()

    is_prod = settings.environment.lower() == "production"
    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description="Hybrid rule-based + AI search query rewriter.",
        lifespan=lifespan,
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
    )

    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.llm = build_llm_client(settings) if llm_client is _USE_DEFAULT else llm_client
    app.state.spelling = SpellingDetector(VOCABULARY_FILE)
    app.state.rate_limiter = RateLimiter(settings.rate_limit_per_minute)

    if settings.cors_origin_list:
        app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list,
                           allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    @app.exception_handler(QueryValidationError)
    async def _query_invalid(_request: Request, exc: QueryValidationError):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(RequestValidationError)
    async def _request_invalid(_request: Request, exc: RequestValidationError):
        # One readable sentence instead of FastAPI's nested error list.
        return JSONResponse(status_code=422, content={"detail": _first_error(exc)})

    @app.exception_handler(Exception)
    async def _unexpected(_request: Request, exc: Exception):
        logger.exception("Unhandled error")
        return JSONResponse(status_code=500, content={"detail": "Something went wrong on the server. Check the server logs."})

    @app.get("/api/health", response_model=HealthOut, tags=["system"])
    def health():
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            db_state = "ok"
        except Exception:  # noqa: BLE001
            logger.exception("Database health check failed")
            db_state = "error"
        return HealthOut(
            status="ok" if db_state == "ok" else "degraded",
            ai_enabled=app.state.llm is not None,
            model=settings.openai_model if app.state.llm is not None else None,
            database=db_state,
        )

    app.include_router(users.router)
    app.include_router(queries.router)

    # Mounted last so /api routes always win. html=True serves index.html at "/".
    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
