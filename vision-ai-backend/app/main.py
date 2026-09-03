from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
import os
import sys
import subprocess
from pathlib import Path
from app.api.router import router
from app.core.database import Base, engine
from app.utils.exceptions import AppException

from app.models.camera_model import Camera
from app.models.recording_model import Recording
from app.utils.resource_path import resource_path

sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

# Project root = parent of the /app folder
_ROOT = Path(__file__).parent.parent   # DHTX-BACKEND/


# ─────────────────────────────────────────
# BOOTSTRAP: .env
# ─────────────────────────────────────────
def _ensure_env_file():
    # Packaged applications keep configuration in the user's data directory;
    # their bundled resources (including an AppImage) are read-only.
    if getattr(sys, "frozen", False):
        return
    env_path = _ROOT / ".env"
    if not env_path.exists():
        env_path.write_text(
            "DATABASE_URL=sqlite:///./vision_ai.db\n"
            "APP_NAME=Vision AI Command Center\n"
            "DEBUG=True\n",
            encoding="utf-8",
        )
        pass
    else:
        pass

_ensure_env_file()


# ─────────────────────────────────────────
# BOOTSTRAP: storage folders
# ─────────────────────────────────────────
def _ensure_storage_dirs():
    dirs = [
        "storage",
        "storage/uploads",
        "storage/recordings",
        "storage/processed",
        "storage/raw",
    ]

    for d in dirs:

        path = Path(resource_path(d))

        if not path.exists():
            path.mkdir(parents=True, exist_ok=True)
            pass
        else:
            pass


_ensure_storage_dirs()
# ─────────────────────────────────────────
# BOOTSTRAP: DB tables (create_all — no Alembic needed)
# ─────────────────────────────────────────
# ─────────────────────────────────────────
# BOOTSTRAP: DB tables + auto column migration
# ─────────────────────────────────────────
def _ensure_tables():
    try:
        from sqlalchemy import inspect, text
        from app.core.database import Base, engine

        # Create missing tables
        Base.metadata.create_all(bind=engine)

        inspector = inspect(engine)

        # =====================================================
        # cameras table migration
        # =====================================================
        if "cameras" in inspector.get_table_names():
            existing_camera_columns = [
                column["name"]
                for column in inspector.get_columns("cameras")
            ]

            camera_migrations = []

            if "recording_video_testing_path" not in existing_camera_columns:
                camera_migrations.append(
                    "ALTER TABLE cameras "
                    "ADD COLUMN recording_video_testing_path VARCHAR"
                )

            if camera_migrations:
                with engine.begin() as conn:
                    for sql in camera_migrations:
                        conn.execute(text(sql))
                        pass

        pass

    except Exception as e:
        pass

_ensure_tables()


# ---------------------------------------------------
#  BOOTSTRAP: Alembic auto-migration
# ---------------------------------------------------
def _run_migrations():
    alembic_ini = os.path.join(os.path.dirname(__file__), "..", "alembic.ini")
    alembic_ini = os.path.normpath(alembic_ini)

    if not os.path.exists(alembic_ini):
        pass
        Base.metadata.create_all(bind=engine)
        return

    try:
        pass
        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        if result.stdout:
            pass
        if result.stderr:
            pass

        if result.returncode == 0:
            pass
        else:
            pass
            Base.metadata.create_all(bind=engine)

    except FileNotFoundError:
        pass
        Base.metadata.create_all(bind=engine)
    except Exception as e:
        pass
        Base.metadata.create_all(bind=engine)


# ---------------------------------------------------
#  FastAPI App
# ---------------------------------------------------
app = FastAPI(
    title="Vision AI Command Center",
    version="1.0"
)


# ---------------------------------------------------
#  GLOBAL EXCEPTION HANDLERS
# ---------------------------------------------------
@app.exception_handler(AppException)
async def app_exception_handler(request: Request, exc: AppException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": False, "message": exc.message, "data": None}
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"status": False, "message": "Internal Server Error", "data": None}
    )


# ---------------------------------------------------
#  CORS
# ---------------------------------------------------
origins = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "http://127.0.0.1",
    "http://localhost",
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "app://localhost"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_origin_regex=r"^https?://.*$",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------
#  STATIC FILES
# ---------------------------------------------------
# app.mount("/storage", StaticFiles(directory="storage"), name="storage")
storage_dir = resource_path("storage")

pass

app.mount(
    "/storage",
    StaticFiles(directory=storage_dir),
    name="storage"
)

# ---------------------------------------------------
#  ROOT HEALTH CHECK
# ---------------------------------------------------
@app.get("/")
def health_check():
    return {"status": "ready", "message": "Vision AI Backend Running", "data": None}


@app.get("/health")
def get_health():
    """Health check endpoint for frontend readiness polling."""
    return {"status": "ready", "message": "Backend is healthy", "data": None}


# ---------------------------------------------------
#  REGISTER ROUTES
# ---------------------------------------------------
app.include_router(router)

from app.ml.debug.debug_routes import router as debug_router
app.include_router(debug_router)

# ---------------------------------------------------
#  STARTUP EVENTS
# ---------------------------------------------------
# Auth/Role seeders have been removed.
