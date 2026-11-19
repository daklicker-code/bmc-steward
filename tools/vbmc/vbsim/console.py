"""Bildschirminhalt der Remote-Konsole (Text 80x25), abhängig vom Power-/POST-Zustand des Servers.

Aus: schwarzer Bildschirm. Einschalten/POST: Herstellerbildschirm mit Fortschritt und Tastenhinweisen.
Dort lässt sich per Taste (F9/F2/F1/Entf) das Setup öffnen: eine einfache, bedienbare Liste der
BIOS-Attribute (Pfeiltasten, Enter schaltet um, Esc beendet). Danach Betriebssystemstart und Login-Prompt.
Alle Änderungen gehen in `bmc.bios` und damit auch an Redfish und das Tool."""

import time

from .state import T_OS_BOOT, time_scale

COLS, ROWS = 80, 25

SETUP_KEY = {"hpe": "F9", "dell": "F2", "lenovo": "F1", "smc": "Delete"}
SETUP_NAME = {"hpe": "System Utilities", "dell": "System Setup", "lenovo": "System Setup",
              "smc": "BIOS Setup Utility"}
BOOTMENU_KEY = {"hpe": "F11", "dell": "F11", "lenovo": "F12", "smc": "F11"}
CYCLE = {"BootMode": ["Uefi", "Bios"], "SysProfile": ["PerfOptimized", "PerfPerWattOptimizedOs",
                                                     "PerfPerWattOptimizedDapc", "Custom"],
         "SecureBoot": ["Enabled", "Disabled"]}

KERNEL_LINES = [
    "Linux version 5.14.0-427.el9.x86_64 (mockbuild@builder) (gcc 11.4.1)",
    "BIOS-provided physical RAM map:", "ACPI: RSDP 0x000000006F3FE014 000024 (v02)",
    "x86/fpu: Supporting XSAVE feature 0x001: 'x87 floating point registers'",
    "Memory: 262144000K/268435456K available", "SMP: Allowing 64 CPUs, 0 hotplug CPUs",
    "PCI: Using host bridge windows from ACPI",
    "nvme nvme0: pci function 0000:41:00.0", "nvme nvme0: 8/0/0 default/read/poll queues",
    "EXT4-fs (nvme0n1p2): mounted filesystem with ordered data mode.",
    "systemd[1]: Detected architecture x86-64.", "systemd[1]: Reached target Basic System.",
    "[  OK  ] Started NetworkManager.", "[  OK  ] Started OpenSSH server daemon.",
    "[  OK  ] Reached target Multi-User System.",
]


def _pad(lines):
    out = [l[:COLS] for l in lines][:ROWS]
    return out + [""] * (ROWS - len(out))


def _bar(pct, width=40):
    n = int(width * pct / 100)
    return "[" + "#" * n + "." * (width - n) + f"] {pct:3d}%"


def _ip(bmc):
    return (bmc.ipv4.get("Address") or bmc.host) if bmc.ipv4 else bmc.host


def _state(bmc):
    sim = bmc.sim
    c = sim.con
    if c["boot"] != sim.boot_id:                 # neuer Start: Konsole zurücksetzen
        c.update(boot=sim.boot_id, mode="setup" if sim.in_setup else "auto", sel=0)
    return c


def screen(bmc):
    """Aktueller Bildschirm: {"lines": [...], "mode": ..., "power": ..., "cursor": [row, col] | None}."""
    sim, inv, fam = bmc.sim, bmc.sim.inv, bmc.family
    c = _state(bmc)
    ph = sim.phase
    base = {"power": sim.power_state, "mode": c["mode"], "cols": COLS, "rows": ROWS, "cursor": None}
    if ph in ("Off", "PoweringOn"):
        return {**base, "lines": _pad(["", "", "", "  (no video signal)" if ph == "Off" else ""]),
                "dark": True}
    if c["mode"] == "setup":
        return {**base, "lines": _setup_screen(bmc, c), "dark": False}
    if c["mode"] == "bootmenu":
        cd = sim.cd_image_name()
        return {**base, "lines": _pad([f"  {SETUP_NAME[fam]} - One-time Boot Menu", "",
                                       "   1) Embedded NIC 1 Port 1 - PXE IPv4",
                                       "   2) RAID Controller Slot 2 - Virtual Disk 1",
                                       f"   3) UEFI: Virtual CD/DVD ({cd})" if cd
                                       else "   3) UEFI: Virtual CD/DVD (not mounted)", "",
                                       "  Press Esc to continue normal boot."]), "dark": False}
    if ph == "Post":
        pct = sim.post_progress()
        lines = _post_header(bmc) + ["",
                                    f"  Processor : {inv['cpu_count']} x {inv['cpu_model']}",
                                    f"  Memory    : {inv['ram_gb']} GB, {inv['dimms'][0]['type']}-{inv['dimms'][0]['mhz']}, "
                                    f"{len(inv['dimms'])} DIMMs populated" if pct > 30 else "  Memory    : training ...",
                                    f"  BMC       : {bmc.gen_label} {bmc.fw}   IPv4 {_ip(bmc)}",
                                    f"  Boot mode : {bmc.bios.get('BootMode', 'Uefi')}   "
                                    f"Secure Boot: {bmc.bios.get('SecureBoot', 'Disabled')}", "",
                                    f"  {sim.post_text()} ...", f"  {_bar(pct)}", "",
                                    _keyline(fam)]
        return {**base, "lines": _pad(lines), "dark": False}
    if ph == "OsBoot" and sim.boot_device == "disk":
        k = len(KERNEL_LINES)
        frac = (time.monotonic() - sim._since) / max(1e-6, T_OS_BOOT * time_scale())
        n = max(1, min(k, int(frac * k) + 1))
        return {**base, "lines": _pad([f"[{i * 0.37:9.6f}] {t}" for i, t in enumerate(KERNEL_LINES[:n])]),
                "dark": False}
    if ph in ("OsBoot", "On") and sim.boot_device == "vcd":
        cd = sim.cd_image_name() or "virtual media"
        return {**base, "dark": False, "lines": _pad([
            "", f"  Booting from virtual media: {cd}", "",
            "                 Rocky Linux 9.4", "",
            "        Install Rocky Linux 9.4", "        Test this media & install Rocky Linux 9.4",
            "        Troubleshooting  >", "", "",
            "  Press Tab for full configuration options on menu items."])}
    if ph in ("OsBoot", "On") and sim.boot_device == "pxe":
        return {**base, "dark": False, "lines": _pad([
            "", "  Network boot: Embedded NIC 1 Port 1", "",
            "  >>Start PXE over IPv4.", "  PXE-E53: No boot filename received.", "  PXE-M0F: Exiting PXE ROM.", "",
            "  No bootable device found. Reboot and select proper boot device."])}
    host = bmc.hostname or ("srv-" + bmc.serial[-6:].lower())
    lines = ["", "Rocky Linux 9.4 (Blue Onyx)", "Kernel 5.14.0-427.el9.x86_64 on an x86_64", "",
             f"{host} login: "]
    if ph == "ShuttingDown":
        lines = ["", "[  OK  ] Stopped target Multi-User System.", "[  OK  ] Stopped OpenSSH server daemon.",
                 "[  OK  ] Reached target Shutdown.", "systemd-shutdown[1]: Powering off."]
    return {**base, "lines": _pad(lines), "dark": False,
            "cursor": [4, len(lines[4])] if ph == "On" else None}


def _post_header(bmc):
    fam = bmc.family
    title = {"hpe": f"Häpe {bmc.model}", "dell": f"Böll {bmc.model}", "lenovo": f"Venolo {bmc.model}",
             "smc": f"Hypernano {bmc.model}"}[fam]
    return [f"  {title}", f"  Serial Number: {bmc.serial}      BIOS {bmc.sim.inv['bios_ver']} "
                          f"{bmc.sim.inv['bios_date']}"]


def _keyline(fam):
    s, b = SETUP_KEY[fam], BOOTMENU_KEY[fam]
    return f"  <{s}> {SETUP_NAME[fam]}   <{b}> Boot Menu"


def _setup_screen(bmc, c):
    fam = bmc.family
    keys = list(bmc.bios)
    c["sel"] = max(0, min(c["sel"], len(keys) - 1))
    lines = [f"  {SETUP_NAME[fam]}  -  {bmc.model}", "  " + "-" * 60, ""]
    for i, k in enumerate(keys):
        lines.append(f"{'>' if i == c['sel'] else ' '} {k:<28} {bmc.bios[k]}")
    lines += ["", "  Up/Down select   Enter change value   Esc exit and continue boot"]
    return _pad(lines)


def key(bmc, name):
    """Taste verarbeiten. Gibt True zurück, wenn sie etwas bewirkt hat."""
    sim, fam = bmc.sim, bmc.family
    c = _state(bmc)
    ph = sim.phase
    if c["mode"] == "setup":
        keys = list(bmc.bios)
        if name == "ArrowDown":
            c["sel"] = min(len(keys) - 1, c["sel"] + 1)
        elif name == "ArrowUp":
            c["sel"] = max(0, c["sel"] - 1)
        elif name == "Enter":
            k = keys[c["sel"]]
            v = bmc.bios[k]
            opts = CYCLE.get(k) or (["Enabled", "Disabled"] if v in ("Enabled", "Disabled") else None)
            if opts:
                bmc.bios[k] = opts[(opts.index(v) + 1) % len(opts)] if v in opts else opts[0]
                sim.bios_changed("Console")
        elif name == "Escape":
            c["mode"] = "auto"
            sim.in_setup = False                           # Setup verlassen: der Start läuft weiter
        else:
            return False
        return True
    if c["mode"] == "bootmenu":
        if name == "Escape":
            c["mode"] = "auto"
            return True
        return False
    if ph == "Post":
        want = SETUP_KEY[fam]
        alt = {"smc": ("Delete", "F2")}.get(fam, (want,))
        if name in alt:
            c["mode"] = "setup"
            sim.in_setup = True                            # der Start hält im Setup an
            return True
        if name == BOOTMENU_KEY[fam]:
            c["mode"] = "bootmenu"
            return True
    return False
