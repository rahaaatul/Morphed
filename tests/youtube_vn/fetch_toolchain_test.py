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

if __name__ == '__main__':
    unittest.main()