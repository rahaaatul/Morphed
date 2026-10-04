import argparse
import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

proton_vpn = load()

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "proton-vpn"

EXPECTED_PATCHES = [
    "Change installer source",
    "Disable Play Store updates",
    "Unlock VPN Plus",
    "Remove delay",
    "Unlock LAN connections",
    "Unlock custom DNS",
    "Unlock split tunneling",
]


def load_options():
    with open(FIXTURES / "options.json") as fh:
        return json.load(fh)


def unique_enabled_names(options):
    seen = set()
    for entry in options:
        for name, patch in (entry.get("patches") or {}).items():
            if patch.get("enabled") is True:
                seen.add(name)
    return seen


class NormalizeListTest(unittest.TestCase):
    def test_json_array(self):
        self.assertEqual(
            proton_vpn.normalize_list(json.dumps(EXPECTED_PATCHES)),
            EXPECTED_PATCHES,
        )

    def test_newlines(self):
        self.assertEqual(
            proton_vpn.normalize_list("\n".join(EXPECTED_PATCHES)),
            EXPECTED_PATCHES,
        )

    def test_json_array_drops_empty_entries(self):
        self.assertEqual(proton_vpn.normalize_list('["a","","b"]'), ["a", "b"])

    def test_newlines_drops_blank_lines(self):
        self.assertEqual(proton_vpn.normalize_list("a\n\nb\n"), ["a", "b"])

    def test_invalid_json_array_returns_empty(self):
        self.assertEqual(proton_vpn.normalize_list("[not json"), [])

    def test_empty_string_returns_empty(self):
        self.assertEqual(proton_vpn.normalize_list(""), [])


class EnablePatchesTest(unittest.TestCase):
    def test_all_present_all_enabled_no_missing(self):
        options = load_options()
        patched, missing = proton_vpn.enable_patches(options, EXPECTED_PATCHES)
        self.assertEqual(missing, [])
        enabled = unique_enabled_names(patched)
        for name in EXPECTED_PATCHES:
            self.assertIn(name, enabled)
        self.assertEqual(len(enabled), len(EXPECTED_PATCHES))
        for entry in patched:
            for name, patch in (entry.get("patches") or {}).items():
                if name not in EXPECTED_PATCHES:
                    self.assertFalse(patch.get("enabled", False))

    def test_missing_name_reported(self):
        options = load_options()
        want = EXPECTED_PATCHES + ["Nonexistent patch"]
        patched, missing = proton_vpn.enable_patches(options, want)
        self.assertIn("Nonexistent patch", missing)
        enabled = unique_enabled_names(patched)
        for name in EXPECTED_PATCHES:
            self.assertIn(name, enabled)

    def test_does_not_disable_pre_enabled(self):
        options = [
            {
                "package": "p",
                "patches": {
                    "Already on": {"enabled": True},
                    "Turn on": {"enabled": False},
                },
            }
        ]
        patched, missing = proton_vpn.enable_patches(options, ["Turn on"])
        self.assertEqual(missing, [])
        self.assertTrue(patched[0]["patches"]["Already on"]["enabled"])
        self.assertTrue(patched[0]["patches"]["Turn on"]["enabled"])


class SelectOptionsCliTest(unittest.TestCase):
    def test_exits_nonzero_when_a_name_is_missing(self):
        options = load_options()
        options = load_options()
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "options.json"
            path.write_text(json.dumps(options))
            expected = json.dumps(EXPECTED_PATCHES + ["Nonexistent patch"])
            with mock.patch.object(proton_vpn.subprocess, "run") as fake_run:
                fake_run.return_value = subprocess_completed(0)
                with self.assertRaises(SystemExit) as cm:
                    proton_vpn.main(
                        [
                            "select-options",
                            "--options",
                            str(path),
                            "--package",
                            "ch.protonvpn.android",
                            "-p",
                            "morphe-patches.mpp",
                            "-p",
                            "doom-patches.mpp",
                            "-p",
                            "hoodles-patches.mpp",
                            "--expected",
                            expected,
                        ]
                    )
                self.assertEqual(cm.exception.code, 1)
            written = json.loads(path.read_text())
            self.assertEqual(len(unique_enabled_names(written)), len(EXPECTED_PATCHES))

    def test_enables_all_and_writes_options(self):
        options = load_options()
        expected = json.dumps(EXPECTED_PATCHES)
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "options.json"
            path.write_text(json.dumps(options))
            with mock.patch.object(proton_vpn.subprocess, "run") as fake_run:
                fake_run.return_value = subprocess_completed(0)
                rc = proton_vpn.main(
                    [
                        "select-options",
                        "--options",
                        str(path),
                        "--package",
                        "ch.protonvpn.android",
                        "-p",
                        "morphe-patches.mpp",
                        "-p",
                        "doom-patches.mpp",
                        "-p",
                        "hoodles-patches.mpp",
                        "--expected",
                        expected,
                    ]
                )
            self.assertEqual(rc, 0)
            written = json.loads(path.read_text())
            self.assertEqual(unique_enabled_names(written), set(EXPECTED_PATCHES))


class _Completed:
    def __init__(self, returncode):
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


def subprocess_completed(returncode):
    return _Completed(returncode)


if __name__ == "__main__":
    unittest.main()
