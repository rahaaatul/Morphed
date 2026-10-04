import json
import pathlib
import subprocess
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

proton_vpn = load()

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "proton-vpn"


def jq_stdout(program, path):
    out = subprocess.run(
        ["jq", "-r", program, str(path)],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def awk_stdout(program, path):
    with open(path) as fh:
        text = fh.read()
    out = subprocess.run(
        ["awk", program],
        input=text, capture_output=True, text=True, check=True,
    )
    return out.stdout


class ParseListVersionsTest(unittest.TestCase):
    def test_parses_tag_names_newest_first_drops_drafts(self):
        releases = FIXTURES / "releases.json"
        jq_program = ".[] | select(.draft == false) | .tag_name"
        expected = jq_stdout(jq_program, releases).splitlines()
        text = jq_stdout(jq_program, releases)
        self.assertEqual(proton_vpn.parse_list_versions(text), expected)

    def test_round_trip_matches_jq_bytes(self):
        releases = FIXTURES / "releases.json"
        jq_program = ".[] | select(.draft == false) | .tag_name"
        raw = jq_stdout(jq_program, releases)
        parsed = proton_vpn.parse_list_versions(raw)
        self.assertEqual("".join(v + "\n" for v in parsed), raw)

    def test_empty_and_blank_lines_dropped(self):
        self.assertEqual(proton_vpn.parse_list_versions(""), [])
        self.assertEqual(proton_vpn.parse_list_versions("\n\n5.20.39.0\n\n"),
                         ["5.20.39.0"])


class ParseListPatchesTest(unittest.TestCase):
    def test_enabled_names_match_awk(self):
        path = FIXTURES / "list-patches.txt"
        awk_program = '/^Name: /{n=substr($0,7)} /^Enabled: /{if($2=="true") print n}'
        expected = awk_stdout(awk_program, path).splitlines()
        text = path.read_text()
        self.assertEqual(proton_vpn.parse_list_patches(text), expected)

    def test_round_trip_matches_awk_bytes(self):
        path = FIXTURES / "list-patches.txt"
        awk_program = '/^Name: /{n=substr($0,7)} /^Enabled: /{if($2=="true") print n}'
        raw = awk_stdout(awk_program, path)
        parsed = proton_vpn.parse_list_patches(path.read_text())
        self.assertEqual("".join(n + "\n" for n in parsed), raw)

    def test_disabled_and_unrelated_are_excluded(self):
        text = (
            "Name: Unlock VPN Plus\nEnabled: false\n"
            "Name: Change installer source\nEnabled: true\n"
            "Name: Unrelated\nEnabled: false\n"
        )
        self.assertEqual(proton_vpn.parse_list_patches(text),
                         ["Change installer source"])

    def test_blank_input(self):
        self.assertEqual(proton_vpn.parse_list_patches(""), [])


class ParseVersionLinesTest(unittest.TestCase):
    def test_matches_sed_capture(self):
        # Replicates bash: java list-versions | sed -nE 's/^[[:space:]]*([0-9]+(\.[0-9]+)+).*/\1/p'
        path = FIXTURES / "list-versions.txt"
        text = path.read_text()
        sed = subprocess.run(
            ["sed", "-nE", 's/^[[:space:]]*([0-9]+(\\.[0-9]+)+).*/\\1/p'],
            input=text, text=True, capture_output=True, check=True,
        ).stdout
        parsed = proton_vpn.parse_version_lines(text)
        self.assertEqual(parsed, sed.splitlines())
        self.assertEqual("".join(v + "\n" for v in parsed), sed)

    def test_first_is_recommended(self):
        text = (path := (FIXTURES / "list-versions.txt")).read_text()
        self.assertEqual(proton_vpn.parse_version_lines(text)[0], "5.19.78.0")

    def test_no_version_lines_returns_empty(self):
        self.assertEqual(proton_vpn.parse_version_lines("no versions here\nok"), [])


if __name__ == "__main__":
    unittest.main()
