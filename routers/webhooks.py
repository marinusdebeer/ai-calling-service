"""
Webhook endpoints for Twilio callbacks
"""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from services.nextjs_client import forward_webhook, forward_recording_webhook

router = APIRouter()


@router.post("/webhook")
async def handle_webhook(request: Request):
    """
    Handle Twilio status callbacks
    Forwards to Next.js API
    Note: Recording is now handled in TwiML via Dial with agent application
    """
    try:
        form_data = await request.form()
        call_id = request.query_params.get("callId")
        # Note: Recording is now handled in TwiML via Dial with agent application
        # No need to enable recording via REST API anymore
        
        if call_id:
            # Forward to Next.js webhook endpoint
            await forward_webhook(call_id, dict(form_data))
        
        return JSONResponse({"status": "ok"})
    
    except Exception as e:
        print(f"⚠️ Error forwarding webhook: {e}")
        return JSONResponse({"status": "ok"})  # Always return ok to Twilio


@router.post("/recording-webhook")
async def handle_recording_webhook(request: Request):
    """
    Handle Twilio recording callbacks
    Forwards to Next.js API
    """
    try:
        form_data = await request.form()
        call_id = request.query_params.get("callId")
        
        # Always forward to Next.js - it will look up callId by CallSid if needed
        await forward_recording_webhook(call_id, dict(form_data))
        
        return JSONResponse({"status": "ok"})
    
    except Exception as e:
        print(f"⚠️ Error forwarding recording webhook: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse({"status": "ok"})  # Always return ok to Twilio

