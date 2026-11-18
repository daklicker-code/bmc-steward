"""Release-Texte (.github/release-notes) und der Workflow, der daraus fehlende GitHub-Releases anlegt."""
import re
import unittest
from tests._support import REPO

NOTES = REPO / ".github" / "release-notes"
WORKFLOW = REPO / ".github" / "workflows" / "releases.yml"


class TestReleaseNotes(unittest.TestCase):
    def test_every_file_has_title_and_text(self):
        files = sorted(NOTES.glob("*.md"))
        self.assertTrue(files)
        for f in files:
            with self.subTest(file=f.name):
                m = re.fullmatch(r"v(\d+\.\d+\.\d+)\.md", f.name)
                self.assertIsNotNone(m, "Dateiname muss vX.Y.Z.md sein")
                lines = f.read_text(encoding="utf-8").splitlines()
                self.assertTrue(lines[0].startswith(f"# BMC-Steward {m.group(1)}"), lines[0])
                self.assertTrue("".join(lines[1:]).strip(), "Release-Text fehlt")

    def test_current_version_has_notes(self):
        from bmc_steward.core import __version__
        self.assertTrue((NOTES / f"v{__version__}.md").exists(),
                        f"Für Version {__version__} fehlt .github/release-notes/v{__version__}.md")

    def test_workflow_writes_only_in_its_job_and_only_on_demand(self):
        text = WORKFLOW.read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^permissions:\n\s+contents: read")       # Vorgabe: nur lesen
        self.assertRegex(text, r"(?m)^    permissions:\n\s+contents: write")   # Schreiben nur im Job
        on = text.split("\non:", 1)[1].split("\npermissions:", 1)[0]
        self.assertIn("workflow_dispatch", on)
        self.assertNotRegex(on, r"(?m)^\s+(push|pull_request|schedule):")       # nie von selbst
        self.assertIn("gh release view", text)                                  # Vorhandenes bleibt


if __name__ == "__main__":
    unittest.main()
