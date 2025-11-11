"""
Twilio service for call management
"""
from twilio.rest import Client as TwilioClient
from config import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN

# Initialize Twilio client
twilio_client = None
if TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN:
    twilio_client = TwilioClient(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)


def get_twilio_client():
    """Get the Twilio client instance"""
    return twilio_client


def is_twilio_configured():
    """Check if Twilio is configured"""
    return twilio_client is not None

