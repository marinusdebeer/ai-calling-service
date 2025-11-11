"""
AI Calling Service
FastAPI service that handles Twilio Media Streams and bridges to OpenAI Realtime API
"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import (
    validate_env_vars,
    OPENAI_API_KEY,
    APP_URL,
    AI_CALLING_SERVICE_URL,
    TWILIO_ZEN_ZONE_AGENT_SID,
)
from services.twilio_service import is_twilio_configured
from routers import calls, admin, webhooks, health

# Validate environment variables on startup
validate_env_vars()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events"""
    print("🚀 AI Calling Service starting...")
    print(f"   OpenAI API Key: {'✅ Set' if OPENAI_API_KEY else '❌ Missing'}")
    print(f"   Twilio Client: {'✅ Configured' if is_twilio_configured() else '❌ Missing'}")
    print(f"   Twilio Agent SID: {'✅ Set' if TWILIO_ZEN_ZONE_AGENT_SID else '❌ Missing'}")
    print(f"   Next.js API URL: {APP_URL}")
    print(f"   AI Service URL: {AI_CALLING_SERVICE_URL}")
    yield
    print("🛑 AI Calling Service shutting down...")


app = FastAPI(
    title="AI Calling Service",
    description="Handles Twilio Media Streams and bridges to OpenAI Realtime API",
    lifespan=lifespan,
)

# CORS middleware (if needed for API endpoints)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(calls.router)
app.include_router(admin.router)
app.include_router(webhooks.router)
app.include_router(health.router)


if __name__ == "__main__":
    import uvicorn
    
    port = int(os.getenv("PORT", "8000"))
    host = os.getenv("HOST", "0.0.0.0")
    
    uvicorn.run(
        "main:app",
        host=host,
        port=port,
        reload=os.getenv("ENV") == "development",
    )
