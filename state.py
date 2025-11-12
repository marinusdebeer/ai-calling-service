"""
Global state management for call mappings and active connections
"""
import websockets

# Store active OpenAI WebSocket connections by call_sid for admin prompts
active_connections: dict[str, websockets.WebSocketServerProtocol] = {}

# Store mapping between original callSid and callId for incoming calls
# Key: original_call_sid (from incoming-call), Value: {"call_id": str, "from": str, "timestamp": float}
incoming_call_mapping: dict[str, dict[str, str | float]] = {}

# Store mapping between agent callSid and original callSid
# Key: agent_call_sid (from agent-call), Value: original_call_sid
agent_call_mapping: dict[str, str] = {}

def cleanup_call_mappings(call_sid: str, agent_call_sid: str | None = None):
    """
    Clean up all mappings related to a specific call.
    
    Args:
        call_sid: Original call SID
        agent_call_sid: Optional agent call SID (for incoming calls)
    """
    # Remove from incoming_call_mapping
    incoming_call_mapping.pop(call_sid, None)
    
    # Remove from agent_call_mapping
    if agent_call_sid:
        agent_call_mapping.pop(agent_call_sid, None)

