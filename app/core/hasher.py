from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed

import imagehash
from PIL import Image

from app.core.models import CloudFile, DuplicateGroup


def find_exact_duplicates(files, start_group_id=1):
    """Group files by SHA-256 hash. Files with identical hashes are exact duplicates.

    No downloads needed — SHA-256 comes from cloud API metadata.
    Returns list of DuplicateGroup for groups with 2+ files.
    """
    hash_groups = defaultdict(list)
    for f in files:
        if f.sha256:
            hash_groups[f.sha256].append(f)

    groups = []
    group_id = start_group_id
    for sha256, group_files in hash_groups.items():
        if len(group_files) >= 2:
            # Keep the oldest file (earliest created_time)
            sorted_files = sorted(group_files, key=lambda f: f.created_time)
            groups.append(DuplicateGroup(
                group_id=group_id,
                match_type="exact",
                files=sorted_files,
                suggested_keep=sorted_files[0],
                hamming_distance=0,
            ))
            group_id += 1

    return groups


def _download_and_hash(args):
    """Download a single thumbnail and compute its pHash. Used by thread pool."""
    cloud_file, provider, temp_dir = args
    try:
        thumb_path = provider.download_thumbnail(
            cloud_file.file_id, temp_dir,
            thumbnail_url=cloud_file.thumbnail_url,
        )
        if thumb_path:
            img = Image.open(thumb_path)
            phash = imagehash.phash(img)
            cloud_file.phash = str(phash)
            return (cloud_file, phash)
    except Exception:
        pass
    return None


def find_similar_photos(files, provider, temp_dir, threshold=10,
                        progress_callback=None, max_workers=10):
    """Find visually similar photos using perceptual hashing on thumbnails.

    Downloads thumbnails in parallel (10 at a time) and compares pHash values.
    Returns list of DuplicateGroup for groups with 2+ similar files.
    """
    # Download thumbnails and compute perceptual hashes in parallel
    hashed_files = []
    completed = 0
    total = len(files)

    tasks = [(f, provider, temp_dir) for f in files]

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_download_and_hash, task): task for task in tasks}
        for future in as_completed(futures):
            completed += 1
            if progress_callback and completed % 5 == 0:
                progress_callback("hashing", completed, total)
            result = future.result()
            if result:
                hashed_files.append(result)

    if progress_callback:
        progress_callback("hashing", total, total)

    if len(hashed_files) < 2:
        return []

    # Pre-filter: group by similar file size to reduce comparisons
    # Photos that differ by more than 10x in size are unlikely to be visual duplicates
    if progress_callback:
        progress_callback("comparing", 0, len(hashed_files))

    # Union-Find to cluster similar photos
    parent = {f.file_id: f.file_id for f, _ in hashed_files}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # Sort by file size to enable size-based skip
    hashed_files.sort(key=lambda x: x[0].size)

    # Compare pairs, skipping files with very different sizes
    for i in range(len(hashed_files)):
        f1, h1 = hashed_files[i]
        for j in range(i + 1, len(hashed_files)):
            f2, h2 = hashed_files[j]
            # Skip if sizes differ by more than 10x
            if f1.size > 0 and f2.size > 0:
                ratio = f2.size / f1.size
                if ratio > 10:
                    break  # Sorted by size, so all further files are even larger
            distance = h1 - h2
            if distance <= threshold:
                union(f1.file_id, f2.file_id)

    # Collect clusters
    clusters = defaultdict(list)
    file_map = {f.file_id: f for f, _ in hashed_files}
    for file_id in parent:
        root = find(file_id)
        clusters[root].append(file_map[file_id])

    # Build DuplicateGroups for clusters with 2+ files
    groups = []
    group_id = 1000  # Start high to avoid collision with exact groups
    for cluster_files in clusters.values():
        if len(cluster_files) >= 2:
            # Keep the largest file (likely highest quality)
            sorted_files = sorted(cluster_files, key=lambda f: f.size, reverse=True)
            groups.append(DuplicateGroup(
                group_id=group_id,
                match_type="similar",
                files=sorted_files,
                suggested_keep=sorted_files[0],
                hamming_distance=threshold,
            ))
            group_id += 1

    return groups
