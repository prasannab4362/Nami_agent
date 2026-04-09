from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import RedirectResponse, HTMLResponse
from sqlalchemy.orm import Session
from google_auth_oauthlib.flow import Flow
import requests as http_requests
import os

from app.db import get_db
from app.models import User

router = APIRouter(prefix="/auth")

oauth_state_store = {}

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar",
    "https://www.googleapis.com/auth/gmail.send",
]


def get_redirect_uri(request: Request) -> str:
    """Prefer configured deploy URLs, then fall back to the current request host."""
    configured = os.getenv("REDIRECT_URI")
    if configured:
        return configured.rstrip("/")

    app_url = os.getenv("APP_URL")
    if app_url:
        return f"{app_url.rstrip('/')}/auth/callback"

    base = str(request.base_url).rstrip("/")
    return f"{base}/auth/callback"


def get_google_flow(redirect_uri: str) -> Flow:
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=SCOPES,
    )
    flow.redirect_uri = redirect_uri
    return flow


@router.get("/login")
def login(request: Request):
    redirect_uri = get_redirect_uri(request)
    flow = get_google_flow(redirect_uri)

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    if hasattr(flow, "code_verifier"):
        oauth_state_store[state] = flow.code_verifier

    request.session["state"] = state
    return RedirectResponse(authorization_url)


@router.get("/callback", name="auth_callback")
def auth_callback(
    request: Request,
    code: str = Query(default=None),
    state: str = Query(default=None),
    error: str = Query(default=None),
    db: Session = Depends(get_db),
):
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
    expected_state = request.session.get("state")
    code_verifier = oauth_state_store.pop(state, None) if state else None

    if error:
        return HTMLResponse(
            f"<h3>Google denied access: {error}</h3><a href='/'>Back</a>"
        )
    if not code:
        return HTMLResponse(
            "<h3>Missing auth code.</h3><a href='/auth/login'>Try again</a>"
        )
    if not state or not expected_state or state != expected_state:
        return HTMLResponse(
            "<h3>Invalid OAuth state.</h3><a href='/auth/login'>Try again</a>",
            status_code=400,
        )

    redirect_uri = get_redirect_uri(request)

    # ── Direct token exchange — no flow.fetch_token, no state issues ──
    try:
        token_resp = http_requests.post(
            "https://oauth2.googleapis.com/token",
            data={
                "code": code,
                "client_id": os.getenv("GOOGLE_CLIENT_ID"),
                "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                **({"code_verifier": code_verifier} if code_verifier else {}),
            },
            timeout=15,
        )
        tokens = token_resp.json()

        if "error" in tokens:
            raise Exception(f"{tokens['error']}: {tokens.get('error_description', '')}")

        access_token = tokens["access_token"]
        refresh_token = tokens.get("refresh_token")

    except Exception as e:
        print(f"Token exchange error: {e}")
        return HTMLResponse(
            f"<h3>Login failed. Please try again.</h3>"
            f"<p style='color:red;font-size:13px'>{e}</p>"
            f"<a href='/auth/login'>Login again</a>"
        )

    # ── Get user email ────────────────────────────────────────────
    try:
        user_info = http_requests.get(
            "https://www.googleapis.com/oauth2/v1/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=10,
        ).json()
        email = user_info.get("email")
    except Exception as e:
        return HTMLResponse(
            f"<h3>Could not get user info: {e}</h3>"
            f"<a href='/auth/login'>Retry</a>"
        )

    if not email:
        return HTMLResponse(
            "<h3>No email from Google.</h3><a href='/auth/login'>Retry</a>"
        )

    # ── Upsert user in DB ─────────────────────────────────────────
    user = db.query(User).filter(User.email == email).first()
    if not user:
        user = User(email=email, access_token=access_token, refresh_token=refresh_token)
        db.add(user)
    else:
        user.access_token = access_token
        if refresh_token:
            user.refresh_token = refresh_token
    db.commit()
    db.refresh(user)

    request.session.pop("state", None)
    request.session["user_id"] = user.id
    return RedirectResponse(url="/")
