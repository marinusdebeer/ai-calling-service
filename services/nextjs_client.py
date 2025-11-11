"""
Next.js API client for call status updates and transcriptions
"""
import time
from datetime import datetime
import httpx
from config import APP_URL
from state import incoming_call_mapping, agent_call_mapping


async def fetch_call_id(call_sid: str) -> str | None:
    """Fetch callId from Next.js API using callSid"""
    # For outgoing calls with inline TwiML, call_sid might be callId (from WebSocket URL)
    if call_sid in incoming_call_mapping:
        mapping = incoming_call_mapping[call_sid]
        call_id = mapping.get("call_id")
        if call_id:
            return call_id
        # If call_sid is the callId itself (outgoing call with inline TwiML)
        if mapping.get("is_outgoing") and mapping.get("twilio_call_sid"):
            return call_sid
    
    # Check if this is an agent callSid - look up the original callSid
    if call_sid in agent_call_mapping:
        original_call_sid = agent_call_mapping[call_sid]
        if original_call_sid in incoming_call_mapping:
            return incoming_call_mapping[original_call_sid].get("call_id")
    
    # Fallback: try to fetch from Next.js API
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{APP_URL}/api/calls",
                params={"callSid": call_sid},
                timeout=5.0
            )
            if response.status_code == 200:
                data = response.json()
                calls = data.get("calls", [])
                if calls and len(calls) > 0:
                    return calls[0].get("id")
    except Exception as e:
        print(f"⚠️ Error fetching callId: {e}")
    return None


async def fetch_call_details(call_id: str) -> dict | None:
    """Fetch call details from Next.js API to get timestamps"""
    if not call_id:
        return None
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{APP_URL}/api/calls/{call_id}",
                timeout=5.0
            )
            if response.status_code == 200:
                return response.json()
    except Exception as e:
        print(f"⚠️ Error fetching call details: {e}")
    return None


async def update_call_status(
    call_id: str,
    status: str,
    answered_at: int | None = None,
    ended_at: int | None = None
):
    """Update call status in Next.js"""
    if not call_id:
        return
    
    try:
        async with httpx.AsyncClient() as client:
            payload = {"status": status}
            if answered_at:
                payload["answeredAt"] = answered_at
            if ended_at:
                payload["endedAt"] = ended_at
            
            # If setting status to IN_PROGRESS, check if this is an incoming call
            # that needs metadata updated (for active AI calls list)
            if status == "IN_PROGRESS":
                call_details = await fetch_call_details(call_id)
                if call_details:
                    call_data = call_details.get("call", {})
                    direction = call_data.get("direction")
                    metadata = call_data.get("metadata", {}) or {}
                    
                    # If it's an INBOUND call and doesn't have routedToAI set, update metadata
                    if direction == "INBOUND" and not metadata.get("routedToAI"):
                        await update_call_metadata(call_id, {"routedToAI": True, "aiMode": True})
            
            # If ending the call, calculate duration
            if status == "COMPLETED" and ended_at:
                call_details = await fetch_call_details(call_id)
                if call_details:
                    call_data = call_details.get("call", {})
                    # Use answeredAt if available, otherwise startedAt, otherwise createdAt
                    start_time = call_data.get("answeredAt") or call_data.get("startedAt") or call_data.get("createdAt")
                    if start_time:
                        try:
                            # Parse the timestamp (could be ISO string or timestamp in milliseconds)
                            if isinstance(start_time, str):
                                # Handle ISO format with or without timezone
                                if start_time.endswith('Z'):
                                    start_time = start_time.replace('Z', '+00:00')
                                start_dt = datetime.fromisoformat(start_time)
                                start_ts = int(start_dt.timestamp() * 1000)
                            elif isinstance(start_time, (int, float)):
                                # Already a timestamp - assume milliseconds if > 1e10, otherwise seconds
                                start_ts = int(start_time * 1000) if start_time < 1e10 else int(start_time)
                            else:
                                start_ts = None
                            
                            if start_ts:
                                duration_seconds = max(0, (ended_at - start_ts) // 1000)
                                payload["duration"] = duration_seconds
                        except Exception as e:
                            print(f"⚠️ Error calculating duration: {e}")
                            # Continue without duration - server will calculate it
            
            response = await client.patch(
                f"{APP_URL}/api/calls/{call_id}/status",
                json=payload,
                timeout=5.0
            )
            if response.status_code != 200:
                print(f"⚠️ Failed to update call status: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ Error updating call status: {e}")
        import traceback
        traceback.print_exc()


async def send_transcript(call_id: str, text: str, speaker: str):
    """Send transcript to Next.js API for real-time display"""
    if not call_id:
        print(f"⚠️ Cannot send transcript: call_id is None")
        return
    
    if not text or not text.strip():
        print(f"⚠️ Cannot send transcript: text is empty")
        return
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{APP_URL}/api/calls/{call_id}/transcript",
                json={
                    "text": text.strip(),
                    "speaker": speaker,
                    "timestamp": int(time.time() * 1000),  # milliseconds
                },
                timeout=5.0
            )
            if response.status_code != 200:
                print(f"⚠️ Next.js API returned {response.status_code}: {response.text}")
    except Exception as e:
        print(f"❌ Error sending transcript to Next.js: {e}")
        import traceback
        traceback.print_exc()


async def update_call_record(call_id: str, twilio_call_sid: str, status: str = "RINGING"):
    """Update call record in Next.js database"""
    try:
        async with httpx.AsyncClient() as client:
            await client.put(
                f"{APP_URL}/api/calls/{call_id}",
                json={
                    "twilioCallSid": twilio_call_sid,
                    "status": status,
                },
                timeout=5.0
            )
    except Exception as e:
        print(f"⚠️ Error updating call record in Next.js: {e}")


async def update_call_metadata(call_id: str, metadata: dict):
    """Update call metadata in Next.js database"""
    if not call_id:
        return
    
    try:
        async with httpx.AsyncClient() as client:
            response = await client.patch(
                f"{APP_URL}/api/calls/{call_id}/metadata",
                json={"metadata": metadata},
                timeout=5.0
            )
            if response.status_code != 200:
                print(f"⚠️ Failed to update call metadata: {response.status_code} - {response.text}")
    except Exception as e:
        print(f"❌ Error updating call metadata: {e}")
        import traceback
        traceback.print_exc()


async def forward_webhook(call_id: str | None, form_data: dict):
    """Forward Twilio webhook to Next.js API"""
    try:
        webhook_url = f"{APP_URL}/api/calls/webhook"
        if call_id:
            webhook_url = f"{webhook_url}?callId={call_id}"
        
        async with httpx.AsyncClient() as client:
            await client.post(
                webhook_url,
                data=form_data,
                timeout=5.0
            )
    except Exception as e:
        print(f"⚠️ Error forwarding webhook: {e}")


async def forward_recording_webhook(call_id: str | None, form_data: dict):
    """Forward Twilio recording webhook to Next.js API"""
    try:
        webhook_url = f"{APP_URL}/api/calls/recording-webhook"
        if call_id:
            webhook_url = f"{webhook_url}?callId={call_id}"
        
        call_sid = form_data.get('CallSid', 'N/A')
        print(f"📼 Forwarding recording webhook to Next.js: callId={call_id}, CallSid={call_sid}")
        
        async with httpx.AsyncClient() as client:
            await client.post(
                webhook_url,
                data=form_data,
                timeout=5.0
            )
    except Exception as e:
        print(f"⚠️ Error forwarding recording webhook: {e}")
        import traceback
        traceback.print_exc()

