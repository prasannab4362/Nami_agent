import json
import os
import re
from datetime import datetime
from contextlib import contextmanager
from google import genai

GEMINI_MODEL = "gemini-2.5-flash"


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

def extract_details(text: str) -> dict:
    """
    Extract meeting details from natural language text using Gemini API.
    Passes current IST date so the model can resolve relative dates correctly.
    """
    today = datetime.now().strftime("%Y-%m-%d %H:%M")

    prompt = f"""Today is {today} (IST, Asia/Kolkata timezone).

Extract meeting details from this text:
"{text}"

Return ONLY valid JSON with no markdown, no explanation, no code fences:
{{
  "summary": "event title",
  "location": "location or empty string",
  "time": "ISO 8601 datetime e.g. 2025-04-11T14:00:00",
  "duration_minutes": 60,
  "attendee_emails": ["guest@example.com"],
  "clear_attendees": false
}}

Rules:
- Resolve relative dates (tomorrow, next Monday, in 2 days, this Friday, etc.) based on today's date above.
- If no specific time is mentioned, default to 10:00 AM on the resolved date.
- If no duration is mentioned, default to 60.
- If guest emails are mentioned, include them in attendee_emails.
- If the user asks to remove all guests/attendees, set clear_attendees to true.
- Return time without timezone suffix (timezone is handled separately as Asia/Kolkata).
"""

    try:
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise RuntimeError("Gemini is not configured. Set GEMINI_API_KEY and restart the app.")
        with _without_proxy_env():
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=prompt
            )

        result = response.text.strip()

        match = re.search(r'\{.*\}', result, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        else:
            raise Exception(f"No JSON block found in Gemini output: {result}")

    except Exception as e:
        print(f"Error parsing with Gemini: {e}")
        raise e
