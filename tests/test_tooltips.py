"""Tooltips als schmale Kästchen: Zeilenumbrüche aus dem Quelltext werden zu Absätzen, Listen bleiben Listen,
kurze Texte bleiben unverändert; der Filter greift auch bei Tabellenköpfen."""
import unittest

from tests._support import qapp

qapp()
from bmc_steward.tooltips import MARK, WIDTH, format_tip


class TestFormat(unittest.TestCase):
    def test_short_text_stays_as_is(self):
        self.assertEqual(format_tip("Entfernt die markierten Zeilen."), "Entfernt die markierten Zeilen.")
        self.assertEqual(format_tip(""), "")

    def test_long_text_becomes_a_fixed_width_box(self):
        t = ("Trägt das Passwort von oben in die Spalte „Passwort“ aller Zeilen ein\n"
             "und überschreibt dort vorhandene Einträge.")
        out = format_tip(t)
        self.assertTrue(out.startswith(MARK))
        self.assertIn(f"width='{WIDTH}'", out)
        self.assertIn("Zeilen ein und überschreibt", out)         # Umbruch aus dem Quelltext entfernt
        self.assertEqual(format_tip(out), out)                     # schon formatiert: unverändert

    def test_paragraphs_lists_and_escaping(self):
        out = format_tip("Kurzer Titel\n\nErster Absatz mit <Zeichen> & mehr.\n- Punkt eins\n- Punkt zwei")
        self.assertIn("<b>Kurzer Titel</b>", out)
        self.assertIn("&lt;Zeichen&gt; &amp; mehr", out)
        self.assertEqual(out.count("<li"), 2)

    def test_meaningful_line_breaks_stay(self):
        out = format_tip("Lädt eine CSV-Datei, z. B. aus Excel:\naktuelle_ip,passwort,neue_ip\n(seriennummer optional)")
        self.assertIn("Excel:<br>aktuelle_ip,passwort,neue_ip<br>(seriennummer", out)


class TestFilter(unittest.TestCase):
    def test_header_tooltip_is_found(self):
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtWidgets import QTableWidget, QTableWidgetItem
        from bmc_steward.tooltips import TipFilter
        t = QTableWidget(1, 2)
        item = QTableWidgetItem("A")
        item.setToolTip("Spalte A")
        t.setHorizontalHeaderItem(0, item)
        t.resize(300, 100)
        t.show()
        hv = t.horizontalHeader()
        text, rect = TipFilter._lookup(hv.viewport(), QPoint(hv.sectionViewportPosition(0) + 3, 3))
        self.assertEqual(text, "Spalte A")
        self.assertIsNotNone(rect)
        t.close()


if __name__ == "__main__":
    unittest.main()
