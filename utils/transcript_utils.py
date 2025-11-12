"""
Transcript-related utility functions
"""
from utils.constants import SPEAKER_ADMIN


async def get_existing_transcripts(call_id: str) -> list[dict]:
    """
    Fetch existing transcripts from call metadata.
    
    Args:
        call_id: Call ID to fetch transcripts for
    
    Returns:
        List of existing transcript entries
    """
    # Import here to avoid circular dependency
    from services.nextjs_client import fetch_call_details
    
    call_details = await fetch_call_details(call_id)
    if not call_details:
        return []
    
    call_data = call_details.get("call", {})
    metadata = call_data.get("metadata", {}) or {}
    return metadata.get("transcripts", [])


async def check_and_send_initial_prompts(
    call_id: str,
    initial_prompts: list[str],
    existing_transcripts: list[dict] | None = None,
    send_transcript_func = None,
    already_sent: bool = False
) -> bool:
    """
    Check if initial prompts already exist in transcripts and send missing ones.
    
    This prevents duplicate prompts from appearing in the UI.
    
    Args:
        call_id: Call ID to send transcripts to
        initial_prompts: List of initial prompt strings
        existing_transcripts: Optional list of existing transcript entries (fetched if None)
        send_transcript_func: Async function to send transcript (call_id, text, speaker)
        already_sent: Flag indicating if prompts have already been sent
    
    Returns:
        True if prompts were sent (or already existed), False otherwise
    """
    if already_sent or not initial_prompts or not call_id:
        return False
    
    # Fetch transcripts if not provided
    if existing_transcripts is None:
        existing_transcripts = await get_existing_transcripts(call_id)
    
    # Import here to avoid circular dependency
    if send_transcript_func is None:
        from services.nextjs_client import send_transcript
        send_transcript_func = send_transcript
    
    sent_any = False
    for prompt in initial_prompts:
        if not prompt or not prompt.strip():
            continue
        
        # Check if this prompt already exists in transcripts
        prompt_exists = any(
            t.get("text", "").strip() == prompt.strip() and 
            t.get("speaker") == SPEAKER_ADMIN
            for t in existing_transcripts
        )
        
        if not prompt_exists:
            await send_transcript_func(call_id, prompt.strip(), SPEAKER_ADMIN)
            sent_any = True
    
    return sent_any
