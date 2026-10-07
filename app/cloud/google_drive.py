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
        self.service = build("drive", "v3", credentials=creds, cache_discovery=False)
        self._token = credentials_dict["token"]

    @property
    def provider_name(self):
        return "google_drive"

    def list_folders(self):
        """List top-level folders only (no recursive expansion).

        Children are loaded on-demand via list_subfolders() when the user
        expands a folder in the UI.
        """
        return self.list_subfolders("root")

    def list_subfolders(self, folder_id):
        """List immediate subfolders of a given folder (one level only)."""
        folders = []
        query = (
            f"'{folder_id}' in parents and "
            "mimeType='application/vnd.google-apps.folder' and trashed=false"
        )
        page_token = None
        while True:
            try:
                resp = self.service.files().list(
                    q=query,
                    fields="nextPageToken, files(id, name)",
                    pageSize=200,
                    pageToken=page_token,
                ).execute()
            except Exception:
                break
            for item in resp.get("files", []):
                folders.append({
                    "id": item["id"],
                    "name": item["name"],
                    "has_children": True,  # We don't know without a query; assume yes
                })
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        folders.sort(key=lambda f: f["name"].lower())
        return folders

    def _resolve_parent_names(self, parent_ids):
        """Return {folder_id: folder_name} for a set of Drive IDs (cached)."""
        result = {}
        for fid in parent_ids:
            if fid == "root":
                result[fid] = ""
                continue
            try:
                meta = self.service.files().get(
                    fileId=fid, fields="name"
                ).execute()
                result[fid] = meta.get("name", "")
            except Exception:
                result[fid] = ""
        return result

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
                parents = item.get("parents", [])
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
                    folder_path=parents[0] if parents else "",  # resolved below
                )
                all_files.append(cf)

            if progress_callback:
                progress_callback("listing", len(all_files), len(all_files))

            page_token = response.get("nextPageToken")
            if not page_token:
                break

        # Resolve parent IDs → folder names in one pass
        parent_ids = {f.folder_path for f in all_files if f.folder_path}
        name_map = self._resolve_parent_names(parent_ids)
        for cf in all_files:
            cf.folder_path = name_map.get(cf.folder_path, "")

        return all_files

    def _list_photos_in_folders(self, folder_ids, progress_callback=None):
        """List image files only in the specified folders and their subfolders."""
        all_files = []

        # Pre-resolve selected folder IDs → names for path display
        folder_name_map = self._resolve_parent_names(set(folder_ids))

        # Collect all folder IDs including subfolders; track each folder's parent
        all_folder_ids = set()
        # Map: folder_id → display path (from the selected root downward)
        folder_paths = {fid: folder_name_map.get(fid, fid) for fid in folder_ids}
        folders_to_scan = list(folder_ids)

        while folders_to_scan:
            fid = folders_to_scan.pop()
            if fid in all_folder_ids:
                continue
            all_folder_ids.add(fid)
            parent_path = folder_paths.get(fid, "")

            # Find subfolders and inherit path
            query = (
                f"'{fid}' in parents and "
                "mimeType='application/vnd.google-apps.folder' and trashed=false"
            )
            page_token = None
            while True:
                resp = self.service.files().list(
                    q=query,
                    fields="nextPageToken, files(id, name)",
                    pageSize=1000,
                    pageToken=page_token,
                ).execute()
                for item in resp.get("files", []):
                    child_id = item["id"]
                    child_name = item.get("name", "")
                    folder_paths[child_id] = (
                        f"{parent_path}/{child_name}" if parent_path else child_name
                    )
                    folders_to_scan.append(child_id)
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
                        folder_path=folder_paths.get(fid, ""),
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

            # thumbnailLink requires the OAuth token when fetched server-side
            headers = {"Authorization": f"Bearer {self._token}"}
            resp = http_requests.get(thumb_url, headers=headers, timeout=5)
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
