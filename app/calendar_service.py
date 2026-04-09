from datetime import datetime, timedelta
import uuid

from dateutil import parser as dateutil_parser
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError


def _parse_datetime(time_str: str) -> datetime:
    """Parse ISO 8601 or a natural datetime string. Falls back to tomorrow 10am."""
    try:
        return dateutil_parser.parse(time_str)
    except Exception:
        tomorrow = datetime.now() + timedelta(days=1)
        return tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)


def _normalize_attendees(data: dict) -> list[dict]:
    emails = data.get("attendee_emails") or []
    single_email = data.get("attendee_email")
    if single_email:
        emails = [single_email, *emails]

    seen = set()
    attendees = []
    for email in emails:
        normalized = str(email).strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        attendees.append({"email": normalized})
    return attendees


def create_event(creds: Credentials, data: dict) -> dict | None:
    """Create a Google Calendar event with Meet link."""
    try:
        service = build("calendar", "v3", credentials=creds)

        start_dt = _parse_datetime(data.get("time", ""))
        duration = int(data.get("duration_minutes") or 60)
        end_dt = start_dt + timedelta(minutes=duration)

        event = {
            "summary": data.get("summary", "New Meeting"),
            "location": data.get("location", ""),
            "start": {
                "dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "Asia/Kolkata",
            },
            "end": {
                "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "Asia/Kolkata",
            },
            "conferenceData": {
                "createRequest": {"requestId": str(uuid.uuid4())},
            },
        }

        attendees = _normalize_attendees(data)
        if attendees:
            event["attendees"] = attendees

        created = (
            service.events()
            .insert(
                calendarId="primary",
                body=event,
                conferenceDataVersion=1,
                sendUpdates="all" if attendees else "none",
            )
            .execute()
        )

        return created

    except HttpError as error:
        print(f"Calendar API error (create): {error}")
        return None


def list_events(creds: Credentials, max_results: int = 10) -> list:
    """Return upcoming events from the primary calendar."""
    try:
        service = build("calendar", "v3", credentials=creds)
        now = datetime.utcnow().isoformat() + "Z"

        result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=now,
                maxResults=max_results,
                singleEvents=True,
                orderBy="startTime",
            )
            .execute()
        )

        return result.get("items", [])

    except HttpError as error:
        print(f"Calendar API error (list): {error}")
        return []


def update_event(creds: Credentials, event_id: str, data: dict) -> dict | None:
    """Update an existing event by ID."""
    try:
        service = build("calendar", "v3", credentials=creds)
        event = service.events().get(calendarId="primary", eventId=event_id).execute()

        if data.get("summary"):
            event["summary"] = data["summary"]

        if data.get("location") is not None:
            event["location"] = data["location"]

        if data.get("time"):
            duration = int(data.get("duration_minutes") or 60)
            start_dt = _parse_datetime(data["time"])
            end_dt = start_dt + timedelta(minutes=duration)
            event["start"] = {
                "dateTime": start_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "Asia/Kolkata",
            }
            event["end"] = {
                "dateTime": end_dt.strftime("%Y-%m-%dT%H:%M:%S"),
                "timeZone": "Asia/Kolkata",
            }

        attendees_provided = any(
            key in data for key in ("attendee_email", "attendee_emails", "clear_attendees")
        )
        if attendees_provided:
            attendees = [] if data.get("clear_attendees") else _normalize_attendees(data)
            event["attendees"] = attendees

        updated = (
            service.events()
            .update(
                calendarId="primary",
                eventId=event_id,
                body=event,
                conferenceDataVersion=1,
                sendUpdates="all",
            )
            .execute()
        )

        return updated

    except HttpError as error:
        print(f"Calendar API error (update): {error}")
        return None


def delete_event(creds: Credentials, event_id: str) -> bool:
    """Delete an event by ID."""
    try:
        service = build("calendar", "v3", credentials=creds)
        service.events().delete(calendarId="primary", eventId=event_id, sendUpdates="all").execute()
        return True
    except HttpError as error:
        print(f"Calendar API error (delete): {error}")
        return False
