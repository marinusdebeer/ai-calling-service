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
    fetch_call_details,
    update_call_status,
    send_transcript,
    update_call_metadata,
    update_call_record,
)
from state import (
    active_connections,
    incoming_call_mapping,
    agent_call_mapping,
    cleanup_call_mappings,
)
from utils.constants import (
    SPEAKER_ADMIN,
    SPEAKER_CALLER,
    SPEAKER_AI,
    STATUS_IN_PROGRESS,
    STATUS_COMPLETED,
    STATUS_FAILED,
)
from utils.call_utils import is_prisma_call_id
from utils.transcript_utils import check_and_send_initial_prompts


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
                await update_call_status(call_id, STATUS_FAILED)
        except Exception as e:
            print(f"⚠️ Error updating call status to FAILED: {e}")
        return
    
    stream_sid = None
    latest_ts = 0
    response_start_ts = None
    last_item = None
    call_id = None  # Store callId for transcript updates
    initial_prompts_sent = False  # Track if we've already sent initial prompts as transcripts
    
    # Path param might be callId (for incoming calls) or callSid (for outgoing)
    # Use utility function to detect Prisma callId format
    if is_prisma_call_id(call_sid):
        # Likely a Prisma callId - use it directly
        call_id = call_sid
    else:
        # Likely a Twilio callSid - try to fetch callId
        call_id = await fetch_call_id(call_sid)
    
    if call_id:
        # Update call status to IN_PROGRESS when media stream connects
        await update_call_status(call_id, STATUS_IN_PROGRESS, answered_at=int(time.time() * 1000))
        
        # Check and send initial prompts if we have them (utility handles fetching transcripts)
        if initial_prompts and not initial_prompts_sent:
            await check_and_send_initial_prompts(call_id, initial_prompts, None, send_transcript, initial_prompts_sent)
            initial_prompts_sent = True
    
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
                
                if evt == "connected":
                    # Extract actual callSid from WebSocket connection data
                    event_call_sid = (
                        data.get("start", {}).get("callSid") or
                        data.get("callSid") or
                        data.get("CallSid")
                    )
                    actual_call_sid = event_call_sid
                    if actual_call_sid:
                        print(f"📞 Twilio Media Stream connected: callSid={actual_call_sid}")
                    
                    # Try to get initial prompts using actual callSid
                    if actual_call_sid and actual_call_sid != call_sid:
                        if actual_call_sid in incoming_call_mapping:
                            prompts = incoming_call_mapping[actual_call_sid].get("initial_prompts", [])
                            if prompts:
                                initial_prompts = prompts
                                # Send initial prompts as transcript entries if we haven't already
                                if call_id and prompts and not initial_prompts_sent:
                                    await check_and_send_initial_prompts(call_id, prompts, None, send_transcript, initial_prompts_sent)
                                    initial_prompts_sent = True
                    
                    # Update call_id if we have actual_call_sid and don't have it yet
                    if actual_call_sid and not call_id:
                        call_id = await fetch_call_id(actual_call_sid)
                        if call_id:
                            # Send initial prompts as transcript entries if we have them and haven't sent them yet
                            if initial_prompts and not initial_prompts_sent:
                                await check_and_send_initial_prompts(call_id, initial_prompts, None, send_transcript, initial_prompts_sent)
                                initial_prompts_sent = True
                            
                            # Store the agent callSid in database
                            if actual_call_sid:
                                await update_call_record(call_id, None, None, actual_call_sid, None)
                            
                            await update_call_status(call_id, STATUS_IN_PROGRESS, answered_at=int(time.time() * 1000))
                    
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
                    if transcript and transcript.strip() and call_id:
                        print(f"📝 Caller transcript: {transcript}")
                        await send_transcript(call_id, transcript.strip(), SPEAKER_CALLER)
                
                elif typ == "response.audio_transcript.done":
                    # AI speaking (assistant output) - complete transcription
                    transcript = msg.get("transcript", "")
                    if transcript and transcript.strip() and call_id:
                        print(f"📝 AI transcript: {transcript}")
                        await send_transcript(call_id, transcript.strip(), SPEAKER_AI)
                
                elif typ == "response.text.done":
                    # This is the AI's text response (alternative to audio transcription)
                    text = msg.get("text")
                    if text and call_id:
                        print(f"📝 AI text response (callSid={call_sid}): {text}")
                        # Send to Next.js for real-time display
                        await send_transcript(call_id, text, SPEAKER_AI)
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
        
        # Determine original call_sid for cleanup
        # For incoming calls, cleanup_sid might be agent_call_sid, so we need to find original
        original_call_sid = cleanup_sid
        if cleanup_sid in agent_call_mapping:
            original_call_sid = agent_call_mapping[cleanup_sid]
        
        # Clean up mappings using utility function
        cleanup_call_mappings(original_call_sid, cleanup_sid if cleanup_sid != original_call_sid else None)
        
        # Also clean up by path param if different
        if call_sid != cleanup_sid and call_sid != original_call_sid:
            cleanup_call_mappings(call_sid, None)
        
        # Remove from active connections (try both)
        if cleanup_sid in active_connections:
            del active_connections[cleanup_sid]
        if call_sid != cleanup_sid and call_sid in active_connections:
            del active_connections[call_sid]
        
        # Update call status to COMPLETED and compile final transcript
        if call_id:
            try:
                # Get call details to compile transcripts from metadata
                call_details = await fetch_call_details(call_id)
                if call_details:
                    call_data = call_details.get("call", {})
                    metadata = call_data.get("metadata", {}) or {}
                    transcripts = metadata.get("transcripts", [])
                    
                    # Compile all transcripts into a single transcription field
                    if transcripts:
                        # Sort by timestamp
                        sorted_transcripts = sorted(transcripts, key=lambda x: x.get("timestamp", 0))
                        
                        # Get initial prompts from metadata to include at the start
                        initial_prompts = metadata.get("initialPrompts", [])
                        transcription_parts = []
                        
                        # Add initial prompts at the beginning if they exist
                        if initial_prompts:
                            for prompt in initial_prompts:
                                if prompt and prompt.strip():
                                    transcription_parts.append(f"[ADMIN]: {prompt.strip()}")
                        
                        # Add all transcripts
                        transcription_parts.extend([
                            f"[{t.get('speaker', 'unknown').upper()}]: {t.get('text', '')}"
                            for t in sorted_transcripts
                        ])
                        
                        transcription_text = "\n".join(transcription_parts)
                        
                        # Update call with compiled transcription in metadata
                        # The transcription field will be updated when recording is processed
                        await update_call_metadata(call_id, {
                            "finalTranscription": transcription_text,
                            "transcriptCount": len(transcripts)
                        })
                
                await update_call_status(call_id, STATUS_COMPLETED, ended_at=int(time.time() * 1000))
                print(f"✅ Call status updated to {STATUS_COMPLETED}: callId={call_id}")
            except Exception as e:
                print(f"❌ Error updating call status to {STATUS_COMPLETED}: {e}")
                import traceback
                traceback.print_exc()
        
        if openai_ws and openai_ws.open:
            try:
                await openai_ws.close()
            except Exception as e:
                print(f"Error closing OpenAI socket: {e}")

