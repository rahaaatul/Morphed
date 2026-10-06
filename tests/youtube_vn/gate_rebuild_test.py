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


class GateRebuildTest(unittest.TestCase):
    def setUp(self):
        # Set up common test data
        self.sources = {
            "MorpheApp": {
                "morphe-patches": {
                    "1.0.0": {
                        "youtube": {
                            "patches": ["patch1", "patch2"]
                        }
                    }
                }
            }
        }
        self.covered = ["patch1"]  # patches that have been applied

    def test_build_expected_patches_dedup(self):
        # Test that build_expected_patches deduplicates bundle names
        bundle_names = ["patch1", "patch2", "patch1", "patch3"]
        expected = YouTube_vn.build_expected_patches(bundle_names)
        # Should contain each patch only once
        self.assertEqual(set(expected), set(["patch1", "patch2", "patch3"]))
        # Should preserve order of first occurrence
        self.assertEqual(expected, ["patch1", "patch2", "patch3"])

    def test_build_expected_patches_forced_patches(self):
        # Test that forced patches are included only if absent
        bundle_names = ["patch1", "patch2"]
        # Assuming forced patches are ["Change installer source", "Disable Play store updates"]
        # This would need to be configured somehow
        expected = YouTube_vn.build_expected_patches(bundle_names)
        # For now, just check it returns something
        self.assertIsInstance(expected, list)

    def test_version_range_change_gate(self):
        # To be implemented
        pass

    def test_bundle_version_change_gate(self):
        # To be implemented
        pass

    def test_no_change_gate(self):
        # To be implemented
        pass

if __name__ == '__main__':
    unittest.main()