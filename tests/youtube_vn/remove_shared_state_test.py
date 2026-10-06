import json
import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

YouTube_vn = load()


class RemoveSharedStateTest(unittest.TestCase):
    def test_remove_youtube_entry_from_shared_state(self):
        # Test that remove_youtube_entry_from_shared_state removes the youtube key
        # Actual schema: source.MorpheApp.morphe-patches.<tag>.<app>
        test_data = {
            "source": {
                "MorpheApp": {
                    "morphe-patches": {
                        "1.46.0-dev.8": {
                            "youtube": {"versions": ["19.0.0"]},
                            "reddit": {"versions": ["1.0.0"]},
                            "youtube-music": {"versions": ["2.0.0"]},
                        }
                    }
                }
            }
        }
        tmp_path = pathlib.Path("/tmp/test_patch_version.json")
        tmp_path.write_text(json.dumps(test_data, indent=2) + "\n")

        YouTube_vn.remove_youtube_entry_from_shared_state(str(tmp_path))
        result = json.loads(tmp_path.read_text())
        patches = result["source"]["MorpheApp"]["morphe-patches"]["1.46.0-dev.8"]
        self.assertNotIn("youtube", patches)
        self.assertIn("reddit", patches)
        self.assertIn("youtube-music", patches)
        tmp_path.unlink(missing_ok=True)


if __name__ == '__main__':
    unittest.main()