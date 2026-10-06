import pathlib
import sys
import unittest

_HERE = pathlib.Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from loader import load  # noqa: E402

YouTube_vn = load()

FIXTURES = pathlib.Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "youtube"


class RenderNotesTest(unittest.TestCase):
    def test_render_notes_structure(self):
        # Data where there is a clean version (all expected patches applied)
        records = [
            {
                "version": "21.40.156",
                "size": 100,
                "arch": "arm64-v8a",
                "applied": ["Change installer source", "Disable Play Store updates", "Remove delay"],
                "failed": [],
            },
            {
                "version": "21.39.522",
                "size": 90,
                "arch": "arm64-v8a",
                "applied": ["Change installer source"],
                "failed": ["Disable Play Store updates", "Remove delay"],
            },
        ]
        expected = ["Change installer source", "Disable Play Store updates", "Remove delay"]
        tools = [
            ("Morphe Desktop", "https://github.com/MorpheApp/morphe-desktop", "v1.0.0"),
            ("Morphe Patches", "https://github.com/MorpheApp/morphe-patches", "v2.0.0"),
            ("APKMD", "https://github.com/tanishqmanuja/apkmirror-downloader", "v3.0.0"),
            ("MicroG", "https://github.com/MorpheApp/MicroG-RE", "v4.0.0"),
        ]
        microg_tag = "v4.0.0"
        result = YouTube_vn.render_notes(records, expected, tools, microg_tag)
        # Check that the result starts with the MicroG IMPORTANT callout (three lines)
        self.assertTrue(result.startswith("> [!IMPORTANT]\n"))
        self.assertIn("> **[MicroG](https://github.com/MorpheApp/MicroG-RE/releases/tag/v4.0.0)** is required to use this app\n", result)
        self.assertIn("> Install the newest version before following the tip.\n\n", result)
        # Check for the TIP block (two lines)
        self.assertIn("> [!TIP]\n> Install `21.40.156`. It is the newest version where every patch applied cleanly.\n\n", result)
        # Check for the details block (outer)
        self.assertIn("<details>\n<summary><b>Click here</b> to see the patches applied</summary>\n<br>\n\n", result)
        self.assertIn("</details>\n\n", result)  # closing outer details and blank line before download table
        # Check for inner details (at least one)
        self.assertIn("<details>\n<summary><b>v21.40.156</b> - <code>3 of 3</code></summary>\n<br>\n\n|Status|Patch|\n|:---:|:---:|\n|🟢|Change installer source|\n|🟢|Disable Play Store updates|\n|🟢|Remove delay|\n</details>\n", result)
        # Check for the download table header
        self.assertIn("| Version | Channel | Arch | Size | Download |\n|:-------:|:-------:|:---------:|:-----:|:------------------------:|\n", result)
        # Check for a download row (we'll check the first one)
        self.assertIn("| 21.40.156 | Stable | arm64-v8a | 100.0MB | <a href=", result)
        # Check for the tools table
        self.assertIn("## Tools used\n| Tool | Version |\n|------|---------|\n", result)
        self.assertIn("| [Morphe Desktop](https://github.com/MorpheApp/morphe-desktop/releases/tag/v1.0.0) | `v1.0.0` |\n", result)
        # Ensure the string ends with a single newline
        self.assertTrue(result.endswith("\n"))
        self.assertFalse(result.endswith("\n\n"))

    def test_render_notes_microg_missing(self):
        records = [
            {
                "version": "21.40.156",
                "size": 100,
                "arch": "arm64-v8a",
                "applied": ["Change installer source"],
                "failed": [],
            },
        ]
        expected = ["Change installer source"]
        tools = [
            ("Morphe Desktop", "https://github.com/MorpheApp/morphe-desktop", "v1.0.0"),
            ("Morphe Patches", "https://github.com/MorpheApp/morphe-patches", "v2.0.0"),
            ("APKMD", "https://github.com/tanishqmanuja/apkmirror-downloader", "v3.0.0"),
            ("MicroG", "https://github.com/MorpheApp/MicroG-RE", "v4.0.0"),
        ]
        microg_tag = ""  # no block
        result = YouTube_vn.render_notes(records, expected, tools, microg_tag)
        # Should not contain the MicroG IMPORTANT line
        self.assertNotIn("> [!IMPORTANT]", result)
        # Should have the TIP block
        self.assertIn("> [!TIP]\n> Install `21.40.156`. It is the newest version where every patch applied cleanly.\n\n", result)
        # Should have the details block
        self.assertIn("<details>\n<summary><b>Click here</b> to see the patches applied</summary>\n<br>\n\n<details>\n<summary><b>v21.40.156</b> - <code>1 of 1</code></summary>\n<br>\n\n|Status|Patch|\n|:---:|:---:|\n|🟢|Change installer source|\n</details>\n\n</details>\n", result)
        # Should have the download table and tools table
        self.assertIn("| Version | Channel | Arch | Size | Download |\n", result)
        self.assertIn("## Tools used\n", result)

    def test_render_notes_anchor_newest_clean(self):
        records = [
            {
                "version": "21.39.522",
                "size": 90,
                "arch": "arm64-v8a",
                "applied": ["Change installer source", "Disable Play Store updates"],
                "failed": ["Remove delay"],
            },
            {
                "version": "21.40.156",
                "size": 100,
                "arch": "arm64-v8a",
                "applied": ["Change installer source", "Disable Play Store updates", "Remove delay"],
                "failed": [],
            },
            {
                "version": "21.41.100",
                "size": 110,
                "arch": "arm64-v8a",
                "applied": ["Change installer source"],
                "failed": ["Disable Play Store updates", "Remove delay"],
            },
        ]
        expected = ["Change installer source", "Disable Play Store updates", "Remove delay"]
        tools = []  # not used in this test
        microg_tag = ""
        result = YouTube_vn.render_notes(records, expected, tools, microg_tag)
        # The anchor should be the newest version where all expected patches are applied.
        # Version 21.40.156 has all three applied -> anchor should be 21.40.156
        # Version 21.41.100 missing two patches -> not eligible.
        self.assertIn("> [!TIP]\n> Install `21.40.156`. It is the newest version where every patch applied cleanly.\n\n", result)
        # Ensure that 21.41.100 is not the anchor
        self.assertNotIn("> [!TIP]\n> Install `21.41.100`. It is the newest version where every patch applied cleanly.\n\n", result)

    def test_render_notes_empty_exits_nonzero(self):
        # This test is for the CLI command, not the pure function.
        # We'll skip it for now and implement in the CLI test.
        self.skipTest("CLI test to be implemented later")

    def test_render_notes_matches_golden(self):
        """Test that our render_notes output matches the golden fixture."""
        # Load the records from our fixture directories (same as used to create golden)
        import tempfile
        import json
        import os
        
        # Create temporary directories with the same structure as our golden fixture
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = os.path.join(tmp, "release")
            applied_dir = os.path.join(tmp, "applied")
            failed_dir = os.path.join(tmp, "failed")
            os.makedirs(release_dir)
            os.makedirs(applied_dir)
            os.makedirs(failed_dir)
            
            # Create the same files as in our golden fixture
            # Release APK files
            open(os.path.join(release_dir, "app-release-21.40.156.apk"), 'w').close()
            open(os.path.join(release_dir, "app-release-21.39.522.apk"), 'w').close()
            
            # Applied patches
            with open(os.path.join(applied_dir, "applied-21.40.156.txt"), 'w') as f:
                f.write("Change installer source\nDisable Play store updates\nRemove delay\n")
            with open(os.path.join(applied_dir, "applied-21.39.522.txt"), 'w') as f:
                f.write("Change installer source\n")
            
            # Failed patches
            open(os.path.join(failed_dir, "failed-21.40.156.txt"), 'w').close()  # empty
            with open(os.path.join(failed_dir, "failed-21.39.522.txt"), 'w') as f:
                f.write("Disable Play store updates\nRemove delay\n")
            
            # Build records from these directories
            records = YouTube_vn.build_records(release_dir, applied_dir, failed_dir, "arm64-v8a")
            
            # Load expected patches
            with open(FIXTURES / "expected_patches.json", 'r') as f:
                expected = json.load(f)
            
            # Load tools
            with open(FIXTURES / "tools.json", 'r') as f:
                tools_data = json.load(f)
            tools = [(t["name"], t["repo_url"], t["tag"]) for t in tools_data]
            
            microg_tag = "v4.0.0"
            
            # Generate release notes
            result = YouTube_vn.render_notes(records, expected, tools, microg_tag)
            
            # Read golden fixture
            golden_path = FIXTURES / "release-notes.md"
            with open(golden_path, 'r') as f:
                golden = f.read()
            
            # They should be identical
            self.assertEqual(result, golden)


if __name__ == '__main__':
    unittest.main()