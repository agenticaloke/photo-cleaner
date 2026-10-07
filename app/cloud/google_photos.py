import hashlib
import os

import requests

from app.cloud.base import CloudProvider
from app.core.models import CloudFile

PHOTOS_BASE = "https://photoslibrary.googleapis.com/v1"


class GooglePhotosProvider(CloudProvider):
    """Google Photos Library API wrapper."""

    def __init__(self, access_token):
        self._token = access_token
        self._headers = {"Authorization": f"Bearer {access_token}"}

    @property
    def provider_name(self):
        return "google_photos"

    def list_folders(self):
        """List Google Photos albums as top-level folders."""
        albums = []
        params = {"pageSize": 50}
        while True:
            try:
                resp = requests.get(
                    f"{PHOTOS_BASE}/albums",
                    headers=self._headers, params=params, timeout=10,
                )
                if resp.status_code != 200:
                    break
                data = resp.json()
            except Exception:
                break
            for album in data.get("albums", []):
                albums.append({
                    "id": album["id"],
                    "name": album.get("title", "Untitled Album"),
                    "has_children": False,
                })
            next_page = data.get("nextPageToken")
            if not next_page:
                break
            params = {"pageSize": 50, "pageToken": next_page}
        albums.sort(key=lambda a: a["name"].lower())
        return albums

    def list_subfolders(self, folder_id):
        """Albums have no subfolders."""
        return []

    def list_photos(self, folder_ids=None, progress_callback=None):
        """List photos from Google Photos (all or from specific albums)."""
        all_files = []
        if folder_ids:
            # Resolve album IDs → names for path display
            album_names = self._get_album_names(folder_ids)
            for album_id in folder_ids:
                album_name = album_names.get(album_id, "")
                self._list_album_photos(album_id, album_name, all_files, progress_callback)
        else:
            self._list_all_photos(all_files, progress_callback)
        return all_files

    def _get_album_names(self, album_ids):
        """Return {album_id: title} for a list of album IDs."""
        names = {}
        for album_id in album_ids:
            try:
                resp = requests.get(
                    f"{PHOTOS_BASE}/albums/{album_id}",
                    headers=self._headers, timeout=10,
                )
                if resp.status_code == 200:
                    names[album_id] = resp.json().get("title", "")
            except Exception:
                names[album_id] = ""
        return names

    def _list_all_photos(self, all_files, progress_callback=None):
        params = {"pageSize": 100}
        while True:
            try:
                resp = requests.get(
                    f"{PHOTOS_BASE}/mediaItems",
                    headers=self._headers, params=params, timeout=15,
                )
                if resp.status_code != 200:
                    break
                data = resp.json()
            except Exception:
                break
            for item in data.get("mediaItems", []):
                cf = self._item_to_cloudfile(item)
                if cf:
                    all_files.append(cf)
            if progress_callback:
                progress_callback("listing", len(all_files), len(all_files))
            next_page = data.get("nextPageToken")
            if not next_page:
                break
            params = {"pageSize": 100, "pageToken": next_page}

    def _list_album_photos(self, album_id, album_name, all_files, progress_callback=None):
        body = {"albumId": album_id, "pageSize": 100}
        while True:
            try:
                resp = requests.post(
                    f"{PHOTOS_BASE}/mediaItems:search",
                    headers={**self._headers, "Content-Type": "application/json"},
                    json=body, timeout=15,
                )
                if resp.status_code != 200:
                    break
                data = resp.json()
            except Exception:
                break
            for item in data.get("mediaItems", []):
                cf = self._item_to_cloudfile(item, folder_path=album_name)
                if cf:
                    all_files.append(cf)
            if progress_callback:
                progress_callback("listing", len(all_files), len(all_files))
            next_page = data.get("nextPageToken")
            if not next_page:
                break
            body = {"albumId": album_id, "pageSize": 100, "pageToken": next_page}

    def _item_to_cloudfile(self, item, folder_path=""):
        mime = item.get("mimeType", "")
        if not mime.startswith("image/"):
            return None

        metadata = item.get("mediaMetadata", {})
        filename = item.get("filename", "")
        creation_time = metadata.get("creationTime", "")
        width = metadata.get("width", "")
        height = metadata.get("height", "")

        # Google Photos API provides no file hash — build a pseudo-hash from
        # stable metadata fields to enable exact-duplicate grouping.
        pseudo_hash = hashlib.sha256(
            f"{filename}:{creation_time}:{width}x{height}".encode()
        ).hexdigest()

        base_url = item.get("baseUrl", "")
        thumb_url = f"{base_url}=w220-h220" if base_url else None

        return CloudFile(
            file_id=item["id"],
            name=filename,
            provider="google_photos",
            size=0,  # Google Photos API does not expose file size
            sha256=pseudo_hash,
            mime_type=mime,
            created_time=creation_time,
            modified_time=creation_time,
            thumbnail_url=thumb_url,
            folder_path=folder_path,
        )

    def download_thumbnail(self, file_id, temp_dir, thumbnail_url=None):
        """Download thumbnail via baseUrl (no auth header needed). Returns local path or None."""
        try:
            url = thumbnail_url
            if not url:
                resp = requests.get(
                    f"{PHOTOS_BASE}/mediaItems/{file_id}",
                    headers=self._headers, timeout=10,
                )
                if resp.status_code != 200:
                    return None
                base_url = resp.json().get("baseUrl", "")
                if not base_url:
                    return None
                url = f"{base_url}=w220-h220"

            resp = requests.get(url, timeout=10)
            if resp.status_code == 200:
                path = os.path.join(temp_dir, f"gp_{file_id}.jpg")
                with open(path, "wb") as f:
                    f.write(resp.content)
                return path
        except Exception:
            pass
        return None

    def delete_file(self, file_id):
        """Google Photos API does not support deletion by third-party apps."""
        return False
