"""vBMC: Redfish-Standardangaben für jede Antwort – @odata.type, Id, Name – und das $metadata-Dokument.

Die Simulatoren beschreiben ihre Ressourcen bewusst knapp. Damit ein Redfish-Client (und der DMTF-Validator in
der CI) sie dennoch dem richtigen Schema zuordnen kann, ergänzt annotate() beim Senden, was fehlt: den Typ nach
dem Pfad, eine Id aus dem letzten Pfadteil und einen Namen. Was eine Ressource selbst angibt, bleibt unverändert.
Herstellereigene Ressourcen (z. B. Dell-Attribute, HPE-DateTime) bleiben ohne Standardtyp.

Versionen: aktuelle Fassungen aus DSP8010 (redfish.dmtf.org/schemas/v1); neuere Versionen enthalten alle
Eigenschaften der älteren.
"""
import re

V1 = "/redfish/v1"
SEG = r"/[^/]+"

# (Pfadmuster ab /redfish/v1, Typ) – erstes passendes Muster gewinnt
TYPES = [
    (r"/?", "ServiceRoot.v1_22_0.ServiceRoot"),
    (r"/Systems", "ComputerSystemCollection.ComputerSystemCollection"),
    (r"/Systems" + SEG, "ComputerSystem.v1_29_0.ComputerSystem"),
    (r"/Systems" + SEG + r"/Processors", "ProcessorCollection.ProcessorCollection"),
    (r"/Systems" + SEG + r"/Processors" + SEG, "Processor.v1_24_0.Processor"),
    (r"/Systems" + SEG + r"/Memory", "MemoryCollection.MemoryCollection"),
    (r"/Systems" + SEG + r"/Memory" + SEG, "Memory.v1_24_0.Memory"),
    (r"/(?:Systems|Managers)" + SEG + r"/EthernetInterfaces", "EthernetInterfaceCollection.EthernetInterfaceCollection"),
    (r"/(?:Systems|Managers)" + SEG + r"/EthernetInterfaces" + SEG, "EthernetInterface.v1_12_6.EthernetInterface"),
    (r"/Systems" + SEG + r"/Storage", "StorageCollection.StorageCollection"),
    (r"/Systems" + SEG + r"/Storage" + SEG, "Storage.v1_23_0.Storage"),
    (r"/Systems" + SEG + r"/Storage" + SEG + r"/Drives" + SEG, "Drive.v1_22_0.Drive"),
    (r"/Systems" + SEG + r"/Bios(?:/Settings)?", "Bios.v1_3_0.Bios"),
    (r"/Systems" + SEG + r"/SecureBoot", "SecureBoot.v1_2_1.SecureBoot"),
    (r"/(?:Systems|Managers)" + SEG + r"/LogServices", "LogServiceCollection.LogServiceCollection"),
    (r"/(?:Systems|Managers)" + SEG + r"/LogServices" + SEG, "LogService.v1_9_0.LogService"),
    (r"/(?:Systems|Managers)" + SEG + r"/LogServices" + SEG + r"/Entries", "LogEntryCollection.LogEntryCollection"),
    (r"/(?:Systems|Managers)" + SEG + r"/LogServices" + SEG + r"/Entries" + SEG, "LogEntry.v1_22_0.LogEntry"),
    (r"/Chassis", "ChassisCollection.ChassisCollection"),
    (r"/Chassis" + SEG, "Chassis.v1_29_0.Chassis"),
    (r"/Chassis" + SEG + r"/Power", "Power.v1_7_3.Power"),
    (r"/Chassis" + SEG + r"/Thermal", "Thermal.v1_7_3.Thermal"),
    (r"/Managers", "ManagerCollection.ManagerCollection"),
    (r"/Managers" + SEG, "Manager.v1_25_0.Manager"),
    (r"/Managers" + SEG + r"/NetworkProtocol", "ManagerNetworkProtocol.v1_14_0.ManagerNetworkProtocol"),
    (r"/Managers" + SEG + r"/NetworkProtocol/HTTPS/Certificates", "CertificateCollection.CertificateCollection"),
    (r"/Managers" + SEG + r"/NetworkProtocol/HTTPS/Certificates" + SEG, "Certificate.v1_11_0.Certificate"),
    (r"/Managers" + SEG + r"/VirtualMedia", "VirtualMediaCollection.VirtualMediaCollection"),
    (r"/Managers" + SEG + r"/VirtualMedia" + SEG, "VirtualMedia.v1_7_0.VirtualMedia"),
    (r"/AccountService", "AccountService.v1_19_0.AccountService"),
    (r"/AccountService/Accounts", "ManagerAccountCollection.ManagerAccountCollection"),
    (r"/AccountService/Accounts" + SEG, "ManagerAccount.v1_14_2.ManagerAccount"),
    (r"/AccountService/Accounts" + SEG + r"/Keys", "KeyCollection.KeyCollection"),
    (r"/AccountService/Accounts" + SEG + r"/Keys" + SEG, "Key.v1_4_1.Key"),
    (r"/SessionService", "SessionService.v1_2_0.SessionService"),
    (r"/SessionService/Sessions", "SessionCollection.SessionCollection"),
    (r"/SessionService/Sessions" + SEG, "Session.v1_9_0.Session"),
    (r"/UpdateService", "UpdateService.v1_17_1.UpdateService"),
    (r"/UpdateService/FirmwareInventory", "SoftwareInventoryCollection.SoftwareInventoryCollection"),
    (r"/UpdateService/FirmwareInventory" + SEG, "SoftwareInventory.v1_15_0.SoftwareInventory"),
    (r"/CertificateService", "CertificateService.v1_2_2.CertificateService"),
    (r"/CertificateService/CertificateLocations", "CertificateLocations.v1_0_4.CertificateLocations"),
    (r"/LicenseService", "LicenseService.v1_1_2.LicenseService"),
    (r"/LicenseService/Licenses", "LicenseCollection.LicenseCollection"),
    (r"/LicenseService/Licenses" + SEG, "License.v1_1_4.License"),
    (r"/EventService", "EventService.v1_12_0.EventService"),
]
_COMPILED = [(re.compile(re.escape(V1) + pat + r"$"), t) for pat, t in TYPES]


def type_for(path):
    """Redfish-Typ zu einem Pfad oder None (herstellereigen oder unbekannt)."""
    path = path.split("?", 1)[0]
    for rx, t in _COMPILED:
        if rx.match(path):
            return t
    return None


def _name(t):
    base = t.rsplit(".", 1)[1]
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", base)


def annotate(doc):
    """Ergänzt @odata.type, Id und Name in einem Antwortdokument und in eingebetteten Mitgliedern (z. B.
    Protokolleinträge, die direkt in der Liste stehen). Ändert das Dokument an Ort und Stelle."""
    if not isinstance(doc, dict):
        return doc
    path = doc.get("@odata.id")
    if isinstance(path, str) and path.startswith(V1):
        t = type_for(path)
        if t and "@odata.type" not in doc:
            doc["@odata.type"] = "#" + t
        if t:
            doc.setdefault("Name", _name(t))
            if "Collection." not in t:
                last = path.rstrip("/").rsplit("/", 1)[1]
                doc.setdefault("Id", "RootService" if t.startswith("ServiceRoot") else last)
    for m in doc.get("Members") or []:
        if isinstance(m, dict) and len(m) > 1:          # eingebettetes Mitglied, nicht nur ein Verweis
            annotate(m)
    return doc


def metadata_xml():
    """$metadata (OData-CSDL): verweist auf die DMTF-Schemata aller Typen, die die Simulatoren ausliefern."""
    refs = {}
    for _, t in TYPES:
        ns, ver = t.split(".")[0], t.rsplit(".", 1)[0]
        refs.setdefault(ns, set()).update({ns, ver})
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           '<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0">']
    for ns in sorted(refs):
        out.append(f'  <edmx:Reference Uri="http://redfish.dmtf.org/schemas/v1/{ns}_v1.xml">')
        out += [f'    <edmx:Include Namespace="{inc}"/>' for inc in sorted(refs[ns])]
        out.append('  </edmx:Reference>')
    out += ['  <edmx:DataServices>',
            '    <Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="Service">',
            '      <EntityContainer Name="Service" Extends="ServiceRoot.v1_22_0.ServiceContainer"/>',
            '    </Schema>',
            '  </edmx:DataServices>',
            '</edmx:Edmx>']
    return "\n".join(out) + "\n"
