import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

YouTube_vn = load()


class PickAnchorTest(unittest.TestCase):
    def test_pick_anchor_newest_clean_version(self):
        records = [
            {"version": "21.39.522", "applied": ["patch1", "patch2"], "failed": []},
            {"version": "21.40.156", "applied": ["patch1", "patch2", "patch3"], "failed": []},
            {"version": "21.41.100", "applied": ["patch1", "patch2"], "failed": []},
        ]
        expected = ["patch1", "patch2", "patch3"]
        # The newest version that has all expected patches is 21.40.156
        self.assertEqual(YouTube_vn.pick_anchor(records, expected), "21.40.156")

    def test_pick_anchor_skips_versions_with_failed_patches(self):
        records = [
            {"version": "21.39.522", "applied": ["patch1", "patch2"], "failed": []},
            {"version": "21.40.156", "applied": ["patch1", "patch2"], "failed": ["patch3"]},
            {"version": "21.41.100", "applied": ["patch1", "patch2", "patch3"], "failed": []},
        ]
        expected = ["patch1", "patch2", "patch3"]
        # The newest version is 21.41.100, which has all expected patches (even though 21.40.156 has a failure)
        self.assertEqual(YouTube_vn.pick_anchor(records, expected), "21.41.100")

    def test_pick_anchor_returns_empty_if_no_version_meets_expected(self):
        records = [
            {"version": "21.39.522", "applied": ["patch1"], "failed": []},
            {"version": "21.40.156", "applied": ["patch1", "patch2"], "failed": []},
        ]
        expected = ["patch1", "patch2", "patch3"]
        self.assertEqual(YouTube_vn.pick_anchor(records, expected), "")