from abc import ABC, abstractmethod


class CloudProvider(ABC):
    """Abstract interface for cloud storage providers."""

    @property
    @abstractmethod
    def provider_name(self):
        """Return provider identifier string (e.g. 'google_drive', 'onedrive')."""
        ...

    @abstractmethod
    def list_photos(self, folder_path=None, progress_callback=None):
        """List all photo files with metadata including server-side hashes.

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
