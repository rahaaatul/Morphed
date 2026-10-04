import json
import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

proton_vpn = load()

VERSIONS_3 = ["5.20.57.0", "5.20.39.0", "5.20.21.0"]
VERSIONS_LIVE = [
    "5.20.57.0", "5.20.39.0", "5.20.21.0", "5.20.8.0", "5.19.99.0",
    "5.19.78.0", "5.19.72.0", "5.19.61.0",
]


class BuildMatrixTest(unittest.TestCase):
    def test_two_versions(self):
        matrix, channels, nothing_to_build = proton_vpn.build_matrix(VERSIONS_3[:2])
        self.assertEqual(
            matrix,
            '{"include":[{"version":"5.20.57.0","channel":"Canary"},'
            '{"version":"5.20.39.0","channel":"Canary"}]}',
        )
        self.assertEqual(channels, '{"5.20.57.0":"Canary","5.20.39.0":"Canary"}')
        self.assertEqual(nothing_to_build, 0)
        json.loads(matrix)
        json.loads(channels)

    def test_single_version(self):
        matrix, channels, nothing_to_build = proton_vpn.build_matrix(["5.20.57.0"])
        self.assertEqual(matrix, '{"include":[{"version":"5.20.57.0","channel":"Canary"}]}')
        self.assertEqual(channels, '{"5.20.57.0":"Canary"}')
        self.assertEqual(nothing_to_build, 0)

    def test_empty_yields_noop_sentinel(self):
        matrix, channels, nothing_to_build = proton_vpn.build_matrix([])
        self.assertEqual(matrix, '{"include":[{"noop":true}]}')
        self.assertEqual(channels, "{}")
        self.assertEqual(nothing_to_build, 1)
        self.assertEqual(json.loads(matrix), {"include": [{"noop": True}]})
        self.assertEqual(json.loads(channels), {})

    def test_blanks_in_input_are_skipped(self):
        matrix, channels, ntb = proton_vpn.build_matrix(["", "5.20.39.0", ""])
        self.assertEqual(matrix, '{"include":[{"version":"5.20.39.0","channel":"Canary"}]}')
        self.assertEqual(ntb, 0)

    def test_live_eight_versions(self):
        matrix, channels, nothing_to_build = proton_vpn.build_matrix(VERSIONS_LIVE)
        self.assertEqual(json.loads(matrix)["include"], [
            {"version": v, "channel": "Canary"} for v in VERSIONS_LIVE
        ])
        self.assertEqual(json.loads(channels), {v: "Canary" for v in VERSIONS_LIVE})
        self.assertEqual(nothing_to_build, 0)


if __name__ == "__main__":
    unittest.main()
