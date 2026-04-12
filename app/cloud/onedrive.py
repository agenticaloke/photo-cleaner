import os

import requests

from app.cloud.base import CloudProvider
from app.core.models import CloudFile

GRAPH_BASE = "https://graph.microsoft.com/v1.0"

IMAGE_EXTENSIONS = {
    ".jpg", ".jpeg", ".png", ".heic", ".heif",
    ".webp", ".tiff", ".bmp", ".gif",
}


class OneDriveProvider(CloudProvider):
    """OneDrive (Microsoft Graph) API wrapper for photos."""

    def __init__(self, access_token):
        """Initialize with an access token from MSAL."""
        self._token = access_token
        self._headers = {"Authorization": f"Bearer {access_token}"}

    @property
    def provider_name(self):
        return "onedrive"

    def list_photos(self, folder_path=None, progress_callback=None):
        """List all image files in OneDrive by walking the entire folder tree."""
        all_files = []

        # Use a stack for iterative tree traversal (avoids recursion limits)
        # Each entry is (folder_item_path, display_path)
        folders_to_scan = [("/me/drive/root", "")]

        while folders_to_scan:
            folder_api_path, display_path = folders_to_scan.pop()
            url = f"{GRAPH_BASE}{folder_api_path}/children"
            params = {
                "$select": "id,name,file,folder,size,createdDateTime,lastModifiedDateTime,parentReference",
                "$expand": "thumbnails",
                "$top": 200,
            }

            while url:
                resp = requests.get(url, headers=self._headers, params=params)
                if resp.status_code != 200:
                    break

                data = resp.json()
                for item in data.get("value", []):
                    # If it's a folder, add to scan queue
                    if "folder" in item:
                        item_id = item["id"]
                        child_path = f"{display_path}/{item['name']}"
                        folders_to_scan.append(
                            (f"/me/drive/items/{item_id}", child_path)
                        )
                        continue

                    # Only include files that are images
                    if "file" not in item:
                        continue

                    name = item.get("name", "")
                    ext = os.path.splitext(name)[1].lower()
                    mime = item.get("file", {}).get("mimeType", "")

                    if ext not in IMAGE_EXTENSIONS and not mime.startswith("image/"):
                        continue

                    # Extract hashes from the file facet
                    hashes = item.get("file", {}).get("hashes", {})
                    sha256 = hashes.get("sha256Hash")
                    if not sha256:
                        sha256 = hashes.get("sha1Hash")  # Fallback

                    # Extract thumbnail URL from expanded thumbnails
                    thumb_url = None
                    thumbnails = item.get("thumbnails", [])
                    if thumbnails:
                        thumb_url = thumbnails[0].get("medium", {}).get("url")
                        if not thumb_url:
                            thumb_url = thumbnails[0].get("small", {}).get("url")

                    cf = CloudFile(
                        file_id=item["id"],
                        name=name,
                        provider="onedrive",
                        size=int(item.get("size", 0)),
                        sha256=sha256,
                        mime_type=mime,
                        created_time=item.get("createdDateTime", ""),
                        modified_time=item.get("lastModifiedDateTime", ""),
                        thumbnail_url=thumb_url,
                        folder_path=display_path,
                    )
                    all_files.append(cf)

                if progress_callback:
                    progress_callback("listing", len(all_files), len(all_files))

                # Pagination within this folder
                url = data.get("@odata.nextLink")
                params = {}  # nextLink includes params already

        return all_files

    def download_thumbnail(self, file_id, temp_dir, thumbnail_url=None):
        """Download a medium-sized thumbnail. Returns local path or None.

        If thumbnail_url is provided (cached from listing), uses it directly
        to avoid an extra API call.
        """
        try:
            if thumbnail_url:
                # Cached URL from listing — no auth header needed, URL has token
                resp = requests.get(thumbnail_url, timeout=5)
            else:
                # Fallback: fetch via Graph API
                url = f"{GRAPH_BASE}/me/drive/items/{file_id}/thumbnails/0/medium/content"
                resp = requests.get(url, headers=self._headers, timeout=5)

            if resp.status_code == 200:
                path = os.path.join(temp_dir, f"od_{file_id}.jpg")
                with open(path, "wb") as f:
                    f.write(resp.content)
                return path
        except Exception:
            pass
        return None

    def delete_file(self, file_id):
        """Delete file (moves to OneDrive recycle bin, recoverable)."""
        try:
            url = f"{GRAPH_BASE}/me/drive/items/{file_id}"
            resp = requests.delete(url, headers=self._headers)
            return resp.status_code in (200, 204)
        except Exception:
            return False
