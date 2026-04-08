import os

import msal
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
        """List all image files in OneDrive with metadata and hashes."""
        # Search for image files across the entire drive
        all_files = []
        # Use search to find image files, or iterate the drive
        url = f"{GRAPH_BASE}/me/drive/root/search(q='')"
        params = {
            "$select": "id,name,file,size,createdDateTime,lastModifiedDateTime,parentReference,photo",
            "$top": 200,
        }

        while url:
            resp = requests.get(url, headers=self._headers, params=params)
            if resp.status_code != 200:
                break

            data = resp.json()
            for item in data.get("value", []):
                # Only include files (not folders) that look like images
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

                parent = item.get("parentReference", {})
                folder = parent.get("path", "").replace("/drive/root:", "", 1)

                cf = CloudFile(
                    file_id=item["id"],
                    name=name,
                    provider="onedrive",
                    size=int(item.get("size", 0)),
                    sha256=sha256,
                    mime_type=mime,
                    created_time=item.get("createdDateTime", ""),
                    modified_time=item.get("lastModifiedDateTime", ""),
                    folder_path=folder,
                )
                all_files.append(cf)

            if progress_callback:
                progress_callback("listing", len(all_files), len(all_files))

            # Pagination
            url = data.get("@odata.nextLink")
            params = {}  # nextLink includes params already

        return all_files

    def download_thumbnail(self, file_id, temp_dir):
        """Download a medium-sized thumbnail. Returns local path or None."""
        try:
            url = f"{GRAPH_BASE}/me/drive/items/{file_id}/thumbnails/0/medium/content"
            resp = requests.get(url, headers=self._headers, timeout=30)
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
