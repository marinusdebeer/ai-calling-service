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
    print(f"📡 WebSocket connection attempt: callSid={call_sid}")
    await websocket.accept()
    
    print(f"🔌 Twilio Media Stream connected: callSid={call_sid}")
    
    openai_ws = None
    
    # Get initial prompts from mapping if available
    # For outgoing calls, call_sid is the callId from the TwiML URL
    initial_prompts = []
    if call_sid in incoming_call_mapping:
        initial_prompts = incoming_call_mapping[call_sid].get("initial_prompts", [])
        print(f"📝 Found {len(initial_prompts)} initial prompt(s) for call_sid={call_sid}")
    else:
        print(f"⚠️ No mapping found for call_sid={call_sid}, checking all mappings...")
        # Debug: print all keys in mapping
        print(f"Available keys in mapping: {list(incoming_call_mapping.keys())[:10]}")  # First 10 keys
    
    # Connect to OpenAI immediately with initial prompts
    print(f"🤖 Connecting to OpenAI for callSid={call_sid}...")
    try:
        # Pass initial prompts to session initialization so they're in the instructions
        openai_ws = await connect_to_openai_realtime(initial_prompts=initial_prompts)
        # Store connection for admin prompts
        active_connections[call_sid] = openai_ws
        
        # Send initial greeting that incorporates the prompts
        await send_initial_greeting(openai_ws, initial_prompts)
    except Exception as e:
        print(f"❌ Failed to connect to OpenAI: {e}")
        import traceback
        traceback.print_exc()
        return
    
    stream_sid = None
    latest_ts = 0
    response_start_ts = None
    last_item = None
    call_id = None  # Store callId for transcript updates
    
    # Fetch callId at the start
    call_id = await fetch_call_id(call_sid)
    if call_id:
        # Update call status to IN_PROGRESS when media stream connects
        await update_call_status(call_id, "IN_PROGRESS", answered_at=int(time.time() * 1000))
    else:
        print(f"⚠️ Could not find callId for callSid={call_sid}")
    
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
        nonlocal stream_sid, latest_ts
        try:
            async for msg in websocket.iter_text():
                data = json.loads(msg)
                evt = data.get("event")
                
                if evt == "connected":
                    # Log connected event
                    event_call_sid = (
                        data.get("start", {}).get("callSid") or
                        data.get("callSid") or
                        data.get("CallSid")
                    )
                    print(f"📞 Twilio Media Stream connected event: callSid={event_call_sid}")
                
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
                    print(f"📞 Media Stream stopped - call ending")
                    # Close OpenAI WebSocket to signal send_twilio to exit
                    if openai_ws and openai_ws.open:
                        try:
                            await openai_ws.close()
                        except Exception as e:
                            print(f"Error closing OpenAI socket on stop: {e}")
                    # Break the loop to allow finally block to execute
                    break
                else:
                    if evt not in ["connected", "media", "start", "stop"]:
                        print(f"📨 Unknown Twilio event: {evt}")
        except WebSocketDisconnect:
            print("Twilio WebSocket disconnected in recv_twilio - call ending")
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
        finally:
            print("📞 recv_twilio() completed")
    
    async def send_twilio():
        """Receive messages from OpenAI and forward to Twilio"""
        nonlocal response_start_ts, last_item, stream_sid
        try:
            async for raw in openai_ws:
                msg = json.loads(raw)
                typ = msg.get("type")
                
                # Handle audio delta events - OpenAI sends "response.audio.delta"
                if typ == "response.audio.delta" and msg.get("delta") and stream_sid:
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
                        print(f"Error sending audio to Twilio: {e}")
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
                        print(f"📝 Caller transcript (callSid={call_sid}): {transcript}")
                        await send_transcript(call_id, transcript.strip(), "caller")
                
                elif typ == "response.audio_transcript.done":
                    # AI speaking (assistant output) - complete transcription
                    # This is the final, complete transcript - send only this
                    transcript = msg.get("transcript", "")
                    if transcript and transcript.strip():
                        print(f"📝 AI transcript (callSid={call_sid}): {transcript}")
                        await send_transcript(call_id, transcript.strip(), "ai")
                
                elif typ == "response.text.done":
                    # This is the AI's text response (alternative to audio transcription)
                    # Use this as a fallback if audio transcription isn't available
                    text = msg.get("text")
                    if text:
                        print(f"📝 AI text response (callSid={call_sid}): {text}")
                        # Send to Next.js for real-time display
                        await send_transcript(call_id, text, "ai")
        except WebSocketDisconnect:
            print("OpenAI WebSocket disconnected during send_twilio - call ending")
        except Exception as e:
            print(f"❌ Error in send_twilio: {e}")
            import traceback
            traceback.print_exc()
        finally:
            print("📞 send_twilio() completed")
    
    try:
        # Run both directions concurrently
        await asyncio.gather(recv_twilio(), send_twilio())
    except Exception as e:
        print(f"❌ Error in media stream handling: {e}")
        import traceback
        traceback.print_exc()
    finally:
        print(f"🔌 AI call ended, cleaning up for callSid={call_sid}, callId={call_id}")
        # Clean up mappings
        if call_sid in agent_call_mapping:
            original_call_sid = agent_call_mapping[call_sid]
            del agent_call_mapping[call_sid]
            # Clean up incoming call mapping
            if original_call_sid in incoming_call_mapping:
                del incoming_call_mapping[original_call_sid]
        
        # Remove from active connections
        if call_sid in active_connections:
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

