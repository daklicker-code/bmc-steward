"""Übersetzungen: Jeder Oberflächentext (T("…")) hat einen Eintrag für Englisch, Französisch und
Schweizerdeutsch, und die Platzhalter ({0}, {1:.1f} …) stimmen in allen Sprachen überein."""
import ast
import re
import unittest
from tests._support import REPO, qapp

qapp()
from bmc_steward import i18n


def literals():
    for f in sorted((REPO / "bmc_steward").glob("*.py")):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "T" and node.args
                    and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str)):
                yield f.name, node.args[0].value


class TestTranslations(unittest.TestCase):
    def test_every_text_translated(self):
        missing = sorted({f"{f}: {t[:70]!r}" for f, t in literals() if t not in i18n.TR})
        self.assertEqual(missing, [], "ohne Übersetzung:\n" + "\n".join(missing))

    def test_placeholders_match(self):
        bad = []
        for _, t in literals():
            want = sorted(re.findall(r"\{[^}]*\}", t))
            for tr in i18n.TR.get(t, []):
                if sorted(re.findall(r"\{[^}]*\}", tr)) != want:
                    bad.append(f"{t[:50]!r} -> {tr[:50]!r}")
        self.assertEqual(bad, [])

    def test_three_languages(self):
        self.assertFalse([k for k, v in i18n.TR.items() if len(v) != 3 or not all(v)])


if __name__ == "__main__":
    unittest.main()
