from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import base64


def send_email(creds: Credentials, to: str, subject: str, body: str) -> bool:
    """Send an email from the user's Gmail account."""
    try:
        service = build('gmail', 'v1', credentials=creds)

        msg = MIMEMultipart()
        msg['to'] = to
        msg['subject'] = subject
        msg.attach(MIMEText(body, 'plain'))

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        service.users().messages().send(userId='me', body={'raw': raw}).execute()
        return True

    except HttpError as error:
        print(f'Gmail API error: {error}')
        return False
