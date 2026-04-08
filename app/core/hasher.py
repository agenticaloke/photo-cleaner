from collections import defaultdict

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


def find_similar_photos(files, provider, temp_dir, threshold=10,
                        progress_callback=None):
    """Find visually similar photos using perceptual hashing on thumbnails.

    Downloads small thumbnails (a few KB each) and compares pHash values.
    Returns list of DuplicateGroup for groups with 2+ similar files.
    """
    # Download thumbnails and compute perceptual hashes
    hashed_files = []
    for i, f in enumerate(files):
        if progress_callback:
            progress_callback("hashing", i + 1, len(files))
        try:
            thumb_path = provider.download_thumbnail(f.file_id, temp_dir)
            if thumb_path:
                img = Image.open(thumb_path)
                phash = imagehash.phash(img)
                f.phash = str(phash)
                hashed_files.append((f, phash))
        except Exception:
            # Skip files whose thumbnails can't be processed
            continue

    if len(hashed_files) < 2:
        return []

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

    # Compare all pairs (O(n^2) — fine for up to a few thousand photos)
    for i in range(len(hashed_files)):
        for j in range(i + 1, len(hashed_files)):
            f1, h1 = hashed_files[i]
            f2, h2 = hashed_files[j]
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
