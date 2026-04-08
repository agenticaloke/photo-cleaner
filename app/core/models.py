from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CloudFile:
    """Represents a photo file from a cloud storage provider."""
    file_id: str
    name: str
    provider: str  # "google_drive" or "onedrive"
    size: int  # bytes
    sha256: Optional[str]  # From cloud API metadata (no download needed)
    mime_type: str
    created_time: str
    modified_time: str
    thumbnail_url: Optional[str] = None
    phash: Optional[str] = None  # Filled after thumbnail download
    folder_path: str = ""


@dataclass
class DuplicateGroup:
    """A group of files that are duplicates of each other."""
    group_id: int
    match_type: str  # "exact" or "similar"
    files: list = field(default_factory=list)  # list[CloudFile]
    suggested_keep: Optional[CloudFile] = None
    hamming_distance: int = 0  # 0 for exact, >0 for similar


@dataclass
class ScanResult:
    """Results of a duplicate scan."""
    total_photos: int
    exact_groups: list = field(default_factory=list)  # list[DuplicateGroup]
    similar_groups: list = field(default_factory=list)  # list[DuplicateGroup]
    unique_count: int = 0
    space_recoverable: int = 0  # bytes that would be freed
