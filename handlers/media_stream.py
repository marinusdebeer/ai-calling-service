"""
Media Stream WebSocket handler - bridges Twilio and OpenAI
"""
import json
import base64
import asyncio
import time
from fastapi import WebSocket, WebSocketDisconnect
from services.openai_service import connect_to_openai_realtime, send_initial_greeting
from services.nextjs_client import (
    fetch_call_id,
    update_call_status,
    send_transcript,
    update_call_metadata,
)
from state import (
    active_connections,
    incoming_call_mapping,
    agent_call_mapping,
)


async def handle_media_stream(websocket: WebSocket, call_sid: str):
    """
    Handle Twilio Media Stream WebSocket connection
    Bridges audio between Twilio and OpenAI Realtime API
    
    Uses call_sid as path parameter (Twilio doesn't pass query params in WebSocket URLs)
    For outgoing calls, call_sid might be callId (from inline TwiML) or actual callSid
    """
    # Note: call_sid from path might be callId (for outgoing) or callSid (for incoming)
    # We'll get the actual callSid from the WebSocket "connected" event
    
    try:
        await websocket.accept()
    except Exception as e:
        print(f"❌ Failed to accept WebSocket: {e}")
        import traceback
        traceback.print_exc()
        return
    
    openai_ws = None
    actual_call_sid = None  # Will be set from WebSocket connected event
    
    # Try to get initial prompts from mapping using path param (might be callId or callSid)
    initial_prompts = []
    if call_sid in incoming_call_mapping:
        initial_prompts = incoming_call_mapping[call_sid].get("initial_prompts", [])
        if initial_prompts:
            print(f"📝 Found {len(initial_prompts)} initial prompt(s) for call")
    
    # Connect to OpenAI immediately with initial prompts
    # Note: We'll update active_connections with actual_call_sid once we get it from WebSocket
    try:
        # Pass initial prompts to session initialization so they're in the instructions
        openai_ws = await connect_to_openai_realtime(initial_prompts=initial_prompts)
        # Store connection for admin prompts (will update with actual_call_sid later)
        active_connections[call_sid] = openai_ws
        
        # Send initial greeting that incorporates the prompts
        await send_initial_greeting(openai_ws, initial_prompts)
        print(f"✅ Connected to OpenAI and sent initial greeting")
    except Exception as e:
        print(f"❌ Failed to connect to OpenAI: {e}")
        import traceback
        traceback.print_exc()
        # Try to update call status to failed
        try:
            call_id = await fetch_call_id(call_sid)
            if call_id:
                await update_call_status(call_id, "FAILED")
        except Exception:
            pass
        return
    
    stream_sid = None
    latest_ts = 0
    response_start_ts = None
    last_item = None
    call_id = None  # Store callId for transcript updates
    
    # Path param might be callId (for incoming calls) or callSid (for outgoing)
    # Check if it's already a callId by looking in mapping or checking if it looks like a Prisma ID
    # Prisma IDs typically start with 'cm' and are longer alphanumeric strings
    if call_sid.startswith('cm') and len(call_sid) > 20:
        # Likely a Prisma callId - use it directly
        call_id = call_sid
        print(f"✅ Using path param as callId: {call_id}")
    else:
        # Likely a Twilio callSid - try to fetch callId
        call_id = await fetch_call_id(call_sid)
        if call_id:
            print(f"✅ Found callId={call_id} for callSid={call_sid}")
    
    if call_id:
        # Update call status to IN_PROGRESS when media stream connects
        await update_call_status(call_id, "IN_PROGRESS", answered_at=int(time.time() * 1000))
    
    async def on_speech_started():
        """Handle user interruption - cancel OpenAI's current response"""
        nonlocal response_start_ts, last_item
        if last_item and response_start_ts is not None:
            elapsed = latest_ts - response_start_ts
            cancel = {
                "type": "conversation.item.truncate",
                "item_id": last_item,
                "content_index": 0,
                "audio_end_ms": elapsed,
            }
            await openai_ws.send(json.dumps(cancel))
        
        try:
            await websocket.send_json({"event": "clear", "streamSid": stream_sid})
        except (RuntimeError, WebSocketDisconnect):
            print("Twilio WebSocket closed; cannot send clear event")
        
        last_item = None
        response_start_ts = None
    
    async def recv_twilio():
        """Receive messages from Twilio and forward to OpenAI"""
        nonlocal stream_sid, latest_ts, actual_call_sid, call_id
        try:
            async for msg in websocket.iter_text():
                data = json.loads(msg)
                evt = data.get("event")
                
                # Log all events for debugging (except media which is too verbose)
                if evt != "media":
                    print(f"📨 Twilio event: {evt} (callId={call_id or 'N/A'})")
                
                if evt == "connected":
                    # Extract actual callSid from WebSocket connection data
                    event_call_sid = (
                        data.get("start", {}).get("callSid") or
                        data.get("callSid") or
                        data.get("CallSid")
                    )
                    actual_call_sid = event_call_sid
                    if actual_call_sid:
                        print(f"📞 Twilio Media Stream connected: callSid={actual_call_sid}, callId={call_id or 'N/A'}")
                    else:
                        print(f"📞 Twilio Media Stream connected: path_param={call_sid}, callId={call_id or 'N/A'}")
                    
                    # Now try to get initial prompts using actual callSid
                    if actual_call_sid and actual_call_sid != call_sid:
                        if actual_call_sid in incoming_call_mapping:
                            prompts = incoming_call_mapping[actual_call_sid].get("initial_prompts", [])
                            if prompts:
                                initial_prompts = prompts
                                print(f"📝 Found {len(initial_prompts)} initial prompt(s) using callSid={actual_call_sid}")
                    
                    # Update call_id if we have actual_call_sid and don't have it yet
                    # (This handles the case where path param was a callSid, not a callId)
                    if actual_call_sid and not call_id:
                        call_id = await fetch_call_id(actual_call_sid)
                        if call_id:
                            print(f"✅ Found callId={call_id} for callSid={actual_call_sid}")
                            # Update call status to IN_PROGRESS now that we have callId
                            await update_call_status(call_id, "IN_PROGRESS", answered_at=int(time.time() * 1000))
                    elif call_id and not actual_call_sid:
                        # We have callId but no callSid yet - this is fine, we'll use callId for transcripts
                        print(f"✅ Using callId={call_id} (no callSid in connected event)")
                    
                    # Update active_connections to use actual_call_sid if different
                    if actual_call_sid and actual_call_sid != call_sid:
                        if call_sid in active_connections:
                            active_connections[actual_call_sid] = active_connections.pop(call_sid)
                
                elif evt == "media" and openai_ws.open:
                    latest_ts = int(data["media"]["timestamp"])
                    await openai_ws.send(json.dumps({
                        "type": "input_audio_buffer.append",
                        "audio": data["media"]["payload"],
                    }))
                
                elif evt == "start":
                    stream_sid = data["start"]["streamSid"]
                    print(f"📞 Media Stream started: streamSid={stream_sid}")
                
                elif evt == "stop":
                    print(f"🛑 Media Stream stopped - call ending")
                    print(f"   CallSid: {call_sid}, StreamSid: {stream_sid}")
                    # Close OpenAI WebSocket to signal send_twilio to exit
                    if openai_ws and openai_ws.open:
                        try:
                            await openai_ws.close()
                            print(f"   ✅ OpenAI WebSocket closed")
                        except Exception as e:
                            print(f"   ❌ Error closing OpenAI socket on stop: {e}")
                    # Break the loop to allow finally block to execute
                    break
                else:
                    if evt not in ["connected", "media", "start", "stop"]:
                        print(f"📨 Unknown Twilio event: {evt}")
                        if evt:
                            print(f"   Event data keys: {list(data.keys()) if isinstance(data, dict) else 'N/A'}")
        except WebSocketDisconnect as e:
            print(f"🔌 Twilio WebSocket disconnected in recv_twilio - call ending")
            print(f"   CallSid: {call_sid}, Error: {e}")
            # Close OpenAI WebSocket to signal send_twilio to exit
            if openai_ws and openai_ws.open:
                try:
                    await openai_ws.close()
                except Exception as e:
                    print(f"Error closing OpenAI socket in recv_twilio: {e}")
        except Exception as e:
            print(f"❌ Error in recv_twilio: {e}")
            import traceback
            traceback.print_exc()
            if openai_ws and openai_ws.open:
                await openai_ws.close()
    
    async def send_twilio():
        """Receive messages from OpenAI and forward to Twilio"""
        nonlocal response_start_ts, last_item, stream_sid
        try:
            async for raw in openai_ws:
                msg = json.loads(raw)
                typ = msg.get("type")
                
                # Handle audio delta events - OpenAI sends "response.audio.delta"
                if typ == "response.audio.delta":
                    if not msg.get("delta"):
                        print(f"⚠️ OpenAI sent audio delta with no delta data")
                    elif not stream_sid:
                        print(f"⚠️ OpenAI sent audio delta but stream_sid not set yet")
                    else:
                        try:
                            # Decode from base64 (OpenAI sends it base64-encoded)
                            decoded_audio = base64.b64decode(msg["delta"])
                            # Re-encode to base64 (Twilio expects base64-encoded audio)
                            audio_payload = base64.b64encode(decoded_audio).decode('utf-8')
                            audio_delta = {
                                "event": "media",
                                "streamSid": stream_sid,
                                "media": {
                                    "payload": audio_payload
                                }
                            }
                            await websocket.send_json(audio_delta)
                        except (RuntimeError, WebSocketDisconnect):
                            print("Twilio WebSocket closed; stopping send_twilio loop")
                            break
                        except Exception as e:
                            print(f"❌ Error sending audio to Twilio: {e}")
                            import traceback
                            traceback.print_exc()
                    
                    if response_start_ts is None:
                        response_start_ts = latest_ts
                    if item_id := msg.get("item_id"):
                        last_item = item_id
                
                elif typ == "input_audio_buffer.speech_started":
                    await on_speech_started()
                
                elif typ == "conversation.item.input_audio_transcription.completed":
                    # Caller speaking (user input) - complete transcription
                    transcript = msg.get("transcript", "")
                    if transcript and transcript.strip():
                        print(f"📝 Caller transcript: {transcript}")
                        if call_id:
                            await send_transcript(call_id, transcript.strip(), "caller")
                        else:
                            print(f"⚠️ Cannot send transcript: call_id is None")
                
                elif typ == "response.audio_transcript.done":
                    # AI speaking (assistant output) - complete transcription
                    # This is the final, complete transcript - send only this
                    transcript = msg.get("transcript", "")
                    if transcript and transcript.strip():
                        print(f"📝 AI transcript: {transcript}")
                        if call_id:
                            await send_transcript(call_id, transcript.strip(), "ai")
                        else:
                            print(f"⚠️ Cannot send transcript: call_id is None")
                
                elif typ == "response.text.done":
                    # This is the AI's text response (alternative to audio transcription)
                    # Use this as a fallback if audio transcription isn't available
                    text = msg.get("text")
                    if text:
                        print(f"📝 AI text response (callSid={call_sid}): {text}")
                        # Send to Next.js for real-time display
                        await send_transcript(call_id, text, "ai")
        except WebSocketDisconnect as e:
            print(f"🔌 OpenAI WebSocket disconnected - call ending")
        except Exception as e:
            print(f"❌ Error in send_twilio: {e}")
            import traceback
            traceback.print_exc()
    
    try:
        # Run both directions concurrently
        await asyncio.gather(recv_twilio(), send_twilio())
    except Exception as e:
        print(f"❌ Error in media stream handling: {e}")
        import traceback
        traceback.print_exc()
    finally:
        # Use actual_call_sid if we have it, otherwise use path param
        cleanup_sid = actual_call_sid if actual_call_sid else call_sid
        print(f"🔌 AI call ended: callId={call_id or 'N/A'}, callSid={cleanup_sid}")
        
        # Clean up mappings using actual_call_sid if available
        if cleanup_sid in agent_call_mapping:
            original_call_sid = agent_call_mapping[cleanup_sid]
            del agent_call_mapping[cleanup_sid]
            # Clean up incoming call mapping
            if original_call_sid in incoming_call_mapping:
                del incoming_call_mapping[original_call_sid]
        
        # Also clean up by path param if different
        if call_sid != cleanup_sid and call_sid in agent_call_mapping:
            del agent_call_mapping[call_sid]
        
        # Remove from active connections (try both)
        if cleanup_sid in active_connections:
            del active_connections[cleanup_sid]
        if call_sid != cleanup_sid and call_sid in active_connections:
            del active_connections[call_sid]
        
        # Update call status to COMPLETED
        if call_id:
            try:
                await update_call_status(call_id, "COMPLETED", ended_at=int(time.time() * 1000))
            except Exception as e:
                print(f"❌ Error updating call status to COMPLETED: {e}")
                import traceback
                traceback.print_exc()
        
        if openai_ws and openai_ws.open:
            try:
                await openai_ws.close()
            except Exception as e:
                print(f"Error closing OpenAI socket: {e}")

