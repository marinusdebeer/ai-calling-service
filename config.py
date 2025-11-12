"""
Configuration and environment variables
"""
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# OpenAI Realtime API configuration
REALTIME_MODEL = "gpt-realtime"
OPENAI_REALTIME_URL = f"wss://api.openai.com/v1/realtime?model={REALTIME_MODEL}"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# Audio format configuration - using g711_ulaw directly (no conversion needed)
OPENAI_INPUT_FORMAT = "g711_ulaw"
OPENAI_OUTPUT_FORMAT = "g711_ulaw"
VOICE = "ash"
AI_ASSISTANT_INSTRUCTIONS = (
    "You are a helpful assistant for a cleaning service company, Zen Zone Cleaning Services. "
    "Be professional, friendly, and helpful. Your name is Brad. "
    "Introduce yourself once at the beginning of the call; in later responses, continue the conversation naturally "
    "without repeating the same greeting or introduction."
)

# Next.js API configuration
APP_URL = os.getenv("APP_URL")

# Twilio configuration
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN")
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER")
TWILIO_ZEN_ZONE_AGENT_SID = os.getenv("TWILIO_ZEN_ZONE_AGENT_SID")  # Twilio AI Agent application SID
AI_CALLING_SERVICE_URL = os.getenv("AI_CALLING_SERVICE_URL")  # Public URL for this service


def validate_env_vars():
    """Validate that all required environment variables are set"""
    missing_vars = []
    
    if not OPENAI_API_KEY:
        missing_vars.append("OPENAI_API_KEY")
    if not APP_URL:
        missing_vars.append("APP_URL")
    if not AI_CALLING_SERVICE_URL:
        missing_vars.append("AI_CALLING_SERVICE_URL")
    if not TWILIO_ACCOUNT_SID:
        missing_vars.append("TWILIO_ACCOUNT_SID")
    if not TWILIO_AUTH_TOKEN:
        missing_vars.append("TWILIO_AUTH_TOKEN")
    if not TWILIO_PHONE_NUMBER:
        missing_vars.append("TWILIO_PHONE_NUMBER")
    if not TWILIO_ZEN_ZONE_AGENT_SID:
        missing_vars.append("TWILIO_ZEN_ZONE_AGENT_SID")
    
    if missing_vars:
        error_msg = f"❌ Missing required environment variables: {', '.join(missing_vars)}"
        print(error_msg)
        raise ValueError(error_msg)
    
    print("✅ All required environment variables are set")

