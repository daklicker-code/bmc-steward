"""Monitor, Varianten-Schalter und Seitenmenü (bmc_steward.monitor, .sidenav, Window in .main).

Der Monitor zeigt Rauschen, solange keine Konsole verbunden ist, sonst deren Miniatur. Der Schalter
darunter wählt die Konsolen-Variante (HTML5, Text, Browser) und muss mit der Auswahl „Anzeige“ der
Konsolenseite im Gleichschritt laufen. Das Menü links ersetzt die Reiterleiste; Seiten werden weiter
über ihre Nummer angesprochen (z. B. Seite 1 = „Neuware“).
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
from bmc_steward.monitor import (CYCLE_MS, GLOW_FADE_MS, LOGO_MS, logo_progress, _NOISE_LUT, NOISE_H, NOISE_W, MonitorWidget, SegmentSwitch,
                                 glow_strength, logo_stages)
from bmc_steward.sidenav import ACCENT_ROLE, SideNav


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

    def test_accent_entry_is_marked(self):
        nav = SideNav()
        nav.addTab(QWidget(), "gelb", accent="#c9a100")
        nav.addTab(QWidget(), "normal")
        self.assertEqual(nav.menu.item(0).data(ACCENT_ROLE), "#c9a100")
        self.assertIsNone(nav.menu.item(1).data(ACCENT_ROLE))

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

    def test_logo_builds_up_and_check_mark_comes_last(self):
        self.assertTrue(all(v == 0 for v in logo_stages(0.0).values()))
        self.assertTrue(all(v == 1 for v in logo_stages(1.0).values()))
        for i in range(0, 90):                              # bis 89 % ist das Häkchen noch nicht da ...
            self.assertEqual(logo_stages(i / 100)["check"], 0)
        for i in range(90, 101):                            # ... und wenn es kommt, steht der Rest schon
            st = logo_stages(i / 100)
            self.assertEqual((st["sphere"], st["chip"], st["pins"]), (1, 1, 1))
        prev = 0
        for i in range(0, 101):                             # Kugel baut sich gleichmäßig auf, nie rückwärts
            cur = logo_stages(i / 100)["sphere"]
            self.assertGreaterEqual(cur, prev)
            prev = cur

    def test_logo_is_painted_at_every_stage(self):
        for t in (0.0, 0.2, 0.5, 0.8, 0.95, 1.0):
            self.m._logo_progress = lambda t=t: t
            self.assertFalse(self.m.grab().isNull())

    def test_glow_rises_with_check_mark_then_fades_slowly(self):
        for ms in (0, LOGO_MS * 0.5, LOGO_MS * 0.9):        # vor dem Häkchen kein Schein
            self.assertEqual(glow_strength(ms), 0)
        self.assertEqual(glow_strength(LOGO_MS), 1)         # Häkchen fertig = hellster Moment
        rise = LOGO_MS * 0.1                                 # Anstieg dauert so lange wie das Häkchen ...
        self.assertLess(rise, GLOW_FADE_MS)                  # ... das Abklingen deutlich länger
        prev = 0
        for i in range(0, 101):                              # steigt nur ...
            cur = glow_strength(LOGO_MS * 0.9 + rise * i / 100)
            self.assertGreaterEqual(cur, prev)
            prev = cur
        for i in range(0, 101):                              # ... und fällt danach nur
            cur = glow_strength(LOGO_MS + GLOW_FADE_MS * i / 100)
            self.assertLessEqual(cur, prev)
            prev = cur
        self.assertEqual(glow_strength(LOGO_MS + GLOW_FADE_MS), 0)
        self.assertEqual(glow_strength(LOGO_MS * 10), 0)

    def test_logo_builds_up_and_down_every_8_seconds(self):
        self.assertEqual(CYCLE_MS, 8000)
        self.assertEqual(logo_progress(0), 0)
        self.assertEqual(logo_progress(LOGO_MS), 1)          # fertig aufgebaut ...
        self.assertEqual(logo_progress(LOGO_MS + 1000), 1)   # ... bleibt kurz stehen ...
        self.assertEqual(logo_progress(CYCLE_MS - 1), 0)     # ... ist vor dem nächsten Durchlauf wieder abgebaut
        peak = max(range(0, CYCLE_MS, 10), key=logo_progress)
        prev = 1
        for ms in range(peak, CYCLE_MS, 10):                 # nach dem Höhepunkt geht es nur noch abwärts
            cur = logo_progress(ms)
            self.assertLessEqual(cur, prev)
            prev = cur
        for ms in (0, 1234, 4321, 7777):                     # wiederholt sich genau alle 8 Sekunden
            self.assertEqual(logo_progress(ms), logo_progress(ms + 3 * CYCLE_MS))
            self.assertEqual(glow_strength(ms), glow_strength(ms + 3 * CYCLE_MS))
        for ms in range(0, CYCLE_MS, 10):                    # Schein nur, solange das Logo ganz steht
            if glow_strength(ms) > 0:
                self.assertGreater(logo_progress(ms), 0.9)

    def test_animated_logo_runs_only_while_visible_and_paints(self):
        from bmc_steward.monitor import AnimatedLogo
        logo = AnimatedLogo(64)
        self.assertFalse(logo._timer.isActive())
        logo.show()
        self.assertTrue(logo._timer.isActive())
        self.assertFalse(logo.grab().isNull())
        logo.hide()
        self.assertFalse(logo._timer.isActive())

    def test_brand_mark_is_the_sphere_and_renders(self):
        from bmc_steward.branding import MARK_SVG, app_icon
        self.assertIn('<circle cx="60" cy="63" r="45"', MARK_SVG)
        self.assertNotIn("Q96 96 60 112", MARK_SVG)          # das alte Schild ist weg
        self.assertFalse(app_icon().isNull())

    def test_readme_logo_is_animated_in_the_same_rhythm(self):
        import os
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        svg = open(os.path.join(root, "docs", "logo.svg"), encoding="utf-8").read()
        self.assertIn(f"animation-duration:{CYCLE_MS}ms", svg)
        self.assertIn("infinite", svg)
        self.assertIn('r="45"', svg)
        self.assertIn("prefers-reduced-motion", svg)

    def test_noise_is_subtle(self):
        self.assertLessEqual(max(_NOISE_LUT), 40)
        self.assertLessEqual(max(_NOISE_LUT) - min(_NOISE_LUT), 30)

    def test_logo_is_centered_and_large(self):
        from PyQt6.QtGui import QColor, QImage
        self.m.resize(272, 196)
        self.m._noise = QImage(NOISE_W, NOISE_H, QImage.Format.Format_Grayscale8)
        self.m._noise.fill(QColor(0, 0, 0))                  # schwarzes Bild: nur das Logo leuchtet
        self.m._logo_progress = lambda: 1.0
        self.m._logo_glow = lambda: 0.0
        img = self.m.grab().toImage()
        scr = self.m._screen()
        xs, ys = [], []
        for y in range(int(scr.top()), int(scr.bottom())):
            for x in range(int(scr.left()), int(scr.right())):
                if img.pixelColor(x, y).green() > 120:
                    xs.append(x)
                    ys.append(y)
        width = max(xs) - min(xs)
        old = 77 / 120 * 46                                  # früheres Schild samt Strich im 46er-Quadrat: ~30 px
        self.assertGreater(width, old * 1.7)                 # fast doppelt so groß wie früher
        self.assertAlmostEqual((min(xs) + max(xs)) / 2, scr.center().x(), delta=3)
        self.assertLess(abs((min(ys) + max(ys)) / 2 - scr.center().y()), 8)

    def test_logo_with_glow_is_painted(self):
        self.m._logo_progress = lambda: 1.0
        for g in (0.0, 0.5, 1.0):
            self.m._logo_glow = lambda g=g: g
            self.assertFalse(self.m.grab().isNull())

    def test_logo_clock_runs_only_while_noise_is_shown(self):
        self.m._tick()
        self.assertTrue(self.m._logo_clock.isValid())
        console = QWidget()
        console.resize(300, 200)
        self.source = (console, True, "x")
        self.m._tick()
        self.assertFalse(self.m._logo_clock.isValid())      # Verbindung: Logo weg, Miniatur da
        self.source = None
        self.m._tick()
        self.assertTrue(self.m._logo_clock.isValid())       # wieder Rauschen: Aufbau beginnt von vorn
        self.assertLess(self.m._logo_progress(), 0.5)

    def test_timer_only_runs_while_visible(self):
        self.m.show()
        self.assertTrue(self.m._timer.isActive())
        self.m.hide()
        self.assertFalse(self.m._timer.isActive())


class TestWindowLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = Window()

    def test_page_order_and_sections(self):
        # Fest im Code: 0 = DHCP-Server, 1 = Neuware; alles andere wird über indexOf angesprungen
        from bmc_steward.sidenav import MARK_ROLE, SECTION_ROLE
        self.assertEqual(self.w.tabs.count(), 13)
        names = [self.w.tabs.menu.item(i).text() for i in range(13)]
        self.assertEqual(names, ["DHCP-Server", "Neuware", "Bestandsserver", "UEFI-Einstellungen", "Dienste",
                                 "Benutzerkonten", "Firmware", "Mainboard-Tausch", "UEFI-Konsole", "Aufnahme", "Testlabor",
                                 "Aufbauplaner", "Hilfe"])
        sections = {i: self.w.tabs.menu.item(i).data(SECTION_ROLE) for i in range(13)}
        self.assertEqual({i: s for i, s in sections.items() if s}, {0: "Einrichtung", 3: "Konfiguration", 7: "Service", 9: "Werkzeuge"})
        self.assertEqual([self.w.tabs.menu.item(i).data(MARK_ROLE) for i in range(7)], list(range(7)))
        self.assertEqual(self.w.tabs.indexOf(self.w.con_page), 8)
        self.assertEqual(self.w.tabs.indexOf(self.w.swap_page), 7)

    def test_help_page_shows_animated_logo(self):
        from bmc_steward.monitor import AnimatedLogo
        page = self.w.tabs.widget(12)
        self.assertIs(self.w.help_logo.parent(), page)
        self.assertIsInstance(self.w.help_logo, AnimatedLogo)

    def test_only_dhcp_entry_is_dark_yellow_button(self):
        menu = self.w.tabs.menu
        self.assertEqual(menu.item(0).data(ACCENT_ROLE), "#c9a100")
        self.assertTrue(all(not menu.item(i).data(ACCENT_ROLE) for i in range(1, menu.count())))
        pm = menu.grab()                            # Zeichnen mit Delegate wirft nichts
        self.assertFalse(pm.isNull())

    def test_dhcp_button_lights_up_only_on_hover(self):
        from PyQt6.QtCore import QRect
        from PyQt6.QtGui import QColor, QImage, QPainter
        from PyQt6.QtWidgets import QStyle, QStyleOptionViewItem
        menu = self.w.tabs.menu
        delegate = menu.itemDelegate()
        index = menu.model().index(0, 0)

        height = delegate.sizeHint(QStyleOptionViewItem(), index).height()   # mit Bereichsüberschrift

        def render(state):
            img = QImage(200, height, QImage.Format.Format_ARGB32)
            img.fill(QColor("#ffffff"))
            opt = QStyleOptionViewItem()
            opt.rect = QRect(0, 0, 200, height)
            opt.state = state | QStyle.StateFlag.State_Enabled
            opt.font = menu.font()
            opt.palette = menu.palette()
            p = QPainter(img)
            delegate.paint(p, opt, index)
            p.end()
            return img.pixelColor(150, height - 8)   # Fläche des Punkts unter der Überschrift, ohne Text

        yellow = QColor("#c9a100")
        hover = render(QStyle.StateFlag.State_MouseOver)
        self.assertLess(abs(hover.red() - yellow.lighter(115).red()), 6)
        self.assertGreater(hover.red() - hover.blue(), 80)           # deutlich gelb
        idle = render(QStyle.StateFlag.State_None)
        self.assertLess(idle.red() - idle.blue(), 30)                # nicht gelb, wenn die Maus fehlt

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
        self.assertEqual(self.w.tabs.currentIndex(), self.w.tabs.indexOf(self.w.con_page))
        self.assertFalse(self.w.con_panel.isHidden())

    def test_click_on_live_monitor_opens_full_console(self):
        console = QWidget()
        self.w.console_source = lambda: (console, True, "10.20.0.11")
        try:
            self.w.tabs.setCurrentIndex(0)
            self.w.console_panel_show(True)
            self.w.console_enter()
            self.assertEqual(self.w.tabs.currentIndex(), self.w.tabs.indexOf(self.w.con_page))
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
