"""Hilfeseite: Gliederung in allen Sprachen gleich, Inhaltsverzeichnis aus dem Text, Suche mit Trefferzählung."""
import re
import unittest
from tests._support import qapp

app = qapp()
from bmc_steward import i18n
from bmc_steward.core import ABOUT
from bmc_steward.help_tab import HelpPage


class TestHelpText(unittest.TestCase):
    def test_same_outline_in_all_languages(self):
        shape = {lang: [len(re.findall(r"<h2>", g)) for g in re.split(r"<h1>", text)[1:]]
                 for lang, text in i18n.HELP.items()}
        self.assertEqual(set(shape), {code for code, _ in i18n.LANGS})
        self.assertEqual(len({tuple(s) for s in shape.values()}), 1, shape)
        self.assertEqual(len(shape["de"]), 5)

    def test_text_starts_with_group(self):
        for lang, text in i18n.HELP.items():
            self.assertTrue(text.startswith("<h1>"), lang)


class TestHelpPage(unittest.TestCase):
    def setUp(self):
        self.p = HelpPage(i18n.HELP["de"] + ABOUT)
        self.p.resize(1100, 700)
        self.p.show()
        app.processEvents()

    def tearDown(self):
        self.p.close()
        self.p.deleteLater()

    def groups(self):
        return [self.p.toc.topLevelItem(i) for i in range(self.p.toc.topLevelItemCount())]

    def test_toc_matches_headings(self):
        names = [g.text(0) for g in self.groups()]
        self.assertEqual(names, re.findall(r"<h1>(.*?)</h1>", i18n.HELP["de"]))
        children = [g.child(i).text(0) for g in self.groups() for i in range(g.childCount())]
        self.assertEqual(children, re.findall(r"<h2>(.*?)</h2>", i18n.HELP["de"]))

    def test_click_scrolls_to_section(self):
        last = self.groups()[-1].child(0)
        self.p.toc_clicked(last)
        app.processEvents()
        self.assertGreater(self.p.view.verticalScrollBar().value(), 0)
        self.assertIs(self.p.toc.currentItem(), last)

    def test_search_counts_and_filters(self):
        self.p.find.setText("Supermicro")
        app.processEvents()
        n = len(self.p.matches)
        self.assertGreater(n, 3)
        self.assertEqual(self.p.hits_label.text(), "Treffer 1 von {0}".format(n))
        visible = [g for g in self.groups() if not g.isHidden()]
        self.assertIn("Hersteller", [g.text(0).split("  (")[0] for g in visible])
        total = sum(int(re.search(r"\((\d+)\)", g.child(i).text(0)).group(1))
                    for g in visible for i in range(g.childCount()) if not g.child(i).isHidden())
        self.assertEqual(total, n)

    def test_step_wraps_around(self):
        self.p.find.setText("Supermicro")
        self.p.step(-1)
        self.assertEqual(self.p.current, len(self.p.matches) - 1)
        self.p.step(1)
        self.assertEqual(self.p.current, 0)
        self.assertEqual(len(self.p.view.extraSelections()), len(self.p.matches))

    def test_no_match_and_reset(self):
        self.p.find.setText("xyzzy-nirgends")
        self.assertEqual(self.p.matches, [])
        self.assertEqual(self.p.hits_label.text(), "Keine Treffer")
        self.assertTrue(all(g.isHidden() for g in self.groups()))
        self.p.clear_find()
        self.assertFalse(any(g.isHidden() for g in self.groups()))
        self.assertEqual(self.p.view.extraSelections(), [])

    def test_single_letter_does_not_search(self):
        self.p.find.setText("e")
        self.assertEqual(self.p.matches, [])


if __name__ == "__main__":
    unittest.main()
