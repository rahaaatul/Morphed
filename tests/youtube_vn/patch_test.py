import pathlib
import sys
import unittest
from unittest.mock import patch, MagicMock

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

YouTube_vn = load()


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
            self.assertIn("-e", cmd)
            self.assertIn("patcher.mpp", cmd)
            self.assertIn("-l", cmd)
            self.assertIn("19.0.0", cmd)
            self.assertIn("-o", cmd)
            self.assertIn("ship", cmd)
            self.assertIn("-f", cmd)
            self.assertIn("com.google.android.youtube", cmd)
            self.assertIn("-a", cmd)
            self.assertIn("arm64-v8a", cmd)
            self.assertIn("--options", cmd)
            self.assertIn("options.json", cmd)

    def test_cleanup_artifacts_runs_gh(self):
        # Test that cmd_cleanup_artifacts invokes _run_gh with the correct command
        args = MagicMock()
        args.repo_full = "rahaaatul/Morphed"
        args.run_id = "12345"

        with patch.object(YouTube_vn, "_run_gh", return_value='{"artifacts":[{"id":1,"name":"test"}]}') as mock_gh:
            result = YouTube_vn.cmd_cleanup_artifacts(args)
            self.assertEqual(result, 0)
            mock_gh.assert_called()
            first_cmd = mock_gh.call_args_list[0][0][0]
            self.assertIn("api", first_cmd)
            cmd_str = " ".join(first_cmd)
            self.assertIn("artifacts", cmd_str)
            self.assertIn("12345", cmd_str)


if __name__ == '__main__':
    unittest.main()