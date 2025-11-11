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
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

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

# Global exception handler to catch all unhandled errors
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """Catch all unhandled exceptions and log them"""
    import traceback
    error_msg = str(exc)
    error_trace = traceback.format_exc()
    print(f"❌ UNHANDLED EXCEPTION: {error_msg}")
    print(f"   Path: {request.url.path}")
    print(f"   Method: {request.method}")
    print(f"   Traceback:\n{error_trace}")
    
    # Return appropriate response based on exception type
    if isinstance(exc, StarletteHTTPException):
        return JSONResponse(
            {"error": error_msg, "status_code": exc.status_code},
            status_code=exc.status_code
        )
    elif isinstance(exc, RequestValidationError):
        return JSONResponse(
            {"error": "Validation error", "details": str(exc)},
            status_code=422
        )
    else:
        return JSONResponse(
            {"error": "Internal server error", "message": error_msg},
            status_code=500
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
