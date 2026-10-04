import json
import os
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

SOURCES_MAP = {
    "MorpheApp": {
        "version": "1.46.0-dev.2",
        "list": ["Change installer source", "Disable Play Store updates"],
    },
    "rushiranpise": {
        "version": "1.23.0-dev.1",
        "list": ["Unlock VPN Plus"],
    },
    "hoo-dles": {
        "version": "1.47.0-dev.3",
        "list": ["Unlock custom DNS", "Remove delay",
                 "Unlock LAN connections", "Unlock split tunneling"],
    },
}

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
        "applied": list(SOURCES_MAP["MorpheApp"]["list"]) + ["Unlock VPN Plus"]
        + SOURCES_MAP["hoo-dles"]["list"],
        "failed": [],
    },
]


def _expected_record():
    patches = {}
    for owner, src in SOURCES_MAP.items():
        for name in src["list"]:
            applied = [r["version"] for r in RECORDS if name in r["applied"]]
            failed = [r["version"] for r in RECORDS if name in r["failed"]]
            patches.setdefault(owner, {})
            patches[owner][name] = {"applied": applied, "failed": failed}
    sources = {
        owner: {
            "version": src["version"],
            "list": list(src["list"]),
            "patches": patches[owner],
        }
        for owner, src in SOURCES_MAP.items()
    }
    return {
        "owner": "ProtonVPN",
        "repo": "android-app",
        "package": "ch.protonvpn.android",
        "versions": ["5.20.57.0", "5.20.39.0"],
        "patcher": "1.18.1-dev.4",
        "sources": sources,
    }


class RecordStateTest(unittest.TestCase):
    def test_builds_new_schema_with_sibling_sources(self):
        record = proton_vpn.record_state(
            RECORDS, "1.18.1-dev.4", "ch.protonvpn.android", SOURCES_MAP)
        self.assertEqual(record, _expected_record())

    def test_versions_keep_newest_first(self):
        record = proton_vpn.record_state(
            list(reversed(RECORDS)), "1.18.1-dev.4",
            "ch.protonvpn.android", SOURCES_MAP)
        self.assertEqual(record["versions"], ["5.20.57.0", "5.20.39.0"])

    def test_attribution_keys_each_patch_to_its_source(self):
        record = proton_vpn.record_state(
            RECORDS, "1.18.1-dev.4", "ch.protonvpn.android", SOURCES_MAP)
        # Unlock VPN Plus is rushiranpise-only: applied on 5.20.39.0, failed on 5.20.57.0.
        self.assertEqual(
            record["sources"]["rushiranpise"]["patches"]["Unlock VPN Plus"],
            {"applied": ["5.20.39.0"], "failed": ["5.20.57.0"]})
        # MorpheApp universal patches apply to both.
        self.assertEqual(
            record["sources"]["MorpheApp"]["patches"]["Change installer source"],
            {"applied": ["5.20.57.0", "5.20.39.0"], "failed": []})

    def test_patch_not_targeting_a_version_is_empty_in_both(self):
        records = [
            {"version": "5.20.57.0", "applied": ["Remove delay"], "failed": []},
        ]
        record = proton_vpn.record_state(
            records, "1.18.1-dev.4", "ch.protonvpn.android", SOURCES_MAP)
        self.assertEqual(
            record["sources"]["hoo-dles"]["patches"]["Unlock custom DNS"],
            {"applied": [], "failed": []})


class GateRebuildTest(unittest.TestCase):
    def _sources(self, rush="1.23.0-dev.1", hoo="1.47.0-dev.3"):
        return {
            "MorpheApp": {"version": "1.46.0-dev.2", "list": []},
            "rushiranpise": {"version": rush, "list": []},
            "hoo-dles": {"version": hoo, "list": []},
        }

    def test_no_last_state_rebuilds(self):
        self.assertEqual(proton_vpn.gate_rebuild(None, self._sources(), ["5.20.57.0"]), 0)

    def test_proton_targeting_bundle_change_rebuilds(self):
        last = {"sources": self._sources(), "versions": ["5.20.57.0"]}
        self.assertEqual(
            proton_vpn.gate_rebuild(
                last, self._sources(rush="1.23.0-dev.2"), ["5.20.57.0"]), 0)

    def test_morpheapp_only_change_is_noop(self):
        last = {
            "sources": {
                "MorpheApp": {"version": "1.46.0-dev.2"},
                "rushiranpise": {"version": "1.23.0-dev.1"},
                "hoo-dles": {"version": "1.47.0-dev.3"},
            },
            "versions": ["5.20.57.0", "5.20.39.0"],
        }
        # MorpheApp version moved, rush/hoo + versions unchanged -> no-op.
        sources = {
            "MorpheApp": {"version": "1.47.0-dev.1", "list": []},
            "rushiranpise": {"version": "1.23.0-dev.1", "list": []},
            "hoo-dles": {"version": "1.47.0-dev.3", "list": []},
        }
        self.assertEqual(proton_vpn.gate_rebuild(
            last, sources, ["5.20.57.0", "5.20.39.0"]), 1)

    def test_patcher_only_change_is_noop(self):
        last = {"sources": self._sources(), "versions": ["5.20.57.0", "5.20.39.0"]}
        # Last file's patcher differs, but sources + versions unchanged -> no-op.
        last_with_patcher = dict(last)
        last_with_patcher["patcher"] = "1.18.0-dev.3"
        self.assertEqual(
            proton_vpn.gate_rebuild(
                last_with_patcher, self._sources(), ["5.20.57.0", "5.20.39.0"]), 1)

    def test_range_change_rebuilds(self):
        last = {"sources": self._sources(), "versions": ["5.20.57.0"]}
        self.assertEqual(
            proton_vpn.gate_rebuild(
                last, self._sources(), ["5.20.57.0", "5.20.39.0"]), 0)

    def test_missing_targeting_source_in_last_rebuilds(self):
        last = {"sources": {}, "versions": ["5.20.57.0"]}
        self.assertEqual(
            proton_vpn.gate_rebuild(last, self._sources(), ["5.20.57.0"]), 0)


if __name__ == "__main__":
    unittest.main()
