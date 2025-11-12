"""
Constants used throughout the AI calling service
"""
# Speaker types for transcripts
SPEAKER_CALLER = "caller"
SPEAKER_AI = "ai"
SPEAKER_ADMIN = "admin"

# Call status values
STATUS_INITIATED = "INITIATED"
STATUS_RINGING = "RINGING"
STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_COMPLETED = "COMPLETED"
STATUS_PROCESSED = "PROCESSED"
STATUS_FAILED = "FAILED"
STATUS_MISSED = "MISSED"
STATUS_DECLINED = "DECLINED"

# Prisma ID prefix (CUID format)
PRISMA_ID_PREFIX = "cm"
MIN_PRISMA_ID_LENGTH = 20  # CUIDs are typically 25 characters

# Maximum call duration in seconds (1 hour)
MAX_CALL_DURATION_SECONDS = 3600

