"""Aufbauplaner: Platzprüfung, Kabel, Prüfung, Netzplan, Stückliste, Ausfall-Simulation, Speichern/Laden
und die Seite im Hauptfenster (Übernahme in die Serverliste, Bestand aus „Neuware“)."""
import json
import unittest
from tests._support import qapp

qapp()
import bmc_steward.i18n as i18n
i18n.set_language("de")
i18n.build_texts()
from bmc_steward.planner import (Plan, compatible, through_port, col_index, col_name, coord_name, demo_plan, netapp_demo_plan, parse_coord,
                                 port_label, port_name, vendor_of)

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
        self.assertEqual(groups, {"Geräte", "Komponenten", "Kabel", "Optiken", "Montage"})
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


class TestFloor(unittest.TestCase):
    def test_column_names_like_in_the_datacenter(self):
        self.assertEqual([col_name(i) for i in (0, 1, 25, 26, 27, 51, 52, 701)],
                         ["A", "B", "Z", "AA", "AB", "AZ", "BA", "ZZ"])
        for i in range(800):
            self.assertEqual(col_index(col_name(i)), i)
        self.assertEqual(coord_name(2, 6), "C07")
        self.assertEqual(parse_coord("aa12"), (26, 11))
        self.assertEqual(parse_coord(" C-7 "), (2, 6))
        for bad in ("", "7C", "C0", "Ä1", "C", "12"):
            self.assertIsNone(parse_coord(bad), bad)

    def test_racks_stand_on_tiles(self):
        p = Plan()
        p.cols, p.rows = 6, 4
        a = p.add_rack("A", at=(0, 0))
        self.assertEqual(p.coord(a), "A01")
        b = p.add_rack("B")                                    # automatisch daneben
        self.assertEqual(p.coord(b), "B01")
        self.assertFalse(p.move_rack(b["id"], 0, 1))            # A belegt A01/A02
        self.assertFalse(p.move_rack(b["id"], 5, 3))            # zweite Platte läge außerhalb
        self.assertTrue(p.move_rack(b["id"], 4, 3, "O"))        # quer: E04 + F04
        self.assertFalse(p.can_stand(None, 5, 3, "S"))
        self.assertEqual(p.set_grid(5, 4), [b])                 # B stünde dann außerhalb
        self.assertEqual(p.cols, 6)
        self.assertEqual(p.set_grid(30, 20), [])

    def test_full_floor_and_cable_distance(self):
        p = Plan()
        p.cols = p.rows = 2
        p.add_rack("A")
        p.add_rack("B")
        c = p.add_rack("C")                                     # kein Platz mehr
        self.assertIsNone(c["x"])
        self.assertTrue(any("kein Stellplatz" in t for _, t, _ in p.checks()))
        q = Plan()
        q.cols = 30
        r1, r2 = q.add_rack("R1", 10, at=(0, 0)), q.add_rack("R2", 10, at=(20, 0))
        a, b = q.new_device("leaf"), q.new_device("leaf")
        q.place(a, r1["id"], 10)
        q.place(b, r2["id"], 10)
        cab, _ = q.connect(a["id"], "u1", b["id"], "u1")
        self.assertEqual(q.cable_length(cab), 15)               # 20 Platten à 0,6 m + Reserve

    def test_positions_survive_saving(self):
        p = demo_plan(INV)
        q = Plan.from_dict(json.loads(json.dumps(p.to_dict())))
        self.assertEqual([(r["x"], r["y"], r["orient"]) for r in q.racks],
                         [(r["x"], r["y"], r["orient"]) for r in p.racks])
        self.assertEqual((q.cols, q.rows), (p.cols, p.rows))
        data = p.to_dict()
        data["racks"][1].update(x=data["racks"][0]["x"], y=data["racks"][0]["y"])   # Überlappung in der Datei
        data["floor"] = {"cols": "viele"}
        q = Plan.from_dict(data)
        self.assertNotEqual((q.racks[1]["x"], q.racks[1]["y"]), (q.racks[0]["x"], q.racks[0]["y"]))


class TestNetApp(unittest.TestCase):
    def test_example_is_clean_and_labelled(self):
        p = netapp_demo_plan(INV)
        self.assertEqual(p.checks(), [])
        aff = p.placed("storage")[0]
        self.assertEqual(port_name(aff, "a_e0m"), "A e0M")
        fb = p.placed("bridge")[0]
        self.assertEqual(port_name(fb, "sasa"), "SAS A")
        nets = {c["net"] for c in p.cables}
        self.assertTrue({"fc", "sas", "cluster", "mgmt", "data", "uplink"} <= nets)
        rows = p.bom()
        self.assertTrue(any(r[1] == "SFP-Optik FC 32G" for r in rows))
        q = Plan.from_dict(json.loads(json.dumps(p.to_dict())))
        self.assertEqual(port_name(q.dev(aff["id"]), "a_e0m"), "A e0M")       # Beschriftung übersteht das Laden
        self.assertEqual(len(q.cables), len(p.cables))

    def test_checks_and_simulation(self):
        p = Plan()
        r = p.add_rack("S", 42)
        made, err = p.apply_template("metrocluster", r["id"])
        self.assertIsNone(err)
        fc1, fc2, cl1, cl2, aff, fb1, fb2, sh1, sh2 = made
        state = p.simulate(failed=[fc1["id"]])
        self.assertEqual(state[aff["id"]], "degraded")          # zweite Fabric trägt
        self.assertEqual(state[fb1["id"]], "down")
        self.assertEqual(state[sh1["id"]], "degraded")          # noch über das Nachbar-Shelf
        self.assertEqual(p.simulate(failed=[fc1["id"], fc2["id"]])[aff["id"]], "down")
        p.disconnect(p.cable_at(aff["id"], "a_0b")["id"])
        p.disconnect(p.cable_at(aff["id"], "b_0b")["id"])
        p.disconnect(p.cable_at(aff["id"], "b_fcvi")["id"])
        self.assertTrue(any("zweite Fabric" in t for _, t, _ in p.checks()))
        small = Plan()
        tiny = small.add_rack("T", 10)
        made, err = small.apply_template("metrocluster", tiny["id"])
        self.assertEqual(made, [])
        self.assertIn("14 HE", err)

    def test_remove_rack_with_devices(self):
        p, r, oob, leaf, a, b = small_plan()
        p.place(a, r["id"], 5)
        self.assertFalse(p.remove_rack(r["id"]))
        self.assertTrue(p.remove_rack(r["id"], force=True))
        self.assertIn(a, p.devs)                                # Bestand bleibt
        self.assertNotIn(oob, p.devs)


class TestPathsAndPorts(unittest.TestCase):
    def build(self):
        p = Plan()
        a, b = p.add_rack("A", 42), p.add_rack("B", 42, at=(10, 3))
        pp1, pp2 = p.new_device("patch"), p.new_device("patch")
        p.place(pp1, a["id"], 42)
        p.place(pp2, b["id"], 42)
        srv, sw = p.new_device("server1"), p.new_device("leaf")
        p.place(srv, a["id"], 10)
        p.place(sw, b["id"], 40)
        p.connect(srv["id"], "n1", pp1["id"], "p3")
        p.connect(pp1["id"], "r3", pp2["id"], "r3")
        p.connect(pp2["id"], "p3", sw["id"], "p7")
        return p, srv, sw, pp1, pp2

    def test_trace_goes_through_patch_panels_both_ways(self):
        p, srv, sw, pp1, pp2 = self.build()
        hops = p.trace(srv["id"], "n1")
        self.assertEqual([(d["name"], pid) for d, pid, _ in hops],
                         [(srv["name"], "n1"), (pp1["name"], "p3"), (pp1["name"], "r3"), (pp2["name"], "r3"),
                          (pp2["name"], "p3"), (sw["name"], "p7")])
        self.assertEqual(sum(1 for *_, c in hops if c), 3)
        back = p.trace(sw["id"], "p7")
        self.assertEqual(back[-1][0]["id"], srv["id"])
        self.assertEqual(through_port(pp1, "p3"), "r3")
        self.assertIsNone(through_port(srv, "n1"))

    def test_trace_survives_loops_and_open_ends(self):
        p, srv, sw, pp1, pp2 = self.build()
        p.connect(pp1["id"], "p5", pp2["id"], "p5")
        p.connect(pp1["id"], "r5", pp2["id"], "r5")          # Ring über zwei Panels
        hops = p.trace(pp1["id"], "p5")
        self.assertLess(len(hops), 10)
        self.assertEqual(len(p.trace(sw["id"], "p9")), 1)      # freier Port: nur er selbst

    def test_cable_medium_follows_transceivers_through_panels(self):
        p, srv, sw, pp1, pp2 = self.build()
        mid = next(c for c in p.cables if c["a"][0] == pp1["id"] and c["b"][0] == pp2["id"])
        self.assertEqual(p.cable_medium(mid), p.medium(mid["net"]))          # ohne SFP: aus dem Netz
        p.port(srv, "n1")["sfp"] = "SFP28 25G LR (SMF, 1310 nm)"
        for c in p.cables:                                                    # alle Teilstücke: Singlemode
            self.assertIn("OS2", p.cable_medium(c))
        p.port(srv, "n1")["sfp"] = "SFP28 25G SR (MMF, 850 nm)"
        self.assertIn("OM4", p.cable_medium(mid))
        p.port(srv, "n1")["sfp"] = "SFP28 25G DAC"
        self.assertIn("DAC", p.cable_medium(mid))

    def test_port_fields_saved_and_in_participants(self):
        p, srv, sw, pp1, pp2 = self.build()
        port = p.port(srv, "n1")
        port.update(sfp="SFP28 25G SR (MMF, 850 nm)", ip="10.30.0.5/22", mac="00:11:22:33:44:55", vlan="100")
        q = Plan.from_dict(json.loads(json.dumps(p.to_dict())))
        self.assertEqual(q.port(q.dev(srv["id"]), "n1")["mac"], "00:11:22:33:44:55")
        parts = {(r["dev"]["name"], r["port"]["id"]): r for r in q.participants()}
        row = parts[(srv["name"], "n1")]
        self.assertEqual(row["peer"], f"{sw['name']} · Port 7")   # Ende der Strecke, nicht das Patchpanel
        self.assertFalse(any(r["dev"]["kind"] == "patch" for r in q.participants()))
        self.assertIn(("Optiken", "SFP28 25G SR (MMF, 850 nm)", "", 1), q.bom())

    def test_compatibility(self):
        self.assertTrue(compatible("bmc", "mgmt"))
        self.assertTrue(compatible("fc", "fc"))
        self.assertFalse(compatible("fc", "data"))
        self.assertFalse(compatible("sas", "patch"))
        self.assertTrue(compatible("data", "patch"))


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
        self.assertTrue(cells["SN1"][1].startswith("c04-u"))
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
        self.assertEqual(row.childCount(), 8)
        dev_item = row.child(0).child(0)
        w.planner_tree_clicked(dev_item)
        did = dev_item.data(0, 0x0100).partition(":")[2]
        self.assertEqual(w.pl_canvas.sel, ("dev", did))
        self.assertIn("Rack C04", w.pl_crumb.text())
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

    def test_floor_view_and_themes(self):
        from PyQt6.QtCore import Qt
        from PyQt6.QtTest import QTest
        from bmc_steward import planner_tab
        w = self.w
        self.fill_list()
        w.planner_set_plan(demo_plan(w.planner_inventory()), dirty=False)
        w.planner_set_view("floor")
        fc = w.pl_floor
        fc.resize(fc.minimumSize())
        rack = w.plan.racks[0]
        start = fc.rack_rect(rack).center().toPoint()
        QTest.mouseClick(fc, Qt.MouseButton.LeftButton, pos=start)
        self.assertEqual(w.pl_canvas.sel, ("rack", rack["id"]))
        self.assertEqual(w.pl_r_coord.text(), "C04")
        # Rack um drei Platten nach rechts auf freie Fläche ziehen
        end = start + (fc.tile_rect(10, 3).center() - fc.tile_rect(2, 3).center()).toPoint()
        QTest.mousePress(fc, Qt.MouseButton.LeftButton, pos=start)
        QTest.mouseMove(fc, start + (end - start) / 4)
        QTest.mouseMove(fc, end)
        QTest.mouseRelease(fc, Qt.MouseButton.LeftButton, pos=end)
        self.assertEqual(w.plan.coord(rack), "K04")
        # Stellplatz per Eingabe; belegt -> bleibt
        w.pl_r_coord.setText("D04")
        w.planner_rack_place()
        self.assertEqual(w.plan.coord(rack), "K04")
        w.pl_r_coord.setText("aa10")
        w.planner_rack_place()
        self.assertEqual(w.plan.coord(rack), "K04")             # AA liegt außerhalb von 20 Spalten
        w.pl_grid_cols.setValue(30)
        w.pl_r_coord.setText("aa10")
        w.planner_rack_place()
        self.assertEqual(w.plan.coord(rack), "AA10")
        # Doppelklick öffnet das Rack in der Rack-Ansicht
        QTest.mouseDClick(fc, Qt.MouseButton.LeftButton, pos=fc.rack_rect(rack).center().toPoint())
        self.assertEqual(w.planner_current_view(), "front")
        # neues Rack auf gewählter Platte
        w.planner_set_view("floor")
        QTest.mouseClick(fc, Qt.MouseButton.LeftButton, pos=fc.tile_rect(12, 9).center().toPoint())
        w.planner_add_rack()
        self.assertEqual(w.plan.coord(w.plan.racks[-1]), "M10")
        # Design: folgt der Wahl des Programms (links unten), Zeichenflächen wechseln mit
        from bmc_steward import theme
        for choice in ("light", "dark"):
            w.design.setCurrentIndex(w.design.findData(choice))
            w.design_changed()
            self.assertEqual(w.planner_theme, choice)
            self.assertEqual(planner_tab.BG, planner_tab.THEMES[choice]["BG"])
            fc.grab()
            w.pl_canvas.grab()
        w.design.setCurrentIndex(w.design.findData("auto"))
        w.design_changed()
        self.assertEqual(w.planner_theme, theme.system_scheme())
        w.plan_dirty = False

    def test_zoom_menus_undo_and_catalog(self):
        from PyQt6.QtCore import QPoint, QPointF, Qt
        from PyQt6.QtTest import QTest
        from PyQt6.QtGui import QWheelEvent
        from PyQt6.QtWidgets import QApplication
        w = self.w
        self.fill_list()
        w.planner_set_plan(netapp_demo_plan(w.planner_inventory()), dirty=False)
        # Zoom mit dem Mausrad: Modellkoordinaten bleiben gleich, Klicks treffen weiterhin
        w.planner_set_view("floor")
        fc = w.pl_floor
        fc.resize(fc.minimumSize())
        ev = QWheelEvent(QPointF(100, 100), QPointF(100, 100), QPoint(0, 0), QPoint(0, 240),
                         Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        QApplication.sendEvent(fc, ev)
        self.assertAlmostEqual(fc.zoom, 1.15 ** 2, places=3)
        self.assertEqual(w.pl_zoom_label.text(), f"{round(fc.zoom * 100)} %")
        fc.resize(fc.minimumSize())
        rack = w.plan.racks[1]
        QTest.mouseClick(fc, Qt.MouseButton.LeftButton, pos=(fc.rack_rect(rack).center() * fc.zoom).toPoint())
        self.assertEqual(w.pl_canvas.sel, ("rack", rack["id"]))
        w.planner_zoom(None)
        self.assertEqual(fc.zoom, 1.0)
        w.planner_set_view("front")
        w.planner_zoom(1.15)
        self.assertGreater(w.pl_canvas.zoom, 1.0)
        self.assertEqual(fc.zoom, 1.0)                          # jede Ansicht hat ihren eigenen Zoom
        w.pl_canvas.set_zoom(1.0)
        # Kontextmenüs
        texts = lambda m: [a.text() for a in m.actions()]
        m = w.planner_context_menu("tile", (10, 10))
        self.assertIn("Rack hier aufstellen", texts(m))
        m = w.planner_context_menu("rack", rack["id"])
        self.assertIn("Rack löschen …", texts(m))
        self.assertIn("Vorlage einsetzen", texts(m))
        aff = w.plan.placed("storage")[0]
        m = w.planner_context_menu("dev", aff["id"])
        self.assertTrue(any(t.startswith("Alle Kabel abziehen") for t in texts(m)))
        m = w.planner_context_menu("port", (aff["id"], "a_0a"))
        self.assertTrue(any(t.startswith("Gegenstelle zeigen") for t in texts(m)))
        empty = w.plan.racks[2]
        m = w.planner_context_menu("slot", (empty["id"], 20))
        sub = next(a.menu() for a in m.actions() if a.text() == "Hier einbauen")
        netapp = next(a.menu() for a in sub.actions() if a.text() == "NetApp-Speicher")
        next(a for a in netapp.actions() if a.text().startswith("NetApp DS224C")).trigger()
        shelf = next(d for d in w.plan.placed("shelf") if d["rack"] == empty["id"])
        self.assertEqual((shelf["pos"], shelf["u"]), (19, 2))   # oben bündig an HE 20
        # Rückgängig / Wiederholen
        w.planner_undo()
        self.assertFalse(any(d["rack"] == empty["id"] for d in w.plan.placed("shelf")))
        w.planner_redo()
        self.assertTrue(any(d["rack"] == empty["id"] for d in w.plan.placed("shelf")))
        before = len(w.plan.cables)
        w.planner_unplug(w.plan.placed("storage")[0]["id"])
        self.assertLess(len(w.plan.cables), before)
        w.planner_undo()
        self.assertEqual(len(w.plan.cables), before)
        # Rack mit Inhalt löschen (Rückfrage bestätigt) und zurückholen
        from unittest import mock
        from PyQt6.QtWidgets import QMessageBox
        racks = len(w.plan.racks)
        with mock.patch.object(QMessageBox, "exec", lambda box: None), \
                mock.patch.object(QMessageBox, "clickedButton",
                                  lambda box: next(b for b in box.buttons() if b.text() == "Löschen")):
            w.planner_delete_rack(rack["id"])
        self.assertEqual(len(w.plan.racks), racks - 1)
        w.planner_undo()
        self.assertEqual(len(w.plan.racks), racks)
        # Katalog: Suche und Racks per Doppelklick
        w.pl_lib_filter.setText("atto")
        shown = [w.pl_lib.item(i).text() for i in range(w.pl_lib.count()) if not w.pl_lib.item(i).isHidden()]
        self.assertTrue(any("ATTO FibreBridge" in t for t in shown))
        self.assertFalse(any("Leaf" in t for t in shown))
        w.pl_lib_filter.clear()
        item = next(w.pl_lib.item(i) for i in range(w.pl_lib.count())
                    if w.pl_lib.item(i).data(0x0100) == "rack:47")
        w.planner_lib_double(item)
        self.assertEqual(w.plan.racks[-1]["h"], 47)
        # Rack per Drag & Drop aus dem Katalog auf eine Bodenplatte
        w.pl_floor.rackDropped.emit(24, 12, 9)
        self.assertEqual((w.plan.racks[-1]["h"], w.plan.coord(w.plan.racks[-1])), (24, "M10"))
        # Tooltip an einem Port in der Rückansicht
        w.planner_set_view("rear")
        c = w.pl_canvas.port_center(aff["id"], "a_0a")
        self.assertIn("A 0a", w.pl_canvas.tip_at(c, (aff["id"], "a_0a")))
        w.plan_dirty = False

    def test_port_panel_connect_dialog_trace_and_participants(self):
        from unittest import mock
        from bmc_steward import planner_tab
        w = self.w
        self.fill_list()
        p = Plan()
        a, b = p.add_rack("A", 42), p.add_rack("B", 42, at=(10, 3))
        w.planner_set_plan(p, dirty=False)
        srv, sw = w.plan.new_device("server1"), w.plan.new_device("leaf")
        w.plan.place(srv, a["id"], 10)
        w.plan.place(sw, b["id"], 40)
        w.planner_refresh()
        # Port auswählen und Angaben pflegen
        w.planner_select_port(srv["id"], "n1")
        self.assertEqual(w.pl_canvas.sel, ("port", (srv["id"], "n1")))
        self.assertEqual(w.pl_props.currentIndex(), 4)
        w.pl_p_mac.setText("00-11-22-33-44-AA")
        w.planner_port_edit("mac", w.pl_p_mac.text())
        self.assertEqual(w.plan.port(srv, "n1")["mac"], "00:11:22:33:44:aa")
        w.planner_port_edit("ip", "kein-ip")
        self.assertNotIn("ip", w.plan.port(srv, "n1"))          # ungültig: nichts übernommen
        w.planner_port_edit("sfp", "SFP28 25G SR (MMF, 850 nm)")
        w.planner_undo()
        self.assertNotIn("sfp", w.plan.port(w.plan.dev(srv["id"]), "n1"))
        w.planner_redo()
        # Verbinden über die Rackgrenze per Dialog
        dlg = planner_tab.ConnectDialog(w, w.plan, srv["id"], "n1")
        names = [dlg.devs.item(i).text() for i in range(dlg.devs.count())]
        self.assertTrue(any(sw["name"] in n for n in names))
        target = next(i for i in range(dlg.devs.count()) if sw["name"] in dlg.devs.item(i).text())
        dlg.devs.setCurrentRow(target)
        self.assertTrue(all(w.plan.port(sw, dlg.ports.item(i).data(0x0100))["k"] in ("data", "up")
                            for i in range(dlg.ports.count())))
        with mock.patch.object(planner_tab.ConnectDialog, "exec",
                               lambda d: (d.devs.setCurrentRow(target), d.accept_choice(), 1)[2]):
            w.planner_connect_dialog(srv["id"], "n1")
        c = w.plan.cable_at(srv["id"], "n1")
        self.assertIsNotNone(c)
        self.assertEqual(w.plan.dev(w.plan.other_end(c, srv["id"], "n1")[0])["id"], sw["id"])
        # Strecke: Dialog zeigt beide Enden, Kabel sind währenddessen hervorgehoben
        seen = {}

        def fake_exec(d):
            seen["text"] = d.text
            seen["hl"] = set(w.pl_canvas.highlight)
            return 1
        with mock.patch.object(planner_tab.TraceDialog, "exec", fake_exec):
            w.planner_show_trace(srv["id"], "n1")
        self.assertIn(srv["name"], seen["text"])
        self.assertIn(sw["name"], seen["text"])
        self.assertEqual(seen["hl"], {c["id"]})
        self.assertEqual(w.pl_canvas.highlight, set())
        # Teilnehmerliste mit Filter
        self.assertGreaterEqual(w.pl_parts.rowCount(), 2)
        w.pl_part_filter.setText("00:11:22")
        shown = [r for r in range(w.pl_parts.rowCount()) if not w.pl_parts.isRowHidden(r)]
        self.assertEqual(len(shown), 1)
        w.pl_part_filter.clear()
        w.pl_canvas.grab()
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
