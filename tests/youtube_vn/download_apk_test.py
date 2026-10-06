import pathlib
import sys
import unittest
from unittest.mock import patch, MagicMock

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

YouTube_vn = load()


class DownloadApkTest(unittest.TestCase):
    def test_download_apk_calls_bun(self):
        # Test that cmd_download_apk invokes _run_bun with the correct command
        args = MagicMock()
        args.version = "19.0.0"

        with (
            patch.object(YouTube_vn, "_run_bun", return_value="") as mock_bun,
            patch(
                "pathlib.Path.glob", return_value=[pathlib.Path("download/test.apk")]
            ),
        ):
            result = YouTube_vn.cmd_download_apk(args)
            self.assertEqual(result, 0)
            mock_bun.assert_called_once()
            cmd = mock_bun.call_args[0][0]
            self.assertIn("node_modules/apkmirror-downloader/dist/cli.js", cmd)
            self.assertIn("download", cmd)
            self.assertIn("google-inc", cmd)
            self.assertIn("youtube", cmd)
            self.assertIn("--version=19.0.0", cmd)
            self.assertIn("--outdir=download", cmd)

    def test_download_apk_exits_nonzero_when_no_apk(self):
        # Test that cmd_download_apk exits non-zero when no APK appears
        args = MagicMock()
        args.version = "19.0.0"

        # Mock _run_bun to return empty output (no APK found)
        with (
            patch.object(YouTube_vn, "_run_bun", return_value=""),
            patch("pathlib.Path.glob", return_value=[]),
        ):
            result = YouTube_vn.cmd_download_apk(args)
            self.assertEqual(result, 1)


class PatchTest(unittest.TestCase):
    def test_patch_runs_java(self):
        # Test that cmd_patch invokes _run_java with the correct command
        args = MagicMock()
        args.version = "19.0.0"
        args.arch = "arm64-v8a"

        with patch.object(YouTube_vn, "_run_java", return_value="") as mock_java:
            result = YouTube_vn.cmd_patch(args)
            self.assertEqual(result, 0)
            mock_java.assert_called_once()
            cmd = mock_java.call_args[0][0]
            self.assertIn("java", cmd)
            self.assertIn("-jar", cmd)
            self.assertIn("morphe-desktop.jar", cmd)
            self.assertIn("patch", cmd)
            self.assertIn("-p", cmd)
            self.assertIn("patches.mpp", cmd)
            self.assertIn("-e", cmd)
            self.assertIn("Change installer source", cmd)
            self.assertIn("Disable Play Store updates", cmd)
            self.assertIn("--options-file", cmd)
            self.assertIn("./options.json", cmd)
            self.assertIn("--striplibs", cmd)
            self.assertIn("arm64-v8a", cmd)
            self.assertIn("--out", cmd)
            self.assertIn("./release/youtube-19.0.0-arm64-v8a.apk", cmd)
            self.assertIn("-r", cmd)
            self.assertIn("./result.json", cmd)
            self.assertIn("--keystore", cmd)
            self.assertIn("./src/keystore/morphe.keystore", cmd)
            self.assertIn("--force", cmd)
            self.assertIn("--continue-on-error", cmd)
            self.assertIn("./download/youtube-19.0.0-arm64-v8a.apk", cmd)

    def test_cleanup_artifacts_runs_gh(self):
        # Test that cmd_cleanup_artifacts invokes _run_gh with the correct command
        args = MagicMock()
        args.repo_full = "rahaaatul/Morphed"
        args.run_id = "12345"

        with patch.object(
            YouTube_vn, "_run_gh", return_value='{"artifacts":[{"id":1,"name":"test"}]}'
        ) as mock_gh:
            result = YouTube_vn.cmd_cleanup_artifacts(args)
            self.assertEqual(result, 0)
            mock_gh.assert_called()
            # Verify the first call lists artifacts
            first_cmd = mock_gh.call_args_list[0][0][0]
            self.assertIn("api", first_cmd)
            cmd_str = " ".join(first_cmd)
            self.assertIn("artifacts", cmd_str)
            self.assertIn("12345", cmd_str)

if __name__ == "__main__":
    unittest.main()
