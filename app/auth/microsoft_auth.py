import secrets

import msal
from flask import Blueprint, redirect, request, session, url_for, current_app

microsoft_auth_bp = Blueprint("microsoft_auth", __name__, url_prefix="/auth/microsoft")

AUTHORITY = "https://login.microsoftonline.com/consumers"
SCOPES = ["User.Read", "Files.Read.All", "Files.ReadWrite.All"]


def _build_msal_app():
    """Build an MSAL confidential client application."""
    return msal.ConfidentialClientApplication(
        current_app.config["MICROSOFT_CLIENT_ID"],
        authority=AUTHORITY,
        client_credential=current_app.config["MICROSOFT_CLIENT_SECRET"],
    )


@microsoft_auth_bp.route("/login")
def login():
    """Redirect user to Microsoft's OAuth consent page."""
    app = _build_msal_app()
    state = secrets.token_urlsafe(32)
    session["ms_oauth_state"] = state

    auth_url = app.get_authorization_request_url(
        scopes=SCOPES,
        state=state,
        redirect_uri=current_app.config["MICROSOFT_REDIRECT_URI"],
    )
    return redirect(auth_url)


@microsoft_auth_bp.route("/callback")
def callback():
    """Handle the OAuth callback from Microsoft."""
    if request.args.get("state") != session.get("ms_oauth_state"):
        return "Invalid state parameter", 403

    if "error" in request.args:
        return f"OAuth error: {request.args.get('error_description', 'Unknown')}", 400

    app = _build_msal_app()
    result = app.acquire_token_by_authorization_code(
        request.args["code"],
        scopes=SCOPES,
        redirect_uri=current_app.config["MICROSOFT_REDIRECT_URI"],
    )

    if "error" in result:
        return f"Token error: {result.get('error_description', 'Unknown')}", 400

    session["ms_token"] = result["access_token"]
    session["ms_connected"] = True

    return redirect(url_for("web.index"))


@microsoft_auth_bp.route("/logout")
def logout():
    """Disconnect OneDrive."""
    session.pop("ms_token", None)
    session.pop("ms_connected", None)
    session.pop("ms_oauth_state", None)
    return redirect(url_for("web.index"))
