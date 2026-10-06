import json
import pathlib
import sys
import unittest
from unittest.mock import patch, Mock

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

YouTube_vn = load()

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "youtube"


class FetchToolchainTest(unittest.TestCase):
    def setUp(self):
        self.owner = "example"
        self.patches_repo = "patches"
        self.desktop_repo = "desktop"
        self.token = "fake_token"

    def test_parity_extract_versions_from_releases(self):
        # Load fixture
        with open(FIXTURES / "releases.json", 'r') as f:
            data = json.load(f)
        # Mock the requests.get call
        with patch('requests.Session.get') as mock_get:
            mock_resp = Mock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = data
            mock_get.return_value = mock_resp
            result = YouTube_vn.fetch_toolchain(self.owner, self.patches_repo, self.desktop_repo, self.token)
            # Expect patches_ver from the latest prerelease (since we prefer prerelease)
            # The fixture has two releases: 1.0.0 (stable) and 2.0.0 (prerelease)
            # We prefer prerelease, so we expect 2.0.0
            self.assertEqual(result["patches_ver"], "2.0.0")
            # Desktop repo has no assets in this fixture, so desktop_ver should be empty
            self.assertEqual(result["desktop_ver"], "")

    def test_transient_failure_retry_success(self):
        # Mock the requests.get to fail twice then succeed
        with patch('requests.Session.get') as mock_get:
            mock_resp_fail = Mock()
            mock_resp_fail.status_code = 500
            mock_resp_success = Mock()
            mock_resp_success.status_code = 200
            mock_resp_success.json.return_value = []
            mock_get.side_effect = [mock_resp_fail, mock_resp_fail, mock_resp_success,
                                  mock_resp_fail, mock_resp_fail, mock_resp_success]
            result = YouTube_vn.fetch_toolchain(self.owner, self.patches_repo, self.desktop_repo, self.token)
            # Should eventually succeed and return empty versions (since no assets)
            self.assertEqual(result["patches_ver"], "")
            self.assertEqual(result["desktop_ver"], "")
            # Should have been called 6 times (3 attempts each for patches and desktop)
            self.assertEqual(mock_get.call_count, 6)

    def test_tag_name_basename_alignment(self):
        # Test that the version extracted from the asset name matches the tag_name prefix
        with open(FIXTURES / "releases.json", 'r') as f:
            data = json.load(f)
        with patch('requests.Session.get') as mock_get:
            mock_resp = Mock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = data
            mock_get.return_value = mock_resp
            result = YouTube_vn.fetch_toolchain(self.owner, self.patches_repo, self.desktop_repo, self.token)
            # For each release in the fixture, check that the version extracted matches the tag_name without the prefix
            for release in data:
                tag_name = release["tag_name"]
                # Expected format: patches-<version>
                if tag_name.startswith("patches-"):
                    expected_version = tag_name[8:]
                else:
                    expected_version = tag_name
                # Since we prefer prerelease, we only check the prerelease release (2.0.0)
                if release.get("prerelease", False):
                    self.assertEqual(result["patches_ver"], expected_version)
                    break

    def test_desktop_version_extraction(self):
        # Test desktop version extraction using mock data similar to fixtures
        desktop_data = [
            {
                "tag_name": "v1.0.0",
                "target_commitish": "main",
                "name": "v1.0.0",
                "draft": False,
                "prerelease": False,
                "created_at": "2023-01-01T00:00:00Z",
                "published_at": "2023-01-01T00:00:00Z",
                "assets": [
                    {
                        "url": "https://api.github.com/repos/example/desktop/releases/assets/1",
                        "id": 1,
                        "node_id": "MDEwOlJlbG9hcmFzZV8x",
                        "name": "morphe-desktop-1.0.0-all.jar",
                        "label": "morphe-desktop-1.0.0-all.jar",
                        "content_type": "application/octet-stream",
                        "state": "uploaded",
                        "size": 12345,
                        "download_count": 0,
                        "created_at": "2023-01-01T00:00:00Z",
                        "updated_at": "2023-01-01T00:00:00Z",
                        "browser_download_url": "https://github.com/example/desktop/releases/download/v1.0.0/morphe-desktop-1.0.0-all.jar"
                    }
                ]
            },
            {
                "tag_name": "v2.0.0",
                "target_commitish": "main",
                "name": "v2.0.0",
                "draft": False,
                "prerelease": True,
                "created_at": "2023-01-02T00:00:00Z",
                "published_at": "2023-01-02T00:00:00Z",
                "assets": [
                    {
                        "url": "https://api.github.com/repos/example/desktop/releases/assets/2",
                        "id": 2,
                        "node_id": "MDEwOlJlbG9hcmFzZV8y",
                        "name": "morphe-desktop-2.0.0.jar",
                        "label": "morphe-desktop-2.0.0.jar",
                        "content_type": "application/octet-stream",
                        "state": "uploaded",
                        "size": 67890,
                        "download_count": 0,
                        "created_at": "2023-01-02T00:00:00Z",
                        "updated_at": "2023-01-02T00:00:00Z",
                        "browser_download_url": "https://github.com/example/desktop/releases/download/v2.0.0/morphe-desktop-2.0.0.jar"
                    }
                ]
            }
        ]
        
        # Mock the requests.get call for desktop (patches will return empty)
        with patch("requests.Session.get") as mock_get:
            def side_effect(url, *args, **kwargs):
                mock_resp = Mock()
                mock_resp.status_code = 200
                if "patches" in url:
                    # Return empty releases for patches
                    mock_resp.json.return_value = []
                elif "desktop" in url:
                    # Return desktop data
                    mock_resp.json.return_value = desktop_data
                return mock_resp
            
            mock_get.side_effect = side_effect
            result = YouTube_vn.fetch_toolchain(self.owner, self.patches_repo, self.desktop_repo, self.token)
            # Expect desktop_ver from the latest prerelease (since we prefer prerelease)
            # We prefer prerelease, so we expect 2.0.0
            self.assertEqual(result["desktop_ver"], "2.0.0")
            # Patches repo has no assets in this mock, so patches_ver should be empty
            self.assertEqual(result["patches_ver"], "")

    def test_both_patches_and_desktop_with_prerelease_preference(self):
        # Test that both patches and desktop correctly prefer prerelease versions
        patches_data = [
            {
                "tag_name": "patches-1.0.0",
                "target_commitish": "main",
                "name": "patches-1.0.0",
                "draft": False,
                "prerelease": False,
                "created_at": "2023-01-01T00:00:00Z",
                "published_at": "2023-01-01T00:00:00Z",
                "assets": [
                    {
                        "name": "patches-1.0.0.mpp"
                    }
                ]
            },
            {
                "tag_name": "patches-2.0.0",
                "target_commitish": "main",
                "name": "patches-2.0.0",
                "draft": False,
                "prerelease": True,
                "created_at": "2023-01-02T00:00:00Z",
                "published_at": "2023-01-02T00:00:00Z",
                "assets": [
                    {
                        "name": "patches-2.0.0.mpp"
                    }
                ]
            }
        ]
        
        desktop_data = [
            {
                "tag_name": "v1.0.0",
                "target_commitish": "main",
                "name": "v1.0.0",
                "draft": False,
                "prerelease": False,
                "created_at": "2023-01-01T00:00:00Z",
                "published_at": "2023-01-01T00:00:00Z",
                "assets": [
                    {
                        "name": "morphe-desktop-1.0.0-all.jar"
                    }
                ]
            },
            {
                "tag_name": "v2.0.0",
                "target_commitish": "main",
                "name": "v2.0.0",
                "draft": False,
                "prerelease": True,
                "created_at": "2023-01-02T00:00:00Z",
                "published_at": "2023-01-02T00:00:00Z",
                "assets": [
                    {
                        "name": "morphe-desktop-2.0.0.jar"
                    }
                ]
            }
        ]
        
        # Mock the requests.get call
        with patch("requests.Session.get") as mock_get:
            def side_effect(url, *args, **kwargs):
                mock_resp = Mock()
                mock_resp.status_code = 200
                if "patches" in url:
                    mock_resp.json.return_value = patches_data
                elif "desktop" in url:
                    mock_resp.json.return_value = desktop_data
                return mock_resp
            
            mock_get.side_effect = side_effect
            result = YouTube_vn.fetch_toolchain(self.owner, self.patches_repo, self.desktop_repo, self.token)
            # Expect both to be the prerelease versions
            self.assertEqual(result["patches_ver"], "2.0.0")
            self.assertEqual(result["desktop_ver"], "2.0.0")

if __name__ == '__main__':
    unittest.main()