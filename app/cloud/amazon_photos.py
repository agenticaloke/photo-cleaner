import os
import hashlib

import requests

from app.cloud.base import CloudProvider
from app.core.models import CloudFile

API_BASE = "https://drive.amazonaws.com/drive/v1"

IMAGE_MIMES = {
    "image/jpeg", "image/png", "image/heic", "image/heif",
    "image/webp", "image/tiff", "image/bmp", "image/gif",
    "image/x-adobe-dng", "image/raw",
}


class AmazonPhotosProvider(CloudProvider):
    """Amazon Photos / Amazon Drive API wrapper."""

    def __init__(self, access_token):
        self._token = access_token
        self._headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type":  "application/json",
        }
        self._endpoint = None  # resolved lazily

    @property
    def provider_name(self):
        return "amazon_photos"

    def _get_endpoint(self):
        """Resolve the user's regional Drive endpoint (cached per instance)."""
        if self._endpoint:
            return self._endpoint
        try:
            r = requests.get(
                f"{API_BASE}/account/endpoint",
                headers=self._headers,
                timeout=10,
            )
            if r.status_code == 200:
                data = r.json()
                self._endpoint = data.get("contentUrl", API_BASE).rstrip("/")
            else:
                self._endpoint = API_BASE
        except Exception:
            self._endpoint = API_BASE
        return self._endpoint

    def _api(self, method, path, **kwargs):
        base = self._get_endpoint()
        url = f"{base}{path}"
        return requests.request(method, url, headers=self._headers, timeout=30, **kwargs)

    # ── Folder listing ────────────────────────────────────────────────────

    def list_folders(self):
        """List top-level folders in Amazon Drive."""
        return self.list_subfolders("root")

    def list_subfolders(self, folder_id):
        """List immediate child folders of a given node."""
        folders = []
        params = {
            "filters":   "kind:FOLDER AND status:AVAILABLE",
            "asset":     "ALL",
            "tempLink":  "false",
            "offset":    0,
            "limit":     200,
        }
        if folder_id == "root":
            params["filters"] = "kind:FOLDER AND status:AVAILABLE AND isRoot:false AND parents:root"
        else:
            params["filters"] = f"kind:FOLDER AND status:AVAILABLE AND parents:{folder_id}"

        while True:
            try:
                r = self._api("GET", "/nodes", params=params)
                if r.status_code != 200:
                    break
                data = r.json()
                for node in data.get("data", []):
                    folders.append({
                        "id":           node["id"],
                        "name":         node.get("name", ""),
                        "has_children": node.get("childAssetCount", {}).get("FOLDER", 0) > 0,
                    })
                next_token = data.get("nextToken")
                if not next_token:
                    break
                params["startToken"] = next_token
            except Exception:
                break

        folders.sort(key=lambda f: f["name"].lower())
        return folders

    # ── Photo listing ─────────────────────────────────────────────────────

    def list_photos(self, folder_ids=None, progress_callback=None):
        all_files = []
        if folder_ids:
            seen = set()
            for fid in folder_ids:
                self._list_photos_in_subtree(fid, all_files, seen, progress_callback)
        else:
            self._list_all_photos(all_files, progress_callback)
        return all_files

    def _list_all_photos(self, all_files, progress_callback=None):
        params = {
            "filters":  "kind:FILE AND status:AVAILABLE AND contentProperties.contentType:" + self._mime_filter(),
            "asset":    "ALL",
            "tempLink": "false",
            "offset":   0,
            "limit":    200,
        }
        while True:
            try:
                r = self._api("GET", "/nodes", params=params)
                if r.status_code != 200:
                    break
                data = r.json()
                for node in data.get("data", []):
                    cf = self._node_to_cloudfile(node)
                    if cf:
                        all_files.append(cf)
                if progress_callback:
                    progress_callback("listing", len(all_files), len(all_files))
                next_token = data.get("nextToken")
                if not next_token:
                    break
                params["startToken"] = next_token
            except Exception:
                break

    def _list_photos_in_subtree(self, folder_id, all_files, seen, progress_callback=None):
        """Recursively collect photos under a folder."""
        # Recurse into child folders first
        child_params = {
            "filters":  f"kind:FOLDER AND status:AVAILABLE AND parents:{folder_id}",
            "asset":    "ALL",
            "tempLink": "false",
            "limit":    200,
        }
        try:
            r = self._api("GET", "/nodes", params=child_params)
            if r.status_code == 200:
                for node in r.json().get("data", []):
                    self._list_photos_in_subtree(node["id"], all_files, seen, progress_callback)
        except Exception:
            pass

        # Now list photos in this folder
        photo_params = {
            "filters":  f"kind:FILE AND status:AVAILABLE AND parents:{folder_id}",
            "asset":    "ALL",
            "tempLink": "false",
            "limit":    200,
        }
        while True:
            try:
                r = self._api("GET", "/nodes", params=photo_params)
                if r.status_code != 200:
                    break
                data = r.json()
                for node in data.get("data", []):
                    if node["id"] in seen:
                        continue
                    cf = self._node_to_cloudfile(node)
                    if cf:
                        seen.add(node["id"])
                        all_files.append(cf)
                if progress_callback:
                    progress_callback("listing", len(all_files), len(all_files))
                next_token = data.get("nextToken")
                if not next_token:
                    break
                photo_params["startToken"] = next_token
            except Exception:
                break

    def _mime_filter(self):
        """Build an OR mime-type filter string for the API."""
        # Amazon Drive filters don't support OR on contentType, so we use
        # the PHOTOS kind filter which is more reliable
        return "*"

    def _node_to_cloudfile(self, node):
        """Convert an Amazon Drive node dict to a CloudFile."""
        props = node.get("contentProperties", {})
        mime = props.get("contentType", "")
        # Filter to images only
        if mime and mime.split(";")[0].strip() not in IMAGE_MIMES:
            return None
        # Also accept nodes without a mime (kind=PHOTO nodes)
        if mime and not any(mime.startswith(m.split("/")[0]) for m in ("image",)):
            return None

        md5 = props.get("md5", None)
        size = props.get("size", 0)
        created = node.get("createdDate", "")
        modified = node.get("modifiedDate", "")
        name = node.get("name", "")
        node_id = node["id"]

        # Build thumbnail URL — Amazon Drive serves thumbnails via content endpoint
        thumb_url = None
        for var in node.get("contentProperties", {}).get("videoUrls", []):
            pass  # not used
        # Thumbnail via content endpoint with ?download=false&width=220&height=220
        base = self._get_endpoint()
        thumb_url = f"{base}/nodes/{node_id}/content?download=false&width=220&height=220"

        return CloudFile(
            file_id=node_id,
            name=name,
            provider="amazon_photos",
            size=int(size) if size else 0,
            sha256=md5,  # Amazon returns MD5; used for exact-match deduplication
            mime_type=mime,
            created_time=created,
            modified_time=modified,
            thumbnail_url=thumb_url,
        )

    # ── Thumbnails ────────────────────────────────────────────────────────

    def download_thumbnail(self, file_id, temp_dir, thumbnail_url=None):
        """Download a thumbnail for an Amazon Drive file."""
        try:
            base = self._get_endpoint()
            url = thumbnail_url or f"{base}/nodes/{file_id}/content?download=false&width=220&height=220"
            r = requests.get(url, headers=self._headers, timeout=10)
            if r.status_code == 200 and r.content:
                path = os.path.join(temp_dir, f"amazon_{file_id}.jpg")
                with open(path, "wb") as f:
                    f.write(r.content)
                return path
        except Exception:
            pass
        return None

    # ── Deletion ──────────────────────────────────────────────────────────

    def delete_file(self, file_id):
        """Move an Amazon Drive file to trash (recoverable)."""
        try:
            r = self._api("PUT", f"/trash/{file_id}")
            return r.status_code in (200, 201, 204)
        except Exception:
            return False
