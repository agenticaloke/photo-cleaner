import json
import time


def generate_progress_events(progress_dict):
    """Generator that yields SSE-formatted events from a shared progress dict.

    The progress_dict should be updated by a background thread with keys:
        stage: str (e.g. "listing", "hashing", "exact_matching")
        current: int
        total: int
        done: bool
        error: str or None
    """
    while True:
        data = json.dumps({
            "stage": progress_dict.get("stage", "starting"),
            "current": progress_dict.get("current", 0),
            "total": progress_dict.get("total", 0),
            "done": progress_dict.get("done", False),
            "error": progress_dict.get("error"),
        })
        yield f"data: {data}\n\n"

        if progress_dict.get("done") or progress_dict.get("error"):
            break

        time.sleep(0.5)
