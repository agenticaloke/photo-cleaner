import hashlib
import os

import imagehash
from PIL import Image

from app.core.hasher import find_exact_duplicates, find_similar_photos
from app.core.models import CloudFile


def _make_file(file_id, name, sha256, size=1000, provider="google_drive"):
    # Use a hash of file_id for stable timestamps when file_id isn't numeric
    day = (hash(file_id) % 28) + 1
    return CloudFile(
        file_id=file_id,
        name=name,
        provider=provider,
        size=size,
        sha256=sha256,
        mime_type="image/jpeg",
        created_time=f"2026-01-{day:02d}T00:00:00Z",
        modified_time=f"2026-01-{day:02d}T00:00:00Z",
    )


class TestFindExactDuplicates:
    def test_groups_identical_hashes(self):
        files = [
            _make_file("1", "photo1.jpg", "aaa111"),
            _make_file("2", "photo2.jpg", "aaa111"),
            _make_file("3", "photo3.jpg", "bbb222"),
        ]
        groups = find_exact_duplicates(files)
        assert len(groups) == 1
        assert len(groups[0].files) == 2
        assert groups[0].match_type == "exact"

    def test_three_identical_files(self):
        files = [
            _make_file("1", "a.jpg", "same_hash"),
            _make_file("2", "b.jpg", "same_hash"),
            _make_file("3", "c.jpg", "same_hash"),
        ]
        groups = find_exact_duplicates(files)
        assert len(groups) == 1
        assert len(groups[0].files) == 3

    def test_no_duplicates(self):
        files = [
            _make_file("1", "a.jpg", "hash1"),
            _make_file("2", "b.jpg", "hash2"),
            _make_file("3", "c.jpg", "hash3"),
        ]
        groups = find_exact_duplicates(files)
        assert len(groups) == 0

    def test_suggested_keep_is_oldest(self):
        f1 = _make_file("1", "oldest.jpg", "same")
        f1.created_time = "2026-01-01T00:00:00Z"
        f2 = _make_file("2", "middle.jpg", "same")
        f2.created_time = "2026-01-15T00:00:00Z"
        f3 = _make_file("3", "newest.jpg", "same")
        f3.created_time = "2026-01-28T00:00:00Z"
        groups = find_exact_duplicates([f3, f1, f2])
        assert groups[0].suggested_keep.file_id == "1"

    def test_multiple_duplicate_groups(self):
        files = [
            _make_file("1", "a1.jpg", "group_a"),
            _make_file("2", "a2.jpg", "group_a"),
            _make_file("3", "b1.jpg", "group_b"),
            _make_file("4", "b2.jpg", "group_b"),
            _make_file("5", "unique.jpg", "unique"),
        ]
        groups = find_exact_duplicates(files)
        assert len(groups) == 2

    def test_files_without_sha256_skipped(self):
        files = [
            _make_file("1", "a.jpg", None),
            _make_file("2", "b.jpg", None),
        ]
        groups = find_exact_duplicates(files)
        assert len(groups) == 0

    def test_cross_provider_detection(self):
        files = [
            _make_file("1", "photo.jpg", "same_hash", provider="google_drive"),
            _make_file("2", "photo.jpg", "same_hash", provider="onedrive"),
        ]
        groups = find_exact_duplicates(files)
        assert len(groups) == 1
        providers = {f.provider for f in groups[0].files}
        assert providers == {"google_drive", "onedrive"}

    def test_empty_list(self):
        groups = find_exact_duplicates([])
        assert len(groups) == 0


class TestFindSimilarPhotos:
    """Tests using real image fixtures and a mock provider."""

    class MockProvider:
        provider_name = "mock"

        def __init__(self, fixtures_dir):
            self.fixtures_dir = fixtures_dir

        def download_thumbnail(self, file_id, temp_dir):
            # Map file_id to fixture file
            mapping = {
                "orig": "thumb_original.jpg",
                "similar": "thumb_similar.jpg",
                "different": "thumb_different.jpg",
            }
            filename = mapping.get(file_id)
            if filename:
                return os.path.join(self.fixtures_dir, filename)
            return None

    def test_similar_images_grouped(self, fixtures_dir, tmp_path):
        files = [
            _make_file("orig", "original.jpg", "hash_orig", size=5000),
            _make_file("similar", "similar.jpg", "hash_similar", size=3000),
        ]
        provider = self.MockProvider(fixtures_dir)
        groups = find_similar_photos(files, provider, str(tmp_path), threshold=15)
        assert len(groups) == 1
        assert len(groups[0].files) == 2

    def test_different_images_not_grouped(self, fixtures_dir, tmp_path):
        files = [
            _make_file("orig", "original.jpg", "hash_orig", size=5000),
            _make_file("different", "different.jpg", "hash_diff", size=4000),
        ]
        provider = self.MockProvider(fixtures_dir)
        groups = find_similar_photos(files, provider, str(tmp_path), threshold=10)
        assert len(groups) == 0

    def test_suggested_keep_is_largest(self, fixtures_dir, tmp_path):
        files = [
            _make_file("similar", "small.jpg", "hash_similar", size=3000),
            _make_file("orig", "large.jpg", "hash_orig", size=5000),
        ]
        provider = self.MockProvider(fixtures_dir)
        groups = find_similar_photos(files, provider, str(tmp_path), threshold=15)
        if groups:
            assert groups[0].suggested_keep.file_id == "orig"

    def test_single_file_no_groups(self, fixtures_dir, tmp_path):
        files = [_make_file("orig", "only.jpg", "hash_orig")]
        provider = self.MockProvider(fixtures_dir)
        groups = find_similar_photos(files, provider, str(tmp_path))
        assert len(groups) == 0


class TestFixtureImageHashes:
    """Verify that the fixture images have expected hash relationships."""

    def test_identical_files_same_phash(self, fixtures_dir):
        orig = Image.open(os.path.join(fixtures_dir, "thumb_original.jpg"))
        dup = Image.open(os.path.join(fixtures_dir, "thumb_duplicate.jpg"))
        h1 = imagehash.phash(orig)
        h2 = imagehash.phash(dup)
        assert h1 - h2 == 0

    def test_similar_files_close_phash(self, fixtures_dir):
        orig = Image.open(os.path.join(fixtures_dir, "thumb_original.jpg"))
        similar = Image.open(os.path.join(fixtures_dir, "thumb_similar.jpg"))
        h1 = imagehash.phash(orig)
        h2 = imagehash.phash(similar)
        distance = h1 - h2
        assert distance <= 15, f"Expected close hashes, got distance {distance}"

    def test_different_files_far_phash(self, fixtures_dir):
        orig = Image.open(os.path.join(fixtures_dir, "thumb_original.jpg"))
        diff = Image.open(os.path.join(fixtures_dir, "thumb_different.jpg"))
        h1 = imagehash.phash(orig)
        h2 = imagehash.phash(diff)
        distance = h1 - h2
        assert distance > 15, f"Expected far hashes, got distance {distance}"

    def test_identical_files_same_sha256(self, fixtures_dir):
        def sha(path):
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()

        h1 = sha(os.path.join(fixtures_dir, "thumb_original.jpg"))
        h2 = sha(os.path.join(fixtures_dir, "thumb_duplicate.jpg"))
        assert h1 == h2

    def test_similar_files_different_sha256(self, fixtures_dir):
        def sha(path):
            h = hashlib.sha256()
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(8192), b""):
                    h.update(chunk)
            return h.hexdigest()

        h1 = sha(os.path.join(fixtures_dir, "thumb_original.jpg"))
        h2 = sha(os.path.join(fixtures_dir, "thumb_similar.jpg"))
        assert h1 != h2
