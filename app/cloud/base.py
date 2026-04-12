from abc import ABC, abstractmethod


class CloudProvider(ABC):
    """Abstract interface for cloud storage providers."""

    @property
    @abstractmethod
    def provider_name(self):
        """Return provider identifier string (e.g. 'google_drive', 'onedrive')."""
        ...

    @abstractmethod
    def list_folders(self):
        """List the top-level folder tree for the user to pick from.

        Returns list of dicts: [{"id": ..., "name": ..., "path": ..., "children": [...]}]
        Only goes 2 levels deep to keep it fast.
        """
        ...

    @abstractmethod
    def list_photos(self, folder_ids=None, progress_callback=None):
        """List photo files with metadata including server-side hashes.

        If folder_ids is provided, only scans those folders (and their subfolders).
        If None, scans the entire drive.

        Returns list of CloudFile objects.
        """
        ...

    @abstractmethod
    def download_thumbnail(self, file_id, temp_dir, thumbnail_url=None):
        """Download a small thumbnail image.

        If thumbnail_url is provided (cached from listing), uses it directly
        to avoid an extra API call.

        Returns local file path to the saved thumbnail, or None on failure.
        """
        ...

    @abstractmethod
    def delete_file(self, file_id):
        """Move file to trash/recycle bin (recoverable, not permanent).

        Returns True on success.
        """
        ...
