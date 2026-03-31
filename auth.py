"""
auth.py — Google authentication using a long-lived refresh token.

Instead of the browser-based OAuth flow (which breaks in headless environments
like GitHub Actions), this module builds credentials directly from three values
stored as environment variables / GitHub secrets:

    GOOGLE_CLIENT_ID      — OAuth 2.0 client ID
    GOOGLE_CLIENT_SECRET  — OAuth 2.0 client secret
    GOOGLE_REFRESH_TOKEN  — refresh token from OAuth Playground (never expires
                            as long as it is used at least once every 6 months;
                            with twice-daily runs this is effectively permanent)

No token.json file is needed. No browser. No manual intervention.

How to get these values (one-time setup):
    1. Google Cloud Console → APIs & Services → Credentials → your OAuth client
       → copy Client ID and Client Secret
    2. Go to https://developers.google.com/oauthplayground
       → gear icon → "Use your own OAuth credentials" → paste Client ID + Secret
       → authorize these 4 scopes:
           https://www.googleapis.com/auth/gmail.readonly
           https://www.googleapis.com/auth/gmail.compose
           https://www.googleapis.com/auth/calendar.readonly
           https://www.googleapis.com/auth/calendar.events
       → "Exchange authorization code for tokens" → copy refresh_token
    3. Store all three values as GitHub Actions secrets:
           GOOGLE_CLIENT_ID
           GOOGLE_CLIENT_SECRET
           GOOGLE_REFRESH_TOKEN
    4. Add to your local .env file for local runs too.
"""

import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.compose",
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/calendar.events",
]

# Token endpoint Google uses to exchange refresh token → access token
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"


def get_google_services():
    """
    Builds and returns (gmail_service, calendar_service) using a refresh token.

    Reads credentials from environment variables so this works identically
    in GitHub Actions (secrets) and locally (.env file).

    Raises:
        EnvironmentError: If any of the three required env vars are missing.
        Exception: If the token refresh or service build fails.
    """
    client_id     = os.environ.get("GOOGLE_CLIENT_ID",     "").strip()
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
    refresh_token = os.environ.get("GOOGLE_REFRESH_TOKEN", "").strip()

    missing = [
        name for name, val in [
            ("GOOGLE_CLIENT_ID",     client_id),
            ("GOOGLE_CLIENT_SECRET", client_secret),
            ("GOOGLE_REFRESH_TOKEN", refresh_token),
        ] if not val
    ]
    if missing:
        raise EnvironmentError(
            f"Missing required environment variable(s): {', '.join(missing)}\n"
            f"Set them in your .env file (local) or GitHub Actions secrets (CI).\n"
            f"See auth.py docstring for setup instructions."
        )

    print("  Building credentials from refresh token...")

    # Build credentials directly — no file I/O, no browser, no expiry surprises.
    # google-auth will automatically use the refresh token to obtain a fresh
    # access token on the first API call, and re-refresh whenever it expires.
    creds = Credentials(
        token=None,             # no access token yet — will be fetched on first use
        refresh_token=refresh_token,
        token_uri=GOOGLE_TOKEN_URI,
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )

    # Eagerly refresh now so we catch auth errors before the agent starts
    print("  Refreshing access token...")
    try:
        creds.refresh(Request())
        print("  ✓ Access token obtained successfully")
    except Exception as e:
        raise Exception(
            f"Failed to refresh Google access token: {e}\n"
            f"The refresh token may be expired or revoked.\n"
            f"Re-run the OAuth Playground flow and update the GOOGLE_REFRESH_TOKEN secret."
        )

    print("  Building Gmail and Calendar services...")
    try:
        gmail_service    = build("gmail",    "v1", credentials=creds)
        calendar_service = build("calendar", "v3", credentials=creds)
        print("  ✓ Services ready")
        return gmail_service, calendar_service
    except Exception as e:
        raise Exception(f"Failed to build Google API services: {e}")