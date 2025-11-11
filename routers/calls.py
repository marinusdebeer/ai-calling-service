"""
Call-related endpoints
"""
import re
import time
from urllib.parse import urlparse, urlunparse
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
            # Remove protocol (http:// or https://) and trailing slashes
            domain = re.sub(r'^https?://', '', AI_CALLING_SERVICE_URL).rstrip('/')
            
            # Validate domain format
            if not domain or '/' in domain.split('.')[0] or not domain.count('.') >= 1:
                print(f"❌ Invalid domain extracted: '{domain}' from '{AI_CALLING_SERVICE_URL}'")
                response = VoiceResponse()
                response.say("Sorry, service configuration error.", voice="alice")
                return HTMLResponse(content=str(response), media_type="application/xml", status_code=500)
            
            # Use WSS for production URLs, WS for localhost
            protocol = "wss" if "localhost" not in domain and "127.0.0.1" not in domain else "ws"
            
            # Validate call_sid is URL-safe
            path_segment = str(call_sid).strip()
            if not path_segment or '/' in path_segment or '?' in path_segment or '#' in path_segment:
                print(f"❌ Invalid call_sid for URL: '{call_sid}'")
                response = VoiceResponse()
                response.say("Sorry, call configuration error.", voice="alice")
                return HTMLResponse(content=str(response), media_type="application/xml", status_code=500)
            
            stream_url = f"{protocol}://{domain}/media-stream/{path_segment}"
            
            # Validate the complete URL format
            try:
                parsed = urlparse(stream_url)
                if not parsed.scheme or not parsed.netloc or not parsed.path:
                    raise ValueError("Invalid URL components")
                # Reconstruct to ensure proper formatting
                stream_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
            except Exception as e:
                print(f"❌ Failed to validate Stream URL: {stream_url}, error: {e}")
                response = VoiceResponse()
                response.say("Sorry, service configuration error.", voice="alice")
                return HTMLResponse(content=str(response), media_type="application/xml", status_code=500)
        else:
            # Fallback for local development
            host = request.url.hostname
            protocol = "wss" if "localhost" not in host and "127.0.0.1" not in host else "ws"
            stream_url = f"{protocol}://{host}/media-stream/{call_sid}"
        
        print(f"📞 Setting up Media Stream for agent:")
        print(f"   AI_CALLING_SERVICE_URL: {AI_CALLING_SERVICE_URL}")
        print(f"   Extracted domain: {domain if AI_CALLING_SERVICE_URL else host}")
        print(f"   Protocol: {protocol}")
        print(f"   Stream URL: {stream_url}")
        
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


@router.api_route("/outbound-twiml", methods=["GET", "POST"])
async def outbound_twiml(request: Request):
    """
    Generate TwiML for outbound AI calls
    Called by Twilio when an outbound call is answered
    Uses the actual callSid from Twilio's request to construct the WebSocket URL
    """
    try:
        # Parse form data (POST) or query params (GET)
        if request.method == "POST":
            data = await request.form()
        else:
            data = request.query_params
        
        # Get callSid from Twilio (this is the actual Twilio Call SID)
        call_sid = data.get("CallSid") or data.get("callSid")
        call_id = data.get("callId") or data.get("CallId")
        
        if not call_sid:
            print("❌ CallSid missing in outbound TwiML request")
            response = VoiceResponse()
            response.say("Sorry, call configuration error.", voice="alice")
            return HTMLResponse(content=str(response), media_type="application/xml", status_code=400)
        
        print(f"📋 Generating TwiML for outbound call:")
        print(f"   CallSid from Twilio: {call_sid}")
        print(f"   CallId from query: {call_id}")
        
        # Try to get callId from mapping if not provided
        if not call_id and call_sid in incoming_call_mapping:
            call_id = incoming_call_mapping[call_sid].get("call_id")
            print(f"   Found callId from mapping: {call_id}")
        
        # Extract domain from AI_CALLING_SERVICE_URL
        if not AI_CALLING_SERVICE_URL:
            print("❌ AI_CALLING_SERVICE_URL not configured")
            response = VoiceResponse()
            response.say("Sorry, service configuration error.", voice="alice")
            return HTMLResponse(content=str(response), media_type="application/xml", status_code=500)
        
        # Remove protocol (http:// or https://) and trailing slashes
        domain = re.sub(r'^https?://', '', AI_CALLING_SERVICE_URL).rstrip('/')
        
        # Validate domain format
        if not domain or '/' in domain.split('.')[0] or not domain.count('.') >= 1:
            print(f"❌ Invalid domain extracted: '{domain}' from '{AI_CALLING_SERVICE_URL}'")
            response = VoiceResponse()
            response.say("Sorry, service configuration error.", voice="alice")
            return HTMLResponse(content=str(response), media_type="application/xml", status_code=500)
        
        # Validate call_sid is URL-safe
        path_segment = str(call_sid).strip()
        if not path_segment or '/' in path_segment or '?' in path_segment or '#' in path_segment:
            print(f"❌ Invalid call_sid for URL: '{call_sid}'")
            response = VoiceResponse()
            response.say("Sorry, call configuration error.", voice="alice")
            return HTMLResponse(content=str(response), media_type="application/xml", status_code=500)
        
        # Construct WebSocket URL using the actual callSid from Twilio
        websocket_url = f"wss://{domain}/media-stream/{path_segment}"
        
        # Validate the complete URL format
        try:
            parsed = urlparse(websocket_url)
            if not parsed.scheme or not parsed.netloc or not parsed.path:
                raise ValueError("Invalid URL components")
            # Reconstruct to ensure proper formatting
            websocket_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
        except Exception as e:
            print(f"❌ Failed to validate WebSocket URL: {websocket_url}, error: {e}")
            response = VoiceResponse()
            response.say("Sorry, service configuration error.", voice="alice")
            return HTMLResponse(content=str(response), media_type="application/xml", status_code=500)
        
        print(f"   WebSocket URL: {websocket_url}")
        
        # Generate TwiML with Connect/Stream
        twiml = VoiceResponse()
        connect = Connect()
        connect.stream(url=websocket_url)
        twiml.append(connect)
        
        # Keep call active
        twiml.pause(length=3600)  # Pause for 1 hour (max call duration)
        
        twiml_xml = str(twiml)
        print(f"✅ Generated TwiML for outbound call:")
        print(f"   {twiml_xml}")
        
        return HTMLResponse(content=twiml_xml, media_type="application/xml")
        
    except Exception as e:
        print(f"❌ Error generating outbound TwiML: {e}")
        import traceback
        traceback.print_exc()
        response = VoiceResponse()
        response.say("Sorry, an error occurred.", voice="alice")
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
        
        # Extract domain from AI_CALLING_SERVICE_URL (remove protocol and trailing slashes)
        if not AI_CALLING_SERVICE_URL:
            return JSONResponse(
                {"error": "AI_CALLING_SERVICE_URL not configured"},
                status_code=500
            )
        
        # Remove protocol (http:// or https://) and trailing slashes
        domain = re.sub(r'^https?://', '', AI_CALLING_SERVICE_URL).rstrip('/')
        
        # Validate domain format
        if not domain or '/' in domain.split('.')[0] or not domain.count('.') >= 1:
            print(f"❌ Invalid domain extracted: '{domain}' from '{AI_CALLING_SERVICE_URL}'")
            return JSONResponse(
                {"error": f"Invalid AI_CALLING_SERVICE_URL format: {AI_CALLING_SERVICE_URL}"},
                status_code=500
            )
        
        # Construct WebSocket URL - must be wss:// for production
        # Ensure call_id is URL-safe (it should be, but validate)
        path_segment = str(call_id).strip()
        if not path_segment or '/' in path_segment or '?' in path_segment or '#' in path_segment:
            print(f"❌ Invalid call_id for URL: '{call_id}'")
            return JSONResponse(
                {"error": f"Invalid call_id format: {call_id}"},
                status_code=400
            )
        
        websocket_url = f"wss://{domain}/media-stream/{path_segment}"
        
        # Validate the complete URL format using urlparse
        try:
            parsed = urlparse(websocket_url)
            if not parsed.scheme or not parsed.netloc or not parsed.path:
                raise ValueError("Invalid URL components")
            # Reconstruct to ensure proper formatting
            websocket_url = urlunparse((parsed.scheme, parsed.netloc, parsed.path, '', '', ''))
        except Exception as e:
            print(f"❌ Failed to validate WebSocket URL: {websocket_url}, error: {e}")
            return JSONResponse(
                {"error": f"Invalid WebSocket URL format: {websocket_url}"},
                status_code=500
            )
        
        # Store mapping BEFORE creating call so TwiML endpoint can look it up
        if call_id:
            incoming_call_mapping[call_id] = {
                "call_id": call_id,
                "from": from_phone,
                "to": to_phone,
                "timestamp": time.time(),
                "is_outgoing": True,
                "initial_prompts": initial_prompts,
            }
            print(f"📝 Stored mapping for callId={call_id} (will be updated with callSid after call creation)")
        
        # Use TwiML URL instead of inline TwiML
        # This allows us to use the actual callSid when Twilio requests the TwiML
        twiml_url = f"{AI_CALLING_SERVICE_URL}/outbound-twiml?callId={call_id}"
        
        print(f"📋 Creating call with TwiML URL:")
        print(f"   TwiML URL: {twiml_url}")
        print(f"   From: {from_phone}")
        print(f"   To: {to_phone}")
        print(f"   CallId: {call_id}")
        
        # Enable recording for outgoing AI calls
        recording_callback = None
        if call_id:
            recording_callback = f"{APP_URL}/api/calls/recording-webhook?callId={call_id}"
        
        call = twilio_client.calls.create(
            from_=from_phone,
            to=to_phone,
            url=twiml_url,  # Use URL instead of inline TwiML
            method="POST",  # Twilio will POST to this URL
            record=True,  # Enable recording
            recording_status_callback=recording_callback,
            recording_status_callback_method="POST",
        )
        
        call_sid = call.sid
        print(f"✅ Call started with SID: {call_sid}")
        print(f"   Mapping: callSid={call_sid} → callId={call_id}")
        
        # Update mapping with callSid (already stored by callId above)
        if call_id and call_id in incoming_call_mapping:
            incoming_call_mapping[call_id]["twilio_call_sid"] = call_sid
            # Also store by callSid for reverse lookup
            incoming_call_mapping[call_sid] = incoming_call_mapping[call_id].copy()
            print(f"📝 Updated mappings: callId={call_id} ↔ callSid={call_sid}")
        
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

