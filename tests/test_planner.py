"""Aufbauplaner: Platzprüfung, Kabel, Prüfung, Netzplan, Stückliste, Ausfall-Simulation, Speichern/Laden
und die Seite im Hauptfenster (Übernahme in die Serverliste, Bestand aus „Neuware“)."""
import json
import unittest
from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
i18n.build_texts()
from bmc_steward.planner import Plan, demo_plan, port_label, vendor_of

INV = [{"key": "sn:A1", "name": "srv-a", "vendor": "dell", "model": "Dell iDRAC9", "serial": "A1"},
       {"key": "sn:B2", "name": "srv-b", "vendor": "hpe", "model": "HPE iLO 6", "serial": "B2"}]


def small_plan():
    p = Plan()
    r = p.add_rack("A1", 10)
    p.sync_inventory(INV)
    oob, leaf = p.new_device("oob"), p.new_device("leaf")
    p.place(oob, r["id"], 10)
    p.place(leaf, r["id"], 9)
    a, b = (next(d for d in p.devs if d.get("key") == k) for k in ("sn:A1", "sn:B2"))
    return p, r, oob, leaf, a, b


class TestPlacement(unittest.TestCase):
    def test_fits_and_overlap(self):
        p, r, oob, leaf, a, b = small_plan()
        self.assertTrue(p.place(a, r["id"], 7))
        self.assertFalse(p.fits(r["id"], 7, 1))                 # belegt
        self.assertFalse(p.fits(r["id"], 8, 2))                 # ragt in den Leaf hinein
        self.assertFalse(p.fits(r["id"], 0, 1))
        self.assertFalse(p.fits(r["id"], 10, 2))                # über die Rackhöhe hinaus
        self.assertTrue(p.fits(r["id"], 7, 1, ignore=a["id"]))  # Umsetzen an dieselbe Stelle

    def test_height_change_respects_neighbours(self):
        p, r, oob, leaf, a, b = small_plan()
        p.place(a, r["id"], 8)
        self.assertFalse(p.set_height(a, 2))                    # HE 9 ist der Leaf
        p.place(a, r["id"], 5)
        self.assertTrue(p.set_height(a, 2))
        self.assertEqual(len([x for x in a["ports"] if x["k"] == "data"]), 4)

    def test_rack_height_cannot_cut_devices(self):
        p, r, *_ = small_plan()
        over = p.set_rack_height(r["id"], 8)
        self.assertEqual(r["h"], 10)
        self.assertEqual({d["name"] for d in over}, {"oob-01", "leaf-01"})
        self.assertFalse(p.remove_rack(r["id"]))

    def test_unplace_keeps_inventory_drops_catalog(self):
        p, r, oob, leaf, a, b = small_plan()
        p.place(a, r["id"], 7)
        p.connect(a["id"], "bmc", oob["id"], "p1")
        p.unplace(a)
        self.assertIn(a, p.devs)
        self.assertIsNone(a["rack"])
        self.assertEqual(p.cables, [])
        p.unplace(leaf)
        self.assertNotIn(leaf, p.devs)


class TestCables(unittest.TestCase):
    def test_rules(self):
        p, r, oob, leaf, a, b = small_plan()
        p.place(a, r["id"], 7)
        c, err = p.connect(a["id"], "bmc", oob["id"], "p1")
        self.assertIsNone(err)
        self.assertEqual(c["net"], "mgmt")
        self.assertIsNotNone(p.connect(a["id"], "bmc", oob["id"], "p2")[1])     # Port belegt
        self.assertIsNotNone(p.connect(oob["id"], "p3", oob["id"], "p4")[1])    # mit sich selbst
        self.assertIsNotNone(p.connect(b["id"], "bmc", oob["id"], "p5")[1])     # b nicht eingebaut
        self.assertEqual(p.connect(oob["id"], "u1", leaf["id"], "u1")[0]["net"], "uplink")
        self.assertEqual(p.connect(a["id"], "n1", leaf["id"], "p1")[0]["net"], "data")

    def test_auto_cable(self):
        p, r, oob, leaf, a, b = small_plan()
        p.place(a, r["id"], 7)
        p.place(b, r["id"], 6)
        self.assertEqual(p.auto_cable(), 6)                    # je BMC + NIC 1 + NIC 2 (beide am einen Leaf)
        self.assertEqual(p.auto_cable(), 0)

    def test_lengths_round_up(self):
        p, r, oob, leaf, a, b = small_plan()
        p.place(a, r["id"], 1)
        c, _ = p.connect(a["id"], "bmc", oob["id"], "p1")
        self.assertEqual(p.cable_length(c), 1.5)                # 9 HE ≈ 0,4 m + 0,6 m Reserve
        r2 = p.add_rack("A2", 10)
        p.place(b, r2["id"], 1)
        c2, _ = p.connect(b["id"], "bmc", oob["id"], "p2")
        self.assertGreater(p.cable_length(c2), p.cable_length(c))


class TestChecksAndNets(unittest.TestCase):
    def test_checks_find_gaps(self):
        p, r, oob, leaf, a, b = small_plan()
        p.place(a, r["id"], 7)
        texts = [t for _, t, _ in p.checks()]
        self.assertTrue(any("BMC nicht verkabelt" in t for t in texts))
        self.assertTrue(any("keine PDU" in t for t in texts))
        self.assertTrue(any("noch nicht eingebaut" in t for t in texts))
        p.place(p.new_device("pdu"), r["id"], 1)
        p.connect(a["id"], "bmc", oob["id"], "p1")
        texts = [t for _, t, _ in p.checks()]
        self.assertFalse(any("BMC nicht verkabelt" in t for t in texts))
        self.assertFalse(any("keine PDU" in t for t in texts))
        self.assertEqual(p.checks()[0][0], "warn")               # sortiert: Warnungen vor Hinweisen

    def test_pdu_overload(self):
        p, r, *_ = small_plan()
        pdu = p.new_device("pdu")
        p.place(pdu, r["id"], 1)
        pdu["cap"] = 100
        self.assertEqual(p.checks()[0][0], "err")

    def test_net_plan_and_hostnames(self):
        p, r, oob, leaf, a, b = small_plan()
        p.place(a, r["id"], 6)
        p.place(b, r["id"], 7)
        p.schema = "{rack}-u{pos}"
        p.ip_start = "10.0.0.250"
        rows = p.net_plan()
        self.assertEqual([n["host"] for n in rows], ["a1-u07", "a1-u06"])   # von oben nach unten
        self.assertEqual([n["ip"] for n in rows], ["10.0.0.250", "10.0.0.251"])
        p.schema = "Server {name}!"
        self.assertEqual(p.net_plan()[0]["host"], "server-srv-b")
        p.ip_start = "kein.ip"
        self.assertIsNone(p.net_plan()[0]["ip"])
        self.assertEqual(p.checks()[0][0], "err")

    def test_bom(self):
        p = demo_plan(INV)
        rows = p.bom()
        groups = {g for g, *_ in rows}
        self.assertEqual(groups, {"Geräte", "Kabel", "Montage"})
        self.assertIn(("Kabel", "Transceiver QSFP28", "", 6), rows)   # drei Uplink-Kabel


class TestSimulation(unittest.TestCase):
    def test_states(self):
        p, r, oob, leaf, a, b = small_plan()
        p.place(a, r["id"], 7)
        bmc, _ = p.connect(a["id"], "bmc", oob["id"], "p1")
        n1, _ = p.connect(a["id"], "n1", leaf["id"], "p1")
        p.connect(a["id"], "n2", leaf["id"], "p2")
        self.assertEqual(p.simulate()[a["id"]], "ok")
        self.assertEqual(p.simulate(broken=[n1["id"]])[a["id"]], "degraded")
        self.assertEqual(p.simulate(broken=[bmc["id"]])[a["id"]], "down")
        self.assertEqual(p.simulate(failed=[oob["id"]])[a["id"]], "down")
        self.assertEqual(p.simulate(failed=[leaf["id"]])[a["id"]], "degraded")


class TestPersistence(unittest.TestCase):
    def test_roundtrip(self):
        p = demo_plan(INV)
        q = Plan.from_dict(json.loads(json.dumps(p.to_dict())))
        self.assertEqual(q.to_dict(), p.to_dict())
        new = q.add_rack()
        self.assertNotIn(new["id"], {r["id"] for r in p.racks})   # Zähler läuft nach dem Laden weiter

    def test_rejects_and_sanitises(self):
        with self.assertRaises(ValueError):
            Plan.from_dict({"racks": []})
        with self.assertRaises(ValueError):
            Plan.from_dict([1, 2])
        bad = {"format": "bmc-steward-plan", "racks": [{"id": "r1", "h": 999}, {"h": 4}, "x"],
               "devices": [{"id": "d1", "kind": "switch", "u": 1, "rack": "r1", "pos": 1,
                            "ports": [{"id": "p1", "k": "mgmt"}, {"id": 3, "k": "x"}]},
                           {"id": "d2", "kind": "rocket", "u": 1, "rack": "r1", "pos": 2},
                           {"id": "d3", "kind": "server", "u": 1, "rack": "r1", "pos": 1},     # überlappt d1
                           {"id": "d4", "kind": "server", "u": 1, "inv": True, "rack": "nope", "pos": 5}],
               "cables": [{"a": ["d1", "p1"], "b": ["d1", "p1"]}, {"a": "kaputt"}]}
        p = Plan.from_dict(bad)
        self.assertEqual(p.racks[0]["h"], 60)
        self.assertEqual([d["id"] for d in p.devs], ["d1", "d4"])
        self.assertEqual(len(p.devs[0]["ports"]), 1)
        self.assertIsNone(p.dev("d4")["rack"])
        self.assertEqual(p.cables, [])


class TestHelpers(unittest.TestCase):
    def test_labels_and_vendors(self):
        self.assertEqual(port_label("bmc"), "BMC")
        self.assertEqual(port_label("n2"), "NIC 2")
        self.assertEqual(port_label("u1"), "Uplink 1")
        self.assertEqual(port_label("p7"), "Port 7")
        self.assertEqual(vendor_of("Dell iDRAC9"), "dell")
        self.assertEqual(vendor_of("HPE iLO 6"), "hpe")
        self.assertEqual(vendor_of("Lenovo XCC3"), "lenovo")
        self.assertEqual(vendor_of("Supermicro X13"), "smc")
        self.assertEqual(vendor_of(""), "gen")

    def test_inventory_sync_keeps_placed(self):
        p, r, oob, leaf, a, b = small_plan()
        p.place(a, r["id"], 7)
        p.sync_inventory(INV[1:])
        self.assertTrue(a.get("missing"))
        p.sync_inventory([])
        self.assertNotIn(b, p.devs)
        self.assertIn(a, p.devs)


class TestPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from bmc_steward.main import Window
        cls.w = Window()

    @classmethod
    def tearDownClass(cls):
        cls.w.plan_dirty = cls.w.dirty = False
        cls.w.close()

    def fill_list(self):
        w = self.w
        w.table.setRowCount(0)
        for ip, host, sn, vendor in (("192.0.2.11", "srv-1", "SN1", "Dell iDRAC9"),
                                     ("192.0.2.12", "srv-2", "SN2", "HPE iLO 6")):
            vals = [""] * len(i18n.COLS)
            vals[0], vals[5], vals[6] = ip, host, sn
            w.add_row(vals, vendor)

    def test_page_and_transfer(self):
        from unittest import mock
        from PyQt6.QtWidgets import QMessageBox
        w = self.w
        self.fill_list()
        w.tabs.setCurrentIndex(w.tabs.indexOf(w.planner_page))
        self.assertEqual(w.tabs.indexOf(w.planner_page), w.tabs.count() - 2)   # vor „Hilfe“
        self.assertEqual({d["name"] for d in w.plan.devs if d.get("inv")}, {"srv-1", "srv-2"})
        w.plan_dirty = False                                   # sonst fragt „Beispiel laden“ nach
        w.planner_demo()
        self.assertEqual(len(w.plan.placed("server")), 2)
        self.assertTrue(w.pl_canvas.minimumWidth() > 600)
        w.pl_canvas.grab()                                     # Zeichnen ohne Ausnahme
        w.plan.ip_start, w.plan.schema = "10.9.0.5", "{rack}-u{pos}"
        with mock.patch.object(QMessageBox, "exec", lambda box: box.setProperty("x", 1)), \
                mock.patch.object(QMessageBox, "clickedButton", lambda box: box.buttons()[0]):
            w.planner_to_neuware()
        self.assertEqual(w.tabs.currentIndex(), 1)
        cells = {w.cell(r, 6): (w.cell(r, 2), w.cell(r, 5)) for r in range(w.table.rowCount())}
        self.assertEqual(cells["SN1"][0][:7], "10.9.0.")
        self.assertTrue(cells["SN1"][1].startswith("c01-u"))
        self.assertEqual(w.table.rowCount(), 2)                # nichts doppelt angelegt

    def test_library_double_click_and_delete(self):
        w = self.w
        from bmc_steward.planner import Plan
        p = Plan()
        p.add_rack("X", 12)
        w.planner_set_plan(p, dirty=False)
        item = next(w.pl_lib.item(i) for i in range(w.pl_lib.count())
                    if w.pl_lib.item(i).data(0x0100) == "cat:leaf")
        w.planner_lib_double(item)
        leaf = w.plan.placed("switch")[0]
        self.assertEqual(leaf["pos"], 12)
        self.assertTrue(w.plan_dirty)
        w.pl_canvas.select(("dev", leaf["id"]))
        w.planner_delete_selected()
        self.assertEqual(w.plan.placed(), [])
        w.pl_canvas.select(("rack", p.racks[0]["id"]))
        w.planner_delete_selected()
        self.assertEqual(w.plan.racks, [])

    def test_mouse_cabling_and_moving(self):
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtTest import QTest
        w = self.w
        self.fill_list()
        p = Plan()
        r = p.add_rack("M", 12)
        w.planner_set_plan(p, dirty=False)
        oob = w.plan.new_device("oob")
        w.plan.place(oob, r["id"], 12)
        srv = next(d for d in w.plan.devs if d.get("inv"))
        w.plan.place(srv, r["id"], 10)
        w.planner_refresh()
        cv = w.pl_canvas
        cv.resize(cv.minimumSize())
        at = lambda did, pid: cv.port_center(did, pid).toPoint()
        w.pl_cable_btn.setChecked(True)
        QTest.mouseClick(cv, Qt.MouseButton.LeftButton, pos=at(srv["id"], "bmc"))
        QTest.mouseClick(cv, Qt.MouseButton.LeftButton, pos=at(oob["id"], "p3"))
        c = w.plan.cable_at(srv["id"], "bmc")
        self.assertEqual(tuple(c["b"]), (oob["id"], "p3"))
        self.assertEqual(c["net"], "mgmt")
        w.pl_cable_btn.setChecked(False)
        # Server um drei HE nach unten ziehen
        start = cv.dev_rect(srv).center().toPoint() - QPoint(60, 0)
        end = start + QPoint(0, 3 * cv.UH)
        QTest.mousePress(cv, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(cv, start + QPoint(0, 8))
        QTest.mouseMove(cv, end)
        QTest.mouseRelease(cv, Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(srv["pos"], 7)
        self.assertIsNotNone(w.plan.cable_at(srv["id"], "bmc"))   # Kabel bleibt beim Umsetzen stecken

    def test_navigator_objects_and_views(self):
        w = self.w
        self.fill_list()
        w.planner_set_plan(demo_plan(w.planner_inventory()), dirty=False)
        # Navigator: Standort › Raum › Reihe › Racks › Geräte
        top = w.pl_tree.topLevelItem(0)
        self.assertEqual(top.text(0), "FRA")
        row = top.child(0).child(0)
        self.assertEqual(row.text(0), "Reihe C")
        self.assertEqual(row.childCount(), 2)
        dev_item = row.child(0).child(0)
        w.planner_tree_clicked(dev_item)
        did = dev_item.data(0, 0x0100).partition(":")[2]
        self.assertEqual(w.pl_canvas.sel, ("dev", did))
        self.assertIn("Rack C01", w.pl_crumb.text())
        # Objektliste mit Filter
        self.assertEqual(w.pl_objects.rowCount(), len(w.plan.placed()))
        w.pl_filter.setText("leaf")
        shown = [r for r in range(w.pl_objects.rowCount()) if not w.pl_objects.isRowHidden(r)]
        self.assertEqual(len(shown), 2)
        w.pl_filter.clear()
        # Vorderseite: keine Ports und Kabel zum Anklicken; Verkabeln schaltet auf die Rückseite
        w.planner_set_view("front")
        srv = w.plan.placed("server")[0]
        c = w.pl_canvas.port_center(srv["id"], "bmc")
        self.assertIsNone(w.pl_canvas.port_at(c))
        w.pl_cable_btn.setChecked(True)
        self.assertEqual(w.pl_canvas.view, "rear")
        self.assertEqual(w.pl_canvas.port_at(c), (srv["id"], "bmc"))
        w.planner_set_view("front")
        self.assertFalse(w.pl_cable_btn.isChecked())
        w.pl_canvas.grab()
        # Standort wird gespeichert
        w.pl_loc["room"].setText("RZ-Süd")
        w.planner_loc_edit("room", w.pl_loc["room"])
        self.assertEqual(Plan.from_dict(w.plan.to_dict()).room, "RZ-Süd")
        w.plan_dirty = False

    def test_simulation_toggle(self):
        w = self.w
        self.fill_list()
        w.planner_set_plan(demo_plan(w.planner_inventory()), dirty=False)
        w.pl_sim_btn.setChecked(True)
        srv = w.plan.placed("server")[0]
        bmc = w.plan.cable_at(srv["id"], "bmc")
        w.planner_sim_click("cable", bmc["id"])
        self.assertEqual(w.pl_canvas.sim[srv["id"]], "down")
        w.pl_sim_btn.setChecked(False)
        self.assertIsNone(w.pl_canvas.sim)
        self.assertFalse(w.plan_dirty)                          # Simulation ändert den Plan nicht


if __name__ == "__main__":
    unittest.main()
