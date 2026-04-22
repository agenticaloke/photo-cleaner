import json
import logging
import os
import queue
import threading
import tempfile
import shutil
import traceback
import time

from flask import (
    Blueprint, render_template, redirect, url_for, session,
    request, Response, jsonify, current_app, stream_with_context,
)

from app.cloud.google_drive import GoogleDriveProvider
from app.cloud.onedrive import OneDriveProvider
from app.core.grouper import scan_for_duplicates
from app.core.models import ScanResult

web_bp = Blueprint("web", __name__)

logger = logging.getLogger("photocleaner")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# Process ID — used to detect worker restarts
_WORKER_PID = os.getpid()
logger.info(f"Routes module loaded in PID {_WORKER_PID}")

# Thread-safe in-memory storage for scan progress and results
_lock = threading.Lock()
_scan_progress = {}   # scan_id -> {stage, current, total, done, error, debug_log}
_scan_results = {}    # scan_id -> ScanResult


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
    """Write scan progress to in-memory dict (thread-safe)."""
    with _lock:
        existing = _scan_progress.get(scan_id, {})
        if debug_log is None:
            debug_log = existing.get("debug_log", [])
        _scan_progress[scan_id] = {
            "stage": stage,
            "current": current,
            "total": total,
            "done": done,
            "error": error,
            "debug_log": debug_log,
            "pid": os.getpid(),
        }


def _read_progress(scan_id):
    """Read scan progress from in-memory dict (thread-safe)."""
    with _lock:
        return _scan_progress.get(scan_id, {}).copy() if scan_id in _scan_progress else None


def _append_debug(scan_id, message):
    """Append a debug message to the scan's progress (thread-safe)."""
    with _lock:
        if scan_id in _scan_progress:
            log = _scan_progress[scan_id].get("debug_log") or []
            log.append(message)
            _scan_progress[scan_id]["debug_log"] = log[-50:]
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


@web_bp.route("/folders/debug")
def folders_debug():
    """Debug: show raw Graph API response for OneDrive root children."""
    import requests as req
    token = session.get("ms_token")
    if not token:
        return jsonify({"error": "Not connected to OneDrive"})

    headers = {"Authorization": f"Bearer {token}"}
    url = "https://graph.microsoft.com/v1.0/me/drive/root/children"
    params = {"$select": "id,name,folder,file", "$top": 10}
    try:
        resp = req.get(url, headers=headers, params=params, timeout=10)
        return jsonify({
            "status_code": resp.status_code,
            "response": resp.json(),
        })
    except Exception as e:
        return jsonify({"error": str(e)})


@web_bp.route("/folders")
def folders():
    """Show folder picker so users can choose which folders to scan."""
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

    providers = _get_providers()
    if not providers:
        return redirect(url_for("web.index"))

    folder_tree = {}
    for p in providers:
        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(p.list_folders)
                result = future.result(timeout=15)
                logger.info(f"list_folders for {p.provider_name}: {len(result)} folders")
                folder_tree[p.provider_name] = result
        except FuturesTimeout:
            logger.error(f"list_folders timed out for {p.provider_name}")
            folder_tree[p.provider_name] = []
        except Exception as e:
            logger.error(f"Failed to list folders for {p.provider_name}: {e}", exc_info=True)
            folder_tree[p.provider_name] = []

    return render_template(
        "folders.html",
        folder_tree=folder_tree,
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
    # Folder IDs come as comma-separated string from the folder picker
    folder_ids_str = request.args.get("folders", "")
    return render_template(
        "scanning.html",
        debug_mode=current_app.config.get("DEBUG_MODE", False),
        scan_mode=mode,
        threshold=threshold,
        folder_ids=folder_ids_str,
    )


@web_bp.route("/scan/start", methods=["POST"])
def scan_start():
    """Run the scan and stream progress as newline-delimited JSON.

    Uses a background thread for the actual scan work and a thread-safe
    queue to pass progress updates back to the streaming response generator.
    This keeps the HTTP connection alive with regular progress chunks.
    """
    logger.info(f"=== /scan/start called (PID {os.getpid()}) ===")

    providers = _get_providers()
    logger.info(f"Providers found: {len(providers)}")

    if not providers:
        return jsonify({"error": "No cloud accounts connected"}), 400

    threshold = request.form.get("threshold", 10, type=int)
    scan_mode = request.form.get("mode", "basic")
    folder_ids_str = request.form.get("folders", "")
    folder_ids = [f.strip() for f in folder_ids_str.split(",") if f.strip()] or None
    logger.info(f"Scan mode: {scan_mode}, threshold: {threshold}, folders: {len(folder_ids) if folder_ids else 'all'}")

    import secrets
    scan_id = secrets.token_urlsafe(16)
    session["scan_id"] = scan_id

    # Thread-safe queue: scan thread pushes progress, generator yields it
    progress_queue = queue.Queue()

    def run_scan():
        """Background thread that runs the scan and pushes progress to queue."""
        debug_log = [f"Scan started (PID {os.getpid()}, mode={scan_mode})"]

        def push(stage="starting", current=0, total=0, done=False, error=None):
            progress_queue.put({
                "scan_id": scan_id,
                "stage": stage,
                "current": current,
                "total": total,
                "done": done,
                "error": error,
                "debug_log": list(debug_log[-20:]),
            })

        last_push = [0]
        last_stage = [None]

        def progress_callback(stage, current, total):
            now = time.time()
            stage_changed = stage != last_stage[0]
            # Always push on stage change; rate-limit within same stage to 0.5s
            if stage_changed or now - last_push[0] >= 0.5:
                if stage_changed:
                    debug_log.append(f"Stage: {stage} (total={total})")
                last_push[0] = now
                last_stage[0] = stage
                push(stage, current, total)

        try:
            debug_log.append(f"Calling scan_for_duplicates (folders={'selected' if folder_ids else 'all'})...")
            push("starting")
            result = scan_for_duplicates(
                providers, threshold, progress_callback,
                mode=scan_mode, folder_ids=folder_ids,
            )
            _scan_results[scan_id] = result
            debug_log.append(f"Complete: {result.total_photos} photos, "
                           f"{len(result.exact_groups)} exact, "
                           f"{len(result.similar_groups)} similar")
            logger.info(f"Scan {scan_id[:8]} complete")
            push("done", done=True)
        except Exception as e:
            error_msg = f"{type(e).__name__}: {str(e)}"
            tb = traceback.format_exc()
            logger.error(f"Scan failed: {error_msg}\n{tb}")
            debug_log.append(f"ERROR: {error_msg}")
            debug_log.append(f"TB: {tb[-300:]}")
            push(error=error_msg)

        # Signal that scan thread is done
        progress_queue.put(None)

    def generate():
        """Generator that yields queued progress updates as NDJSON lines."""
        # Start scan in background thread
        thread = threading.Thread(target=run_scan, daemon=True)
        thread.start()

        while True:
            try:
                # Wait up to 2 seconds for a progress update
                msg = progress_queue.get(timeout=2)
            except queue.Empty:
                # Send a keepalive to prevent proxy timeout
                yield json.dumps({"keepalive": True, "scan_id": scan_id}) + "\n"
                continue

            if msg is None:
                # Scan thread finished
                break

            yield json.dumps(msg) + "\n"

            if msg.get("done") or msg.get("error"):
                break

    return Response(
        stream_with_context(generate()),
        mimetype="application/x-ndjson",
    )


@web_bp.route("/scan/progress")
def scan_progress():
    """Polling endpoint — kept as fallback but primary flow is now streaming."""
    scan_id = request.args.get("scan_id") or session.get("scan_id")

    if not scan_id:
        return jsonify({
            "error": "No active scan",
            "debug": "scan_id missing from both URL params and session",
            "pid": os.getpid(),
        })

    progress = _read_progress(scan_id)
    if not progress:
        with _lock:
            known_ids = list(_scan_progress.keys())
        return jsonify({
            "error": "No active scan",
            "debug": f"scan_id={scan_id}, known_scans={known_ids[:5]}, pid={os.getpid()}",
        })

    return jsonify(progress)


@web_bp.route("/scan/debug")
def scan_debug():
    """Debug endpoint showing all scan state."""
    scan_id = request.args.get("scan_id") or session.get("scan_id")
    progress = _read_progress(scan_id) if scan_id else None

    with _lock:
        known_scan_ids = list(_scan_progress.keys())

    return jsonify({
        "scan_id_from_args": request.args.get("scan_id"),
        "scan_id_from_session": session.get("scan_id"),
        "current_pid": os.getpid(),
        "module_load_pid": _WORKER_PID,
        "worker_restarted": os.getpid() != _WORKER_PID,
        "storage": "in-memory (thread-safe dict)",
        "known_scan_ids": known_scan_ids[:20],
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


@web_bp.route("/delete", methods=["GET", "POST"])
def delete():
    """Delete selected files (move to cloud trash)."""
    logger.info(f"=== /delete called, method={request.method} ===")

    if request.method == "GET":
        # Redirected from POST (trailing slash, proxy, etc.) — can't delete via GET
        logger.warning("DELETE called with GET — likely a redirect from POST")
        return redirect(url_for("web.index"))

    file_ids = request.form.getlist("file_ids")
    logger.info(f"Delete requested for {len(file_ids)} files")
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
    with _lock:
        _scan_progress.pop(scan_id, None)

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
