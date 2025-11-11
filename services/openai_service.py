"""
OpenAI Realtime API service
"""
import json
import websockets
from config import (
    OPENAI_REALTIME_URL,
    OPENAI_API_KEY,
    OPENAI_INPUT_FORMAT,
    OPENAI_OUTPUT_FORMAT,
    VOICE,
    AI_ASSISTANT_INSTRUCTIONS,
)


async def initialize_session(openai_ws, initial_prompts=None):
    """Initialize OpenAI Realtime session with proper configuration"""
    instructions = AI_ASSISTANT_INSTRUCTIONS
    
    # Incorporate initial prompts into session instructions if provided
    if initial_prompts:
        valid_prompts = [p.strip() for p in initial_prompts if p and p.strip()]
        if valid_prompts:
            combined_instruction = "\n".join([f"- {prompt}" for prompt in valid_prompts])
            instructions = (
                f"{instructions}\n\nIMPORTANT CALL OBJECTIVES:\n{combined_instruction}\n\n"
                "During your first response, incorporate these objectives into the greeting. "
                "After the opening, continue the conversation naturally without repeating the same greeting or introduction."
            )
    
    payload = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": OPENAI_INPUT_FORMAT,
            "output_audio_format": OPENAI_OUTPUT_FORMAT,
            "voice": VOICE,
            "instructions": instructions,
            "modalities": ["text", "audio"],
            "temperature": 0.8,
            # Enable input audio transcription for caller speech
            "input_audio_transcription": {
                "model": "whisper-1",
                "language": "en"  # Force English transcription
            },
        },
    }
    
    await openai_ws.send(json.dumps(payload))
    # Don't trigger response here - we'll do it after sending the initial greeting
    print(f"✅ OpenAI session initialized with input transcription enabled")


async def connect_to_openai_realtime(initial_prompts=None):
    """Connect to OpenAI Realtime API and initialize session"""
    if not OPENAI_API_KEY:
        raise ValueError("OPENAI_API_KEY not configured")
    
    try:
        # Connect to OpenAI Realtime WebSocket using extra_headers
        openai_ws = await websockets.connect(
            OPENAI_REALTIME_URL,
            extra_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1",
            },
        )
        
        print(f"✅ Connected to OpenAI Realtime API WebSocket")
        
        # Initialize the session with proper configuration and initial prompts
        await initialize_session(openai_ws, initial_prompts=initial_prompts)
        
        return openai_ws
    except Exception as e:
        print(f"❌ Failed to connect to OpenAI: {e}")
        import traceback
        traceback.print_exc()
        raise


async def send_initial_greeting(openai_ws, initial_prompts=None):
    """Send initial greeting to OpenAI to make AI speak first"""
    if initial_prompts:
        valid_prompts = [p.strip() for p in initial_prompts if p and p.strip()]
        if valid_prompts:
            print(f"📝 Sending initial greeting with {len(valid_prompts)} prompt(s) incorporated")
            # Create a conversation item that prompts the AI to greet incorporating the objectives
            combined_instruction = "\n".join([f"- {prompt}" for prompt in valid_prompts])
            greeting_prompt = (
                "For your first response only, greet the caller warmly and immediately address these objectives: "
                f"{combined_instruction}. After this opening, continue the conversation naturally without repeating the same greeting."
            )
            greeting_message = {
                "type": "conversation.item.create",
                "item": {
                    "type": "message",
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": greeting_prompt
                        }
                    ]
                }
            }
            await openai_ws.send(json.dumps(greeting_message))
            # Trigger the AI to respond (this will make it speak first)
            await openai_ws.send(json.dumps({"type": "response.create"}))
            print(f"✅ Initial greeting sent with objectives: {combined_instruction}")
    else:
        # No initial prompts - send a generic greeting to make AI speak first
        greeting_message = {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": "For your first response only, greet the caller warmly and introduce yourself. After that, continue the conversation without repeating the greeting."
                    }
                ]
            }
        }
        await openai_ws.send(json.dumps(greeting_message))
        await openai_ws.send(json.dumps({"type": "response.create"}))
        print(f"✅ Initial greeting sent (no specific objectives)")

