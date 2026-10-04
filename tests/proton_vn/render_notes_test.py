import importlib.util  # noqa: F401  (kept for parity with loader bootstrap below)
import os
import pathlib
import sys
import tempfile
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

proton_vpn = load()

FIXTURES = _ROOT / "tests" / "fixtures" / "proton-vpn"

PACKAGE = "ch.protonvpn.android"
EXPECTED = [
    "Unlock VPN Plus",
    "Unlock custom DNS",
    "Remove delay",
    "Unlock LAN connections",
    "Unlock split tunneling",
    "Change installer source",
    "Disable Play Store updates",
]

RECOMMENDED = {
    "Doom's Morphe Patches": {
        "url": "https://github.com/rushiranpise/morphe-patches/",
        "value": "5.19.78.0",
    },
    "hoodles Morphe Patches": {
        "url": "https://github.com/hoo-dles/morphe-patches",
        "value": "5.20.57.0",
    },
}

TOOLS = [
    ["Morphe Desktop", "https://github.com/MorpheApp/morphe-desktop", "v1.18.1-dev.4"],
    ["Morphe Patches", "https://github.com/MorpheApp/morphe-patches", "v1.46.0-dev.2"],
    ["Doom's Morphe Patches", "https://github.com/rushiranpise/morphe-patches/", "v1.23.0-dev.1"],
    ["hoodles Morphe Patches", "https://github.com/hoo-dles/morphe-patches", "v1.47.0-dev.3"],
]

REPO_FULL = "rahaaatul/Morphed"
RELEASE_TAG = "proton-vpn"
ICON_URL = "https://raw.githubusercontent.com/rahaaatul/Morphed/main/.github/icons/download.png"
ICON_WIDTH = "20"

RECORDS = [
    {
        "version": "5.20.57.0",
        "size": 61,
        "arch": "Universal",
        "applied": [
            "Unlock custom DNS",
            "Remove delay",
            "Unlock LAN connections",
            "Unlock split tunneling",
            "Change installer source",
            "Disable Play Store updates",
        ],
        "failed": ["Unlock VPN Plus"],
    },
    {
        "version": "5.20.39.0",
        "size": 63,
        "arch": "Universal",
        "applied": list(EXPECTED),
        "failed": [],
    },
]


class RenderNotesTest(unittest.TestCase):
    def test_render_notes_matches_golden(self):
        body = proton_vpn.render_notes(
            RECORDS,
            EXPECTED,
            RECOMMENDED,
            TOOLS,
            ["5.20.57.0"],
            [],
            repo_full=REPO_FULL,
            release_tag=RELEASE_TAG,
            icon_url=ICON_URL,
            icon_width=ICON_WIDTH,
        )
        golden = (FIXTURES / "release-notes.md").read_text()
        self.assertEqual(body, golden)


class VersionSortKeyTest(unittest.TestCase):
    def test_orders_numeric_per_component(self):
        self.assertGreater(
            proton_vpn.version_sort_key("5.20.21.0"),
            proton_vpn.version_sort_key("5.20.8.0"),
        )

    def test_orders_descending_by_newest(self):
        keys = [proton_vpn.version_sort_key(v) for v in
                ["5.20.57.0", "5.20.39.0", "5.20.21.0", "5.20.8.0"]]
        self.assertEqual(keys, sorted(keys, reverse=True))


class PickAnchorTest(unittest.TestCase):
    def test_returns_newest_fully_patched(self):
        self.assertEqual(proton_vpn.pick_anchor(RECORDS, EXPECTED), "5.20.39.0")

    def test_returns_empty_when_none_complete(self):
        incomplete = [dict(r, applied=["Change installer source",
                                      "Disable Play Store updates"]) for r in RECORDS]
        self.assertEqual(proton_vpn.pick_anchor(incomplete, EXPECTED), "")


class BuildRecordsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.release = root / "release"
        self.applied = root / "applied"
        self.failed = root / "failed"
        for d in (self.release, self.applied, self.failed):
            d.mkdir()
        self._write_apk("5.20.57.0", 64468188)
        self._write_apk("5.20.39.0", 66851480)
        self._write_applied("5.20.57.0", [
            "Unlock custom DNS", "Remove delay", "Unlock LAN connections",
            "Unlock split tunneling", "Change installer source",
            "Disable Play Store updates"])
        self._write_failed("5.20.57.0", ["Unlock VPN Plus"])
        self._write_applied("5.20.39.0", list(EXPECTED))
        self._write_failed("5.20.39.0", [])

    def tearDown(self):
        self.tmp.cleanup()

    def _write_apk(self, version, bytes_):
        path = self.release / f"proton-vpn-v{version}.apk"
        path.touch()
        os.truncate(path, bytes_)

    def _write_applied(self, version, names):
        (self.applied / f"applied-{version}.txt").write_text(
            "\n".join(names) + ("\n" if names else ""))

    def _write_failed(self, version, names):
        (self.failed / f"failed-{version}.txt").write_text(
            "\n".join(names) + ("\n" if names else ""))

    def test_builds_records_newest_first_with_sizes(self):
        records = proton_vpn.build_records(self.release, self.applied,
                                           self.failed, "Universal")
        self.assertEqual([r["version"] for r in records],
                         ["5.20.57.0", "5.20.39.0"])
        first = records[0]
        self.assertEqual(first["size"], 61)          # 64468188 // 1048576
        self.assertEqual(first["arch"], "Universal")
        self.assertEqual(first["failed"], ["Unlock VPN Plus"])
        self.assertCountEqual(first["applied"], [
            "Unlock custom DNS", "Remove delay", "Unlock LAN connections",
            "Unlock split tunneling", "Change installer source",
            "Disable Play Store updates"])

    def test_missing_patch_list_fails(self):
        os.remove(self.applied / "applied-5.20.39.0.txt")
        with self.assertRaises(SystemExit):
            proton_vpn.build_records(self.release, self.applied,
                                     self.failed, "Universal")


if __name__ == "__main__":
    unittest.main()
