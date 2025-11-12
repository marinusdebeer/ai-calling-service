"""
URL parsing and construction utilities
"""
from config import AI_CALLING_SERVICE_URL


def extract_domain_from_url(url: str) -> str:
    """
    Extract domain from URL, removing protocol and trailing slashes.
    
    Args:
        url: Full URL (e.g., "https://example.com/path" or "http://localhost:8000")
    
    Returns:
        Clean domain string (e.g., "example.com" or "localhost:8000")
    """
    if not url:
        return ""
    
    # Remove protocol
    if url.startswith('https://'):
        domain = url[8:].rstrip('/').strip()
    elif url.startswith('http://'):
        domain = url[7:].rstrip('/').strip()
    else:
        domain = url.rstrip('/').strip()
    
    return domain


def get_websocket_protocol(domain: str) -> str:
    """
    Determine WebSocket protocol (ws or wss) based on domain.
    
    Args:
        domain: Domain string (e.g., "example.com" or "localhost:8000")
    
    Returns:
        "wss" for production domains, "ws" for localhost/127.0.0.1
    """
    if "localhost" in domain or "127.0.0.1" in domain:
        return "ws"
    return "wss"


def build_media_stream_url(call_id: str, domain: str | None = None) -> str:
    """
    Build WebSocket URL for media stream endpoint.
    
    Args:
        call_id: Call ID to use in the URL path
        domain: Optional domain override (defaults to AI_CALLING_SERVICE_URL)
    
    Returns:
        WebSocket URL (e.g., "wss://example.com/media-stream/callId123")
    """
    if not domain:
        domain = AI_CALLING_SERVICE_URL or ""
    
    if not domain:
        raise ValueError("AI_CALLING_SERVICE_URL must be configured")
    
    clean_domain = extract_domain_from_url(domain)
    protocol = get_websocket_protocol(clean_domain)
    
    # Ensure no whitespace or newlines in URL
    return f"{protocol}://{clean_domain}/media-stream/{call_id}".strip().replace('\n', '').replace('\r', '')

