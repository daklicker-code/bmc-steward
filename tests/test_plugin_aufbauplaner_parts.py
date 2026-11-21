"""Aufbauplaner-Plugin: Server-Komponenten (Netzteile, Netzwerkkarten mit Transceivern, Laufwerke), ihre Anzeige in der
Streckenansicht, das Bearbeitungsmenü per Rechtsklick und das Verschieben der Ansicht mit der linken Maustaste."""
import json
import unittest
from unittest import mock

from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n  # noqa: E402
i18n.set_language("de")
i18n.build_texts()
from tests import _aufbauplaner  # noqa: E402

_, planner, planner_tab, _texts = _aufbauplaner.package()
Plan, drive_bays, sfp_fits = planner.Plan, planner.drive_bays, planner.sfp_fits


def rig():
    """Rack mit Server (1 HE), Leaf und OOB; der Server hängt mit NIC 1 am Leaf."""
    p = Plan()
    r = p.add_rack("A1", 20)
    srv, leaf, oob = p.new_device("server1"), p.new_device("leaf"), p.new_device("oob")
    p.place(srv, r["id"], 5)
    p.place(leaf, r["id"], 20)
    p.place(oob, r["id"], 19)
    return p, r, srv, leaf, oob


class TestComponents(unittest.TestCase):
    def test_defaults(self):
        p, r, srv, leaf, oob = rig()
        self.assertEqual(srv["psus"], ["psu800", "psu800"])
        self.assertEqual([(n["slot"], n["m"]) for n in srv["nics"]], [("OCP", "ocp_25g2")])
        self.assertEqual([x["id"] for x in srv["ports"]], ["bmc", "n1", "n2"])
        self.assertEqual(p.port(srv, "n2")["l"], "OCP P2")
        big = p.new_device("server2")
        self.assertEqual([x["id"] for x in big["ports"]], ["bmc", "n1", "n2", "n3", "n4"])
        self.assertEqual(big["drives"], [])

    def test_nics_slots_and_ports(self):
        p, r, srv, leaf, oob = rig()
        n, err = p.add_nic(srv, "pcie_100g2", "PCIe 2")
        self.assertIsNone(err)
        self.assertEqual(n["ports"], ["n3", "n4"])
        self.assertEqual(p.port(srv, "n3")["l"], "PCIe 2 P1")
        self.assertIsNotNone(p.add_nic(srv, "ocp_10g2", "PCIe 1")[1])       # OCP-Karte passt nicht in PCIe
        self.assertIsNotNone(p.add_nic(srv, "pcie_25g2", "PCIe 2")[1])      # belegt
        fc, _ = p.add_nic(srv, "pcie_fc32")
        self.assertEqual(fc["slot"], "PCIe 1")
        self.assertEqual(p.port(srv, fc["ports"][0])["k"], "fc")
        self.assertIsNotNone(p.add_nic(srv, "pcie_25g2")[1])                # 1 HE: keine PCIe-Plätze mehr
        # Reihenfolge der Ports folgt den Steckplätzen
        self.assertEqual([p.nic_of(srv, x["id"])["slot"] for x in srv["ports"][1:]],
                         ["OCP", "OCP", "PCIe 1", "PCIe 1", "PCIe 2", "PCIe 2"])
        c, _ = p.connect(srv["id"], "n3", leaf["id"], "u1")
        self.assertIsNotNone(c)
        p.remove_nic(srv, n["id"])
        self.assertIsNone(p.port(srv, "n3"))
        self.assertIsNone(p.cable(c["id"]))                                  # Kabel mit der Karte weg
        n5, _ = p.add_nic(srv, "pcie_10g2", "PCIe 2")
        self.assertEqual(n5["ports"], ["n7", "n8"])                          # keine alten Kennungen wiederverwendet

    def test_transceivers_must_fit_the_cage(self):
        p, r, srv, leaf, oob = rig()
        self.assertIsNone(p.set_sfp(srv, "n1", "SFP28 25G SR (MMF, 850 nm)"))
        self.assertIsNone(p.set_sfp(srv, "n2", "SFP+ 10G LR (SMF, 1310 nm)"))    # SFP+ passt in SFP28
        self.assertIsNotNone(p.set_sfp(srv, "n2", "QSFP28 100G DAC"))
        self.assertEqual(p.port(srv, "n2")["sfp"], "SFP+ 10G LR (SMF, 1310 nm)")
        self.assertIsNotNone(p.set_sfp(srv, "bmc", "SFP 1G RJ45"))
        rj, _ = p.add_nic(srv, "pcie_10gt2")
        self.assertIsNotNone(p.set_sfp(srv, rj["ports"][0], "SFP+ 10G DAC"))
        self.assertEqual(p.sfp_choices(srv, rj["ports"][0]), [])
        self.assertIn("QSFP28 100G DAC", p.sfp_choices(leaf, "u1"))            # Switches: Vorschläge je Portart
        self.assertIsNone(p.set_sfp(srv, "n1", ""))
        self.assertNotIn("sfp", p.port(srv, "n1"))
        self.assertTrue(sfp_fits("FC", "SFP+ FC 32G SW (MMF, 850 nm)"))
        self.assertFalse(sfp_fits("SFP28", "SFP+ FC 32G SW (MMF, 850 nm)"))
        self.assertTrue(sfp_fits("SFP28", "SFP28 eigener Typ"))

    def test_psus_and_drives(self):
        p, r, srv, leaf, oob = rig()
        self.assertIsNotNone(p.add_psu(srv, "psu1400"))                          # beide Schächte belegt
        p.remove_psu(srv, 0)
        self.assertIsNone(p.add_psu(srv, "psu1400"))
        self.assertEqual(sorted(srv["psus"]), ["psu1400", "psu800"])
        self.assertEqual(p.add_drives(srv, "nvme3840", 50), drive_bays(1))
        self.assertEqual(p.add_drives(srv, "ssd960", 1), 0)
        p.remove_drive(srv, 0)
        self.assertEqual(len(srv["drives"]), drive_bays(1) - 1)

    def test_height_change(self):
        p, r, srv, leaf, oob = rig()
        p.connect(srv["id"], "n1", leaf["id"], "p1")
        self.assertTrue(p.set_height(srv, 2))                                    # Vorgabe-Karten wachsen mit
        self.assertEqual([x["id"] for x in srv["ports"]], ["bmc", "n1", "n2", "n3", "n4"])
        self.assertTrue(p.cable_at(srv["id"], "n1"))
        p.add_nic(srv, "pcie_100g2", "PCIe 5")
        self.assertFalse(p.set_height(srv, 1))                                   # PCIe 5 gibt es in 1 HE nicht
        self.assertIn("PCIe 5", p.height_problem(srv, 1))
        self.assertEqual(srv["u"], 2)

    def test_checks(self):
        p, r, srv, leaf, oob = rig()
        p.connect(srv["id"], "n1", leaf["id"], "p1")
        texts = lambda: [t for _, t, _ in p.checks()]
        self.assertTrue(any("kein Transceiver" in t for t in texts()))
        p.set_sfp(srv, "n1", "SFP28 25G SR (MMF, 850 nm)")
        p.port(leaf, "p1")["sfp"] = "SFP28 25G LR (SMF, 1310 nm)"
        self.assertTrue(any("passen nicht zusammen" in t for t in texts()))
        p.port(leaf, "p1")["sfp"] = "SFP28 25G SR (MMF, 850 nm)"
        self.assertFalse(any("passen nicht zusammen" in t or "kein Transceiver" in t for t in texts()))
        srv["w"] = 1000
        self.assertTrue(any("fällt eines aus" in t for t in texts()))
        srv["psus"] = []
        self.assertIn(("err", "neu-01: kein Netzteil gesteckt", srv["id"]), p.checks())

    def test_bom_lists_components(self):
        p, r, srv, leaf, oob = rig()
        p.add_drives(srv, "ssd960", 2)
        p.set_sfp(srv, "n1", "SFP28 25G DAC")
        rows = p.bom()
        self.assertIn(("Komponenten", "Netzteil 800 W", "", 2), rows)
        self.assertIn(("Komponenten", "OCP 3.0 2×25G SFP28", "", 1), rows)
        self.assertIn(("Komponenten", "SSD SATA 960 GB", "", 2), rows)
        self.assertIn(("Optiken", "SFP28 25G DAC", "", 1), rows)

    def test_roundtrip_and_garbage(self):
        p, r, srv, leaf, oob = rig()
        p.add_nic(srv, "pcie_fc32", "PCIe 2")
        p.add_drives(srv, "hdd12t", 3)
        p.set_sfp(srv, "n1", "SFP28 25G SR (MMF, 850 nm)")
        q = Plan.from_dict(json.loads(json.dumps(p.to_dict())))
        self.assertEqual(q.to_dict(), p.to_dict())
        data = p.to_dict()
        raw = next(d for d in data["devices"] if d["id"] == srv["id"])
        raw["nics"] += [{"id": "c9", "slot": "PCIe 7", "m": "pcie_25g2", "ports": ["n20", "n21"]},     # kein Platz
                        {"id": "c8", "slot": "PCIe 1", "m": "pcie_25g2", "ports": ["n1", "n22"]},      # n1 doppelt
                        {"id": "c7", "slot": "PCIe 1", "m": "gibtsnicht", "ports": ["n30", "n31"]},
                        "Unsinn"]
        raw["psus"] = ["psu800", "psu9999", "psu1100", "psu2400"]
        raw["drives"] = ["ssd960"] * 99
        raw["ports"].append({"id": "n3", "k": "data", "sfp": "QSFP28 100G DAC"})   # passt nicht: fällt weg
        q = Plan.from_dict(data)
        s = q.dev(srv["id"])
        self.assertEqual([n["slot"] for n in s["nics"]], ["OCP", "PCIe 2"])
        self.assertEqual(s["psus"], ["psu800", "psu1100"])
        self.assertEqual(len(s["drives"]), drive_bays(1))
        self.assertNotEqual(q.port(s, "n3").get("sfp"), "QSFP28 100G DAC")

    def test_old_plans_keep_cables(self):
        """Pläne der Version 1 kennen keine Komponenten: Vorgabe-Karten mit den alten Port-Kennungen."""
        old = {"format": "bmc-steward-plan", "version": 1, "racks": [{"id": "r1", "name": "A", "h": 42}],
               "devices": [{"id": "d1", "kind": "server", "u": 2, "rack": "r1", "pos": 5, "name": "alt",
                            "ports": [{"id": "bmc", "k": "bmc"}, {"id": "n1", "k": "data", "sfp": "SFP28 25G DAC"},
                                      {"id": "n4", "k": "data", "mac": "00:11:22:33:44:55"}]},
                           {"id": "d2", "kind": "switch", "role": "leaf", "u": 1, "rack": "r1", "pos": 40,
                            "name": "leaf", "ports": [{"id": "p1", "k": "data"}, {"id": "p2", "k": "data"}]}],
               "cables": [{"id": "c1", "a": ["d1", "n4"], "b": ["d2", "p1"]}]}
        p = Plan.from_dict(old)
        s = p.dev("d1")
        self.assertEqual([x["id"] for x in s["ports"]], ["bmc", "n1", "n2", "n3", "n4"])
        self.assertEqual(p.port(s, "n1")["sfp"], "SFP28 25G DAC")
        self.assertEqual(p.port(s, "n4")["mac"], "00:11:22:33:44:55")
        self.assertIsNotNone(p.cable_at("d1", "n4"))
        self.assertEqual(len(s["psus"]), 2)

    def test_auto_cable_plugs_dacs(self):
        p, r, srv, leaf, oob = rig()
        self.assertEqual(p.auto_cable(), 3)
        self.assertEqual(p.port(srv, "n1")["sfp"], "SFP28 25G DAC")
        self.assertFalse(any("Transceiver" in t for _, t, _ in p.checks()))


class TestPage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.win, cls.w = _aufbauplaner.window()

    @classmethod
    def tearDownClass(cls):
        _aufbauplaner.close(cls.win, cls.w)

    def setUp(self):
        self.p, self.r, self.srv, self.leaf, self.oob = rig()
        self.w.planner_set_plan(self.p, dirty=False)
        self.w.planner_set_view("rear")

    def actions(self, menu):
        out = {}
        for a in menu.actions():
            if a.menu():
                out[a.text()] = self.actions(a.menu())
            elif a.text():
                out[a.text()] = a
        return out

    def test_context_menu_parts(self):
        w, srv = self.w, self.w.plan.dev(self.srv["id"])
        menu = w.planner_context_menu("dev", srv["id"])
        parts = self.actions(menu)["Komponenten"]
        self.assertIn("Komponenten bearbeiten …", parts)
        nic = parts["Netzwerkkarte stecken"]["PCIe 1"]
        self.assertIn("PCIe 2×100G QSFP28", nic)
        self.assertNotIn("OCP 3.0 2×25G SFP28", nic)
        nic["PCIe 2×100G QSFP28"].trigger()
        self.assertEqual(len(w.plan.dev(srv["id"])["nics"]), 2)
        parts = self.actions(w.planner_context_menu("dev", srv["id"]))["Komponenten"]
        parts["Laufwerke stecken (10 Schächte frei)"]["SSD NVMe 3,84 TB"]["4 ×"].trigger()
        self.assertEqual(len(w.plan.dev(srv["id"])["drives"]), 4)
        parts = self.actions(w.planner_context_menu("dev", srv["id"]))["Komponenten"]
        parts["Netzteil ziehen"]["PSU 1: Netzteil 800 W"].trigger()
        self.assertEqual(len(w.plan.dev(srv["id"])["psus"]), 1)
        w.planner_undo()
        self.assertEqual(len(w.plan.dev(srv["id"])["psus"]), 2)
        w.pl_canvas.grab()                                            # Rückseite mit Netzteilen und Karten
        w.planner_set_view("front")
        w.pl_canvas.grab()                                            # Front mit Laufwerksschächten

    def test_port_menu_transceiver(self):
        w = self.w
        sid = self.srv["id"]
        acts = self.actions(w.planner_context_menu("port", (sid, "n1")))
        sfps = acts["Transceiver stecken"]
        self.assertIn("SFP28 25G SR (MMF, 850 nm)", sfps)
        self.assertNotIn("QSFP28 100G DAC", sfps)
        sfps["SFP28 25G SR (MMF, 850 nm)"].trigger()
        self.assertEqual(w.plan.port(w.plan.dev(sid), "n1")["sfp"], "SFP28 25G SR (MMF, 850 nm)")
        acts = self.actions(w.planner_context_menu("port", (sid, "n1")))
        self.assertIn("Transceiver ziehen (SFP28 25G SR (MMF, 850 nm))", acts)
        self.assertIn("Netzwerkkarte OCP ziehen", acts)
        self.assertNotIn("Transceiver stecken", self.actions(w.planner_context_menu("port", (sid, "bmc"))))
        # Eigenschaften: unpassender Transceiver wird abgelehnt
        w.planner_select_port(sid, "n2")
        w.planner_port_edit("sfp", "QSFP28 100G DAC")
        self.assertNotIn("sfp", w.plan.port(w.plan.dev(sid), "n2"))

    def test_trace_shows_nic_and_sfp(self):
        w = self.w
        sid, lid = self.srv["id"], self.leaf["id"]
        w.plan.connect(sid, "n1", lid, "p3")
        w.plan.set_sfp(w.plan.dev(sid), "n1", "SFP28 25G SR (MMF, 850 nm)")
        seen = {}
        with mock.patch.object(planner_tab.TraceDialog, "exec", lambda d: seen.setdefault("t", d.text) and 1):
            w.planner_show_trace(sid, "n1")
        self.assertIn("Netzwerkkarte: OCP · OCP 3.0 2×25G SFP28", seen["t"])
        self.assertIn("Transceiver: SFP28 25G SR (MMF, 850 nm)", seen["t"])
        self.assertIn("Transceiver: –", seen["t"])                    # Leaf-Port ohne Modul

    def test_components_dialog(self):
        w = self.w
        sid = self.srv["id"]
        dlg = planner_tab.ComponentsDialog(w, w.plan, sid, w.planner_changed)
        self.assertFalse(dlg.psu_add.isEnabled())                     # beide Schächte belegt
        dlg.psu_list.setCurrentRow(1)
        dlg.remove_psu()
        self.assertEqual(len(w.plan.dev(sid)["psus"]), 1)
        dlg.nic_model.setCurrentIndex(dlg.nic_model.findData("pcie_25g2"))
        self.assertEqual([dlg.nic_slot.itemText(i) for i in range(dlg.nic_slot.count())], ["PCIe 1", "PCIe 2"])
        dlg.add_nic()
        nic = w.plan.dev(sid)["nics"][1]
        dlg.nic_tree.setCurrentItem(dlg.nic_tree.topLevelItem(1))
        dlg.sfp_model.setEditText("SFP+ 10G SR (MMF, 850 nm)")
        dlg.set_sfp_all()
        self.assertTrue(all(w.plan.port(w.plan.dev(sid), x)["sfp"] == "SFP+ 10G SR (MMF, 850 nm)"
                            for x in nic["ports"]))
        dlg.drv_model.setCurrentIndex(dlg.drv_model.findData("hdd2400"))
        dlg.drv_count.setValue(3)
        dlg.add_drives()
        self.assertEqual(w.plan.dev(sid)["drives"], ["hdd2400"] * 3)
        dlg.drv_list.setCurrentRow(0)
        dlg.remove_drives()
        self.assertEqual(len(w.plan.dev(sid)["drives"]), 2)
        dlg.nic_tree.setCurrentItem(dlg.nic_tree.topLevelItem(0))
        dlg.remove_nic()
        self.assertEqual([n["slot"] for n in w.plan.dev(sid)["nics"]], ["PCIe 1"])
        self.assertTrue(w.plan_dirty)
        dlg.close()
        w.pl_canvas.select(("dev", sid))
        w.planner_fill_props()
        self.assertIn("PCIe 1: PCIe 2×25G SFP28", w.pl_d_parts.text())
        self.assertTrue(w.pl_d_parts_box.isVisibleTo(w.pl_props))

    def test_left_drag_pans_and_click_selects(self):
        from PyQt6.QtCore import QPoint, Qt
        from PyQt6.QtTest import QTest
        w = self.w
        self.win.tabs.setCurrentIndex(self.win.tabs.indexOf(w))
        for _ in range(4):
            w.plan.add_rack()
        w.planner_refresh()
        w.pl_scroll.resize(300, 200)
        w.pl_canvas.set_zoom(2.5)
        cv, hb = w.pl_canvas, w.pl_scroll.horizontalScrollBar()
        self.assertGreater(hb.maximum(), 0)
        hb.setValue(hb.maximum() // 2)
        before = hb.value()
        free = QPoint(int(cv.rack_x(1) * cv.zoom - cv.GAP / 2 * cv.zoom), int((cv.top() + 40) * cv.zoom))
        w.pl_canvas.select(None)
        QTest.mousePress(cv, Qt.MouseButton.LeftButton, pos=free)
        QTest.mouseMove(cv, free + QPoint(20, 0))
        QTest.mouseMove(cv, free + QPoint(80, 0))
        QTest.mouseRelease(cv, Qt.MouseButton.LeftButton, pos=free + QPoint(80, 0))
        self.assertLess(hb.value(), before)                             # Ansicht folgt der Maus
        self.assertIsNone(cv.sel)
        rack = w.plan.racks[1]
        inside = QPoint(int((cv.rack_x(1) + 40) * cv.zoom), int((cv.top() + 10) * cv.zoom))
        hb.setValue(int(cv.rack_x(1) * cv.zoom))
        inside -= QPoint(0, 0)
        QTest.mouseClick(cv, Qt.MouseButton.LeftButton, pos=QPoint(int((cv.rack_x(1) + 40) * cv.zoom),
                                                                    int((cv.top() + 10) * cv.zoom)))
        self.assertEqual(cv.sel, ("rack", rack["id"]))                 # Klick ohne Ziehen: wählt aus
        # Grundriss: Ziehen auf freier Fläche verschiebt, Klick wählt die Bodenplatte
        w.planner_set_view("floor")
        fc, fh = w.pl_floor, w.pl_floor_scroll.horizontalScrollBar()
        w.pl_floor_scroll.resize(300, 200)
        fc.set_zoom(2.5)
        fh.setValue(fh.maximum())
        before = fh.value()
        start = fc.tile_rect(w.plan.cols - 2, 1).center().toPoint() * 1
        start = QPoint(int(start.x() * fc.zoom), int(start.y() * fc.zoom))
        QTest.mousePress(fc, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(fc, start + QPoint(30, 0))
        QTest.mouseMove(fc, start + QPoint(90, 0))
        QTest.mouseRelease(fc, Qt.MouseButton.LeftButton, pos=start + QPoint(90, 0))
        self.assertLess(fh.value(), before)
        self.assertIsNone(fc.tile_sel)


if __name__ == "__main__":
    unittest.main()
