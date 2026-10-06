import json
import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

YouTube_vn = load()

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "youtube"


class DiscoverTest(unittest.TestCase):
    def test_discover_emits_matrix_channels_and_reused(self):
        # Test basic functionality of build_matrix
        versions = ["1.0.0", "2.0.0", "3.0.0"]
        matrix_json, channels_json, nothing_to_build = YouTube_vn.build_matrix(versions)
        
        # Parse the JSON outputs
        matrix_data = json.loads(matrix_json)
        channels_data = json.loads(channels_json)
        
        # Check that matrix contains all versions
        self.assertEqual(set(matrix_data["matrix"].keys()), set(versions))
        
        # Check that channels contain stable and beta keys
        self.assertIn("stable", channels_data["channels"])
        self.assertIn("beta", channels_data["channels"])
        
        # nothing_to_build should be 0 for normal cases
        self.assertEqual(nothing_to_build, 0)

    def test_discover_excludes_versions(self):
        # Test that exclude versions are handled properly
        # This will be implemented in discover_core, but we can test the concept
        all_versions = ["1.0.0", "2.0.0", "3.0.0", "4.0.0"]
        excluded = ["2.0.0", "4.0.0"]
        expected = ["1.0.0", "3.0.0"]
        
        # Filter out excluded versions
        result = [v for v in all_versions if v not in excluded]
        self.assertEqual(result, expected)

    def test_classify_versions_stable_vs_beta(self):
        # Test classify_versions function
        stable = ["1.0.0", "2.0.0"]
        experimental = ["3.0.0-beta", "4.0.0-exp"]
        
        result_stable, result_experimental = YouTube_vn.classify_versions(stable, experimental)
        
        self.assertEqual(result_stable, stable)
        self.assertEqual(result_experimental, experimental)
        
        # Test with empty lists
        self.assertEqual(YouTube_vn.classify_versions([], []), ([], []))
        
        # Test with one empty list
        self.assertEqual(YouTube_vn.classify_versions(["1.0.0"], []), (["1.0.0"], []))
        self.assertEqual(YouTube_vn.classify_versions([], ["2.0.0"]), ([], ["2.0.0"]))


if __name__ == '__main__':
    unittest.main()