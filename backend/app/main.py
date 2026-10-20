import time
import uuid
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse
import os

from backend.app.config import settings
from backend.app.core.logging import logger, set_correlation_id
from backend.app.database import init_db, SessionLocal
from backend.app.services.seed_data import seed_demonstration_data
from backend.app.models.user import User
from backend.app.core.security import hash_password

from backend.app.api.v1.auth import router as auth_router
from backend.app.api.v1.companies import router as companies_router
from backend.app.api.v1.prospects import router as prospects_router
from backend.app.api.v1.review import router as review_router
from backend.app.api.v1.ingestion import router as ingestion_router
from backend.app.api.v1.system import router as system_router

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing Working-Capital Platform database...")
    init_db()
    
    # Initialize default admin/broker user if not present
    db = SessionLocal()
    try:
        admin = db.query(User).filter(User.email == "broker@commercialfinance.co.uk").first()
        if not admin:
            admin = User(
                email="broker@commercialfinance.co.uk",
                hashed_password=hash_password("FintechBroker2026!"),
                full_name="Commercial Prospecting Broker",
                role="broker"
            )
            db.add(admin)
            db.commit()
            logger.info("Created default broker user (broker@commercialfinance.co.uk)")

        # Auto-seed demonstration data if in demo mode
        if settings.DEMO_MODE:
            seed_demonstration_data(db)
    finally:
        db.close()
    yield

app = FastAPI(
    title=settings.PROJECT_NAME,
    version="1.0.0",
    description="UK Working-Capital Intelligence & Prospect Scoring Platform API",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.BACKEND_CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Correlation ID Middleware
@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    cid = request.headers.get("X-Correlation-ID", str(uuid.uuid4()))
    set_correlation_id(cid)
    start_time = time.time()
    
    response = await call_next(request)
    
    process_time = (time.time() - start_time) * 1000.0
    response.headers["X-Correlation-ID"] = cid
    response.headers["X-Process-Time-Ms"] = f"{process_time:.2f}"
    return response

# Include API Routers
app.include_router(auth_router, prefix=settings.API_V1_STR)
app.include_router(companies_router, prefix=settings.API_V1_STR)
app.include_router(prospects_router, prefix=settings.API_V1_STR)
app.include_router(review_router, prefix=settings.API_V1_STR)
app.include_router(ingestion_router, prefix=settings.API_V1_STR)
app.include_router(system_router, prefix=settings.API_V1_STR)

# Mount frontend static directory if exists
frontend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "frontend", "dist")
if os.path.exists(frontend_dir):
    app.mount("/static", StaticFiles(directory=frontend_dir), name="static")

    @app.get("/")
    def serve_frontend():
        return FileResponse(os.path.join(frontend_dir, "index.html"))
