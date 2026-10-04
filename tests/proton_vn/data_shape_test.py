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


def jq_stdout(program, args=None):
    cmd = ["jq", "-r", program] if args is None else ["jq"] + args + [program]
    out = subprocess.run(cmd, capture_output=True, text=True, check=True)
    return out.stdout


class ClassifyVersionsTest(unittest.TestCase):
    def test_experimental_excludes_stable_preserving_order(self):
        stable = ["5.19.78.0", "5.20.57.0"]
        experimental = ["5.19.78.0", "5.20.57.0", "5.20.8.0", "5.19.61.0"]
        # bash: declared(-x) | grep -vxF -f <(printf stable)
        expected_exp = subprocess.run(
            ["grep", "-vxF", "-f", "-", "dev/null"],
            input="\n".join(experimental), text=True, capture_output=True,
        )
        # Replicate with a heredoc-free approach: grep -vxF against stable lines.
        import os, tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as sf:
            sf.write("\n".join(stable) + "\n")
            stable_file = sf.name
        try:
            raw = "\n".join(experimental)
            out = subprocess.run(
                ["grep", "-vxF", "-f", stable_file, "-"],
                input=raw, text=True, capture_output=True, check=True,
            ).stdout
            expected = out.splitlines()
        finally:
            os.unlink(stable_file)
        stable_out, exp_out = proton_vpn.classify_versions(stable, experimental)
        self.assertEqual(stable_out, stable)
        self.assertEqual(exp_out, expected)

    def test_no_overlap_keeps_all_experimental(self):
        stable = ["1.0.0"]
        experimental = ["2.0.0", "3.0.0"]
        _, exp_out = proton_vpn.classify_versions(stable, experimental)
        self.assertEqual(exp_out, experimental)


class BuildRecommendedTest(unittest.TestCase):
    def test_matches_qj_new_compact(self):
        doom = "5.19.78.0"
        hoo = "5.20.57.0"
        label = "Doom's Morphe Patches"
        program = (
            '{' + f'($label):' +
            '{url:"https://github.com/rushiranpise/morphe-patches/",value:$d},' +
            '("hoodles Morphe Patches"):' +
            '{url:"https://github.com/hoo-dles/morphe-patches",value:$h}}'
        )
        jq_out = subprocess.run(
            ["jq", "-nc",
             "--arg", "d", doom, "--arg", "h", hoo, "--arg", "label", label,
             program],
            capture_output=True, text=True, check=True,
        ).stdout
        self.assertEqual(proton_vpn.build_recommended(doom, hoo), jq_out.strip())
        # And it parses to the expected dict.
        self.assertEqual(json.loads(proton_vpn.build_recommended(doom, hoo)), {
            "Doom's Morphe Patches": {
                "url": "https://github.com/rushiranpise/morphe-patches/",
                "value": doom,
            },
            "hoodles Morphe Patches": {
                "url": "https://github.com/hoo-dles/morphe-patches",
                "value": hoo,
            },
        })


class BuildExpectedPatchesTest(unittest.TestCase):
    def test_dedup_first_occurrence_drops_blanks(self):
        source_lists = [
            ["Change installer source", "Disable Play Store updates", "A"],
            ["A", "Unlock VPN Plus"],
        ]
        extra = "Change installer source\nDisable Play Store updates"
        result = proton_vpn.build_expected_patches(source_lists, extra)
        self.assertEqual(result, [
            "Change installer source",
            "Disable Play Store updates",
            "A",
            "Unlock VPN Plus",
        ])

    def test_matches_sed_awk_dedup(self):
        items = ["b", "a", "a", "c", "", "b"]
        raw = "\n".join(items)
        sed = subprocess.run(["sed", "/^$/d"], input=raw,
                             text=True, capture_output=True, check=True).stdout
        awk = subprocess.run(["awk", "!seen[$0]++"], input=sed,
                             text=True, capture_output=True, check=True).stdout
        expected = awk.splitlines()
        self.assertEqual(expected, ["b", "a", "c"])
        result = proton_vpn.build_expected_patches([items[:3], items[3:]], "")
        self.assertEqual(result, expected)


if __name__ == "__main__":
    unittest.main()
