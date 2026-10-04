import json
import pathlib
import subprocess
import sys
import tempfile
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

proton_vpn = load()

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "proton-vpn"

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


def jq_lines(program, path):
    out = subprocess.run(
        ["jq", "-r", program, str(path)],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


class ExtractPatchListsTest(unittest.TestCase):
    def test_extract_applied_and_failed(self):
        result = load_result("result-5.20.57.0.json")
        applied, failed = proton_vpn.extract_patch_lists(result)
        self.assertEqual(applied, APPLIED)
        self.assertEqual(failed, FAILED)

    def test_null_and_missing_patch_names_are_skipped(self):
        result = {
            "appliedPatches": [{"name": None}, {"name": "Keep me"}, {"name": None}],
            "failedPatches": [{"patch": None}, {"patch": {"name": "Fail"}}],
        }
        applied, failed = proton_vpn.extract_patch_lists(result)
        self.assertEqual(applied, ["Keep me"])
        self.assertEqual(failed, ["Fail"])

    def test_handles_empty_result_and_missing_arrays(self):
        applied, failed = proton_vpn.extract_patch_lists({})
        self.assertEqual(applied, [])
        self.assertEqual(failed, [])

    def test_preserves_order(self):
        result = {
            "appliedPatches": [{"name": "z"}, {"name": "a"}, {"name": "m"}],
            "failedPatches": [{"patch": {"name": "f"}}],
        }
        applied, failed = proton_vpn.extract_patch_lists(result)
        self.assertEqual(applied, ["z", "a", "m"])
        self.assertEqual(failed, ["f"])


class PatchListBytesMatchJqTest(unittest.TestCase):
    """The Python extractor must be byte-for-byte equal to the jq it replaces."""

    def test_applied_matches_jq(self):
        path = FIXTURES / "result-5.20.57.0.json"
        result = load_result("result-5.20.57.0.json")
        applied, _ = proton_vpn.extract_patch_lists(result)
        expected = "".join(name + "\n" for name in applied)
        self.assertEqual(jq_lines(".appliedPatches[]?.name // empty", path), expected)

    def test_failed_matches_jq(self):
        path = FIXTURES / "result-5.20.57.0.json"
        result = load_result("result-5.20.57.0.json")
        _, failed = proton_vpn.extract_patch_lists(result)
        expected = "".join(name + "\n" for name in failed)
        self.assertEqual(jq_lines(".failedPatches[]?.patch.name // empty", path), expected)

    def test_empty_lists_match_jq(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = pathlib.Path(tmp) / "empty.json"
            empty.write_text('{"appliedPatches": [], "failedPatches": []}')
            self.assertEqual(jq_lines(".appliedPatches[]?.name // empty", empty), "")
            self.assertEqual(jq_lines(".failedPatches[]?.patch.name // empty", empty), "")


class WritePatchListsCliTest(unittest.TestCase):
    def test_writes_files_byte_identical_to_jq(self):
        path = FIXTURES / "result-5.20.57.0.json"
        with tempfile.TemporaryDirectory() as tmp:
            applied_out = pathlib.Path(tmp) / "applied.txt"
            failed_out = pathlib.Path(tmp) / "failed.txt"
            rc = proton_vpn.main([
                "write-patch-lists",
                "--result", str(path),
                "--applied-out", str(applied_out),
                "--failed-out", str(failed_out),
            ])
            self.assertEqual(rc, 0)
            self.assertEqual(
                applied_out.read_text(),
                jq_lines(".appliedPatches[]?.name // empty", path),
            )
            self.assertEqual(
                failed_out.read_text(),
                jq_lines(".failedPatches[]?.patch.name // empty", path),
            )

    def test_empty_files_for_empty_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            applied_out = pathlib.Path(tmp) / "applied.txt"
            failed_out = pathlib.Path(tmp) / "failed.txt"
            empty = pathlib.Path(tmp) / "empty.json"
            empty.write_text("{}")
            rc = proton_vpn.main([
                "write-patch-lists",
                "--result", str(empty),
                "--applied-out", str(applied_out),
                "--failed-out", str(failed_out),
            ])
            self.assertEqual(rc, 0)
            self.assertEqual(applied_out.read_text(), "")
            self.assertEqual(failed_out.read_text(), "")


if __name__ == "__main__":
    unittest.main()
