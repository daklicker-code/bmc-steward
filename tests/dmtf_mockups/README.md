# Herstellereigene DMTF-Mockups

Ein Ordner je Hersteller (`dell/`, `hpe/`, `lenovo/`, `smc/`), Baumstruktur im DMTF-Redfish-Mockup-Server-Format:
Jede Redfish-Ressource ist ein Ordner, der Ordnerpfad relativ zum Hersteller-Wurzelordner ist der Redfish-Pfad,
der Inhalt steht in `index.json` darin. Beispiel: `dell/redfish/v1/Managers/iDRAC.Embedded.1/Attributes/index.json`
beantwortet `GET/PATCH https://<gerät>/redfish/v1/Managers/iDRAC.Embedded.1/Attributes`.

Bewusst **nicht** vollständig – nur die Ressourcen, die `bmc_steward.redfish.ServicesEngine.svc_hard()` und
`RedfishClient.vendor()` anfassen (ServiceRoot, Manager, NetworkProtocol, AccountService, SessionService;
die `Attributes`-Ressource gibt es absichtlich **nur bei Dell**, weil das auf echter Hardware bei den anderen
drei Herstellern laut `luecken-und-roadmap.md` noch ungeprüft ist).

Serviert werden diese Bäume über `tests/_dmtf_mock.py` (`DmtfMockServer`), ein kleiner eigener HTTPS-Mock
(GET + PATCH, `openssl` für ein Wegwerf-Zertifikat), kein externes Programm nötig. Genutzt von
`tests/test_dmtf_mockups.py`.

Weitere Hersteller/Ressourcen: einfach einen neuen Ordner nach demselben Schema anlegen und in
`VENDORS`/`MGR` in `tests/test_dmtf_mockups.py` eintragen.
