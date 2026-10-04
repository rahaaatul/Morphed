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

EXPECTED_PATCHES = [
    "Unlock VPN Plus",
    "Unlock custom DNS",
    "Remove delay",
    "Unlock LAN connections",
    "Unlock split tunneling",
    "Change installer source",
    "Disable Play Store updates",
]

VERSIONS_LIVE = [
    "5.20.57.0", "5.20.39.0", "5.20.21.0", "5.20.8.0", "5.19.99.0",
    "5.19.78.0", "5.19.72.0", "5.19.61.0",
]


class _Completed:
    def __init__(self, stdout, returncode=0):
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


DOOM_LIST_VERSIONS = ("INFO: Package name: ch.protonvpn.android\n"
                      "Most common compatible versions:\n\t5.19.78.0 [versionCodes: x] (1 patch)\n \n")
HOO_LIST_VERSIONS = ("INFO: Package name: ch.protonvpn.android\n"
                     "Most common compatible versions:\n\t5.20.57.0 (4 patches)\n \n")
MPP_LIST_VERSIONS = "INFO: Package name: ch.protonvpn.android\n"
MPP_LIST_VERSIONS_EXP = "INFO: Package name: ch.protonvpn.android\n"
DOOM_LIST_VERSIONS_EXP = "5.19.78.0\n"
HOO_LIST_VERSIONS_EXP = "5.20.57.0\n"
DOOM_LIST_PATCHES = (
    "Name: Unlock VPN Plus\nEnabled: true\n"
    "Name: Disable PairIP license check\nEnabled: false\n"
)
HOO_LIST_PATCHES = (
    "Name: Hide app icon\nEnabled: false\n"
    "Name: Unlock custom DNS\nEnabled: true\n"
    "Name: Remove delay\nEnabled: true\n"
    "Name: Unlock LAN connections\nEnabled: true\n"
    "Name: Unlock split tunneling\nEnabled: true\n"
)


def _releases_payload():
    return [
        {"tag_name": v, "draft": False, "prerelease": False,
         "published_at": "2026-01-01T00:00:00Z"}
        for v in VERSIONS_LIVE
    ] + [{"tag_name": "v9.9.9-draft", "draft": True, "prerelease": False,
          "published_at": "2020-01-01T00:00:00Z"}]


def _fake_run(stdout_for):
    lv = {
        ("morphe-patches.mpp", False): stdout_for["lv_mpp"],
        ("morphe-patches.mpp", True): stdout_for["lv_mpp_exp"],
        ("doom-patches.mpp", False): stdout_for["lv_doom"],
        ("doom-patches.mpp", True): stdout_for["lv_doom_exp"],
        ("hoodles-patches.mpp", False): stdout_for["lv_hoo"],
        ("hoodles-patches.mpp", True): stdout_for["lv_hoo_exp"],
    }
    lp = {
        "doom-patches.mpp": stdout_for["lp_doom"],
        "hoodles-patches.mpp": stdout_for["lp_hoo"],
    }

    def fake(cmd, **_kw):
        if cmd[0] == "gh":
            return _Completed(json.dumps(stdout_for.get("releases", [])))
        if "list-versions" in cmd:
            bundle = next((a.split("=", 1)[1]
                           for a in cmd if a.startswith("--patches=")), "")
            flag = "-x" in cmd
            return _Completed(lv[(bundle, flag)])
        if "list-patches" in cmd:
            bundle = next((a.split("=", 1)[1]
                           for a in cmd if a.startswith("--patches=")), "")
            return _Completed(lp[bundle])
        raise AssertionError(f"unexpected subprocess: {cmd}")

    return fake


def _env(tmpdir):
    return {
        "GITHUB_OUTPUT": str(pathlib.Path(tmpdir) / "out.txt"),
        "PACKAGE": "ch.protonvpn.android",
        "UPSTREAM": "ProtonVPN/android-app",
        "EXTRA_PATCHES": "Change installer source\nDisable Play Store updates",
        "GH_TOKEN": "token",
        "DESKTOP_VER": "1.18.1-dev.4",
        "PATCHES_VER": "1.46.0-dev.2",
        "DOOM_VER": "1.23.0-dev.1",
        "HOODLES_VER": "1.47.0-dev.3",
        "PATH": os.environ["PATH"],
    }


class DiscoverSubcommandTest(unittest.TestCase):
    def test_emits_new_outputs_and_rebuilds_when_no_last_state(self):
        stdout_for = {
            "releases": _releases_payload(),
            "lv_mpp": MPP_LIST_VERSIONS,
            "lv_mpp_exp": MPP_LIST_VERSIONS_EXP,
            "lv_doom": DOOM_LIST_VERSIONS,
            "lv_doom_exp": DOOM_LIST_VERSIONS_EXP,
            "lv_hoo": HOO_LIST_VERSIONS,
            "lv_hoo_exp": HOO_LIST_VERSIONS_EXP,
            "lp_doom": DOOM_LIST_PATCHES,
            "lp_hoo": HOO_LIST_PATCHES,
        }
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            state_file = pathlib.Path(tmp) / "tags.json"
            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(proton_vpn.subprocess, "run",
                                   side_effect=_fake_run(stdout_for)):
                rc = proton_vpn.main(
                    ["discover", "--state-file", str(state_file)])
            self.assertEqual(rc, 0)
            out = pathlib.Path(env["GITHUB_OUTPUT"])
            data = {}
            for line in out.read_text().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    data[k] = v

        self.assertEqual(set(data), {
            "matrix", "versions", "patcher", "sources", "expected_patches",
            "recommended", "stable_versions", "experimental_versions", "skip",
        })
        self.assertEqual(data["skip"], "0")  # no last state -> rebuild
        self.assertEqual(data["patcher"], "1.18.1-dev.4")
        self.assertEqual(json.loads(data["versions"]), VERSIONS_LIVE)
        self.assertEqual(json.loads(data["expected_patches"]), EXPECTED_PATCHES)
        self.assertEqual(json.loads(data["stable_versions"]),
                         ["5.19.78.0", "5.20.57.0"])
        self.assertEqual(json.loads(data["experimental_versions"]), [])

        sources = json.loads(data["sources"])
        self.assertEqual(sources["MorpheApp"]["version"], "1.46.0-dev.2")
        self.assertEqual(sources["MorpheApp"]["list"],
                         ["Change installer source", "Disable Play Store updates"])
        self.assertEqual(sources["rushiranpise"]["version"], "1.23.0-dev.1")
        self.assertEqual(sources["rushiranpise"]["list"], ["Unlock VPN Plus"])
        self.assertEqual(sources["hoo-dles"]["version"], "1.47.0-dev.3")
        self.assertEqual(sources["hoo-dles"]["list"], [
            "Unlock custom DNS", "Remove delay",
            "Unlock LAN connections", "Unlock split tunneling"])


class DiscoverGateTest(unittest.TestCase):
    def test_no_op_when_only_morpheapp_and_patcher_changed(self):
        stdout_for = {
            "releases": _releases_payload(),
            "lv_mpp": MPP_LIST_VERSIONS, "lv_mpp_exp": MPP_LIST_VERSIONS_EXP,
            "lv_doom": DOOM_LIST_VERSIONS, "lv_doom_exp": DOOM_LIST_VERSIONS_EXP,
            "lv_hoo": HOO_LIST_VERSIONS, "lv_hoo_exp": HOO_LIST_VERSIONS_EXP,
            "lp_doom": DOOM_LIST_PATCHES, "lp_hoo": HOO_LIST_PATCHES,
        }
        last = {
            "owner": "ProtonVPN", "repo": "android-app",
            "package": "ch.protonvpn.android",
            "versions": VERSIONS_LIVE,
            "patcher": "0.0.0-dev.0",
            "sources": {
                "MorpheApp": {"version": "1.45.0-dev.1", "list": []},
                "rushiranpise": {"version": "1.23.0-dev.1", "list": []},
                "hoo-dles": {"version": "1.47.0-dev.3", "list": []},
            },
        }
        with tempfile.TemporaryDirectory() as tmp:
            env = _env(tmp)
            state_file = pathlib.Path(tmp) / "tags.json"
            state_file.write_text(json.dumps(last) + "\n")
            with mock.patch.dict(os.environ, env, clear=False), \
                 mock.patch.object(proton_vpn.subprocess, "run",
                                   side_effect=_fake_run(stdout_for)):
                rc = proton_vpn.main(
                    ["discover", "--state-file", str(state_file)])
            out = pathlib.Path(env["GITHUB_OUTPUT"])
            data = {}
            for line in out.read_text().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    data[k] = v
        self.assertEqual(rc, 0)
        self.assertEqual(data["skip"], "1")  # morpheapp + patcher changed only


if __name__ == "__main__":
    unittest.main()
