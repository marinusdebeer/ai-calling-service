"""
Admin endpoints for call management
"""
import json
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from state import active_connections, agent_call_mapping, incoming_call_mapping

router = APIRouter()


@router.post("/admin-prompt")
async def admin_prompt(request: Request):
    """
    Receive admin prompt and inject it into the active OpenAI conversation
    """
    try:
        data = await request.json()
        call_sid = data.get("callSid")  # This is the Twilio callSid from Next.js
        prompt = data.get("prompt")
        
        if not call_sid or not prompt:
            return JSONResponse(
                {"error": "callSid and prompt are required"},
                status_code=400
            )
        
        # Find the connection key (callId or agent callSid)
        connection_key = None
        
        # Check if this is already a connection key (for outgoing calls, callId is used)
        if call_sid in active_connections:
            connection_key = call_sid
        else:
            # For incoming calls: look up callId from original callSid
            # The incoming_call_mapping stores: original_callSid -> {call_id: ...}
            if call_sid in incoming_call_mapping:
                call_id = incoming_call_mapping[call_sid].get("call_id")
                if call_id and call_id in active_connections:
                    connection_key = call_id
                    print(f"✅ Found connection via incoming_call_mapping: callSid={call_sid} -> callId={call_id}")
            
            # Also try looking up agent callSid from original callSid
            if not connection_key:
                for agent_sid, original_sid in agent_call_mapping.items():
                    if original_sid == call_sid:
                        if agent_sid in active_connections:
                            connection_key = agent_sid
                            print(f"✅ Found connection via agent_call_mapping: callSid={call_sid} -> agentSid={agent_sid}")
                            break
                        # Also try to get callId from the agent callSid
                        if agent_sid in incoming_call_mapping:
                            call_id = incoming_call_mapping[agent_sid].get("call_id")
                            if call_id and call_id in active_connections:
                                connection_key = call_id
                                print(f"✅ Found connection via agent callId: callSid={call_sid} -> callId={call_id}")
                                break
        
        if not connection_key:
            print(f"⚠️ No connection found for callSid={call_sid}")
            print(f"   Available active_connections keys: {list(active_connections.keys())[:10]}")
            print(f"   Available incoming_call_mapping keys: {list(incoming_call_mapping.keys())[:10]}")
            print(f"   Available agent_call_mapping keys: {list(agent_call_mapping.keys())[:10]}")
            return JSONResponse(
                {"error": "No active connection found for this call"},
                status_code=404
            )
        
        # Get the OpenAI WebSocket connection for this call
        openai_ws = active_connections.get(connection_key)
        
        if not openai_ws or not openai_ws.open:
            print(f"⚠️ No active OpenAI connection for connection_key={connection_key}")
            return JSONResponse(
                {"error": "No active connection found for this call"},
                status_code=404
            )
        
        # Send the prompt as a text input item to OpenAI
        # This will be processed as if the admin said it
        prompt_message = {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"[Admin instruction: {prompt}]"
                    }
                ]
            }
        }
        
        await openai_ws.send(json.dumps(prompt_message))
        
        # Trigger a response
        await openai_ws.send(json.dumps({"type": "response.create"}))
        
        return JSONResponse({
            "success": True,
            "message": "Prompt sent to AI"
        })
        
    except Exception as e:
        print(f"❌ Error handling admin prompt: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            {"error": str(e)},
            status_code=500
        )


@router.post("/end-call")
async def end_call(request: Request):
    """
    End an active AI call (called by Next.js when admin ends the call)
    """
    try:
        data = await request.json()
        call_sid = data.get("callSid")
        
        if not call_sid:
            return JSONResponse(
                {"error": "callSid is required"},
                status_code=400
            )
        
        # Get the OpenAI WebSocket connection for this call
        openai_ws = active_connections.get(call_sid)
        
        if openai_ws and openai_ws.open:
            try:
                # Close the OpenAI WebSocket connection
                # This will trigger the finally block in handle_media_stream
                await openai_ws.close()
            except Exception as e:
                print(f"⚠️ Error closing OpenAI connection for callSid={call_sid}: {e}")
        
        # Remove from active connections
        if call_sid in active_connections:
            del active_connections[call_sid]
        
        return JSONResponse({
            "success": True,
            "message": "Call ended"
        })
        
    except Exception as e:
        print(f"❌ Error handling end call request: {e}")
        import traceback
        traceback.print_exc()
        return JSONResponse(
            {"error": str(e)},
            status_code=500
        )

