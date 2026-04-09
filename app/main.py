import os
import traceback
from typing import Any

from fastapi import FastAPI, Depends, Request, Form
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.responses import JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from starlette.middleware.sessions import SessionMiddleware
from dotenv import load_dotenv

load_dotenv(".env")

from app.db import engine, Base, get_db, run_migrations
from app.models import User
from app.auth import router as auth_router
from app.whatsapp import router as whatsapp_router
from app.agent import process_message
from app.memory_service import get_history
from app.utils import build_credentials
from app.calendar_service import list_events, update_event, delete_event

Base.metadata.create_all(bind=engine)
run_migrations()

app = FastAPI()
app.add_middleware(
    SessionMiddleware,
    secret_key=os.getenv("SESSION_SECRET_KEY", "fallback-secret-key")
)
app.include_router(auth_router)
app.include_router(whatsapp_router)

templates = Jinja2Templates(directory="templates")


def get_current_user(request: Request, db: Session) -> User | None:
    user_id = request.session.get("user_id")
    if not user_id:
        return None
    return db.query(User).filter(User.id == user_id).first()


async def _extract_message(request: Request) -> str:
    content_type = request.headers.get("content-type", "").lower()

    if "application/json" in content_type:
        payload: dict[str, Any] = await request.json()
        return str(payload.get("message", "")).strip()

    form = await request.form()
    return str(form.get("message", "")).strip()


@app.get("/healthz")
def healthz():
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def index(request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    history = []
    events = []
    if user:
        history = get_history(user.id, db, limit=20)
        creds = build_credentials(user.access_token, user.refresh_token)
        events = list_events(creds, max_results=5)
    return templates.TemplateResponse(
        request=request, name="index.html",
        context={"user": user, "history": history, "events": events}
    )


@app.post("/chat")
async def chat(request: Request, db: Session = Depends(get_db)):
    try:
        user = get_current_user(request, db)
        if not user:
            return JSONResponse({"error": "Not logged in"}, status_code=401)
        message = await _extract_message(request)
        if not message:
            return JSONResponse({"error": "Message is required"}, status_code=400)
        reply = process_message(user, message, db)
        return JSONResponse({"reply": reply})
    except Exception as e:
        tb = traceback.format_exc()
        print(f"\n[CHAT ERROR]\n{tb}")
        return JSONResponse({"error": str(e), "trace": tb}, status_code=500)


@app.post("/link-phone")
def link_phone(request: Request, phone: str = Form(...), db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)
    phone = phone.strip().replace("+", "").replace(" ", "").replace("-", "")
    user.phone = phone
    db.commit()
    return RedirectResponse(url="/", status_code=303)


# ── Calendar management routes (web UI) ──────────────────────────

@app.get("/edit/{event_id}", response_class=HTMLResponse)
def edit_form(event_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)
    creds = build_credentials(user.access_token, user.refresh_token)
    events = list_events(creds, max_results=50)
    event = next((e for e in events if e["id"] == event_id), None)
    return templates.TemplateResponse(
        request=request, name="edit.html",
        context={"user": user, "event": event, "event_id": event_id}
    )


@app.post("/edit/{event_id}")
def edit_submit(event_id: str, request: Request, text: str = Form(...), db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)
    # Use agent to parse the edit text
    from app.parser import extract_details
    try:
        parsed = extract_details(text)
    except Exception as e:
        return {"error": str(e)}
    creds = build_credentials(user.access_token, user.refresh_token)
    update_event(creds, event_id, parsed)
    return RedirectResponse(url="/", status_code=303)


@app.post("/delete/{event_id}")
def delete(event_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user(request, db)
    if not user:
        return RedirectResponse(url="/auth/login", status_code=303)
    creds = build_credentials(user.access_token, user.refresh_token)
    delete_event(creds, event_id)
    return RedirectResponse(url="/", status_code=303)


@app.get("/auth/logout")
def logout(request: Request):
    request.session.pop("user_id", None)
    return RedirectResponse(url="/")
