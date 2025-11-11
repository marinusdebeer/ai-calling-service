"""
Health check and root endpoints
"""
from fastapi import APIRouter
from config import OPENAI_API_KEY
from state import active_connections

router = APIRouter()


@router.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "openai_configured": bool(OPENAI_API_KEY),
        "active_connections": len(active_connections),
    }


@router.get("/")
async def root():
    """Root endpoint"""
    return {
        "service": "AI Calling Service",
        "version": "1.0.0",
        "endpoints": {
            "websocket": "/media-stream/{call_sid}",
            "initiate_call": "POST /initiate-call",
            "incoming_call": "GET/POST /incoming-call",
            "admin_prompt": "POST /admin-prompt",
            "end_call": "POST /end-call",
            "webhook": "POST /webhook",
            "recording_webhook": "POST /recording-webhook",
            "health": "/health",
        },
    }

