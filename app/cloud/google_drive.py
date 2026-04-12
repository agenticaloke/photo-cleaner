import os

import requests as http_requests
from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

from app.cloud.base import CloudProvider
from app.core.models import CloudFile

IMAGE_MIMES = (
    "image/jpeg", "image/png", "image/heic", "image/heif",
    "image/webp", "image/tiff", "image/bmp", "image/gif",
)


class GoogleDriveProvider(CloudProvider):
    """Google Drive API wrapper for listing, thumbnailing, and trashing photos."""

    def __init__(self, credentials_dict):
        """Initialize with OAuth credentials dict from session."""
        creds = Credentials(
            token=credentials_dict["token"],
            refresh_token=credentials_dict.get("refresh_token"),
            token_uri=credentials_dict.get("token_uri", "https://oauth2.googleapis.com/token"),
            client_id=credentials_dict.get("client_id"),
            client_secret=credentials_dict.get("client_secret"),
        )
        self.service = build("drive", "v3", credentials=creds)
        self._token = credentials_dict["token"]

    @property
    def provider_name(self):
        return "google_drive"

    def list_photos(self, folder_path=None, progress_callback=None):
        """List all image files in Google Drive with metadata and SHA-256 hashes."""
        query_parts = []
        for mime in IMAGE_MIMES:
            query_parts.append(f"mimeType='{mime}'")
        query = "(" + " or ".join(query_parts) + ") and trashed=false"

        fields = (
            "nextPageToken, files(id, name, mimeType, size, sha256Checksum, "
            "md5Checksum, thumbnailLink, createdTime, modifiedTime, parents)"
        )

        all_files = []
        page_token = None
        batch_count = 0

        while True:
            # Fetch up to 1000 per API call, accumulate up to 5000 per batch
            response = self.service.files().list(
                q=query,
                fields=fields,
                pageSize=1000,
                pageToken=page_token,
            ).execute()

            for item in response.get("files", []):
                cf = CloudFile(
                    file_id=item["id"],
                    name=item.get("name", ""),
                    provider="google_drive",
                    size=int(item.get("size", 0)),
                    sha256=item.get("sha256Checksum"),
                    mime_type=item.get("mimeType", ""),
                    created_time=item.get("createdTime", ""),
                    modified_time=item.get("modifiedTime", ""),
                    thumbnail_url=item.get("thumbnailLink"),
                )
                all_files.append(cf)
                batch_count += 1

            if progress_callback:
                progress_callback("listing", len(all_files), len(all_files))

            page_token = response.get("nextPageToken")
            if not page_token:
                break

            # Yield control every 5000 files to keep progress responsive
            if batch_count >= 5000:
                batch_count = 0

        return all_files

    def download_thumbnail(self, file_id, temp_dir, thumbnail_url=None):
        """Download thumbnail for a file. Returns local path or None.

        If thumbnail_url is provided (cached from listing), uses it directly.
        Otherwise falls back to fetching metadata (slower, costs an API call).
        """
        try:
            thumb_url = thumbnail_url
            if not thumb_url:
                # Fallback: fetch metadata to get thumbnailLink
                file_meta = self.service.files().get(
                    fileId=file_id, fields="thumbnailLink"
                ).execute()
                thumb_url = file_meta.get("thumbnailLink")

            if not thumb_url:
                return None

            # thumbnailLink already includes auth for Google-hosted thumbnails
            resp = http_requests.get(thumb_url, timeout=5)
            if resp.status_code == 200:
                path = os.path.join(temp_dir, f"gdrive_{file_id}.jpg")
                with open(path, "wb") as f:
                    f.write(resp.content)
                return path
        except Exception:
            pass
        return None

    def delete_file(self, file_id):
        """Move file to Google Drive trash (recoverable)."""
        try:
            self.service.files().update(
                fileId=file_id, body={"trashed": True}
            ).execute()
            return True
        except Exception:
            return False
