from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api.routes import claims, members, health
from core.config import settings

app = FastAPI(
    title="IntelliClaim — AI Claims Processing",
    description="Automated health insurance claims processing for Plum",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router)
app.include_router(claims.router,  prefix="/api/v1")
app.include_router(members.router, prefix="/api/v1")