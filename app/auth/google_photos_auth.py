import hashlib
import base64
import secrets

from flask import Blueprint, redirect, request, session, url_for, current_app
from google_auth_oauthlib.flow import Flow

google_photos_auth_bp = Blueprint("google_photos_auth", __name__, url_prefix="/auth/google-photos")

SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/photoslibrary.readonly",
]


def _build_flow():
    client_config = {
        "web": {
            "client_id": current_app.config["GOOGLE_CLIENT_ID"],
            "client_secret": current_app.config["GOOGLE_CLIENT_SECRET"],
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [current_app.config["GOOGLE_PHOTOS_REDIRECT_URI"]],
        }
    }
    flow = Flow.from_client_config(
        client_config, scopes=SCOPES,
        code_verifier=session.get("gp_code_verifier"),
    )
    flow.redirect_uri = current_app.config["GOOGLE_PHOTOS_REDIRECT_URI"]
    return flow


def _generate_pkce():
    code_verifier = secrets.token_urlsafe(64)
    code_challenge = base64.urlsafe_b64encode(
        hashlib.sha256(code_verifier.encode("ascii")).digest()
    ).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


@google_photos_auth_bp.route("/login")
def login():
    code_verifier, code_challenge = _generate_pkce()
    session["gp_code_verifier"] = code_verifier
    state = secrets.token_urlsafe(32)
    session["gp_oauth_state"] = state

    flow = _build_flow()
    auth_url, _ = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        state=state,
        prompt="consent",
        code_challenge=code_challenge,
        code_challenge_method="S256",
    )
    return redirect(auth_url)


@google_photos_auth_bp.route("/callback")
def callback():
    if request.args.get("state") != session.get("gp_oauth_state"):
        return "Invalid state parameter", 403

    flow = _build_flow()
    authorization_response = request.url.replace("http://", "https://", 1)
    flow.fetch_token(authorization_response=authorization_response)

    session["gp_token"] = flow.credentials.token
    session["gp_connected"] = True
    session.pop("gp_code_verifier", None)

    return redirect(url_for("web.index"))


@google_photos_auth_bp.route("/logout")
def logout():
    session.pop("gp_token", None)
    session.pop("gp_connected", None)
    session.pop("gp_oauth_state", None)
    session.pop("gp_code_verifier", None)
    return redirect(url_for("web.index"))
