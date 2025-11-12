"""
Call-related utility functions
"""
from utils.constants import PRISMA_ID_PREFIX, MIN_PRISMA_ID_LENGTH


def is_prisma_call_id(identifier: str) -> bool:
    """
    Check if an identifier is likely a Prisma callId (CUID format).
    
    Args:
        identifier: String to check
    
    Returns:
        True if identifier appears to be a Prisma callId
    """
    return (
        identifier and
        identifier.startswith(PRISMA_ID_PREFIX) and
        len(identifier) >= MIN_PRISMA_ID_LENGTH
    )


def validate_call_id(call_id: str) -> bool:
    """
    Validate that a call_id is safe to use in URLs.
    
    Args:
        call_id: Call ID to validate
    
    Returns:
        True if call_id is valid, False otherwise
    """
    if not call_id:
        return False
    
    # Check for dangerous characters that could break URL construction
    dangerous_chars = "!@#$%^&*()[]{};:,./<>?\\|`~"
    if any(c in call_id for c in dangerous_chars):
        return False
    
    return True

