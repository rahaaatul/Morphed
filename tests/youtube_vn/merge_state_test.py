import json
import pathlib
import sys
import unittest
from unittest.mock import patch, MagicMock

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

YouTube_vn = load()


class MergeStateTest(unittest.TestCase):
    def test_state_put_succeeds_after_retry_on_conflict(self):
        # Test that state_put retries on conflict and succeeds
        mock_get = MagicMock()
        mock_get.status_code = 200
        mock_get.json.return_value = {"sha": "abc123"}

        mock_put_409 = MagicMock()
        mock_put_409.status_code = 409

        mock_put_200 = MagicMock()
        mock_put_200.status_code = 200

        with patch("requests.get", return_value=mock_get), \
             patch("requests.put", side_effect=[mock_put_409, mock_put_200]) as mock_put, \
             patch("time.sleep"):
            result = YouTube_vn.state_put("rahaaatul/Morphed", ".github/tags/youtube.json", "{}\n", "test message", token="test_token")
            # Should not raise
            self.assertIsNone(result)

    def test_state_put_exits_nonzero_after_final_failure(self):
        # Test that state_put exits non-zero after 5 failures
        mock_get = MagicMock()
        mock_get.status_code = 200
        mock_get.json.return_value = {"sha": "abc123"}

        mock_put = MagicMock()
        mock_put.status_code = 409

        with patch("requests.get", return_value=mock_get), \
             patch("requests.put", return_value=mock_put), \
             patch("time.sleep"):
            with self.assertRaises(SystemExit) as cm:
                YouTube_vn.state_put("rahaaatul/Morphed", ".github/tags/youtube.json", "{}\n", "test message", token="test_token")
            self.assertEqual(cm.exception.code, 1)


if __name__ == '__main__':
    unittest.main()