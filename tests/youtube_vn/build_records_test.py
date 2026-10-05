import os
import pathlib
import sys
import tempfile
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

YouTube_vn = load()


class BuildRecordsTest(unittest.TestCase):
    def test_builds_records_sorted_newest_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = pathlib.Path(tmp) / "release"
            applied_dir = pathlib.Path(tmp) / "applied"
            failed_dir = pathlib.Path(tmp) / "failed"
            release_dir.mkdir()
            applied_dir.mkdir()
            failed_dir.mkdir()

            # Create two APK files with different versions
            (release_dir / "app-release-21.39.522.apk").write_bytes(b"fake apk")
            (release_dir / "app-release-21.40.156.apk").write_bytes(b"fake apk")

            # Create applied and failed lists for each version
            for v in ["21.39.522", "21.40.156"]:
                (applied_dir / f"applied-{v}.txt").write_text("patch1\npatch2\n")
                (failed_dir / f"failed-{v}.txt").write_text("patch3\n")

            records = YouTube_vn.build_records(str(release_dir), str(applied_dir), str(failed_dir), "arm64-v8a")
            self.assertEqual(len(records), 2)
            # Should be sorted newest first: 21.40.156 then 21.39.522
            self.assertEqual(records[0]["version"], "21.40.156")
            self.assertEqual(records[1]["version"], "21.39.522")
            # Check that the applied and failed lists are correct
            self.assertEqual(records[0]["applied"], ["patch1", "patch2"])
            self.assertEqual(records[0]["failed"], ["patch3"])
            self.assertEqual(records[1]["applied"], ["patch1", "patch2"])
            self.assertEqual(records[1]["failed"], ["patch3"])
            # Check size and arch
            self.assertEqual(records[0]["arch"], "arm64-v8a")
            self.assertEqual(records[1]["arch"], "arm64-v8a")
            # Size is in MB, and our fake apk is 0 bytes, so size should be 0
            self.assertEqual(records[0]["size"], 0)
            self.assertEqual(records[1]["size"], 0)

    def test_build_records_missing_applied_txt_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = pathlib.Path(tmp) / "release"
            applied_dir = pathlib.Path(tmp) / "applied"
            failed_dir = pathlib.Path(tmp) / "failed"
            release_dir.mkdir()
            applied_dir.mkdir()
            failed_dir.mkdir()

            # Create an APK file
            (release_dir / "app-release-21.39.522.apk").write_bytes(b"fake apk")

            # Create the failed list but not the applied list
            (failed_dir / f"failed-21.39.522.txt").write_text("patch3\n")

            # We expect the function to print an error and exit with non-zero
            with self.assertRaises(SystemExit) as cm:
                YouTube_vn.build_records(str(release_dir), str(applied_dir), str(failed_dir), "arm64-v8a")
            self.assertEqual(cm.exception.code, 1)

    def test_build_records_missing_failed_txt_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = pathlib.Path(tmp) / "release"
            applied_dir = pathlib.Path(tmp) / "applied"
            failed_dir = pathlib.Path(tmp) / "failed"
            release_dir.mkdir()
            applied_dir.mkdir()
            failed_dir.mkdir()

            # Create an APK file
            (release_dir / "app-release-21.39.522.apk").write_bytes(b"fake apk")

            # Create the applied list but not the failed list
            (applied_dir / f"applied-21.39.522.txt").write_text("patch1\npatch2\n")

            # We expect the function to print an error and exit with non-zero
            with self.assertRaises(SystemExit) as cm:
                YouTube_vn.build_records(str(release_dir), str(applied_dir), str(failed_dir), "arm64-v8a")
            self.assertEqual(cm.exception.code, 1)