import json
import logging
import os
import threading
import tempfile
import shutil
import traceback

from flask import (
    Blueprint, render_template, redirect, url_for, session,
    request, Response, jsonify, current_app,
)

from app.cloud.google_drive import GoogleDriveProvider
from app.cloud.onedrive import OneDriveProvider
from app.core.grouper import scan_for_duplicates
from app.core.models import ScanResult

web_bp = Blueprint("web", __name__)

logger = logging.getLogger("photocleaner")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Store scan results in memory (keyed by scan_id)
_scan_results = {}

# Directory for scan progress files — hardcode /tmp to avoid tempfile.gettempdir() inconsistencies
PROGRESS_DIR = "/tmp/photocleaner-progress"
os.makedirs(PROGRESS_DIR, exist_ok=True)
logger.info(f"PROGRESS_DIR initialized: {PROGRESS_DIR} (exists={os.path.exists(PROGRESS_DIR)})")


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


def _write_progress(scan_id, stage="starting", current=0, total=0, done=False, error=None, debug_log=None):
    """Write scan progress to a file in /tmp."""
    data = {
        "stage": stage,
        "current": current,
        "total": total,
        "done": done,
        "error": error,
        "debug_log": debug_log,
    }
    os.makedirs(PROGRESS_DIR, exist_ok=True)  # Ensure dir exists on every write
    path = os.path.join(PROGRESS_DIR, f"{scan_id}.json")
    try:
        with open(path, "w") as f:
            json.dump(data, f)
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        logger.error(f"Failed to write progress file {path}: {e}")


def _read_progress(scan_id):
    """Read scan progress from file."""
    path = os.path.join(PROGRESS_DIR, f"{scan_id}.json")
    try:
        with open(path, "r") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        logger.warning(f"Failed to read progress for {scan_id}: {e}")
        return None


def _append_debug(scan_id, message):
    """Append a debug message to the scan's progress file."""
    progress = _read_progress(scan_id)
    if progress:
        log = progress.get("debug_log") or []
        log.append(message)
        # Keep only last 50 messages
        progress["debug_log"] = log[-50:]
        path = os.path.join(PROGRESS_DIR, f"{scan_id}.json")
        try:
            with open(path, "w") as f:
                json.dump(progress, f)
        except Exception:
            pass
    logger.info(f"[{scan_id[:8]}] {message}")


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
    mode = request.args.get("mode", "basic")
    threshold = request.args.get("threshold", 10, type=int)
    return render_template(
        "scanning.html",
        debug_mode=current_app.config.get("DEBUG_MODE", False),
        scan_mode=mode,
        threshold=threshold,
    )


@web_bp.route("/scan/start", methods=["POST"])
def scan_start():
    """Kick off the scan in a background thread and return immediately."""
    logger.info("=== /scan/start called ===")

    providers = _get_providers()
    logger.info(f"Providers found: {len(providers)}")
    for p in providers:
        logger.info(f"  - {p.provider_name}")

    if not providers:
        logger.warning("No providers connected, returning 400")
        return jsonify({"error": "No cloud accounts connected"}), 400

    threshold = request.form.get("threshold", 10, type=int)
    scan_mode = request.form.get("mode", "basic")
    logger.info(f"Scan mode: {scan_mode}, threshold: {threshold}")

    import secrets
    scan_id = secrets.token_urlsafe(16)
    session["scan_id"] = scan_id
    logger.info(f"Created scan_id: {scan_id}")

    # Write initial progress file
    _write_progress(scan_id, stage="starting", debug_log=["Scan created"])

    # Verify progress file was written
    expected_path = os.path.join(PROGRESS_DIR, f"{scan_id}.json")
    file_exists = os.path.exists(expected_path)
    verify = _read_progress(scan_id)
    logger.info(f"Progress file verification: file_exists={file_exists}, readable={'OK' if verify else 'FAILED'}")
    logger.info(f"  path={expected_path}")
    logger.info(f"  dir_exists={os.path.exists(PROGRESS_DIR)}, "
                f"dir_contents={os.listdir(PROGRESS_DIR) if os.path.exists(PROGRESS_DIR) else 'N/A'}")

    def run_scan():
        _append_debug(scan_id, f"Background thread started, {len(providers)} providers")

        def progress_callback(stage, current, total):
            _write_progress(scan_id, stage=stage, current=current, total=total)

        try:
            _append_debug(scan_id, f"Calling scan_for_duplicates (mode={scan_mode})...")
            result = scan_for_duplicates(providers, threshold, progress_callback, mode=scan_mode)
            _scan_results[scan_id] = result
            _append_debug(scan_id, f"Scan complete: {result.total_photos} photos, "
                          f"{len(result.exact_groups)} exact groups, "
                          f"{len(result.similar_groups)} similar groups")
            _write_progress(scan_id, stage="done", current=100, total=100, done=True)
        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            tb = traceback.format_exc()
            logger.error(f"Scan failed: {error_msg}\n{tb}")
            _append_debug(scan_id, f"ERROR: {error_msg}")
            _write_progress(scan_id, error=error_msg)

    thread = threading.Thread(target=run_scan, daemon=True)
    thread.start()
    logger.info(f"Background thread started for scan {scan_id}")

    return jsonify({"scan_id": scan_id})


@web_bp.route("/scan/progress")
def scan_progress():
    """Polling endpoint that returns current scan progress as JSON."""
    scan_id = request.args.get("scan_id") or session.get("scan_id")

    if not scan_id:
        logger.warning("/scan/progress called with no scan_id (not in args or session)")
        return jsonify({
            "error": "No active scan",
            "debug": "scan_id missing from both URL params and session",
        })

    progress = _read_progress(scan_id)
    if not progress:
        logger.warning(f"/scan/progress: no progress file for scan_id={scan_id}")
        # Check what files exist
        existing = os.listdir(PROGRESS_DIR) if os.path.exists(PROGRESS_DIR) else []
        return jsonify({
            "error": "No active scan",
            "debug": f"scan_id={scan_id}, progress_dir_exists={os.path.exists(PROGRESS_DIR)}, "
                     f"files_in_dir={existing[:10]}",
        })

    return jsonify(progress)


@web_bp.route("/scan/debug")
def scan_debug():
    """Debug endpoint showing all scan state."""
    scan_id = request.args.get("scan_id") or session.get("scan_id")
    existing_files = os.listdir(PROGRESS_DIR) if os.path.exists(PROGRESS_DIR) else []
    progress = _read_progress(scan_id) if scan_id else None

    # Check if the expected file exists directly
    expected_file = os.path.join(PROGRESS_DIR, f"{scan_id}.json") if scan_id else None

    return jsonify({
        "scan_id_from_args": request.args.get("scan_id"),
        "scan_id_from_session": session.get("scan_id"),
        "progress_dir": PROGRESS_DIR,
        "tempfile_gettempdir": tempfile.gettempdir(),
        "progress_dir_exists": os.path.exists(PROGRESS_DIR),
        "expected_file": expected_file,
        "expected_file_exists": os.path.exists(expected_file) if expected_file else None,
        "files_in_progress_dir": existing_files[:20],
        "progress_data": progress,
        "scan_results_keys": list(_scan_results.keys())[:10],
        "session_keys": list(session.keys()),
        "google_connected": session.get("google_connected", False),
    })


@web_bp.route("/results")
def results():
    """Show duplicate groups found by the scan."""
    scan_id = request.args.get("scan_id") or session.get("scan_id")
    if not scan_id or scan_id not in _scan_results:
        return redirect(url_for("web.index"))

    result = _scan_results[scan_id]

    return render_template(
        "results.html",
        result=result,
        scan_id=scan_id,
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

    scan_id = request.form.get("scan_id") or session.get("scan_id")
    result = _scan_results.get(scan_id) if scan_id else None

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

    # Clean up
    if scan_id in _scan_results:
        del _scan_results[scan_id]
    progress_file = os.path.join(PROGRESS_DIR, f"{scan_id}.json")
    if os.path.exists(progress_file):
        os.remove(progress_file)

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
        shutil.rmtree(temp_dir, ignore_errors=True)

    return "", 404
