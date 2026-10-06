import json
import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

YouTube_vn = load()


class RecordStateTest(unittest.TestCase):
    def test_builds_new_schema_with_morpheapp_source(self):
        # Test that youtube_state produces the correct schema with MorpheApp source
        records = [
            {
                "version": "19.0.0",
                "applied": ["patch1", "patch2"],
                "failed": ["patch3"],
            }
        ]
        sources_map = {
            "MorpheApp": {
                "version": "1.46.0-dev.8",
                "list": ["patch1", "patch2", "patch3"],
            }
        }
        result = YouTube_vn.youtube_state(records, "morphe-patches.mpp", "com.google.android.youtube", sources_map)
        self.assertEqual(result["owner"], "MorpheApp")
        self.assertEqual(result["repo"], "morphe-patches")
        self.assertEqual(result["package"], "com.google.android.youtube")
        self.assertEqual(result["versions"], ["19.0.0"])
        self.assertEqual(result["patcher"], "morphe-patches.mpp")
        self.assertIn("MorpheApp", result["sources"])
        morpheapp = result["sources"]["MorpheApp"]
        self.assertEqual(morpheapp["version"], "1.46.0-dev.8")
        self.assertEqual(morpheapp["list"], ["patch1", "patch2", "patch3"])
        self.assertEqual(morpheapp["patches"]["patch1"]["applied"], ["19.0.0"])
        self.assertEqual(morpheapp["patches"]["patch1"]["failed"], [])
        self.assertEqual(morpheapp["patches"]["patch3"]["applied"], [])
        self.assertEqual(morpheapp["patches"]["patch3"]["failed"], ["19.0.0"])

    def test_versions_keep_newest_first(self):
        # Test that versions are sorted newest first
        records = [
            {"version": "18.0.0", "applied": [], "failed": []},
            {"version": "19.0.0", "applied": [], "failed": []},
            {"version": "17.0.0", "applied": [], "failed": []},
        ]
        result = YouTube_vn.youtube_state(records, "morphe-patches.mpp", "com.google.android.youtube", {})
        self.assertEqual(result["versions"], ["19.0.0", "18.0.0", "17.0.0"])

    def test_patch_history_kept_per_version(self):
        # Test that patch history is kept per version
        records = [
            {"version": "19.0.0", "applied": ["patch1"], "failed": []},
            {"version": "18.0.0", "applied": [], "failed": ["patch2"]},
        ]
        sources_map = {
            "MorpheApp": {
                "version": "1.46.0-dev.8",
                "list": ["patch1", "patch2"],
            }
        }
        result = YouTube_vn.youtube_state(records, "morphe-patches.mpp", "com.google.android.youtube", sources_map)
        morpheapp = result["sources"]["MorpheApp"]
        self.assertEqual(morpheapp["patches"]["patch1"]["applied"], ["19.0.0"])
        self.assertEqual(morpheapp["patches"]["patch1"]["failed"], [])
        self.assertEqual(morpheapp["patches"]["patch2"]["applied"], [])
        self.assertEqual(morpheapp["patches"]["patch2"]["failed"], ["18.0.0"])


if __name__ == '__main__':
    unittest.main()