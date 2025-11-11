"""
Call-related endpoints
"""
import re
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
from services.nextjs_client import fetch_call_id, update_call_record
from state import incoming_call_mapping, agent_call_mapping
from handlers.media_stream import handle_media_stream

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
        if call_id:
            print(f"   Found callId={call_id} for callSid={call_sid}")
        
        print(f"📞 Routing call to Twilio AI Agent (callId={call_id})")
        if call_id:
            print(f"   Recording callback: {APP_URL}/api/calls/recording-webhook?callId={call_id}")
        
        # Store mapping for agent-call to look up later
        if call_id:
            incoming_call_mapping[call_sid] = {
                "call_id": call_id,
                "from": caller,
                "timestamp": time.time()
            }
            print(f"   Stored mapping: originalCallSid={call_sid} → callId={call_id}")
        
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
        print(f"🔔 Agent-call endpoint called: method={request.method}, url={request.url}")
        
        # Parse form data (POST) or query params (GET)
        if request.method == "POST":
            data = await request.form()
            print(f"📥 POST form data received: {dict(data)}")
        else:
            data = request.query_params
            print(f"📥 GET query params received: {dict(data)}")
        
        call_sid = data.get("CallSid")  # This is the agent's callSid (different from original)
        if not call_sid:
            print("❌ CallSid missing in agent-call")
            return HTMLResponse("Error: CallSid missing", media_type="text/plain", status_code=400)
        
        from_number = data.get("From", "")
        print(f"📞 Agent call details: CallSid={call_sid}, From={from_number}")
        
        original_call_sid = None
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
            
            print(f"🤖 Agent call received (incoming): agentCallSid={call_sid}, originalCallSid={original_call_sid}, callId={call_id}")
        else:
            print(f"🤖 Agent call received: callSid={call_sid}, from={from_number} (no matching call found)")
            # Fallback: try to look up by callSid in Next.js API
            call_id = await fetch_call_id(call_sid)
            if call_id:
                print(f"   Found callId={call_id} via Next.js API lookup")
            
            # If we still don't have a callId, this is likely a duplicate or orphaned agent call
            # Reject it to prevent it from showing up in the browser
            if not call_id:
                print(f"⚠️ Rejecting agent call without callId: callSid={call_sid}, from={from_number}")
                twiml = VoiceResponse()
                twiml.hangup()
                return HTMLResponse(content=str(twiml), media_type="application/xml")
        
        # Generate TwiML
        # Use AI_CALLING_SERVICE_URL for production (Railway proxy issue)
        # Fallback to request hostname for local development
        if AI_CALLING_SERVICE_URL:
            raw_domain = AI_CALLING_SERVICE_URL
            # Properly extract domain - remove protocol and trailing slashes, strip all whitespace
            if raw_domain.startswith('https://'):
                domain = raw_domain[8:].rstrip('/').strip()  # Remove 'https://' and trailing /
            elif raw_domain.startswith('http://'):
                domain = raw_domain[7:].rstrip('/').strip()  # Remove 'http://' and trailing /
            else:
                domain = raw_domain.rstrip('/').strip()
            # Use WSS for production URLs, WS for localhost
            protocol = "wss" if "localhost" not in domain and "127.0.0.1" not in domain else "ws"
            stream_url = f"{protocol}://{domain}/media-stream/{call_id}".strip().replace('\n', '').replace('\r', '')
        else:
            # Fallback for local development
            host = request.url.hostname
            protocol = "wss" if "localhost" not in host and "127.0.0.1" not in host else "ws"
            stream_url = f"{protocol}://{host}/media-stream/{call_id}".strip().replace('\n', '').replace('\r', '')
        
        print(f"📞 Setting up Media Stream for agent: stream_url={stream_url}")
        print(f"   Using AI_CALLING_SERVICE_URL: {AI_CALLING_SERVICE_URL}")
        print(f"   Domain extracted: {domain if AI_CALLING_SERVICE_URL else 'N/A (using request hostname)'}")
        
        # Incoming call: Set up Media Stream (this bridges to OpenAI Realtime API)
        twiml = VoiceResponse()
        connect = Connect()
        connect.stream(url=stream_url)
        twiml.append(connect)
        
        # Keep call active
        twiml.pause(length=3600)  # Pause for 1 hour (max call duration)
        
        twiml_xml = str(twiml)
        print(f"📋 Generated TwiML for agent-call:")
        print(f"   {twiml_xml}")
        
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
        
        # Use inline TwiML with Connect/Stream
        # Note: We use call_id in the URL path, but Twilio will send the actual callSid
        # in the WebSocket connection data. The handler will extract callSid from the
        # WebSocket "connected" event and use that to look up the mapping.
        raw_domain = AI_CALLING_SERVICE_URL or ""
        # Properly extract domain - remove protocol and trailing slashes, strip all whitespace
        if raw_domain.startswith('https://'):
            domain = raw_domain[8:].rstrip('/').strip()  # Remove 'https://' and trailing /
        elif raw_domain.startswith('http://'):
            domain = raw_domain[7:].rstrip('/').strip()  # Remove 'http://' and trailing /
        else:
            domain = raw_domain.rstrip('/').strip()
        
        # Also, ensure call_id is URL-safe (no special characters)
        # The call_id should already be safe, but let's make sure
        # For now, we'll just ensure it's not empty and doesn't have special chars
        if not call_id or any(c in call_id for c in "!@#$%^&*()[]{};:,./<>?\\|`~"):
            print(f"⚠️ Rejecting call with invalid call_id: {call_id}")
            return JSONResponse(
                {"error": "Invalid call_id format. It should be alphanumeric or hyphens only."},
                status_code=400
            )

        # Use call_id in URL - handler will get actual callSid from WebSocket connection
        # Use Twilio's VoiceResponse to ensure proper XML encoding
        # Strip any whitespace/newlines from URL to prevent XML encoding issues
        stream_url = f"wss://{domain}/media-stream/{call_id}".strip().replace('\n', '').replace('\r', '')
        twiml_response = VoiceResponse()
        connect = Connect()
        connect.stream(url=stream_url)
        twiml_response.append(connect)
        # Keep call active
        twiml_response.pause(length=3600)  # Pause for 1 hour (max call duration)
        outbound_twiml = str(twiml_response)
        
        print(f"📋 Generated TwiML for outgoing call:")
        print(f"   {outbound_twiml}")
        print(f"   WebSocket URL: {stream_url}")
        print(f"   Note: Twilio will send actual callSid in WebSocket connection data")
        
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
        print(f"✅ Call started with SID: {call_sid}")
        print(f"   Mapping: callSid={call_sid} → callId={call_id}")
        
        # Store mapping for Media Stream handler
        # The handler receives call_id in URL path, but needs to map to callSid
        # Store by both call_id (for URL lookup) and callSid (for WebSocket data lookup)
        if call_id:
            incoming_call_mapping[call_id] = {
                "call_id": call_id,
                "twilio_call_sid": call_sid,
                "from": TWILIO_PHONE_NUMBER,
                "to": to_phone,
                "timestamp": time.time(),
                "is_outgoing": True,
                "initial_prompts": initial_prompts,
            }
            incoming_call_mapping[call_sid] = {
                "call_id": call_id,
                "from": TWILIO_PHONE_NUMBER,
                "to": to_phone,
                "timestamp": time.time(),
                "is_outgoing": True,
                "initial_prompts": initial_prompts,
            }
            print(f"📝 Stored mappings: callId={call_id} and callSid={call_sid}")
        
        # Update call record in Next.js database
        await update_call_record(call_id, call.sid, "RINGING")
        
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

