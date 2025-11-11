# AI Calling Service

FastAPI service that handles Twilio Media Streams and bridges audio to OpenAI Realtime API.

## Setup

1. **Install dependencies:**
```bash
pip install -r requirements.txt
```

2. **Configure environment variables:**
```bash
cp .env.example .env
# Edit .env with your configuration
```

3. **Run the service:**
```bash
python main.py
```

Or with uvicorn directly:
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

Then run ngrok:
ngrok http --url=limo.ngrok.app 8000

## Environment Variables

- `OPENAI_API_KEY` - Your OpenAI API key (required)
- `APP_URL` - URL of your Next.js API (required for production)
- `AI_CALLING_SERVICE_URL` - Public URL of this AI calling service (required for production, used by Twilio for webhooks/media streams)
- `TWILIO_ZEN_ZONE_AGENT_SID` - Twilio AI Agent application SID (required, enables recording by creating 2 participants)
- `PORT` - Port to run the service on (default: 8000)
- `HOST` - Host to bind to (default: 0.0.0.0)
- `ENV` - Environment (development/production)

## Endpoints

- `GET /` - Service information
- `GET /health` - Health check
- `POST /incoming-call` - Handle incoming calls, routes to Twilio AI Agent (enables recording)
- `POST /agent-call` - Handle connection from Twilio AI Agent, sets up Media Stream
- `WebSocket /media-stream/{callSid}` - Twilio Media Stream handler (bridges to OpenAI Realtime API)

## Deployment

### Railway

1. Create a new Railway project
2. Connect your repository
3. Set environment variables
4. Railway will auto-detect Python and install dependencies

### Docker

```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

### Render

1. Create a new Web Service
2. Set build command: `pip install -r requirements.txt`
3. Set start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
4. Add environment variables

## Integration with Next.js

Update your Next.js TwiML endpoint to point to this service:

```typescript
const aiServiceUrl = process.env.AI_CALLING_SERVICE_URL; // e.g., "wss://ai-service.railway.app"
const mediaStreamUrl = `${aiServiceUrl}/media-stream?callId=${callId}&callSid=${callSid}`;
```

