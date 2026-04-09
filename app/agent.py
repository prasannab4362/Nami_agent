"""
Central AI agent for chat, web, and WhatsApp interactions.
"""
from contextlib import contextmanager
from datetime import datetime, timedelta
import json
import os
import re

from dotenv import load_dotenv
from google import genai
from sqlalchemy.orm import Session

from app.calendar_service import create_event, delete_event, list_events, update_event
from app.gmail_service import send_email
from app.memory_service import add_to_history, get_history, get_memories, set_memory
from app.models import User
from app.utils import build_credentials

load_dotenv()

BOT_NAME = "Nami"
BOT_INTRO = (
    f"You are {BOT_NAME}, a smart AI virtual assistant manager. "
    "You manage calendars, send emails, schedule meetings, update existing events, "
    "delete events, and remember important details about the user. "
    "You are friendly, concise, and always professional."
)
GEMINI_MODEL = "gemini-2.5-flash"
EMAIL_PATTERN = r"[\w\.-]+@[\w\.-]+\.\w+"


@contextmanager
def _without_proxy_env():
    proxy_keys = [
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
    ]
    original = {key: os.environ.get(key) for key in proxy_keys}
    try:
        for key in proxy_keys:
            os.environ.pop(key, None)
        yield
    finally:
        for key, value in original.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _call_gemini(prompt: str) -> str:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Gemini is not configured. Set GEMINI_API_KEY and restart the app.")

    with _without_proxy_env():
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(model=GEMINI_MODEL, contents=prompt)
    return response.text.strip()


def _extract_emails(text: str) -> list[str]:
    seen = set()
    emails = []
    for email in re.findall(EMAIL_PATTERN, text):
        normalized = email.strip()
        if normalized not in seen:
            seen.add(normalized)
            emails.append(normalized)
    return emails


def _format_display_time(value: str) -> str:
    if not value:
        return ""

    normalized = value.rstrip("Z")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        return value

    formatted = dt.strftime("%d %b %Y, %I:%M %p")
    return formatted.lstrip("0").replace(" 0", " ")


def _event_start_text(event: dict) -> str:
    start = event.get("start", {})
    if start.get("dateTime"):
        return _format_display_time(start["dateTime"])
    if start.get("date"):
        try:
            dt = datetime.strptime(start["date"], "%Y-%m-%d")
            return dt.strftime("%d %b %Y")
        except ValueError:
            return start["date"]
    return ""


def _format_event(event: dict) -> str:
    summary = event.get("summary") or "Untitled event"
    when = _event_start_text(event)
    attendees = event.get("attendees") or []
    attendee_text = ""
    if attendees:
        emails = ", ".join(a.get("email", "") for a in attendees if a.get("email"))
        if emails:
            attendee_text = f"\nGuests: {emails}"
    return f"{summary} - {when}{attendee_text}"


def _match_event(events: list[dict], query: str) -> tuple[dict | None, str | None]:
    if not events:
        return None, "No upcoming events were found."

    normalized = query.strip().lower()
    if not normalized:
        return events[0], None

    for event in events:
        summary = (event.get("summary") or "").lower()
        if normalized in summary or summary in normalized:
            return event, None

    for event in events:
        haystack = f"{(event.get('summary') or '').lower()} {_event_start_text(event).lower()}"
        if all(token in haystack for token in normalized.split() if len(token) > 2):
            return event, None

    matches = [
        event
        for event in events
        if any(token in (event.get("summary") or "").lower() for token in normalized.split() if len(token) > 2)
    ]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        options = "\n".join(f"- {_format_event(event)}" for event in matches[:5])
        return None, f"I found multiple matching events. Please be more specific:\n{options}"

    upcoming = "\n".join(f"- {_format_event(event)}" for event in events[:5])
    return None, f"I couldn't find a matching event. Here are the next events:\n{upcoming}"


def _local_fallback(text: str, memories: dict) -> dict:
    normalized = text.strip()
    lowered = normalized.lower()
    name = memories.get("name")
    emails = _extract_emails(normalized)

    remember_match = re.search(
        r"\b(?:my name is|i am|call me)\s+([A-Za-z][A-Za-z .'-]{0,49})",
        normalized,
        re.IGNORECASE,
    )
    if remember_match:
        remembered_name = remember_match.group(1).strip().rstrip(".")
        return {
            "intent": "remember",
            "response": f"Noted{', ' + remembered_name if remembered_name else ''}. I'll remember your name.",
            "data": {"key": "name", "value": remembered_name},
        }

    if "delete" in lowered or "cancel" in lowered or "remove" in lowered:
        return {
            "intent": "delete_appointment",
            "response": "I parsed that as an event deletion request.",
            "data": {"event_query": normalized},
        }

    if any(word in lowered for word in ["move", "change", "modify", "reschedule", "update"]):
        return {
            "intent": "update_appointment",
            "response": "I parsed that as an event update request.",
            "data": {
                "event_query": normalized,
                "time": normalized,
                "attendee_emails": emails,
            },
        }

    if (
        "who are you" in lowered
        or "what can you do" in lowered
        or re.search(r"\b(hi|hello|hey)\b", lowered)
    ):
        display_name = name or "there"
        return {
            "intent": "greet",
            "response": (
                f"Hi {display_name}. I'm Nami. I can manage calendar events, update or delete them, "
                "send Gmail messages, and remember a few details for you."
            ),
            "data": {},
        }

    if "send mail" in lowered or "send email" in lowered:
        if emails:
            return {
                "intent": "send_email",
                "response": "I parsed that as an email request and will try to send it.",
                "data": {
                    "to": emails[0],
                    "subject": "Message from Nami",
                    "body": normalized,
                },
            }

    if any(word in lowered for word in ["meeting", "appointment", "schedule", "book", "create"]):
        time_match = re.search(r"\b(\d{1,2})(?::(\d{2}))?\s*(am|pm)\b", lowered)
        hour = 10
        minute = 0
        if time_match:
            hour = int(time_match.group(1)) % 12
            minute = int(time_match.group(2) or 0)
            if time_match.group(3) == "pm":
                hour += 12
        scheduled = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        if scheduled <= datetime.now():
            scheduled += timedelta(days=1)
        return {
            "intent": "create_appointment",
            "response": "I parsed that as a calendar request and will try to create it.",
            "data": {
                "summary": normalized[:80],
                "time": scheduled.strftime("%Y-%m-%dT%H:%M:%S"),
                "duration_minutes": 60,
                "location": "",
                "attendee_emails": emails,
            },
        }

    if "event" in lowered or "meeting" in lowered or "calendar" in lowered:
        return {
            "intent": "list_events",
            "response": "I'll check your upcoming events.",
            "data": {},
        }

    return {
        "intent": "answer",
        "response": (
            "I couldn't reach the AI service, so I can only handle simple calendar, email, "
            "and memory requests right now."
        ),
        "data": {},
    }


def _normalize_result(result: dict, text: str) -> dict:
    data = result.get("data") or {}
    if "attendee_emails" not in data:
        data["attendee_emails"] = _extract_emails(text)
    if data.get("attendee_email") and data["attendee_email"] not in data["attendee_emails"]:
        data["attendee_emails"].insert(0, data["attendee_email"])
    result["data"] = data
    return result


def process_message(user: User, text: str, db: Session) -> str:
    """
    Main entry point for both web chat and WhatsApp.
    Returns the assistant's reply string.
    """
    user_id = user.id
    access_token = user.access_token
    refresh_token = user.refresh_token

    try:
        memories = get_memories(user_id, db)
    except Exception as exc:
        db.rollback()
        print(f"Memory load error: {exc}")
        memories = {}

    try:
        history = get_history(user_id, db, limit=6)
    except Exception as exc:
        db.rollback()
        print(f"History load error: {exc}")
        history = []

    today = datetime.now().strftime("%Y-%m-%d %H:%M")
    memory_ctx = "\n".join(f"- {key}: {value}" for key, value in memories.items()) or "None yet"
    history_ctx = "\n".join(
        f"{'Assistant' if item.role == 'assistant' else 'User'}: {item.message}"
        for item in history
    ) or "No prior conversation"

    prompt = f"""{BOT_INTRO}

=== User Memory ===
{memory_ctx}

=== Recent Conversation ===
{history_ctx}

Today: {today} IST (Asia/Kolkata)

User message: "{text}"

Reply with ONLY valid JSON:
{{
  "intent": "greet | answer | remember | send_email | create_appointment | update_appointment | delete_appointment | list_events | unknown",
  "response": "Friendly natural reply",
  "data": {{}}
}}

Intent data requirements:
  remember           -> {{"key": "name", "value": "John"}}
  send_email         -> {{"to": "x@email.com", "subject": "...", "body": "full body text"}}
  create_appointment -> {{"summary": "title", "time": "2026-04-11T14:00:00", "duration_minutes": 60, "location": "", "attendee_emails": ["a@email.com"]}}
  update_appointment -> {{"event_query": "existing event reference from user words", "summary": "", "time": "", "duration_minutes": 60, "location": "", "attendee_emails": ["a@email.com"], "clear_attendees": false}}
  delete_appointment -> {{"event_query": "existing event reference from user words"}}
  list_events        -> {{}}
  greet/answer       -> {{}}

Rules:
  - "I am X" / "My name is X" / "Call me X" -> remember
  - "send email/mail to X about Y" -> send_email
  - "schedule/book/create meeting/appointment" -> create_appointment
  - "move/change/modify/reschedule meeting" -> update_appointment
  - "delete/cancel/remove meeting" -> delete_appointment
  - "what meetings/events do I have" -> list_events
  - Extract guest emails into attendee_emails when present
  - If user says remove all guests -> clear_attendees=true
  - Resolve relative dates using Today
  - event_query should contain only the part that identifies the existing event, not the whole instruction
  - Always use the user's known name when appropriate
"""

    try:
        raw = _call_gemini(prompt)
    except Exception as exc:
        print(f"Gemini unavailable, using local fallback: {exc}")
        result = _local_fallback(text, memories)
    else:
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            reply = "Sorry, I didn't quite understand. Could you rephrase?"
            _save(user_id, text, reply, db)
            return reply
        try:
            result = json.loads(match.group(0))
        except json.JSONDecodeError:
            reply = "I had trouble processing that. Please try again."
            _save(user_id, text, reply, db)
            return reply

    result = _normalize_result(result, text)
    intent = result.get("intent", "unknown")
    data = result.get("data", {})
    reply = result.get("response", "I'm here to help!")

    try:
        if intent == "remember":
            set_memory(user_id, data.get("key", "info"), data.get("value", ""), db)

        elif intent == "send_email":
            creds = build_credentials(access_token, refresh_token)
            ok = send_email(
                creds,
                to=data.get("to", ""),
                subject=data.get("subject", ""),
                body=data.get("body", ""),
            )
            if not ok:
                reply = (
                    "I couldn't send the email. Gmail permission may be missing. "
                    "Please log out and log in again to re-authorize."
                )

        elif intent == "create_appointment":
            creds = build_credentials(access_token, refresh_token)
            event = create_event(creds, data)
            if event:
                start = _event_start_text(event)
                meet = event.get("hangoutLink", "")
                attendees = data.get("attendee_emails") or []
                attendee_line = f"\nGuests: {', '.join(attendees)}" if attendees else ""
                meet_line = f"\nMeet: {meet}" if meet else ""
                reply = f"Done! '{data.get('summary')}' is booked on {start}{attendee_line}{meet_line}"
            else:
                reply = "Couldn't create the appointment. Your Google token may have expired, so please log in again."

        elif intent == "update_appointment":
            creds = build_credentials(access_token, refresh_token)
            events = list_events(creds, max_results=20)
            event, error = _match_event(events, data.get("event_query") or text)
            if error:
                reply = error
            else:
                payload = {
                    key: value
                    for key, value in data.items()
                    if key in {"summary", "time", "duration_minutes", "location", "attendee_email", "attendee_emails", "clear_attendees"}
                }
                if not payload:
                    reply = "I found the event, but I need the change details like new time, title, location, or guests."
                else:
                    updated = update_event(creds, event["id"], payload)
                    if updated:
                        reply = f"Updated the event:\n{_format_event(updated)}"
                    else:
                        reply = "I found the event, but updating it failed. Please try logging in again."

        elif intent == "delete_appointment":
            creds = build_credentials(access_token, refresh_token)
            events = list_events(creds, max_results=20)
            event, error = _match_event(events, data.get("event_query") or text)
            if error:
                reply = error
            else:
                ok = delete_event(creds, event["id"])
                if ok:
                    reply = f"Deleted the event '{event.get('summary') or 'Untitled event'}' scheduled for {_event_start_text(event)}."
                else:
                    reply = "I found the event, but deleting it failed. Please try logging in again."

        elif intent == "list_events":
            creds = build_credentials(access_token, refresh_token)
            events = list_events(creds, max_results=5)
            if events:
                lines = [f"- {_format_event(event)}" for event in events]
                reply = "Your upcoming events:\n" + "\n".join(lines)
            else:
                reply = "You have no upcoming events."

    except Exception as exc:
        print(f"Agent action error [{intent}]: {exc}")
        reply = f"I understood but hit an error while doing that: {exc}"

    _save(user_id, text, reply, db)
    return reply


def _save(user_id: int, user_msg: str, bot_reply: str, db: Session):
    try:
        add_to_history(user_id, "user", user_msg, db)
        add_to_history(user_id, "assistant", bot_reply, db)
    except Exception as exc:
        db.rollback()
        print(f"History save error: {exc}")
