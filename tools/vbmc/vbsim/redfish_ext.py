"""Zusätzliche Redfish-Ressourcen des virtuellen BMC: Sessions, Chassis (Thermal/Power),
Prozessoren, Speicher, Ereignisprotokolle. Alles wird aus `bmc.sim` gerechnet, damit die
Weboberflächen (die genau diese Endpunkte lesen) und das Tool dasselbe sehen."""
import time

from .state import BOOT_TARGETS, TIERS

SEV = {"OK": "OK", "Informational": "OK", "Warning": "Warning", "Critical": "Critical"}


def iso(ts):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def log_service_id(bmc):
    return {"hpe": "IEL", "dell": "Lclog", "lenovo": "StdEventLog", "smc": "Log1"}[bmc.family]


def system_extras(bmc, p):
    """Felder, die in jedes ComputerSystem-Dokument gehören."""
    sim = bmc.sim
    inv = sim.inv
    d = {
        "UUID": inv["uuid"], "BiosVersion": inv["bios_ver"], "HostName": bmc.hostname or "",
        "IndicatorLED": sim.uid_led,
        "Status": {"State": "Enabled" if sim.is_on else "Disabled", "Health": sim.health()},
        "ProcessorSummary": {"Count": inv["cpu_count"], "Model": inv["cpu_model"],
                             "Status": {"Health": "OK", "State": "Enabled" if sim.is_on else "Disabled"}},
        "MemorySummary": {"TotalSystemMemoryGiB": inv["ram_gb"],
                          "Status": {"Health": "OK", "State": "Enabled" if sim.is_on else "Disabled"}},
        "Processors": {"@odata.id": p + "/Processors"},
        "Memory": {"@odata.id": p + "/Memory"},
        "LogServices": {"@odata.id": p + "/LogServices"},
        "BootProgress": {"LastState": {"Off": "None", "PoweringOn": "None",
                                       "Post": "MemoryInitializationStarted",
                                       "OsBoot": "SystemHardwareInitializationComplete",
                                       "On": "OSRunning", "ShuttingDown": "OSRunning"}[sim.phase]},
        "Boot": {"BootSourceOverrideEnabled": sim.boot_override["enabled"],
                 "BootSourceOverrideTarget": sim.boot_override["target"],
                 "BootSourceOverrideTarget@Redfish.AllowableValues": list(BOOT_TARGETS),
                 "BootOrder": list(inv["boot_order"])},
    }
    if bmc.family == "hpe":
        d["Oem"] = {"Hpe": {"PostState": sim.post_state, "PowerOnTimeSeconds": sim.uptime_s() if sim.is_on else 0}}
    elif bmc.family == "dell":
        d["Oem"] = {"Dell": {"DellSystem": {"LastSystemInventoryTime": iso(time.time()),
                                            "BIOSReleaseDate": inv["bios_date"]}}}
    return d


def get(bmc, p, SYS, MGR):
    """Dokument zu Pfad `p` oder None, wenn dieser Pfad hier nicht behandelt wird."""
    sim = bmc.sim
    sysid, mgrid = SYS[bmc.family], MGR[bmc.family]
    sysp = f"/redfish/v1/Systems/{sysid}"
    chp = f"/redfish/v1/Chassis/{sysid}"
    mgp = f"/redfish/v1/Managers/{mgrid}"

    if p == "/redfish/v1/SessionService":
        return {"@odata.id": p, "Id": "SessionService", "ServiceEnabled": True,
                "SessionTimeout": sim.SESSION_TTL, "Sessions": {"@odata.id": p + "/Sessions"}}
    if p == "/redfish/v1/SessionService/Sessions":
        with sim.lock:
            mem = [{"@odata.id": f"{p}/{t[:8]}"} for t in sim.sessions]
        return {"@odata.id": p, "Members@odata.count": len(mem), "Members": mem}
    if p == "/redfish/v1/Chassis":
        return {"@odata.id": p, "Members@odata.count": 1, "Members": [{"@odata.id": chp}]}
    if p == chp:
        return {"@odata.id": p, "Id": sysid, "Name": "Computer System Chassis", "ChassisType": "RackMount",
                "Model": bmc.model, "SerialNumber": bmc.serial, "PowerState": sim.power_state,
                "IndicatorLED": sim.uid_led, "Status": {"State": "Enabled", "Health": sim.health()},
                "Thermal": {"@odata.id": p + "/Thermal"}, "Power": {"@odata.id": p + "/Power"},
                "Manufacturer": {"hpe": "HPE", "dell": "Dell Inc.", "lenovo": "Lenovo", "smc": "Supermicro"}[bmc.family]}
    if p == chp + "/Thermal":
        s = sim.sensors()
        on = sim.is_on
        return {"@odata.id": p, "Id": "Thermal",
                "Temperatures": [{"@odata.id": f"{p}#/Temperatures/{i}", "MemberId": str(i),
                                  "Name": n, "ReadingCelsius": v, "UpperThresholdCritical": c,
                                  "UpperThresholdFatal": f,
                                  "Status": {"State": "Enabled", "Health": "OK"}}
                                 for i, (n, v, c, f) in enumerate(s["temps"])],
                "Fans": [{"@odata.id": f"{p}#/Fans/{i}", "MemberId": str(i), "Name": n,
                          "Reading": v, "ReadingUnits": "Percent",
                          "Status": {"State": "Enabled" if on else "Disabled", "Health": "OK"}}
                         for i, (n, v) in enumerate(s["fans"])]}
    if p == chp + "/Power":
        s = sim.sensors()
        return {"@odata.id": p, "Id": "Power",
                "PowerControl": [{"MemberId": "0", "PowerConsumedWatts": s["watts"],
                                  "PowerMetrics": {"AverageConsumedWatts": s["avg_watts"],
                                                   "MaxConsumedWatts": s["peak_watts"],
                                                   "MinConsumedWatts": 0, "IntervalInMin": 20},
                                  "PowerCapacityWatts": bmc.sim.inv["psu_watts"] * 2}],
                "PowerSupplies": [{"@odata.id": f"{p}#/PowerSupplies/{x['id'] - 1}",
                                   "MemberId": str(x["id"] - 1), "Name": f"Power Supply {x['id']}",
                                   "Model": x["model"], "SerialNumber": x["serial"],
                                   "PowerCapacityWatts": x["capacity"], "LastPowerOutputWatts": x["watts"],
                                   "LineInputVoltage": x["input_v"],
                                   "Status": {"State": x["state"], "Health": x["health"]}}
                                  for x in s["psu"]]}
    if p == sysp + "/Processors":
        n = sim.inv["cpu_count"]
        return {"@odata.id": p, "Members@odata.count": n,
                "Members": [{"@odata.id": f"{p}/{i}"} for i in range(1, n + 1)]}
    if p.startswith(sysp + "/Processors/"):
        i = p.rsplit("/", 1)[1]
        if i.isdigit() and 1 <= int(i) <= sim.inv["cpu_count"]:
            return {"@odata.id": p, "Id": i, "Name": f"Processor {i}", "Socket": f"CPU {i}",
                    "Model": sim.inv["cpu_model"], "TotalCores": sim.inv["cores"],
                    "TotalThreads": sim.inv["threads"], "MaxSpeedMHz": sim.inv["mhz"] + 400,
                    "OperatingSpeedMHz": sim.inv["mhz"], "ProcessorType": "CPU",
                    "Status": {"State": "Enabled", "Health": "OK"}}
    if p == sysp + "/Memory":
        dm = sim.inv["dimms"]
        return {"@odata.id": p, "Members@odata.count": len(dm),
                "Members": [{"@odata.id": f"{p}/{i}"} for i in range(1, len(dm) + 1)]}
    if p.startswith(sysp + "/Memory/"):
        i = p.rsplit("/", 1)[1]
        dm = sim.inv["dimms"]
        if i.isdigit() and 1 <= int(i) <= len(dm):
            d = dm[int(i) - 1]
            return {"@odata.id": p, "Id": i, "Name": d["slot"], "DeviceLocator": d["slot"],
                    "CapacityMiB": d["size_gb"] * 1024, "MemoryDeviceType": d["type"],
                    "OperatingSpeedMhz": d["mhz"], "Manufacturer": d["vendor"], "PartNumber": d["part"],
                    "Status": {"State": d["state"], "Health": d["health"]}}
    if p == sysp + "/EthernetInterfaces":
        n = len(sim.inv["nics"])
        return {"@odata.id": p, "Members@odata.count": n,
                "Members": [{"@odata.id": f"{p}/{i}"} for i in range(1, n + 1)]}
    if p.startswith(sysp + "/EthernetInterfaces/"):
        i = p.rsplit("/", 1)[1]
        nics = sim.inv["nics"]
        if i.isdigit() and 1 <= int(i) <= len(nics):
            n = nics[int(i) - 1]
            return {"@odata.id": p, "Id": i, "Name": n["name"], "PermanentMACAddress": n["mac"],
                    "MACAddress": n["mac"], "SpeedMbps": 25000 if "25" in n["speed"] else 1000,
                    "LinkStatus": "LinkUp" if n["state"] == "Up" and sim.is_on else "NoLink",
                    "Status": {"State": "Enabled", "Health": "OK"}}
    if p == sysp + "/Storage":
        return {"@odata.id": p, "Members@odata.count": 1, "Members": [{"@odata.id": p + "/1"}]}
    if p == sysp + "/Storage/1":
        dr = sim.inv["drives"]
        return {"@odata.id": p, "Id": "1", "Name": "Storage Controller (Slot 1)",
                "Status": {"State": "Enabled", "Health": "OK"},
                "Drives": [{"@odata.id": f"{p}/Drives/{i}"} for i in range(1, len(dr) + 1)]}
    if p.startswith(sysp + "/Storage/1/Drives/"):
        i = p.rsplit("/", 1)[1]
        dr = sim.inv["drives"]
        if i.isdigit() and 1 <= int(i) <= len(dr):
            d = dr[int(i) - 1]
            return {"@odata.id": p, "Id": i, "Name": d["id"], "Model": d["model"],
                    "CapacityBytes": d["cap_gb"] * 10**9, "MediaType": d["media"], "Protocol": d["protocol"],
                    "SerialNumber": d["serial"], "Status": {"State": d["state"], "Health": d["health"]}}
    if p == f"{mgp}/VirtualMedia":
        ids = list(sim.vmedia)
        return {"@odata.id": p, "Members@odata.count": len(ids),
                "Members": [{"@odata.id": f"{p}/{i}"} for i in ids]}
    if p.startswith(f"{mgp}/VirtualMedia/") and p.count("/") == mgp.count("/") + 2:
        mid = p.rsplit("/", 1)[1]
        m = sim.vmedia.get(mid)
        if m is not None:
            return {"@odata.id": p, "Id": mid, "Name": f"Virtual Media {mid}", "MediaTypes": list(m["types"]),
                    "Image": m["image"] or None, "ImageName": sim.vm_name(mid) or None,
                    "Inserted": m["inserted"], "WriteProtected": m["wp"],
                    "ConnectedVia": "URI" if m["inserted"] else "NotConnected",
                    "Actions": {"#VirtualMedia.InsertMedia": {"target": p + "/Actions/VirtualMedia.InsertMedia"},
                                "#VirtualMedia.EjectMedia": {"target": p + "/Actions/VirtualMedia.EjectMedia"}}}
    if p in ("/redfish/v1/LicenseService", f"{mgp}/LicenseService"):
        return {"@odata.id": p, "Id": "LicenseService", "ServiceEnabled": True,
                "Licenses": {"@odata.id": "/redfish/v1/LicenseService/Licenses"},
                "Actions": {"#LicenseService.Install": {
                    "target": "/redfish/v1/LicenseService/Actions/LicenseService.Install"}}}
    if p == "/redfish/v1/LicenseService/Licenses":
        return {"@odata.id": p, "Members@odata.count": 1,
                "Members": [{"@odata.id": p + "/1"}]}
    if p in ("/redfish/v1/LicenseService/Licenses/1", f"{mgp}/LicenseService/1"):
        lic = sim.lic
        return {"@odata.id": p, "Id": "1", "Name": sim.lic_name,
                "LicenseType": "Production" if sim.licensed else "Built-in",
                "LicenseOrigin": "Installed" if sim.licensed else "BuiltIn",
                "Removable": sim.licensed, "LicenseString": sim.lic_masked() or None,
                "InstallDate": iso(lic["since"]) if lic["since"] else None, "ExpirationDate": None,
                "Status": {"State": "Enabled", "Health": "OK"},
                "Oem": {"Tier": sim.lic["tier"], "AvailableTiers": TIERS[bmc.family]}}
    ls = log_service_id(bmc)
    for base in (sysp, mgp):
        if p == base + "/LogServices":
            return {"@odata.id": p, "Members@odata.count": 1,
                    "Members": [{"@odata.id": f"{base}/LogServices/{ls}"}]}
        if p == f"{base}/LogServices/{ls}":
            return {"@odata.id": p, "Id": ls, "Name": "Event Log", "MaxNumberOfRecords": 512,
                    "OverWritePolicy": "WrapsWhenFull", "LogEntryType": "Multiple",
                    "Entries": {"@odata.id": p + "/Entries"},
                    "Actions": {"#LogService.ClearLog": {"target": p + "/Actions/LogService.ClearLog"}}}
        if p == f"{base}/LogServices/{ls}/Entries":
            with sim.lock:
                ev = list(sim.events)
            ev.reverse()
            return {"@odata.id": p, "Members@odata.count": len(ev),
                    "Members": [_entry(p, e) for e in ev]}
    return None


def _entry(base, e):
    return {"@odata.id": f"{base}/{e['id']}", "Id": str(e["id"]), "EntryType": "Event",
            "Severity": SEV.get(e["severity"], "OK"), "Created": iso(e["time"]),
            "Message": e["message"], "MessageId": "Event." + e["key"],
            "Oem": {"Source": e["source"], "Category": e["source"]}}
