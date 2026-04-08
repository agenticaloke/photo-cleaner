import os
import tempfile

from app.core.hasher import find_exact_duplicates, find_similar_photos
from app.core.models import ScanResult


def scan_for_duplicates(providers, threshold=10, progress_callback=None):
    """Run the full duplicate detection pipeline across all connected providers.

    Args:
        providers: list of CloudProvider instances (Google Drive, OneDrive, etc.)
        threshold: Hamming distance threshold for similar photos
        progress_callback: function(stage, current, total) for progress updates

    Returns:
        ScanResult with all duplicate groups found
    """
    # Step 1: List all photos from all providers
    all_files = []
    for provider in providers:
        if progress_callback:
            progress_callback("listing", 0, 0)
        files = provider.list_photos(progress_callback=progress_callback)
        all_files.extend(files)

    if not all_files:
        return ScanResult(total_photos=0)

    total = len(all_files)

    # Step 2: Find exact duplicates (zero downloads — uses SHA-256 from metadata)
    if progress_callback:
        progress_callback("exact_matching", 0, total)
    exact_groups = find_exact_duplicates(all_files)

    # Step 3: Find files NOT already in an exact group
    exact_file_ids = set()
    for group in exact_groups:
        for f in group.files:
            exact_file_ids.add(f.file_id)
    remaining = [f for f in all_files if f.file_id not in exact_file_ids]

    # Step 4: Find visually similar photos (downloads thumbnails only)
    similar_groups = []
    if remaining and providers:
        temp_dir = tempfile.mkdtemp(prefix="photocleaner-")
        try:
            # Build a provider lookup by name
            provider_map = {p.provider_name: p for p in providers}

            # Group remaining files by provider for thumbnail download
            by_provider = {}
            for f in remaining:
                by_provider.setdefault(f.provider, []).append(f)

            all_similar = []
            for provider_name, files in by_provider.items():
                provider = provider_map.get(provider_name)
                if provider:
                    groups = find_similar_photos(
                        files, provider, temp_dir, threshold, progress_callback
                    )
                    all_similar.extend(groups)

            # Also do cross-provider comparison if multiple providers
            if len(by_provider) > 1:
                first_provider = providers[0]
                cross_groups = find_similar_photos(
                    remaining, first_provider, temp_dir, threshold, progress_callback
                )
                # Merge with provider-specific results, avoiding duplicates
                existing_ids = set()
                for g in all_similar:
                    for f in g.files:
                        existing_ids.add(f.file_id)
                for g in cross_groups:
                    if any(f.file_id not in existing_ids for f in g.files):
                        all_similar.append(g)

            similar_groups = all_similar
        finally:
            # Temp dir cleanup is handled by the background cleanup thread
            pass

    # Step 5: Calculate stats
    duplicate_file_ids = set()
    space_recoverable = 0
    for group in exact_groups + similar_groups:
        for f in group.files:
            if f.file_id != group.suggested_keep.file_id:
                duplicate_file_ids.add(f.file_id)
                space_recoverable += f.size

    unique_count = total - len(duplicate_file_ids) - len(exact_groups) - len(similar_groups)

    return ScanResult(
        total_photos=total,
        exact_groups=exact_groups,
        similar_groups=similar_groups,
        unique_count=max(0, unique_count),
        space_recoverable=space_recoverable,
    )
