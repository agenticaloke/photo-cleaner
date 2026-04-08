import json
import threading

from flask import (
    Blueprint, render_template, redirect, url_for, session,
    request, Response, stream_with_context, jsonify,
)

from app.cloud.google_drive import GoogleDriveProvider
from app.cloud.onedrive import OneDriveProvider
from app.core.grouper import scan_for_duplicates
from app.core.models import ScanResult
from app.web.sse import generate_progress_events

web_bp = Blueprint("web", __name__)


def _get_providers():
    """Build list of active cloud providers from session credentials."""
    providers = []
    if session.get("google_connected") and session.get("google_credentials"):
        providers.append(GoogleDriveProvider(session["google_credentials"]))
    if session.get("ms_connected") and session.get("ms_token"):
        providers.append(OneDriveProvider(session["ms_token"]))
    return providers


def _format_size(bytes_val):
    """Format bytes as human-readable string."""
    for unit in ("B", "KB", "MB", "GB"):
        if bytes_val < 1024:
            return f"{bytes_val:.1f} {unit}"
        bytes_val /= 1024
    return f"{bytes_val:.1f} TB"


@web_bp.app_template_filter("filesize")
def filesize_filter(value):
    return _format_size(value)


@web_bp.route("/")
def index():
    """Home page — shows connection status and scan button."""
    return render_template(
        "index.html",
        google_connected=session.get("google_connected", False),
        ms_connected=session.get("ms_connected", False),
    )


@web_bp.route("/scan")
def scan():
    """Start scanning — shows progress page."""
    providers = _get_providers()
    if not providers:
        return redirect(url_for("web.index"))

    # Initialize progress dict in session
    session["scan_progress"] = {
        "stage": "starting",
        "current": 0,
        "total": 0,
        "done": False,
        "error": None,
    }

    return render_template("scanning.html")


@web_bp.route("/scan/start", methods=["POST"])
def scan_start():
    """Kick off the scan in a background thread and return immediately."""
    providers = _get_providers()
    if not providers:
        return jsonify({"error": "No cloud accounts connected"}), 400

    threshold = request.form.get("threshold", 10, type=int)

    # Store scan config so progress endpoint can access it
    # We use a module-level dict keyed by a scan ID
    import secrets
    scan_id = secrets.token_urlsafe(16)
    session["scan_id"] = scan_id

    _active_scans[scan_id] = {
        "progress": {
            "stage": "starting",
            "current": 0,
            "total": 0,
            "done": False,
            "error": None,
        },
        "result": None,
    }

    def run_scan():
        progress = _active_scans[scan_id]["progress"]

        def progress_callback(stage, current, total):
            progress["stage"] = stage
            progress["current"] = current
            progress["total"] = total

        try:
            result = scan_for_duplicates(providers, threshold, progress_callback)
            _active_scans[scan_id]["result"] = result
            progress["done"] = True
        except Exception as e:
            progress["error"] = str(e)

    thread = threading.Thread(target=run_scan, daemon=True)
    thread.start()

    return jsonify({"scan_id": scan_id})


# Module-level storage for active scans (in-memory, suitable for single-server)
_active_scans = {}


@web_bp.route("/scan/progress")
def scan_progress():
    """SSE endpoint streaming scan progress."""
    scan_id = session.get("scan_id")
    if not scan_id or scan_id not in _active_scans:
        return Response("data: {\"error\": \"No active scan\"}\n\n",
                        mimetype="text/event-stream")

    progress = _active_scans[scan_id]["progress"]
    return Response(
        stream_with_context(generate_progress_events(progress)),
        mimetype="text/event-stream",
    )


@web_bp.route("/results")
def results():
    """Show duplicate groups found by the scan."""
    scan_id = session.get("scan_id")
    if not scan_id or scan_id not in _active_scans:
        return redirect(url_for("web.index"))

    scan_data = _active_scans[scan_id]
    result = scan_data.get("result")
    if not result:
        return redirect(url_for("web.scan"))

    return render_template(
        "results.html",
        result=result,
        google_connected=session.get("google_connected", False),
        ms_connected=session.get("ms_connected", False),
    )


@web_bp.route("/delete", methods=["POST"])
def delete():
    """Delete selected files (move to cloud trash)."""
    file_ids = request.form.getlist("file_ids")
    if not file_ids:
        return redirect(url_for("web.results"))

    providers = _get_providers()
    provider_map = {p.provider_name: p for p in providers}

    # Get the scan result to look up file metadata
    scan_id = session.get("scan_id")
    result = _active_scans.get(scan_id, {}).get("result") if scan_id else None

    if not result:
        return redirect(url_for("web.index"))

    # Build lookup of all files
    all_files = {}
    for group in result.exact_groups + result.similar_groups:
        for f in group.files:
            all_files[f.file_id] = f

    deleted = 0
    failed = 0
    space_freed = 0

    for file_id in file_ids:
        cf = all_files.get(file_id)
        if not cf:
            continue
        provider = provider_map.get(cf.provider)
        if provider and provider.delete_file(file_id):
            deleted += 1
            space_freed += cf.size
        else:
            failed += 1

    # Clean up scan data
    if scan_id in _active_scans:
        del _active_scans[scan_id]

    return render_template(
        "deleted.html",
        deleted=deleted,
        failed=failed,
        space_freed=space_freed,
        google_connected=session.get("google_connected", False),
        ms_connected=session.get("ms_connected", False),
    )


@web_bp.route("/thumbnail/<provider>/<file_id>")
def thumbnail(provider, file_id):
    """Proxy route to serve cloud thumbnails to the browser."""
    import tempfile
    import os

    providers = _get_providers()
    provider_map = {p.provider_name: p for p in providers}
    p = provider_map.get(provider)
    if not p:
        return "", 404

    temp_dir = tempfile.mkdtemp(prefix="photocleaner-thumb-")
    try:
        path = p.download_thumbnail(file_id, temp_dir)
        if path and os.path.exists(path):
            with open(path, "rb") as f:
                data = f.read()
            return Response(data, mimetype="image/jpeg")
    except Exception:
        pass
    finally:
        import shutil
        shutil.rmtree(temp_dir, ignore_errors=True)

    return "", 404
