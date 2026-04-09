import os
import requests
from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from app.db import SessionLocal
from app.models import User
from app.agent import process_message

router = APIRouter(prefix="/webhook")

WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "my-verify-token")
APP_URL = os.getenv("APP_URL", "http://localhost:8000")


def send_whatsapp(to: str, text: str):
    requests.post(
        f"https://graph.facebook.com/v19.0/{PHONE_NUMBER_ID}/messages",
        json={
            "messaging_product": "whatsapp",
            "to": to,
            "text": {"body": text}
        },
        headers={
            "Authorization": f"Bearer {WHATSAPP_TOKEN}",
            "Content-Type": "application/json"
        },
        timeout=10
    )


@router.get("/whatsapp")
def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")
    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge)
    return PlainTextResponse("Forbidden", status_code=403)


@router.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    body = await request.json()
    try:
        entry = body.get("entry", [{}])[0]
        changes = entry.get("changes", [{}])[0]
        value = changes.get("value", {})
        messages = value.get("messages", [])

        if not messages:
            return {"status": "ok"}

        msg = messages[0]
        phone = msg.get("from", "")
        text = msg.get("text", {}).get("body", "").strip()

        if not text or not phone:
            return {"status": "ok"}

        db = SessionLocal()
        try:
            user = db.query(User).filter(User.phone == phone).first()

            if not user:
                send_whatsapp(
                    phone,
                    f"Hi! I'm Nami, your AI assistant.\n\n"
                    f"To get started, link your Google account at:\n{APP_URL}\n\n"
                    "Login with Google → enter your WhatsApp number → come back and chat!"
                )
                return {"status": "ok"}

            # Let the AI agent handle everything
            reply = process_message(user, text, db)
            send_whatsapp(phone, reply)

        finally:
            db.close()

    except Exception as e:
        print(f"WhatsApp webhook error: {e}")

    return {"status": "ok"}
