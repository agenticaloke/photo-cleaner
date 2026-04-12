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

    def list_folders(self):
        """List folder tree from Google Drive (2 levels deep)."""
        def get_children(parent_id, depth=0):
            if depth >= 2:
                return []
            folders = []
            query = (
                f"'{parent_id}' in parents and "
                "mimeType='application/vnd.google-apps.folder' and trashed=false"
            )
            page_token = None
            while True:
                resp = self.service.files().list(
                    q=query,
                    fields="nextPageToken, files(id, name)",
                    pageSize=100,
                    pageToken=page_token,
                ).execute()
                for item in resp.get("files", []):
                    folders.append({
                        "id": item["id"],
                        "name": item["name"],
                        "children": get_children(item["id"], depth + 1),
                    })
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break
            folders.sort(key=lambda f: f["name"].lower())
            return folders

        return get_children("root", depth=0)

    def list_photos(self, folder_ids=None, progress_callback=None):
        """List image files in Google Drive.

        If folder_ids is provided, only lists photos in those folders
        and their subfolders. Otherwise lists all photos.
        """
        if folder_ids:
            return self._list_photos_in_folders(folder_ids, progress_callback)
        return self._list_all_photos(progress_callback)

    def _list_all_photos(self, progress_callback=None):
        """List all image files across the entire Drive."""
        query_parts = [f"mimeType='{m}'" for m in IMAGE_MIMES]
        query = "(" + " or ".join(query_parts) + ") and trashed=false"

        fields = (
            "nextPageToken, files(id, name, mimeType, size, sha256Checksum, "
            "md5Checksum, thumbnailLink, createdTime, modifiedTime, parents)"
        )

        all_files = []
        page_token = None

        while True:
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

            if progress_callback:
                progress_callback("listing", len(all_files), len(all_files))

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return all_files

    def _list_photos_in_folders(self, folder_ids, progress_callback=None):
        """List image files only in the specified folders and their subfolders."""
        all_files = []

        # Collect all folder IDs including subfolders
        all_folder_ids = set()
        folders_to_scan = list(folder_ids)

        while folders_to_scan:
            fid = folders_to_scan.pop()
            if fid in all_folder_ids:
                continue
            all_folder_ids.add(fid)

            # Find subfolders
            query = (
                f"'{fid}' in parents and "
                "mimeType='application/vnd.google-apps.folder' and trashed=false"
            )
            page_token = None
            while True:
                resp = self.service.files().list(
                    q=query,
                    fields="nextPageToken, files(id)",
                    pageSize=1000,
                    pageToken=page_token,
                ).execute()
                for item in resp.get("files", []):
                    folders_to_scan.append(item["id"])
                page_token = resp.get("nextPageToken")
                if not page_token:
                    break

        # Now list photos in all collected folders (deduplicate by file_id
        # since Google Drive files can have multiple parents)
        seen_ids = set()
        for fid in all_folder_ids:
            query_parts = [f"mimeType='{m}'" for m in IMAGE_MIMES]
            query = (
                "(" + " or ".join(query_parts) + ") and "
                f"'{fid}' in parents and trashed=false"
            )
            fields = (
                "nextPageToken, files(id, name, mimeType, size, sha256Checksum, "
                "md5Checksum, thumbnailLink, createdTime, modifiedTime, parents)"
            )
            page_token = None
            while True:
                resp = self.service.files().list(
                    q=query,
                    fields=fields,
                    pageSize=1000,
                    pageToken=page_token,
                ).execute()
                for item in resp.get("files", []):
                    if item["id"] in seen_ids:
                        continue
                    seen_ids.add(item["id"])
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

                if progress_callback:
                    progress_callback("listing", len(all_files), len(all_files))

                page_token = resp.get("nextPageToken")
                if not page_token:
                    break

        return all_files

    def download_thumbnail(self, file_id, temp_dir, thumbnail_url=None):
        """Download thumbnail for a file. Returns local path or None."""
        try:
            thumb_url = thumbnail_url
            if not thumb_url:
                file_meta = self.service.files().get(
                    fileId=file_id, fields="thumbnailLink"
                ).execute()
                thumb_url = file_meta.get("thumbnailLink")

            if not thumb_url:
                return None

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
