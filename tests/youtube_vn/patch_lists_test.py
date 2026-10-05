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

APPLIED = [
    "Change installer source",
    "Disable Play Store updates",
    "Remove delay",
    "Unlock LAN connections",
    "Unlock split tunneling",
    "Unlock custom DNS",
]
FAILED = ["Unlock VPN Plus"]


def load_result(name):
    with open(FIXTURES / name) as fh:
        return json.load(fh)


class PatchListTest(unittest.TestCase):
    def test_extract_applied_and_failed(self):
        result = load_result("result-5.20.57.0.json")
        applied, failed = YouTube_vn.extract_patch_lists(result)
        self.assertEqual(applied, APPLIED)
        self.assertEqual(failed, FAILED)

    def test_null_and_missing_patch_names_are_skipped(self):
        result = {
            "appliedPatches": [{"name": None}, {"name": "Keep me"}, {"name": None}],
            "failedPatches": [{"patch": None}, {"patch": {"name": "Fail"}}],
        }
        applied, failed = YouTube_vn.extract_patch_lists(result)
        self.assertEqual(applied, ["Keep me"])
        self.assertEqual(failed, ["Fail"])

    def test_handles_empty_result_and_missing_arrays(self):
        applied, failed = YouTube_vn.extract_patch_lists({})
        self.assertEqual(applied, [])
        self.assertEqual(failed, [])

    def test_preserves_order(self):
        result = {
            "appliedPatches": [{"name": "z"}, {"name": "a"}, {"name": "m"}],
            "failedPatches": [{"patch": {"name": "f"}}],
        }
        applied, failed = YouTube_vn.extract_patch_lists(result)
        self.assertEqual(applied, ["z", "a", "m"])
        self.assertEqual(failed, ["f"])


# We'll also need a test for parse_list_patches, but let's first implement the functions and then add the test.