"""
Call-related endpoints
"""
import time
from fastapi import APIRouter, Request, WebSocket
from fastapi.responses import HTMLResponse, JSONResponse
from twilio.twiml.voice_response import VoiceResponse, Connect

from config import (
    APP_URL,
    TWILIO_PHONE_NUMBER,
    TWILIO_ZEN_ZONE_AGENT_SID,
    AI_CALLING_SERVICE_URL,
)
from services.twilio_service import get_twilio_client, is_twilio_configured
from services.nextjs_client import fetch_call_id, fetch_call_details, update_call_record
from state import incoming_call_mapping, agent_call_mapping
from handlers.media_stream import handle_media_stream
from utils.url_parser import build_media_stream_url
from utils.call_utils import validate_call_id
from utils.constants import STATUS_RINGING, MAX_CALL_DURATION_SECONDS

router = APIRouter()


@router.websocket("/media-stream/{call_sid}")
async def media_stream_websocket(websocket: WebSocket, call_sid: str):
    """Media Stream WebSocket endpoint"""
    await handle_media_stream(websocket, call_sid)


@router.api_route("/incoming-call", methods=["GET", "POST"])
async def incoming_call(request: Request):
    """
    Handle incoming calls and route to Twilio AI Agent
    Called by Twilio when someone calls your Twilio number
    Uses Dial with agent application to create 2 participants (enables recording)
    """
    try:
        # Parse form data (POST) or query params (GET)
        if request.method == "POST":
            data = await request.form()
        else:
            data = request.query_params
        
        call_sid = data.get("CallSid")
        if not call_sid:
            print("❌ CallSid missing in incoming call")
            return HTMLResponse("Error: CallSid missing", media_type="text/plain", status_code=400)
        
        caller = data.get("From", "")
        print(f"📞 Incoming call from: {caller}, callSid={call_sid}")
        
        # Try to find callId from Next.js API using callSid
        call_id = await fetch_call_id(call_sid)
        
        # Fetch call details to get clientId and phone number
        client_id = None
        to_phone = None
        if call_id:
            call_details = await fetch_call_details(call_id)
            if call_details:
                call_data = call_details.get("call", {})
                client_id = call_data.get("clientId")
                to_phone = call_data.get("toPhone") or call_data.get("fromPhone")
        
        # Store mapping for agent-call to look up later
        if call_id:
            incoming_call_mapping[call_sid] = {
                "call_id": call_id,
                "client_id": client_id,  # Store clientId
                "from": caller,
                "to": to_phone,  # Store phone number
                "timestamp": time.time()
            }
        
        twiml = VoiceResponse()
        
        # Dial the Twilio AI Agent application
        # This creates 2 participants (caller + agent) which enables recording
        dial = twiml.dial(
            record="record-from-answer-dual",
            recording_status_callback=f"{APP_URL}/api/calls/recording-webhook?callId={call_id}" if call_id else None,
            recording_status_callback_method="POST",
        )
        dial.application(TWILIO_ZEN_ZONE_AGENT_SID)
        
        return HTMLResponse(content=str(twiml), media_type="application/xml")
    
    except Exception as e:
        print(f"❌ Error handling incoming call: {e}")
        import traceback
        traceback.print_exc()
        response = VoiceResponse()
        response.say("Sorry, we encountered an error. Please try again later.", voice="alice")
        return HTMLResponse(content=str(response), media_type="application/xml", status_code=500)


@router.api_route("/agent-call", methods=["GET", "POST"])
async def agent_call(request: Request):
    """
    Handle connection from Twilio AI Agent
    Called by Twilio when the agent application connects
    Sets up Media Stream to bridge to OpenAI Realtime API
    """
    try:
        # Parse form data (POST) or query params (GET)
        if request.method == "POST":
            data = await request.form()
        else:
            data = request.query_params
        
        call_sid = data.get("CallSid")  # This is the agent's callSid (different from original)
        if not call_sid:
            print("❌ CallSid missing in agent-call")
            return HTMLResponse("Error: CallSid missing", media_type="text/plain", status_code=400)
        
        from_number = data.get("From", "")
        
        call_id = None
        
        # Look up call info from mapping for incoming calls
        # Match by phone number (incoming calls only - outgoing calls use direct Media Stream)
        matching_calls = [
            (orig_sid, info) 
            for orig_sid, info in incoming_call_mapping.items() 
            if info.get("from") == from_number and not info.get("is_outgoing")
        ]
        
        if matching_calls:
            # Sort by timestamp (most recent first) and take the first one
            matching_calls.sort(key=lambda x: x[1].get("timestamp", 0), reverse=True)
            original_call_sid, call_info = matching_calls[0]
            call_id = call_info.get("call_id")
            
            # Store mapping for Media Stream handler
            agent_call_mapping[call_sid] = original_call_sid
            
            # Store agent callSid in database
            if call_id:
                await update_call_record(call_id, None, None, call_sid, True)
            
            print(f"🤖 Agent call received: agentCallSid={call_sid}, callId={call_id}")
        else:
            # No matching call found - reject orphaned agent call
            print(f"⚠️ Rejecting agent call without callId: callSid={call_sid}")
            twiml = VoiceResponse()
            twiml.hangup()
            return HTMLResponse(content=str(twiml), media_type="application/xml")
        
        # Ensure we have call_id before proceeding
        if not call_id:
            print(f"⚠️ Rejecting agent call without callId: callSid={call_sid}")
            twiml = VoiceResponse()
            twiml.hangup()
            return HTMLResponse(content=str(twiml), media_type="application/xml")
        
        # Generate TwiML - build media stream URL
        stream_url = build_media_stream_url(call_id, AI_CALLING_SERVICE_URL)
        
        # Incoming call: Set up Media Stream (this bridges to OpenAI Realtime API)
        twiml = VoiceResponse()
        connect = Connect()
        connect.stream(url=stream_url)
        twiml.append(connect)
        
        # Keep call active
        twiml.pause(length=MAX_CALL_DURATION_SECONDS)
        
        twiml_xml = str(twiml)
        
        return HTMLResponse(content=twiml_xml, media_type="application/xml")
    
    except Exception as e:
        print(f"❌ Error handling agent call: {e}")
        import traceback
        traceback.print_exc()
        response = VoiceResponse()
        response.say("Sorry, we encountered an error. Please try again later.", voice="alice")
        return HTMLResponse(content=str(response), media_type="application/xml", status_code=500)


@router.post("/initiate-call")
async def initiate_ai_call(request: Request):
    """
    Initiate an AI call via Twilio
    Called by Next.js to start an AI call
    """
    twilio_client = get_twilio_client()
    if not twilio_client:
        return JSONResponse(
            {"error": "Twilio not configured"},
            status_code=500
        )
    
    try:
        data = await request.json()
        call_id = data.get("callId")
        client_id = data.get("clientId")  # Get clientId from request
        to_phone = data.get("toPhone")
        from_phone = data.get("fromPhone", TWILIO_PHONE_NUMBER)
        initial_prompts = data.get("initialPrompts", [])
        
        if not call_id or not to_phone:
            return JSONResponse(
                {"error": "callId and toPhone are required"},
                status_code=400
            )
        
        # Validate phone number format - must be E.164 format (starts with +)
        # Reject Twilio Client identifiers (start with "client:")
        if to_phone.startswith("client:"):
            error_msg = f"Invalid phone number: '{to_phone}' appears to be a Twilio Client identifier, not a phone number. Phone numbers must be in E.164 format (e.g., +1234567890)"
            print(f"❌ {error_msg}")
            return JSONResponse(
                {"error": error_msg},
                status_code=400
            )
        
        if not to_phone.startswith("+"):
            error_msg = f"Invalid phone number format: '{to_phone}'. Phone numbers must be in E.164 format starting with '+' (e.g., +1234567890)"
            print(f"❌ {error_msg}")
            return JSONResponse(
                {"error": error_msg},
                status_code=400
            )
        
        # Validate call_id is safe for URL construction
        if not validate_call_id(call_id):
            print(f"⚠️ Rejecting call with invalid call_id: {call_id}")
            return JSONResponse(
                {"error": "Invalid call_id format. It should be alphanumeric or hyphens only."},
                status_code=400
            )

        # Use inline TwiML with Connect/Stream
        # Note: We use call_id in the URL path, but Twilio will send the actual callSid
        # in the WebSocket connection data. The handler will extract callSid from the
        # WebSocket "connected" event and use that to look up the mapping.
        stream_url = build_media_stream_url(call_id, AI_CALLING_SERVICE_URL)
        twiml_response = VoiceResponse()
        connect = Connect()
        connect.stream(url=stream_url)
        twiml_response.append(connect)
        # Keep call active
        twiml_response.pause(length=MAX_CALL_DURATION_SECONDS)
        outbound_twiml = str(twiml_response)
        
        # Enable recording for outgoing AI calls
        recording_callback = None
        if call_id:
            recording_callback = f"{APP_URL}/api/calls/recording-webhook?callId={call_id}"
        
        call = twilio_client.calls.create(
            from_=from_phone,
            to=to_phone,
            twiml=outbound_twiml,
            record=True,  # Enable recording
            recording_status_callback=recording_callback,
            recording_status_callback_method="POST",
        )
        
        call_sid = call.sid
        print(f"✅ Call started: callSid={call_sid}, callId={call_id}, clientId={client_id}, toPhone={to_phone}")
        
        # Store mapping for Media Stream handler
        # The handler receives call_id in URL path, but needs to map to callSid
        # Store by both call_id (for URL lookup) and callSid (for WebSocket data lookup)
        if call_id:
            incoming_call_mapping[call_id] = {
                "call_id": call_id,
                "client_id": client_id,  # Store clientId
                "twilio_call_sid": call_sid,
                "from": TWILIO_PHONE_NUMBER,
                "to": to_phone,  # Store phone number
                "timestamp": time.time(),
                "is_outgoing": True,
                "initial_prompts": initial_prompts,
            }
            incoming_call_mapping[call_sid] = {
                "call_id": call_id,
                "client_id": client_id,  # Store clientId
                "from": TWILIO_PHONE_NUMBER,
                "to": to_phone,  # Store phone number
                "timestamp": time.time(),
                "is_outgoing": True,
                "initial_prompts": initial_prompts,
            }
        
        # Update call record in Next.js database
        await update_call_record(call_id, call.sid, STATUS_RINGING, None, None)
        
        return JSONResponse({
            "success": True,
            "callSid": call.sid,
            "status": "initiated",
        })
    
    except Exception as e:
        print(f"❌ Error initiating AI call: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            {"error": str(e)},
            status_code=500
        )

