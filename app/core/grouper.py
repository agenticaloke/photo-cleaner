import logging
import os
import tempfile

from app.core.hasher import find_exact_duplicates, find_similar_photos
from app.core.models import ScanResult

logger = logging.getLogger("photocleaner")

BATCH_SIZE = 5000
MAX_SIMILAR_SCAN = 2000  # Max files to scan for visual similarity (thumbnail downloads)


def scan_for_duplicates(providers, threshold=10, progress_callback=None, mode="basic"):
    """Run the full duplicate detection pipeline across all connected providers.

    Processes photos in batches of 5,000 to avoid memory and timeout issues
    with large libraries. Limits visual similarity scanning to 2,000 files
    to avoid excessive thumbnail downloads.

    Args:
        providers: list of CloudProvider instances (Google Drive, OneDrive, etc.)
        threshold: Hamming distance threshold for similar photos
        progress_callback: function(stage, current, total) for progress updates
        mode: "basic" for exact matches only, "advanced" for exact + similar

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

    # Step 2: Find exact duplicates (fast — just SHA-256 string comparison)
    logger.info(f"Step 2: Finding exact duplicates among {total} files")
    if progress_callback:
        progress_callback("exact_matching", 0, total)

    exact_groups = find_exact_duplicates(all_files)
    logger.info(f"Found {len(exact_groups)} exact duplicate groups")

    if progress_callback:
        progress_callback("exact_matching", total, total)

    # In basic mode, skip similar matching entirely
    if mode != "advanced":
        logger.info("Basic mode — skipping similar photo detection")
        duplicate_file_ids = set()
        space_recoverable = 0
        for group in exact_groups:
            for f in group.files:
                if f.file_id != group.suggested_keep.file_id:
                    duplicate_file_ids.add(f.file_id)
                    space_recoverable += f.size
        unique_count = total - len(duplicate_file_ids) - len(exact_groups)
        return ScanResult(
            total_photos=total,
            exact_groups=exact_groups,
            similar_groups=[],
            unique_count=max(0, unique_count),
            space_recoverable=space_recoverable,
        )

    # Step 3: Find files NOT already in an exact group (advanced mode)
    exact_file_ids = set()
    for group in exact_groups:
        for f in group.files:
            exact_file_ids.add(f.file_id)
    remaining = [f for f in all_files if f.file_id not in exact_file_ids]
    logger.info(f"Step 3: {len(remaining)} files remaining after exact match removal")

    # Step 4: Find visually similar photos (downloads thumbnails) — advanced mode only
    # Cap at MAX_SIMILAR_SCAN to avoid downloading too many thumbnails
    similar_groups = []
    skipped_similar = 0
    if remaining and providers:
        logger.info(f"Step 4: Starting similar photo detection ({len(remaining)} candidates, max {MAX_SIMILAR_SCAN})")
        if len(remaining) > MAX_SIMILAR_SCAN:
            skipped_similar = len(remaining) - MAX_SIMILAR_SCAN
            logger.info(f"Capping at {MAX_SIMILAR_SCAN} files, skipping {skipped_similar}")
            # Prioritize: sort by size descending (larger files more likely to have
            # resized duplicates), take the top MAX_SIMILAR_SCAN
            remaining_for_similar = sorted(remaining, key=lambda f: f.size, reverse=True)[:MAX_SIMILAR_SCAN]
        else:
            remaining_for_similar = remaining

        if progress_callback:
            progress_callback("hashing", 0, len(remaining_for_similar))

        temp_dir = tempfile.mkdtemp(prefix="photocleaner-")
        logger.info(f"Thumbnail temp dir: {temp_dir}")
        try:
            provider_map = {p.provider_name: p for p in providers}

            all_similar = []
            for batch_start in range(0, len(remaining_for_similar), BATCH_SIZE):
                batch = remaining_for_similar[batch_start:batch_start + BATCH_SIZE]
                batch_num = batch_start // BATCH_SIZE + 1
                logger.info(f"Processing batch {batch_num}: {len(batch)} files")

                # Group batch files by provider
                by_provider = {}
                for f in batch:
                    by_provider.setdefault(f.provider, []).append(f)

                for provider_name, files in by_provider.items():
                    provider = provider_map.get(provider_name)
                    if provider:
                        logger.info(f"Hashing {len(files)} files from {provider_name}")
                        groups = find_similar_photos(
                            files, provider, temp_dir, threshold, progress_callback
                        )
                        logger.info(f"Found {len(groups)} similar groups from {provider_name}")
                        all_similar.extend(groups)

                # Cross-provider comparison within this batch
                if len(by_provider) > 1:
                    first_provider = providers[0]
                    cross_groups = find_similar_photos(
                        batch, first_provider, temp_dir, threshold, progress_callback
                    )
                    existing_ids = set()
                    for g in all_similar:
                        for f in g.files:
                            existing_ids.add(f.file_id)
                    for g in cross_groups:
                        if any(f.file_id not in existing_ids for f in g.files):
                            all_similar.append(g)

            similar_groups = all_similar
            logger.info(f"Total similar groups found: {len(similar_groups)}")
        finally:
            import shutil
            shutil.rmtree(temp_dir, ignore_errors=True)

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
