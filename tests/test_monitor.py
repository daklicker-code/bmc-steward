"""Monitor, Varianten-Schalter und Seitenmenü (bmc_steward.monitor, .sidenav, Window in .main).

Der Monitor zeigt Rauschen, solange keine Konsole verbunden ist, sonst deren Miniatur. Der Schalter
darunter wählt die Konsolen-Variante (HTML5, Text, Browser) und muss mit der Auswahl „Anzeige“ der
Konsolenseite im Gleichschritt laufen. Das Menü links ersetzt die Reiterleiste; Seiten werden weiter
über ihre Nummer angesprochen (z. B. Seite 1 = „Server konfigurieren“).
Alles läuft offscreen, ein echter BMC wird nicht gebraucht.
"""
import unittest
from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QWidget
from bmc_steward.main import Window
from bmc_steward.monitor import NOISE_H, NOISE_W, MonitorWidget, SegmentSwitch
from bmc_steward.sidenav import SideNav


class TestSideNav(unittest.TestCase):
    def test_pages_follow_menu(self):
        nav = SideNav()
        pages = [QWidget() for _ in range(3)]
        self.assertEqual([nav.addTab(w, f"Seite {i}") for i, w in enumerate(pages)], [0, 1, 2])
        seen = []
        nav.currentChanged.connect(seen.append)
        nav.setCurrentIndex(2)
        self.assertEqual(nav.currentIndex(), 2)
        self.assertIs(nav.stack.currentWidget(), pages[2])
        self.assertEqual(nav.menu.currentRow(), 2)
        self.assertEqual(seen, [2])
        self.assertEqual(nav.indexOf(pages[1]), 1)
        self.assertEqual(nav.count(), 3)

    def test_click_in_menu_switches_page(self):
        nav = SideNav()
        for i in range(2):
            nav.addTab(QWidget(), str(i))
        nav.menu.setCurrentRow(1)
        self.assertEqual(nav.currentIndex(), 1)


class TestSegmentSwitch(unittest.TestCase):
    def setUp(self):
        self.sw = SegmentSwitch([("A", "a", False), ("B", "b", False), ("C", "c", True)])
        self.got = []
        self.sw.changed.connect(self.got.append)

    def test_first_is_checked(self):
        self.assertEqual(self.sw.index(), 0)

    def test_set_index_is_silent(self):
        self.sw.set_index(2)
        self.assertEqual(self.sw.index(), 2)
        self.assertEqual(self.got, [])

    def test_click_emits_changed(self):
        self.sw._buttons[1].click()
        self.assertEqual(self.sw.index(), 1)
        self.assertEqual(self.got, [1])

    def test_out_of_range_is_ignored(self):
        self.sw.set_index(7)
        self.assertEqual(self.sw.index(), 0)


class TestMonitorWidget(unittest.TestCase):
    def setUp(self):
        self.source = None                  # was die Rückruffunktion gerade liefert
        self.m = MonitorWidget(lambda: self.source, "leer", "live")
        self.m.resize(272, 196)

    def test_noise_is_dark_and_low_contrast(self):
        img = MonitorWidget._make_noise()
        self.assertEqual((img.width(), img.height()), (NOISE_W, NOISE_H))
        values = {QColor(img.pixel(x, y)).red() for x in range(0, NOISE_W, 7) for y in range(0, NOISE_H, 5)}
        self.assertLessEqual(max(values), 70)
        self.assertGreater(len(values), 3)          # es ist wirklich Rauschen, keine Fläche

    def test_idle_shows_noise_without_frame(self):
        self.m._tick()
        self.assertFalse(self.m._live)
        self.assertIsNone(self.m._frame)
        self.assertEqual(self.m.toolTip(), "leer")

    def test_connected_console_becomes_thumbnail(self):
        console = QWidget()
        console.resize(640, 400)
        self.source = (console, True, "10.20.0.11")
        self.m._tick()
        self.assertTrue(self.m._live)
        self.assertTrue(self.m._fresh)
        self.assertIsNotNone(self.m._frame)
        self.assertEqual(self.m._caption, "10.20.0.11")
        self.assertEqual(self.m.toolTip(), "live")
        self.assertFalse(self.m.grab().isNull())    # Zeichnen mit Miniatur wirft nichts

    def test_hidden_web_view_keeps_last_frame_as_still(self):
        console = QWidget()                         # nie angezeigt, also nicht sichtbar
        console.resize(640, 400)
        self.source = (console, False, "10.20.0.11")
        self.m._tick()
        self.assertIsNone(self.m._frame)            # noch nie ein Bild
        self.m._frame = console.grab()
        self.m._tick()
        self.assertFalse(self.m._fresh)             # Standbild statt Live
        self.assertIsNotNone(self.m._frame)
        self.assertFalse(self.m.grab().isNull())

    def test_disconnect_returns_to_noise(self):
        console = QWidget()
        console.resize(300, 200)
        self.source = (console, True, "x")
        self.m._tick()
        self.source = None
        self.m._tick()
        self.assertFalse(self.m._live)
        self.assertIsNone(self.m._frame)
        self.assertEqual(self.m.toolTip(), "leer")

    def test_click_emits_clicked(self):
        hits = []
        self.m.clicked.connect(lambda: hits.append(1))
        QTest.mouseClick(self.m, Qt.MouseButton.LeftButton)
        QTest.keyClick(self.m, Qt.Key.Key_Return)
        self.assertEqual(len(hits), 2)

    def test_timer_only_runs_while_visible(self):
        self.m.show()
        self.assertTrue(self.m._timer.isActive())
        self.m.hide()
        self.assertFalse(self.m._timer.isActive())


class TestWindowLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = Window()

    def test_page_order_is_unchanged(self):
        # Der übrige Code springt über Nummern: 1 = Server konfigurieren, 3 = UEFI, 6 = Konsole
        self.assertEqual(self.w.tabs.count(), 9)
        self.assertEqual(self.w.tabs.indexOf(self.w.con_page), 6)
        self.assertEqual(self.w.tabs.menu.item(1).text(), "2 · Server konfigurieren")

    def test_switch_follows_mode_combo(self):
        for i in range(3):
            self.w.con_mode.setCurrentIndex(i)
            self.assertEqual(self.w.con_switch.index(), i)

    def test_switch_click_sets_mode(self):
        self.w.con_switch._buttons[2].click()
        self.assertEqual(self.w.con_mode.currentData(), "browser")
        self.w.con_switch._buttons[1].click()
        self.assertEqual(self.w.con_mode.currentData(), "serial")
        self.w.con_switch._buttons[0].click()
        self.assertEqual(self.w.con_mode.currentData(), "web")

    def test_switch_is_locked_while_connected(self):
        self.w.console_busy(True)
        self.assertFalse(self.w.con_switch.isEnabled())
        self.w.console_busy(False)
        self.assertTrue(self.w.con_switch.isEnabled())

    def test_nothing_connected_means_no_source(self):
        self.assertIsNone(self.w.console_source())

    def test_click_on_idle_monitor_opens_console_with_settings(self):
        self.w.tabs.setCurrentIndex(0)
        self.w.console_panel_show(False)
        self.w.monitor.clicked.emit()
        self.assertEqual(self.w.tabs.currentIndex(), 6)
        self.assertFalse(self.w.con_panel.isHidden())

    def test_click_on_live_monitor_opens_full_console(self):
        console = QWidget()
        self.w.console_source = lambda: (console, True, "10.20.0.11")
        try:
            self.w.tabs.setCurrentIndex(0)
            self.w.console_panel_show(True)
            self.w.console_enter()
            self.assertEqual(self.w.tabs.currentIndex(), 6)
            self.assertTrue(self.w.con_panel.isHidden())
        finally:
            del self.w.console_source               # zurück zur echten Methode
            self.w.console_panel_show(True)

    def test_fold_button_toggles_settings(self):
        self.w.console_panel_show(True)
        self.w.con_fold.click()
        self.assertTrue(self.w.con_panel.isHidden())
        self.w.con_fold.click()
        self.assertFalse(self.w.con_panel.isHidden())


if __name__ == "__main__":
    unittest.main()
