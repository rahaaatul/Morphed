import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

YouTube_vn = load()

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "youtube"


class ParseTest(unittest.TestCase):
    def test_parse_version_lines_extracts_dotted_versions(self):
        text = ("INFO: Package name: com.google.android.youtube\n"
                "Most common compatible versions:\n\t21.39.522 (156 patches)\n \n"
                "\t21.40.156 (98 patches)\n")
        self.assertEqual(YouTube_vn.parse_version_lines(text),
                         ["21.39.522", "21.40.156"])

    def test_parse_version_lines_empty(self):
        self.assertEqual(YouTube_vn.parse_version_lines("   \n\n  "), [])

    def test_strip_apk_version(self):
        self.assertEqual(YouTube_vn.strip_apk_version("app-release-21.39.522.apk"), "21.39.522")
        self.assertEqual(YouTube_vn.strip_apk_version("21.39.522"), "21.39.522")
        self.assertEqual(YouTube_vn.strip_apk_version("release-21.40.156"), "21.40.156")

    def test_version_sort_key(self):
        versions = ["21.39.522", "21.40.156", "20.41.123", "21.39.522"]
        expected = ["20.41.123", "21.39.522", "21.39.522", "21.40.156"]
        self.assertEqual(sorted(versions, key=YouTube_vn.version_sort_key), expected)