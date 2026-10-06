"""Load the hyphenated .github/scripts/youtube.py as an importable module."""
import importlib.util
import pathlib

_ROOT = pathlib.Path(__file__).resolve().parents[2]
_MODULE = _ROOT / ".github" / "scripts" / "youtube.py"


def load():
    spec = importlib.util.spec_from_file_location("youtube_vn", _MODULE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod