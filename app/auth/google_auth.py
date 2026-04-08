import os
import secrets
import hashlib
import base64

from flask import Blueprint, redirect, request, session, url_for, current_app
from google_auth_oauthlib.flow import Flow

google_auth_bp = Blueprint("google_auth", __name__, url_prefix="/auth/google")

SCOPES_READONLY = [
    "openid",
    "https://www.googleapis.com/auth/drive.readonly",
]

SCOPES_READWRITE = [
    "openid",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/drive.readonly",
]


def _build_flow(scopes=None):
    """Build a Google OAuth flow from environment variables."""
    if scopes is None:
        scopes = SCOPES_READWRITE

    client_config = {
        "web": {
            "client_id": current_app.config["GOOGLE_CLIENT_ID"],
            "client_secret": current_app.config["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [current_app.config["GOOGLE_REDIRECT_URI"]],
        }
    }
    flow = Flow.from_client_config(
        client_config, scopes=scopes, code_verifier=session.get("code_verifier")
    )
    flow.redirect_uri = current_app.config["GOOGLE_REDIRECT_URI"]
    return flow


def _generate_code_verifier():
    """Generate a PKCE code verifier and challenge."""
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


@google_auth_bp.route("/login")
def login():
    """Redirect user to Google's OAuth consent page."""
    # Generate PKCE code verifier/challenge (required by Google)
    code_verifier, code_challenge = _generate_code_verifier()
    session["code_verifier"] = code_verifier

    flow = _build_flow()
    state = secrets.token_urlsafe(32)
    session["google_oauth_state"] = state

    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=state,
        prompt="consent",
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )
    return redirect(auth_url)


@google_auth_bp.route("/callback")
def callback():
    """Handle the OAuth callback from Google."""
    # Verify state to prevent CSRF
    if request.args.get("state") != session.get("google_oauth_state"):
        return "Invalid state parameter", 403

    flow = _build_flow()
    flow.fetch_token(authorization_response=request.url)
    credentials = flow.credentials

    # Store credentials in server-side session
    session["google_credentials"] = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
    }
    session["google_connected"] = True

    # Clean up PKCE verifier
    session.pop("code_verifier", None)

    return redirect(url_for("web.index"))


@google_auth_bp.route("/logout")
def logout():
    """Disconnect Google Drive."""
    session.pop("google_credentials", None)
    session.pop("google_connected", None)
    session.pop("google_oauth_state", None)
    session.pop("code_verifier", None)
    return redirect(url_for("web.index"))
