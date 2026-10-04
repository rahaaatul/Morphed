import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

proton_vpn = load()

VERSIONS_10 = [
    "5.20.57.0", "5.20.39.0", "5.20.21.0", "5.20.8.0", "5.19.99.0",
    "5.19.78.0", "5.19.72.0", "5.19.61.0", "5.19.60.0", "5.19.59.0",
]
VERSIONS_LIVE = [
    "5.20.57.0", "5.20.39.0", "5.20.21.0", "5.20.8.0", "5.19.99.0",
    "5.19.78.0", "5.19.72.0", "5.19.61.0",
]


class ChooseCoverTest(unittest.TestCase):
    def test_live_recs_cover_all_eight(self):
        # doom=5.19.78.0 (1-based index 6), hoo=5.20.57.0 (index 1)
        # deepest=6, want_n=6+2=8, MAX_VERSIONS=10 -> head -8 covers all 8.
        result = proton_vpn.choose_cover(VERSIONS_LIVE, ["5.19.78.0", "5.20.57.0"], 10)
        self.assertEqual(result, VERSIONS_LIVE)

    def test_walk_back_past_older_recommended(self):
        # doom=5.20.21.0 (index 3), hoo=5.20.57.0 (index 1) -> deepest=3, want_n=5.
        result = proton_vpn.choose_cover(VERSIONS_10, ["5.20.21.0", "5.20.57.0"], 10)
        self.assertEqual(result, VERSIONS_10[:5])
        self.assertIn("5.20.57.0", result)
        self.assertNotIn("5.19.61.0", result)

    def test_clamps_to_max_versions(self):
        # rec 5.19.59.0 at index 10 -> want_n=12, clamped to MAX_VERSIONS=5.
        result = proton_vpn.choose_cover(VERSIONS_10, ["5.19.59.0"], 5)
        self.assertEqual(len(result), 5)
        self.assertEqual(result, VERSIONS_10[:5])

    def test_neither_recommended_defaults_to_three(self):
        # Neither rec found -> deepest defaults to 3 -> want_n = 5.
        result = proton_vpn.choose_cover(VERSIONS_10, ["99.0", "88.0"], 10)
        self.assertEqual(len(result), 5)
        self.assertEqual(result, VERSIONS_10[:5])

    def test_empty_recommended_defaults_to_three(self):
        result = proton_vpn.choose_cover(VERSIONS_10, [None, ""], 10)
        self.assertEqual(len(result), 5)

    def test_want_n_exceeding_list_clamps_to_length(self):
        # 4 versions; rec 5.20.8.0 at 1-based index 4 -> want_n=6 -> head -6 -> all 4.
        versions = VERSIONS_10[:4]
        result = proton_vpn.choose_cover(versions, ["5.20.8.0"], 10)
        self.assertEqual(result, versions)
        self.assertEqual(len(result), 4)


if __name__ == "__main__":
    unittest.main()
