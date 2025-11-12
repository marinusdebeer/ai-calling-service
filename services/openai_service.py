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
    # If initial prompts are provided, this is an OUTGOING call initiated by admin
    # For outgoing calls, initial prompts define the call's purpose and override greeting instructions
    # For incoming calls (no initial prompts), use standard greeting format from base instructions
    if initial_prompts:
        valid_prompts = [p.strip() for p in initial_prompts if p and p.strip()]
        if valid_prompts:
            combined_instruction = "\n".join([f"- {prompt}" for prompt in valid_prompts])
            # For OUTGOING calls: Put initial prompts FIRST and override greeting instructions
            # These are admin-provided objectives that define what this specific call is about
            instructions = (
                f"**PRIMARY CALL OBJECTIVES (HIGHEST PRIORITY):**\n{combined_instruction}\n\n"
                "These objectives define the purpose of this call. Use your judgment to determine the best conversation flow:\n\n"
                "**For most calls:** Start with a brief introduction, ask if they have time, then discuss the objectives.\n"
                "**For urgent/time-sensitive matters:** You may address the objectives immediately after a quick greeting.\n"
                "**For casual/friendly topics:** A warm greeting and check-in before discussing objectives is appropriate.\n\n"
                "Adapt your opening naturally based on the urgency, importance, and nature of the objectives. "
                "The key is to be respectful of their time while ensuring the objectives are addressed. "
                "Use natural human conversation flow - sometimes you greet first, sometimes you get straight to the point.\n\n"
                "**CRITICAL - NO REPETITION:** Once you've introduced yourself and stated the purpose, NEVER repeat your introduction or the same information. "
                "After the caller responds (especially with 'Yes' or confirmation), move the conversation forward IMMEDIATELY. "
                "Do NOT reintroduce yourself. Do NOT restate what you already said. Do NOT ask the same question again. "
                "Instead, acknowledge their response briefly and proceed with the next logical step. "
                "If they've confirmed, provide additional details, ask specific follow-up questions, or move to completing the objective. "
                "Example: If you said 'Do you have a moment?' and they said 'Yes', respond with 'Great, [then provide the information or ask the next question]' - NOT another introduction or the same question.\n\n"
                "If these objectives conflict with any general instructions below, these objectives take precedence.\n\n"
                f"{AI_ASSISTANT_INSTRUCTIONS}\n\n"
                "**IMPORTANT:** The PRIMARY CALL OBJECTIVES above take priority over ALL general instructions, "
                "including the greeting format. Use your judgment to determine the most appropriate conversation flow. "
                "NEVER repeat your introduction or the same information you've already shared. Always move the conversation forward after getting a response."
            )
            print(f"\n\n📋 PRIMARY CALL OBJECTIVES (taking priority):\n{combined_instruction}\n\n")
        else:
            instructions = AI_ASSISTANT_INSTRUCTIONS
    else:
        # INCOMING call - use standard greeting format from base instructions
        instructions = AI_ASSISTANT_INSTRUCTIONS
    # Define available functions/tools for the AI
    tools = [
        {
            "type": "function",
            "name": "send_website_link",
            "description": "Send a text message with a link to the company website homepage. Use this when the caller asks for general information, wants to visit the website, or learn more about the company. The link will be sent via SMS to the caller's phone number (which is automatically retrieved from the call). You don't need to provide any parameters.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "type": "function",
            "name": "send_request_form",
            "description": "Send a text message with a link to the request form. Use this when a new lead or existing client needs to fill out the request form to provide their service details, book a service, request a quote, or schedule cleaning. The form link will be sent via SMS to the caller's phone number (which is automatically retrieved from the call). You don't need to provide any parameters.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "type": "function",
            "name": "send_gift_card_form",
            "description": "Send a text message with a link to the gift card purchase form. Use this when the caller asks about gift cards, purchasing gift cards, buying gift cards, or sending gift cards. The gift card form link will be sent via SMS to the caller's phone number (which is automatically retrieved from the call). You don't need to provide any parameters.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        },
        {
            "type": "function",
            "name": "end_call",
            "description": "End the current phone call. IMPORTANT: Before calling this function, you MUST say a polite goodbye to the caller (e.g., 'Thank you for calling, have a great day!' or 'Thanks for your time, goodbye!'). Only call this function AFTER you have finished speaking your goodbye message. Use this when the conversation is complete, the objectives have been achieved, or when the caller wants to end the call. This will gracefully terminate the call. You don't need to provide any parameters.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    ]
    
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
            "tools": tools,
            "tool_choice": "auto",  # Let AI decide when to use tools
            # Enable input audio transcription for caller speech
            "input_audio_transcription": {
                "model": "whisper-1",
                "language": "en"  # Force English transcription
            },
        },
    }
    
    await openai_ws.send(json.dumps(payload))
    # Trigger AI to respond - it will automatically greet based on instructions
    await openai_ws.send(json.dumps({"type": "response.create"}))


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
        
        # Initialize the session with proper configuration and initial prompts
        await initialize_session(openai_ws, initial_prompts=initial_prompts)
        
        return openai_ws
    except Exception as e:
        print(f"❌ Failed to connect to OpenAI: {e}")
        import traceback
        traceback.print_exc()
        raise


# Removed send_initial_greeting - AI now greets automatically based on instructions

