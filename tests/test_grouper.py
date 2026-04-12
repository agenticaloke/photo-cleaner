import os

from app.core.grouper import scan_for_duplicates
from app.core.models import CloudFile


class MockProvider:
    """A mock cloud provider that returns pre-set files and local thumbnails."""

    def __init__(self, files, fixtures_dir=None):
        self._files = files
        self._fixtures_dir = fixtures_dir
        self.provider_name = "mock"

    def list_photos(self, folder_path=None, progress_callback=None):
        if progress_callback:
            progress_callback("listing", len(self._files), len(self._files))
        return self._files

    def download_thumbnail(self, file_id, temp_dir, thumbnail_url=None):
        if not self._fixtures_dir:
            return None
        mapping = {
            "orig": "thumb_original.jpg",
            "similar": "thumb_similar.jpg",
            "different": "thumb_different.jpg",
        }
        filename = mapping.get(file_id)
        if filename:
            return os.path.join(self._fixtures_dir, filename)
        return None

    def delete_file(self, file_id):
        return True


def _make_file(file_id, name, sha256, size=1000, provider="mock"):
    return CloudFile(
        file_id=file_id,
        name=name,
        provider=provider,
        size=size,
        sha256=sha256,
        mime_type="image/jpeg",
        created_time=f"2026-01-0{min(int(file_id) if file_id.isdigit() else 1, 9)}T00:00:00Z",
        modified_time=f"2026-01-0{min(int(file_id) if file_id.isdigit() else 1, 9)}T00:00:00Z",
    )


class TestScanForDuplicates:
    def test_finds_exact_duplicates(self):
        files = [
            _make_file("1", "a.jpg", "same_hash"),
            _make_file("2", "b.jpg", "same_hash"),
            _make_file("3", "c.jpg", "unique"),
        ]
        provider = MockProvider(files)
        result = scan_for_duplicates([provider], threshold=10)

        assert result.total_photos == 3
        assert len(result.exact_groups) == 1
        assert len(result.exact_groups[0].files) == 2

    def test_no_duplicates_returns_empty(self):
        files = [
            _make_file("1", "a.jpg", "hash1"),
            _make_file("2", "b.jpg", "hash2"),
        ]
        provider = MockProvider(files)
        result = scan_for_duplicates([provider], threshold=10)

        assert result.total_photos == 2
        assert len(result.exact_groups) == 0

    def test_empty_provider(self):
        provider = MockProvider([])
        result = scan_for_duplicates([provider])

        assert result.total_photos == 0
        assert len(result.exact_groups) == 0
        assert len(result.similar_groups) == 0

    def test_space_recoverable_calculated(self):
        files = [
            _make_file("1", "a.jpg", "same", size=5000),
            _make_file("2", "b.jpg", "same", size=5000),
            _make_file("3", "c.jpg", "same", size=5000),
        ]
        provider = MockProvider(files)
        result = scan_for_duplicates([provider], threshold=10)

        # Should suggest keeping 1 file, so 2 files' sizes are recoverable
        assert result.space_recoverable == 10000

    def test_multiple_providers_merged(self):
        files_a = [_make_file("1", "a.jpg", "shared_hash", provider="mock")]
        files_b = [_make_file("2", "b.jpg", "shared_hash", provider="mock")]
        provider_a = MockProvider(files_a)
        provider_a.provider_name = "provider_a"
        provider_b = MockProvider(files_b)
        provider_b.provider_name = "provider_b"

        result = scan_for_duplicates([provider_a, provider_b], threshold=10)
        assert result.total_photos == 2
        assert len(result.exact_groups) == 1


class TestScanWithSimilarPhotos:
    def test_finds_similar_via_thumbnails(self, fixtures_dir):
        files = [
            _make_file("orig", "original.jpg", "hash_orig", size=5000),
            _make_file("similar", "similar.jpg", "hash_similar", size=3000),
        ]
        provider = MockProvider(files, fixtures_dir)
        result = scan_for_duplicates([provider], threshold=15, mode="advanced")

        assert len(result.similar_groups) >= 1

    def test_exact_dupes_excluded_from_similar_check(self, fixtures_dir):
        files = [
            _make_file("1", "exact1.jpg", "same_hash", size=5000),
            _make_file("2", "exact2.jpg", "same_hash", size=5000),
            _make_file("orig", "original.jpg", "hash_orig", size=5000),
            _make_file("different", "different.jpg", "hash_diff", size=4000),
        ]
        provider = MockProvider(files, fixtures_dir)
        result = scan_for_duplicates([provider], threshold=10, mode="advanced")

        # The exact pair should be found
        assert len(result.exact_groups) == 1
        # The remaining 2 (orig + different) should NOT be grouped as similar
        exact_ids = {f.file_id for g in result.exact_groups for f in g.files}
        assert "1" in exact_ids
        assert "2" in exact_ids
