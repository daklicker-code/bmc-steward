"""BMC-Steward: Sprache — T/TR/HELP, Sprachwahl, Profile-/Pfad-Konstanten."""
from pathlib import Path
from PyQt6.QtCore import (QT_VERSION_STR, QBuffer, QByteArray, QEvent, QIODevice, QLocale, QObject, QProcess, QRect, QSocketNotifier, QThread, QTimer, QUrl, Qt, pyqtSignal)
import json
import os
import sys


LANGS = [("de", "Deutsch"), ("en", "English"), ("fr", "Français"), ("gsw", "Schwiizerdütsch")]


LANG = "de"




def T(text):
    """Übersetzt einen deutschen Text der Oberfläche in die gewählte Sprache (TR steht am Dateiende)."""
    if LANG == "de":
        return text
    hit = TR.get(text)
    return hit[("en", "fr", "gsw").index(LANG)] if hit else text




# Ordner des Tools (bei einer mit PyInstaller gebauten .exe der Ordner der .exe)
# Ordner des Tools: bei PyInstaller der .exe-Ordner, aus dem Quellcode der Projektordner
# (eine Ebene über dem Paket bmc_steward/), damit Profile/CSV wie bisher neben bmc-steward.py liegen.
HERE = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
        else Path(__file__).resolve().parent.parent)


LIST = HERE / "servers.csv"          # Vorschlag für „Liste speichern“; von selbst wird nichts gespeichert


PROFILES = HERE / "bmc-profile.json"   # Profile: Einstellungen ohne Passwörter und ohne Serverliste


BIOS_PROFILES = HERE / "bmc-bios-profile.json"   # UEFI-Vorlagen: eigener Bereich, getrennt von PROFILES


# Arbeitsdaten des Tools (Zertifikats-Fingerabdrücke, Sicherungen, Revisionsprotokoll). Vorgabe: neben dem
# Tool; mit BMC_STEWARD_DATA lässt sich ein anderer Ordner wählen (z. B. ein verschlüsseltes Laufwerk).
DATA = Path(os.environ.get("BMC_STEWARD_DATA") or HERE)


def private_dir(path):
    """Legt einen Ordner an, der nur für den eigenen Benutzer lesbar ist, und gibt ihn zurück."""
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    if not sys.platform.startswith("win"):
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return path


def write_private(path, text):
    """Schreibt eine Datei atomar mit Rechten 0600 (nur der eigene Benutzer)."""
    path = Path(path)
    private_dir(path.parent)
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)


def build_texts():
    """Texte, die schon vor dem Fenster gebraucht werden; nach einem Sprachwechsel neu aufrufen."""
    global NO_PROFILE, VENDORS, USER_HINT, PASS_HINT, VERSION_HINT, COL_TIPS, COLS
    NO_PROFILE = T("(kein Profil)")
    VENDORS = [(T("Automatisch erkennen"), ""), (T("Dell iDRAC"), "dell"), ("HPE iLO 5/6", "hpe"), ("HPE iLO 3/4", "hpe_old"), ("Supermicro", "smc"),
               (T("Lenovo XCC"), "lenovo")]
    USER_HINT = {"": T("automatisch je Hersteller"), "dell": "root", "hpe": "Administrator", "hpe_old": "Administrator",
                 "smc": "ADMIN", "lenovo": "USERID"}
    PASS_HINT = {"": T("Werkspasswort"), "dell": T("ab Werk calvin oder laut Service-Tag-Auszug"),
                 "hpe": T("steht auf dem Anhänger am Gerät"), "hpe_old": T("steht auf dem Anhänger am Gerät"),
                 "smc": T("steht auf dem Aufkleber am Gerät"),
                 "lenovo": T("steht auf dem Anhänger am Gerät")}
    VERSION_HINT = {
        "": T("Ausgelegt auf: Dell iDRAC9, iDRAC7/8 · HPE iLO 3 bis 6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. "
            "Andere Generationen zuerst an einem einzelnen Gerät testen."),
        "dell": T("iDRAC9 (14. bis 16. Generation, z. B. R6625) und iDRAC7/8 (12./13. Generation) ab Firmware "
                "2.50.50.50. iDRAC10 ist nicht geprüft, iDRAC6 wird nicht unterstützt."),
        "hpe_old": T("iLO 3 (G7) und iLO 4 (Gen8, Gen9), angesprochen über die XML-Schnittstelle RIBCL. "
                   "iLO 2 und älter werden nicht unterstützt."),
        "hpe": T("iLO 5 und 6 (Gen10, Gen10 Plus, Gen11, z. B. DL345/DL365). iLO 7 (Gen12) ist nicht geprüft."),
        "smc": T("Aktuelle X12/X13-Boards mit Redfish 1.x, auch baugleiche Nutanix-NX-Nodes. Bei älteren Boards "
               "können einzelne Redfish-Funktionen eine Lizenz erfordern."),
        "lenovo": T("ThinkSystem mit XCC2/XCC3 (z. B. SR6xx, SR8xx). Ältere Systeme mit XCC1 sind nicht geprüft."),
    }
    COL_TIPS = [
        T("Adresse, unter der der BMC jetzt erreichbar ist, z. B. die per DHCP vergebene."),
        T("Aktuelles Passwort dieses Geräts. Nur nötig, wenn es vom Werkspasswort oben abweicht\n"
        "(bei HPE, Supermicro und Lenovo ist es pro Gerät verschieden)."),
        T("Feste Adresse, die der BMC bekommen soll."),
        T("Subnetzmaske zur neuen Adresse, z. B. 255.255.255.0. Eingabe als /24 wird umgewandelt."),
        T("Gateway im Netz der neuen Adresse."),
        T("Name, den der BMC bekommt (iDRAC-, iLO- bzw. BMC-Hostname), nicht der Name des Betriebssystems."),
        T("Seriennummer bzw. Service-Tag des Geräts, nur zur eigenen Übersicht; wird nirgends an den BMC "
        "gesendet. Lässt sich auch per Handy-Scanner einscannen."),
    ]
    COLS = [T("Aktuelle IP"), T("Passwort"), T("Neue IP"), T("Subnetzmaske"), T("Gateway"), T("Hostname"),
            T("Seriennummer")]




def load_lang():
    """Zuletzt gewählte Sprache, sonst die des Systems, sonst Englisch."""
    try:
        code = json.loads(PROFILES.read_text(encoding="utf-8")).get("lang")
    except (OSError, ValueError, AttributeError):
        code = None
    if code not in dict(LANGS):
        code = QLocale.system().name().split("_")[0].lower()
    return code if code in dict(LANGS) else "en"




def set_language(code):
    global LANG
    LANG = code
    build_texts()




# ======================================================================
# Übersetzungen: deutscher Text -> (English, Français, Schwiizerdütsch)
# ======================================================================
TR = {
"Trockenlauf ist voreingestellt: Es wird erst etwas geändert, wenn „Scharf starten“ gedrückt wird.": ["Dry run is the default: nothing is changed until “Start live run” is pressed.", "Le test à blanc est le réglage par défaut : rien n'est modifié tant que « Lancer l'exécution réelle » n'est pas pressé.", "De Trockelauf isch voreigstellt: Es wird erst öppis gänderet, wänn „Scharf starte“ drückt wird."],
"BMC-Steward beendet": ["BMC-Steward closed", "BMC-Steward fermé", "BMC-Steward beändet"],
"Fenster wird geschlossen": ["Window is closing", "La fenêtre se ferme", "S Fänschter wird zuegmacht"],
"BMC-Steward {0} gestartet": ["BMC-Steward {0} started", "BMC-Steward {0} démarré", "BMC-Steward {0} gstartet"],
"Sprache: {0}": ["Language: {0}", "Langue : {0}", "Sproch: {0}"],
"System: {0} · Python {1} · Qt {2}": ["System: {0} · Python {1} · Qt {2}", "Système : {0} · Python {1} · Qt {2}", "System: {0} · Python {1} · Qt {2}"],
"Datenordner: {0}": ["Data folder: {0}", "Dossier de données : {0}", "Datenordner: {0}"],
"Eigener Rechner: {0}": ["This computer: {0}", "Cet ordinateur : {0}", "Dä Rächner sälber: {0}"],
"echte Hardware": ["real hardware", "matériel réel", "ächti Hardware"],
"Fenster aufgebaut ({0} × {1}), Sprache {2}": ["Window built ({0} × {1}), language {2}", "Fenêtre construite ({0} × {1}), langue {2}", "Fänschter ufbout ({0} × {1}), Sproch {2}"],
"Seite: {0}": ["Page: {0}", "Page : {0}", "Site: {0}"],
"— Testlabor wird gestartet —": ["— Starting the test lab —", "— Démarrage du labo de test —", "— S Testlabor wird gstartet —"],
"Info": ["Info", "Info", "Info"],
"Debug": ["Debug", "Debug", "Debug"],
"Stufe des Protokolls. Info zeigt Aktionen und Ergebnisse, Debug zusätzlich technische Einzelheiten.": ["Log level. Info shows actions and results, Debug adds technical details.", "Niveau du journal. Info affiche les actions et les résultats, Debug ajoute des détails techniques.", "Stufe vom Protokoll. Info zeigt Aktione und Ergäbnis, Debug zuesätzlich technischi Einzelheite."],
"Protokollstufe: {0}": ["Log level: {0}", "Niveau du journal : {0}", "Protokollstufe: {0}"],
"Knopf: {0}": ["Button: {0}", "Bouton : {0}", "Chnopf: {0}"],
"Zahl geändert: {0}": ["Number changed: {0}", "Nombre modifié : {0}", "Zahl gänderet: {0}"],
"Reiter: {0}": ["Tab: {0}", "Onglet : {0}", "Register: {0}"],
"Haken „{0}“: {1}": ["Checkbox “{0}”: {1}", "Case « {0} » : {1}", "Hägli „{0}“: {1}"],
"an": ["on", "activée", "a"],
"aus": ["off", "désactivée", "us"],
"Auswahl: {0}": ["Selection: {0}", "Sélection : {0}", "Uswahl: {0}"],
"Dieser Rechner selbst hat eine Adresse vom DHCP-Server erhalten.\nEr erscheint nicht in der Geräteliste unten.": ["This computer itself received an address from the DHCP server.\nIt does not appear in the device list below.", "Cet ordinateur a lui-même reçu une adresse du serveur DHCP.\nIl n'apparaît pas dans la liste des appareils ci-dessous.", "Dä Rächner sälber het vom DHCP-Server e Adrässe übercho.\nEr erschiint nöd i de Gerätelisch unde."],
"Dieser Rechner: {0}  –  {1}": ["This computer: {0}  –  {1}", "Cet ordinateur : {0}  –  {1}", "Dä Rächner: {0}  –  {1}"],
"IP setzen fehlgeschlagen: {0}": ["Setting the IP failed: {0}", "Échec de la définition de l'IP : {0}", "IP setze isch fählgschlage: {0}"],
"Interface {0}: feste IP {1}/{2} gesetzt (war APIPA)": ["Interface {0}: static IP {1}/{2} set (was APIPA)", "Interface {0} : IP fixe {1}/{2} définie (était APIPA)", "Interface {0}: fescht IP {1}/{2} gsetzt (isch APIPA gsi)"],
"Nach der Kontrolle meldet sich das Tool mit dem neuen Administrator unter der neuen Adresse an und\nsperrt das Werkskonto (root, Administrator, ADMIN, USERID), dessen Passwort auf dem Aufkleber steht.\nNur wenn der neue Administrator dort nachweislich funktioniert und Administratorrechte hat.\nKann ein BMC Konten nicht abschalten, bekommt das Werkskonto ein Zufallspasswort, das nirgends\ngespeichert wird; mit dem neuen Administrator lässt es sich jederzeit zurücksetzen.": [
"After the check, the tool logs in with the new administrator at the new address and\nlocks the factory account (root, Administrator, ADMIN, USERID) whose password is on the sticker.\nOnly if the new administrator demonstrably works there and has administrator rights.\nIf a BMC cannot disable accounts, the factory account gets a random password that is\nstored nowhere; it can be reset with the new administrator at any time.",
"Après le contrôle, l'outil se connecte avec le nouvel administrateur à la nouvelle adresse et\nverrouille le compte d'usine (root, Administrator, ADMIN, USERID) dont le mot de passe figure sur l'étiquette.\nSeulement si le nouvel administrateur y fonctionne de façon prouvée et a les droits d'administrateur.\nSi un BMC ne peut pas désactiver de comptes, le compte d'usine reçoit un mot de passe aléatoire\nqui n'est enregistré nulle part ; il peut être réinitialisé à tout moment avec le nouvel administrateur.",
"Nach de Kontrolle mäldet sich s Tool mit em neue Administrator under de neue Adrässe aa und\nsperrt s Werkskonto (root, Administrator, ADMIN, USERID), wo sis Passwort uf em Chläber staht.\nNume wänn de neu Administrator det nachwiislich funktioniert und Adminrächt hät.\nChan en BMC Konte nöd abschalte, überchunnt s Werkskonto es Zuefallspasswort, wo nienet\ngspicheret wird; mit em neue Administrator lat sich das jederzit zruggsetze."
],
"Nach erfolgreicher Kontrolle wird auf jedem Server das Werkskonto gesperrt. Danach gilt nur noch der neue Administrator – Passwort vorher sicher ablegen.": [
"After a successful check, the factory account is locked on every server. From then on only the new administrator works – store its password safely beforehand.",
"Après un contrôle réussi, le compte d'usine est verrouillé sur chaque serveur. Ensuite, seul le nouvel administrateur fonctionne – conservez d'abord son mot de passe en lieu sûr.",
"Nach erfolgriicher Kontrolle wird uf jedem Server s Werkskonto gsperrt. Dänn gilt nume no de neu Administrator – Passwort vorher sicher ablege."
],
"Werkskonto danach sperren (empfohlen)": [
"Lock factory account afterwards (recommended)",
"Verrouiller ensuite le compte d'usine (recommandé)",
"Werkskonto dänn sperre (empfohle)"
],
"  FEHLER: Neuer Administrator {0} ist nicht als Administrator eingetragen; Werkskonto bleibt zur Sicherheit aktiv.": [
"  ERROR: new administrator {0} is not registered as administrator; the factory account stays active for safety.",
"  ERREUR : le nouvel administrateur {0} n'est pas enregistré comme administrateur ; le compte d'usine reste actif par sécurité.",
"  FEHLER: Neue Administrator {0} isch nöd als Administrator iitreit; s Werkskonto blibt zur Sicherheit aktiv."
],
"  Trockenlauf: würde nach erfolgreicher Kontrolle das Werkskonto {0} sperren (angemeldet als {1}).": [
"  Dry run: would lock the factory account {0} after a successful check (logged in as {1}).",
"  Simulation : verrouillerait le compte d'usine {0} après un contrôle réussi (connecté en tant que {1}).",
"  Trochelauf: würd nach erfolgriicher Kontrolle s Werkskonto {0} sperre (aagmäldet als {1})."
],
"  WERKSKONTO AKTIV   {0}: bitte von Hand sperren oder Passwort ändern.": [
"  FACTORY ACCOUNT ACTIVE   {0}: please lock it by hand or change its password.",
"  COMPTE D'USINE ACTIF   {0} : veuillez le verrouiller à la main ou changer son mot de passe.",
"  WERKSKONTO AKTIV   {0}: bitte vo Hand sperre oder Passwort ändere."
],
"  Werkskonto {0}: Passwort auf Zufallswert gesetzt (iLO 3/4 kann Konten nicht abschalten; Wert wird nicht gespeichert).": [
"  Factory account {0}: password set to a random value (iLO 3/4 cannot disable accounts; the value is not stored).",
"  Compte d'usine {0} : mot de passe remplacé par une valeur aléatoire (iLO 3/4 ne peut pas désactiver de comptes ; la valeur n'est pas enregistrée).",
"  Werkskonto {0}: Passwort uf Zuefallswärt gsetzt (iLO 3/4 chan Konte nöd abschalte; Wärt wird nöd gspicheret)."
],
"  Werkskonto {0}: gesperrt.": [
"  Factory account {0}: locked.",
"  Compte d'usine {0} : verrouillé.",
"  Werkskonto {0}: gsperrt."
],
"  Werkskonto {0}: lässt sich hier nicht abschalten, Passwort auf Zufallswert gesetzt (wird nicht gespeichert; mit dem neuen Administrator jederzeit zurücksetzbar).": [
"  Factory account {0}: cannot be disabled here, password set to a random value (not stored; can be reset with the new administrator at any time).",
"  Compte d'usine {0} : ne peut pas être désactivé ici, mot de passe remplacé par une valeur aléatoire (non enregistrée ; réinitialisable à tout moment avec le nouvel administrateur).",
"  Werkskonto {0}: lat sich da nöd abschalte, Passwort uf Zuefallswärt gsetzt (wird nöd gspicheret; mit em neue Administrator jederzit zruggsetzbar)."
],
"  Werkskonto {0}: nicht (mehr) vorhanden.": [
"  Factory account {0}: not (or no longer) present.",
"  Compte d'usine {0} : absent (ou plus présent).",
"  Werkskonto {0}: nöd (meh) vorhande."
],
"  Werkskonto {0}: schon gesperrt.": [
"  Factory account {0}: already locked.",
"  Compte d'usine {0} : déjà verrouillé.",
"  Werkskonto {0}: scho gsperrt."
],
"  Werkskonto: Anmeldung lief nicht über ein Werkskonto ({0}), es wird nichts gesperrt.": [
"  Factory account: login did not use a factory account ({0}), nothing is locked.",
"  Compte d'usine : la connexion n'a pas utilisé de compte d'usine ({0}), rien n'est verrouillé.",
"  Werkskonto: D Aamäldig isch nöd über es Werkskonto gloffe ({0}), es wird nüt gsperrt."
],
"  Werkskonto: neuer Administrator ist dasselbe Konto, wird nicht gesperrt.": [
"  Factory account: the new administrator is the same account, not locked.",
"  Compte d'usine : le nouvel administrateur est le même compte, non verrouillé.",
"  Werkskonto: De neu Administrator isch s gliiche Konto, wird nöd gsperrt."
],
"Konten lesen": [
"Read accounts",
"Lire les comptes",
"Konte läse"
],
"Werkskonto {0} sperren": [
"Lock factory account {0}",
"Verrouiller le compte d'usine {0}",
"Werkskonto {0} sperre"
],
"Für die Textkonsole fehlt „pyte“: pip install pyte (unter Arch/CachyOS: sudo pacman -S python-pyte)": [
"The text console needs “pyte”: pip install pyte (on Arch/CachyOS: sudo pacman -S python-pyte)",
"La console texte a besoin de « pyte » : pip install pyte (sous Arch/CachyOS : sudo pacman -S python-pyte)",
"Für d Textkonsole fählt „pyte“: pip install pyte (under Arch/CachyOS: sudo pacman -S python-pyte)"
],
"Es fehlt: {0}. Installation: {1}": [
"Missing: {0}. Install: {1}",
"Manquant : {0}. Installation : {1}",
"Es fählt: {0}. Installation: {1}"
],
"SHA-256 laut Hersteller (optional, empfohlen)": [
"SHA-256 from the vendor (optional, recommended)",
"SHA-256 du fabricant (facultatif, recommandé)",
"SHA-256 vom Herställer (optional, empfohle)"
],
"Prüfsumme von der Download-Seite des Herstellers. Stimmt sie nicht mit der Datei überein, wird nichts eingespielt. Die Prüfsumme der gewählten Datei steht im Protokoll und im Tooltip des Dateinamens.": [
"Checksum from the vendor's download page. If it does not match the file, nothing is flashed. The checksum of the chosen file is shown in the log and in the tooltip of the file name.",
"Somme de contrôle de la page de téléchargement du fabricant. Si elle ne correspond pas au fichier, rien n'est flashé. La somme du fichier choisi figure dans le journal et dans l'infobulle du nom de fichier.",
"Prüefsumme vo de Download-Siite vom Herställer. Stimmt si nöd mit de Datei überii, wird nüt iigspielt. D Prüefsumme vo de gwählte Datei staht im Protokoll und im Tooltip vom Dateiname."
],
"Bei Fehler anhalten": [
"Stop on error",
"Arrêter en cas d'erreur",
"Bi Fehler aahalte"
],
"Schlägt ein Server fehl, werden die übrigen nicht mehr bearbeitet. So trifft ein fehlerhaftes Paket höchstens ein Gerät.": [
"If a server fails, the remaining ones are not processed. A faulty package then hits at most one device.",
"Si un serveur échoue, les suivants ne sont pas traités. Un paquet défectueux touche ainsi au plus un appareil.",
"Wänn en Server fehlschlaat, wärded di übrige nümm bearbeitet. So trifft es fählerhafts Paket höchschtens eis Grät."
],
"Die Prüfsumme muss ein SHA-256-Wert mit 64 Hex-Zeichen sein.": [
"The checksum must be a SHA-256 value of 64 hex characters.",
"La somme de contrôle doit être une valeur SHA-256 de 64 caractères hexadécimaux.",
"D Prüefsumme muess en SHA-256-Wärt mit 64 Hex-Zeiche sii."
],
"Seriennummer bzw. Service-Tag des Geräts, nur zur eigenen Übersicht; wird nirgends an den BMC gesendet. Lässt sich auch per Handy-Scanner einscannen.": [
"Serial number or service tag of the device, for your own overview only; never sent to the BMC. Can also be scanned with the phone scanner.",
"Numéro de série ou service tag de l'appareil, pour votre propre vue d'ensemble ; jamais envoyé au BMC. Peut aussi être scanné avec le scanner du téléphone.",
"Seriennummere bzw. Service-Tag vom Grät, nume für di eige Übersicht; wird nienet a de BMC gschickt. Lat sich au mit em Handy-Scanner iiscanne."
],
"Hersteller/Typ, automatisch anhand der Aktuelle-IP ermittelt. Nur Anzeige,\nnicht editierbar; wird beim Eintragen/Ändern der Aktuelle IP neu geprüft.": [
"Vendor/type, detected automatically from the current IP. Display only,\nnot editable; checked again whenever the current IP is entered or changed.",
"Fabricant/type, détecté automatiquement d'après l'IP actuelle. Affichage seul,\nnon modifiable ; revérifié à chaque saisie ou modification de l'IP actuelle.",
"Herställer/Typ, automatisch aahand vo de aktuelle IP ermittlet. Nume Aazeig,\nnöd editierbar; wird bim Iitrage/Ändere vo de aktuelle IP neu prüeft."
],
"Zur Sicherheit das Passwort ein zweites Mal eingeben.\nSolange nicht angehakt, ist das Feld deaktiviert und wird nicht geprüft.\nAngehakt: Stimmen die beiden Eingaben nicht überein, startet kein Lauf.": [
"Enter the password a second time for safety.\nWhile unticked, the field is disabled and not checked.\nTicked: if the two entries differ, no run starts.",
"Saisissez le mot de passe une seconde fois par sécurité.\nTant que la case n'est pas cochée, le champ est désactivé et non vérifié.\nCochée : si les deux saisies diffèrent, aucune exécution ne démarre.",
"Zur Sicherheit s Passwort es zweits Mal iigäh.\nSolang nöd aaghäklet, isch s Fäld deaktiviert und wird nöd prüeft.\nAaghäklet: Stimmed di beide Iigabe nöd überii, startet kei Lauf."
],
"Lädt eine gespeicherte Liste oder eine CSV-Datei, z. B. aus Excel:\naktuelle_ip,passwort,neue_ip,subnetzmaske,gateway,hostname,seriennummer\n(seriennummer ist optional, auch ganz ohne diese Spalte ladbar)": [
"Loads a saved list or a CSV file, e.g. from Excel:\naktuelle_ip,passwort,neue_ip,subnetzmaske,gateway,hostname,seriennummer\n(seriennummer is optional; files without this column load too)",
"Charge une liste enregistrée ou un fichier CSV, p. ex. depuis Excel :\naktuelle_ip,passwort,neue_ip,subnetzmaske,gateway,hostname,seriennummer\n(seriennummer est facultatif, chargeable aussi sans cette colonne)",
"Ladet e gspichereti Lischte oder e CSV-Datei, z. B. us Excel:\naktuelle_ip,passwort,neue_ip,subnetzmaske,gateway,hostname,seriennummer\n(seriennummer isch optional, au ganz ohni die Spalte ladbar)"
],
"Öffnet das Protokoll als Textdatei im Standard-Editor.\nPasswörter stehen nicht darin; geändert wird nichts.": [
"Opens the log as a text file in the default editor.\nNo passwords are in it; nothing is changed.",
"Ouvre le journal comme fichier texte dans l'éditeur par défaut.\nIl ne contient aucun mot de passe ; rien n'est modifié.",
"Öffnet s Protokoll als Textdatei im Standard-Editor.\nPasswörter stönd keini dinn; es wird nüt gänderet."
],
"Zertifikate …": [
"Certificates …",
"Certificats …",
"Zertifikat …"
],
"Gemerkte Zertifikats-Fingerabdrücke der BMCs ansehen und einzelne entfernen (nach gewolltem Zertifikatswechsel oder Mainboard-Tausch).": [
"View the stored certificate fingerprints of the BMCs and remove individual ones (after an intended certificate change or a mainboard replacement).",
"Afficher les empreintes de certificat mémorisées des BMC et en supprimer (après un changement de certificat voulu ou un remplacement de carte mère).",
"Gmerkti Zertifikats-Fingerabdrück vo de BMCs aaluege und einzelni entferne (nach eme gwollte Zertifikatswächsel oder Mainboard-Tuusch)."
],
"Sicherungen/Protokolle": [
"Backups/logs",
"Sauvegardes/journaux",
"Sicherige/Protokoll"
],
"„Automatisch erkennen“ fragt jedes Gerät nach seinem Hersteller.\nEine feste Wahl überspringt die Erkennung und gilt für alle Zeilen.": [
"“Detect automatically” asks each device for its vendor.\nA fixed choice skips detection and applies to all rows.",
"« Détection automatique » interroge chaque appareil sur son fabricant.\nUn choix fixe saute la détection et vaut pour toutes les lignes.",
"„Automatisch erkänne“ fragt jedes Grät nach sim Herställer.\nE feschti Wahl überspringt d Erkännig und gilt für alli Zile."
],
"Benutzer, mit dem sich das Tool am BMC anmeldet. Leer lassen: Werksbenutzer je Hersteller.": [
"User the tool logs in to the BMC with. Leave empty: factory user per vendor.",
"Utilisateur avec lequel l'outil se connecte au BMC. Vide : utilisateur d'usine selon le fabricant.",
"Benutzer, mit dem sich s Tool am BMC aamäldet. Leer lah: Werksbenutzer je Herställer."
],
"Gilt für alle Zeilen, wenn Sie unten auf „Für alle Zeilen übernehmen“ klicken.\nAbweichende Zugangsdaten tragen Sie direkt in der Tabelle je Zeile ein.": [
"Applies to all rows when you click “Apply to all rows” below.\nEnter different credentials directly in the table per row.",
"S'applique à toutes les lignes si vous cliquez sur « Appliquer à toutes les lignes » ci-dessous.\nSaisissez des identifiants différents directement dans le tableau, ligne par ligne.",
"Gilt für alli Zile, wänn Sie une uf „Für alli Zile übernäh“ klicked.\nAbwichendi Zuegangsdate träged Sie direkt i de Tabälle je Zile ii."
],
"Trägt Benutzer und Passwort von oben in alle Zeilen ein und überschreibt vorhandene Werte.": [
"Enters user and password from above into all rows and overwrites existing values.",
"Reporte l'utilisateur et le mot de passe ci-dessus dans toutes les lignes et écrase les valeurs existantes.",
"Treit Benutzer und Passwort vo obe i alli Zile ii und überschribt vorhandeni Wärt."
],
"Fügt für jede Adresse von … bis … eine Zeile ein (höchstens 256 auf einmal).": [
"Adds one row for each address from … to … (at most 256 at once).",
"Ajoute une ligne par adresse de … à … (256 au maximum à la fois).",
"Füegt für jedi Adrässe vo … bis … e Zile ii (höchschtens 256 uf eimal)."
],
"Übernimmt Adressen und Passwörter aus „Neuware“ (Aktuelle IP, Passwort).": [
"Takes addresses and passwords from “New hardware” (current IP, password).",
"Reprend les adresses et mots de passe de « Nouveau matériel » (IP actuelle, mot de passe).",
"Übernimmt Adrässe und Passwörter us „Neuwar“ (aktuelli IP, Passwort)."
],
"Prüft je Server nur lesend: Port, Verschlüsselung, Hersteller, Anmeldung, Modell,\nFirmware und Administratorrechte. Es wird nichts geändert. Jeder Server bekommt\ngenau einen Anmeldeversuch, damit ein falsches Passwort kein Konto sperrt.": [
"Checks each server read-only: port, encryption, vendor, login, model,\nfirmware and administrator rights. Nothing is changed. Each server gets\nexactly one login attempt so that a wrong password cannot lock an account.",
"Vérifie chaque serveur en lecture seule : port, chiffrement, fabricant, connexion, modèle,\nfirmware et droits d'administrateur. Rien n'est modifié. Chaque serveur a\nexactement une tentative de connexion, pour qu'un mauvais mot de passe ne bloque aucun compte.",
"Prüeft je Server nume läsend: Port, Verschlüsselig, Herställer, Aamäldig, Modell,\nFirmware und Adminrächt. Es wird nüt gänderet. Jede Server überchunnt\ngenau ein Aamäldeversuech, damit es falsches Passwort kei Konto sperrt."
],
"Übernimmt alle erfolgreich getesteten Server in die Serverliste („Neuware“) und trägt\nden Benutzer in „Neuware“, „UEFI-Einstellungen“ und „Dienste“ ein. Danach dort die Einstellungen wählen und starten.": [
"Copies all successfully tested servers into the server list (“New hardware”) and enters\nthe user in “New hardware”, “UEFI settings” and “Services”. Then choose the settings there and start.",
"Reprend tous les serveurs testés avec succès dans la liste (« Nouveau matériel ») et saisit\nl'utilisateur dans « Nouveau matériel », « Réglages UEFI » et « Services ». Choisissez ensuite les réglages et lancez.",
"Übernimmt alli erfolgriich teschtete Server i d Serverlischte („Neuwar“) und treit\ndä Benutzer i „Neuwar“, „UEFI-Iistellige“ und „Dienscht“ ii. Dänn det d Iistellige wähle und starte."
],
"Härtung (empfohlen)": [
"Hardening (recommended)",
"Durcissement (recommandé)",
"Härtig (empfohle)"
],
"Schaltet unnötige Netzwerkdienste ab und setzt Kontosperre und Session-Zeitlimit über Standard-Redfish. Was ein BMC nicht kennt, wird übersprungen.": [
"Switches off unneeded network services and sets account lockout and session timeout via standard Redfish. Whatever a BMC does not know is skipped.",
"Désactive les services réseau inutiles et règle le verrouillage de compte et le délai de session via Redfish standard. Ce qu'un BMC ne connaît pas est ignoré.",
"Schaltet unnötigi Netzwärkdienscht ab und setzt Kontosperri und Session-Zitlimit über Standard-Redfish. Was en BMC nöd kennt, wird übersprunge."
],
"IPMI über LAN abschalten": [
"Disable IPMI over LAN",
"Désactiver IPMI sur LAN",
"IPMI über LAN abschalte"
],
"IPMI hat schwache Anmeldeverfahren. Achtung: Die Textkonsole (SOL) bei Supermicro und Überwachungssysteme, die IPMI nutzen, gehen danach nicht mehr.": [
"IPMI has weak authentication. Note: the text console (SOL) on Supermicro and monitoring systems that use IPMI stop working afterwards.",
"IPMI a des procédés d'authentification faibles. Attention : la console texte (SOL) sur Supermicro et les systèmes de supervision utilisant IPMI ne fonctionnent plus ensuite.",
"IPMI hät schwachi Aamäldeverfahre. Achtig: D Textkonsole (SOL) bi Supermicro und Überwachigssystem, wo IPMI bruuched, gönd dänn nümm."
],
"SSDP-Ankündigung abschalten": [
"Disable SSDP announcement",
"Désactiver l'annonce SSDP",
"SSDP-Aakündigung abschalte"
],
"Der BMC kündigt sich dann nicht mehr per Multicast im Netz an.": [
"The BMC then no longer announces itself via multicast on the network.",
"Le BMC ne s'annonce alors plus par multicast sur le réseau.",
"De BMC kündet sich dänn nümm per Multicast im Netz aa."
],
"SNMP abschalten": [
"Disable SNMP",
"Désactiver SNMP",
"SNMP abschalte"
],
"Nur, wenn kein Monitoring per SNMP genutzt wird.": [
"Only if no SNMP monitoring is used.",
"Uniquement si aucune supervision SNMP n'est utilisée.",
"Nume, wänn kei Monitoring per SNMP bruucht wird."
],
"unverändert": [
"unchanged",
"inchangé",
"unveränderet"
],
"Kontosperre nach Fehlversuchen": [
"Account lockout after failed attempts",
"Verrouillage après tentatives échouées",
"Kontosperri nach Fehlversüech"
],
"Sperrt ein Konto nach so vielen falschen Passwörtern (0 = unverändert).": [
"Locks an account after this many wrong passwords (0 = unchanged).",
"Verrouille un compte après autant de mots de passe erronés (0 = inchangé).",
"Sperrt es Konto nach so vill falsche Passwörter (0 = unveränderet)."
],
"Sperrdauer": [
"Lockout duration",
"Durée de verrouillage",
"Sperrduur"
],
"So lange bleibt ein gesperrtes Konto gesperrt.": [
"A locked account stays locked this long.",
"Durée pendant laquelle un compte verrouillé le reste.",
"So lang blibt es gsperrts Konto gsperrt."
],
"Session-Zeitlimit": [
"Session timeout",
"Délai de session",
"Session-Zitlimit"
],
"Untätige Anmeldungen (Weboberfläche, Redfish) enden nach dieser Zeit (0 = unverändert).": [
"Idle logins (web interface, Redfish) end after this time (0 = unchanged).",
"Les connexions inactives (interface web, Redfish) se terminent après ce délai (0 = inchangé).",
"Untätigi Aamäldige (Weboberflächi, Redfish) ändet nach dere Zit (0 = unveränderet)."
],
"Härtung": [
"Hardening",
"Durcissement",
"Härtig"
],
"Zertifikate": [
"Certificates",
"Certificats",
"Zertifikat"
],
"BMC-Steward merkt sich beim ersten Kontakt das Zertifikat jedes BMC. Zeigt dasselbe Gerät später ein anderes, wird es gesperrt, bevor ein Passwort gesendet wird. War der Wechsel gewollt (neues Zertifikat, Mainboard-Tausch, Werksreset), den Eintrag hier entfernen; beim nächsten Kontakt wird das neue Zertifikat gemerkt. Den Fingerabdruck vorher mit der Anzeige in der BMC-Oberfläche vergleichen.": [
"BMC-Steward remembers each BMC's certificate on first contact. If the same device later shows a different one, it is blocked before any password is sent. If the change was intended (new certificate, mainboard replacement, factory reset), remove the entry here; the new certificate is remembered on the next contact. Compare the fingerprint with the one shown in the BMC's web interface first.",
"BMC-Steward mémorise le certificat de chaque BMC au premier contact. Si le même appareil en présente un autre plus tard, il est bloqué avant l'envoi de tout mot de passe. Si le changement était voulu (nouveau certificat, remplacement de carte mère, réinitialisation usine), supprimez l'entrée ici ; le nouveau certificat sera mémorisé au prochain contact. Comparez d'abord l'empreinte avec celle affichée dans l'interface du BMC.",
"BMC-Steward merkt sich bim erschte Kontakt s Zertifikat vo jedem BMC. Zeigt s gliiche Grät spöter es anders, wird es gsperrt, bevor es Passwort gschickt wird. Isch de Wächsel gwollt gsi (neus Zertifikat, Mainboard-Tuusch, Werksreset), dä Iitrag da entferne; bim nächschte Kontakt wird s neue Zertifikat gmerkt. Dä Fingerabdruck vorher mit de Aazeig i de BMC-Oberflächi vergliiche."
],
"Suchen (Adresse, Kennung, Fingerabdruck)": [
"Search (address, identifier, fingerprint)",
"Rechercher (adresse, identifiant, empreinte)",
"Sueche (Adrässe, Kennig, Fingerabdruck)"
],
"Markierte entfernen": [
"Remove selected",
"Supprimer la sélection",
"Markierti entferne"
],
"Schließen": [
"Close",
"Fermer",
"Schliesse"
],
"IPv4-Adresse des BMC (iDRAC, iLO, Supermicro-IPMI, XCC).": [
"IPv4 address of the BMC (iDRAC, iLO, Supermicro IPMI, XCC).",
"Adresse IPv4 du BMC (iDRAC, iLO, IPMI Supermicro, XCC).",
"IPv4-Adrässe vom BMC (iDRAC, iLO, Supermicro-IPMI, XCC)."
],
"HTTPS-Port des BMC, normalerweise 443.": [
"HTTPS port of the BMC, usually 443.",
"Port HTTPS du BMC, normalement 443.",
"HTTPS-Port vom BMC, normalerwiis 443."
],
"Benutzer dieses Geräts. Leer: Werksbenutzer je Hersteller.": [
"User of this device. Empty: factory user per vendor.",
"Utilisateur de cet appareil. Vide : utilisateur d'usine selon le fabricant.",
"Benutzer vo dem Grät. Leer: Werksbenutzer je Herställer."
],
"Passwort dieses Geräts.": [
"Password of this device.",
"Mot de passe de cet appareil.",
"Passwort vo dem Grät."
],
"Ergebnis des Verbindungstests.": [
"Result of the connection test.",
"Résultat du test de connexion.",
"Ergebnis vom Verbindigstest."
],
"Bitte eine gültige Start- und Endadresse (Start vor Ende) eintragen.": [
"Please enter a valid start and end address (start before end).",
"Veuillez saisir une adresse de début et de fin valides (début avant fin).",
"Bitte e gültigi Start- und Ändadrässe (Start vor Änd) iitrage."
],
"Höchstens 256 Adressen auf einmal.": [
"At most 256 addresses at once.",
"256 adresses au maximum à la fois.",
"Höchschtens 256 Adrässe uf eimal."
],
"{0} Server in die Serverliste („Neuware“) übernommen, Benutzer „{1}“ eingetragen.": [
"{0} servers added to the server list (“New hardware”), user “{1}” entered.",
"{0} serveurs repris dans la liste (« Nouveau matériel »), utilisateur « {1} » saisi.",
"{0} Server i d Serverlischte („Neuwar“) übernoh, Benutzer „{1}“ iitreit."
],
"Gerät": [
"Device",
"Appareil",
"Grät"
],
"Fingerabdruck (SHA-256)": [
"Fingerprint (SHA-256)",
"Empreinte (SHA-256)",
"Fingerabdruck (SHA-256)"
],
"Zuletzt gesehen": [
"Last seen",
"Vu pour la dernière fois",
"Zletscht gseh"
],
"Öffnet den Datenordner mit den automatischen Sicherungen vor jeder Änderung und dem Revisionsprotokoll aller scharfen Läufe:\n{0}": [
"Opens the data folder with the automatic backups taken before every change and the audit log of all live runs:\n{0}",
"Ouvre le dossier de données avec les sauvegardes automatiques faites avant chaque modification et le journal d'audit de toutes les exécutions réelles :\n{0}",
"Öffnet dä Dateordner mit de automatische Sicherige vor jeder Änderig und em Revisionsprotokoll vo allne schaarfe Läuf:\n{0}"
],
"Härtung: mindestens eine Einstellung wählen.": [
"Hardening: choose at least one setting.",
"Durcissement : choisissez au moins un réglage.",
"Härtig: mindeschtens ei Iistellig wähle."
],
"Härtung: Nach dem Abschalten von IPMI geht die Textkonsole (SOL) bei Supermicro nicht mehr, ebenso Überwachung per IPMI.": [
"Hardening: after IPMI is switched off, the text console (SOL) on Supermicro no longer works, nor does monitoring via IPMI.",
"Durcissement : une fois IPMI désactivé, la console texte (SOL) sur Supermicro ne fonctionne plus, ni la supervision via IPMI.",
"Härtig: Nach em Abschalte vo IPMI gaht d Textkonsole (SOL) bi Supermicro nümm, und au kei Überwachig per IPMI."
],
"{0} getestete Server blieben draußen: anderer Benutzer als „{1}“ oder Port ungleich 443. „Neuware“, „UEFI-Einstellungen“ und „Dienste“ kennen nur einen Benutzer für alle Server; diese Server in einem zweiten Durchgang übernehmen.": [
"{0} tested servers were left out: user other than “{1}” or port other than 443. “New hardware”, “UEFI settings” and “Services” know only one user for all servers; add these servers in a second pass.",
"{0} serveurs testés ont été laissés de côté : utilisateur autre que « {1} » ou port différent de 443. « Nouveau matériel », « Réglages UEFI » et « Services » ne connaissent qu'un utilisateur pour tous les serveurs ; reprenez ces serveurs lors d'un second passage.",
"{0} teschteti Server sind dusse blibe: andere Benutzer als „{1}“ oder Port ungliich 443. „Neuwar“, „UEFI-Iistellige“ und „Dienscht“ kenned nume ein Benutzer für alli Server; die Server imene zweite Durchgang übernäh."
],
"Zeile {0}: keine gültige IPv4-Adresse": [
"Row {0}: not a valid IPv4 address",
"Ligne {0} : adresse IPv4 non valide",
"Zile {0}: kei gültigi IPv4-Adrässe"
],
"{0} Einträge entfernen? Beim nächsten Kontakt wird das dann gezeigte Zertifikat ungeprüft übernommen.": [
"Remove {0} entries? On the next contact, the certificate then shown is accepted unchecked.",
"Supprimer {0} entrées ? Au prochain contact, le certificat alors présenté sera accepté sans vérification.",
"{0} Iiträg entferne? Bim nächschte Kontakt wird s dänn zeigte Zertifikat ungprüeft übernoh."
],
"Zeile {0}: ungültiger Port": [
"Row {0}: invalid port",
"Ligne {0} : port non valide",
"Zile {0}: ungültige Port"
],
"Zeile {0}: Passwort fehlt": [
"Row {0}: password missing",
"Ligne {0} : mot de passe manquant",
"Zile {0}: Passwort fählt"
],
"Zeile {0}: Adresse und Port kommen mehrfach vor": [
"Row {0}: address and port occur more than once",
"Ligne {0} : adresse et port apparaissent plusieurs fois",
"Zile {0}: Adrässe und Port chömed mehrfach vor"
],
"vorher: {0}": [
"before: {0}",
"avant : {0}",
"vorher: {0}"
],
"Nicht erneut versuchen, bevor Benutzer und Passwort geklärt sind: Mehrfache Fehlversuche können das Konto sperren.": [
"Do not retry before user and password are clarified: repeated failed attempts can lock the account.",
"Ne réessayez pas avant d'avoir clarifié l'utilisateur et le mot de passe : des échecs répétés peuvent verrouiller le compte.",
"Nöd nomal probiere, bevor Benutzer und Passwort klärt sind: Mehrfachi Fehlversüech chönd s Konto sperre."
],
"Zertifikat hat sich während des Laufs geändert (vorher {0}, jetzt {1})": [
"Certificate changed during the run (before {0}, now {1})",
"Le certificat a changé pendant l'exécution (avant {0}, maintenant {1})",
"Zertifikat hät sich während em Lauf gänderet (vorher {0}, jetzt {1})"
],
"  Warnung: Sicherung nicht möglich (nichts lesbar), es geht ohne weiter.": [
"  Warning: backup not possible (nothing readable), continuing without.",
"  Attention : sauvegarde impossible (rien de lisible), on continue sans.",
"  Warnig: Sicherig nöd möglich (nüt läsbar), es gaht ohni wiiter."
],
"IPMI über LAN aus": [
"IPMI over LAN off",
"IPMI sur LAN désactivé",
"IPMI über LAN us"
],
"SSDP aus": [
"SSDP off",
"SSDP désactivé",
"SSDP us"
],
"SNMP aus": [
"SNMP off",
"SNMP désactivé",
"SNMP us"
],
"Prüfsumme stimmt mit dem Sollwert überein.": [
"Checksum matches the expected value.",
"La somme de contrôle correspond à la valeur attendue.",
"Prüefsumme stimmt mit em Sollwärt überii."
],
"Kein HTTPS-Handshake möglich": [
"No HTTPS handshake possible",
"Aucune négociation HTTPS possible",
"Kei HTTPS-Handshake möglich"
],
"Der BMC antwortet am Port, spricht aber kein HTTPS oder ist noch nicht bereit.": [
"The BMC answers on the port but does not speak HTTPS or is not ready yet.",
"Le BMC répond sur le port mais ne parle pas HTTPS ou n'est pas encore prêt.",
"De BMC antwortet am Port, redt aber kei HTTPS oder isch no nöd parat."
],
"nicht erreichbar": [
"not reachable",
"injoignable",
"nöd erreichbar"
],
"Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)": [
"Vendor not recognised (neither iDRAC, iLO, Supermicro nor XCC)",
"Fabricant non reconnu (ni iDRAC, ni iLO, ni Supermicro, ni XCC)",
"Herställer nöd erkännt (weder iDRAC, iLO, Supermicro no XCC)"
],
"Gerät antwortet nicht (Zeitüberschreitung oder Verbindung abgelehnt).": [
"Device does not answer (timeout or connection refused).",
"L'appareil ne répond pas (délai dépassé ou connexion refusée).",
"Grät antwortet nöd (Zitüberschritig oder Verbindig abglehnt)."
],
"  Einstellungen gesichert: {0}": [
"  Settings backed up: {0}",
"  Réglages sauvegardés : {0}",
"  Iistellige gsicheret: {0}"
],
"Hinweis: Die Datei wird unverschlüsselt geholt; der BMC prüft die Signatur des Pakets selbst. Wenn möglich HTTPS verwenden.": [
"Note: the file is fetched unencrypted; the BMC checks the package signature itself. Use HTTPS if possible.",
"Remarque : le fichier est récupéré sans chiffrement ; le BMC vérifie lui-même la signature du paquet. Utilisez HTTPS si possible.",
"Hiiwis: D Datei wird unverschlüsslet gholt; de BMC prüeft d Signatur vom Paket sälber. Wänn möglich HTTPS bruuche."
],
"Datei {0}: {1:.1f} MB, SHA-256 {2}": [
"File {0}: {1:.1f} MB, SHA-256 {2}",
"Fichier {0} : {1:.1f} Mo, SHA-256 {2}",
"Datei {0}: {1:.1f} MB, SHA-256 {2}"
],
"Hinweis: Kein Sollwert für die Prüfsumme angegeben; bitte mit der Angabe des Herstellers vergleichen.": [
"Note: no expected checksum given; please compare with the vendor's value.",
"Remarque : aucune somme de contrôle attendue indiquée ; comparez avec la valeur du fabricant.",
"Hiiwis: Kei Sollwärt für d Prüefsumme aagäh; bitte mit de Aagab vom Herställer vergliiche."
],
"== Angehalten nach dem ersten Fehler, übrige Server nicht bearbeitet („Bei Fehler anhalten“).": [
"== Stopped after the first error, remaining servers not processed (“Stop on error”).",
"== Arrêt après la première erreur, serveurs restants non traités (« Arrêter en cas d'erreur »).",
"== Aaghalte nach em erschte Fehler, übrigi Server nöd bearbeitet („Bi Fehler aahalte“)."
],
"Adresse und Port prüfen, außerdem Routing/Firewall zwischen diesem Rechner und dem BMC-Netz.": [
"Check address and port, as well as routing/firewall between this computer and the BMC network.",
"Vérifiez l'adresse et le port, ainsi que le routage/pare-feu entre cet ordinateur et le réseau BMC.",
"Adrässe und Port prüefe, usserdem Routing/Firewall zwüsched dem Rächner und em BMC-Netz."
],
"Anmeldung ok, aber das Konto darf den BMC nicht lesen (HTTP 403)": [
"Login ok, but the account may not read the BMC (HTTP 403)",
"Connexion ok, mais le compte n'a pas le droit de lire le BMC (HTTP 403)",
"Aamäldig ok, aber s Konto darf dä BMC nöd läse (HTTP 403)"
],
"  Hinweis: Administratorrechte nicht prüfbar.": [
"  Note: administrator rights could not be checked.",
"  Remarque : droits d'administrateur non vérifiables.",
"  Hiiwis: Adminrächt nöd prüefbar."
],
"unbekannt": [
"unknown",
"inconnu",
"unbekannt"
],
"  Zertifikat nach eigener Änderung erneuert, neu gemerkt (SHA-256 {0})": [
"  Certificate renewed after our own change, remembered again (SHA-256 {0})",
"  Certificat renouvelé après notre propre modification, mémorisé à nouveau (SHA-256 {0})",
"  Zertifikat nach eigener Änderig erneueret, neu gmerkt (SHA-256 {0})"
],
"  Neues Gerät: Zertifikat gemerkt (SHA-256 {0})": [
"  New device: certificate remembered (SHA-256 {0})",
"  Nouvel appareil : certificat mémorisé (SHA-256 {0})",
"  Neus Grät: Zertifikat gmerkt (SHA-256 {0})"
],
"  Warnung: Revisionsprotokoll nicht schreibbar ({0})": [
"  Warning: audit log not writable ({0})",
"  Attention : journal d'audit non inscriptible ({0})",
"  Warnig: Revisionsprotokoll nöd schriibbar ({0})"
],
"Kontrolle der neuen Adressen (bis zu {0}s)... Die Liste bleibt bis dahin gesperrt; „Abbrechen“ beendet die Kontrolle vorzeitig.": [
"Checking the new addresses (up to {0}s)... The list stays locked until then; “Cancel” ends the check early.",
"Contrôle des nouvelles adresses (jusqu'à {0} s)... La liste reste verrouillée jusque-là ; « Annuler » termine le contrôle plus tôt.",
"Kontrolle vo de neue Adrässe (bis zu {0}s)... D Lischte blibt bis dänn gsperrt; „Abbräche“ beändet d Kontrolle vorzitig."
],
"  {0}: auf diesem Gerät nicht unterstützt, übersprungen": [
"  {0}: not supported on this device, skipped",
"  {0} : non pris en charge sur cet appareil, ignoré",
"  {0}: uf dem Grät nöd unterstützt, übersprunge"
],
"  FEHLER: {0}": [
"  ERROR: {0}",
"  ERREUR : {0}",
"  FEHLER: {0}"
],
"Weder Dell iDRAC, HPE iLO, Supermicro noch Lenovo XCC erkannt.": [
"Neither Dell iDRAC, HPE iLO, Supermicro nor Lenovo XCC recognised.",
"Ni Dell iDRAC, ni HPE iLO, ni Supermicro, ni Lenovo XCC reconnu.",
"Weder Dell iDRAC, HPE iLO, Supermicro no Lenovo XCC erkännt."
],
"Zertifikat dieses Geräts hat sich geändert (gemerkt {0}, jetzt {1}). Möglicher Angriff (Man-in-the-Middle) – es wurde kein Passwort gesendet. War der Wechsel gewollt (neues Zertifikat, Mainboard-Tausch, Werksreset), den Eintrag unter „Zertifikate“ (links unten) entfernen.": [
"This device's certificate has changed (remembered {0}, now {1}). Possible attack (man-in-the-middle) – no password was sent. If the change was intended (new certificate, mainboard replacement, factory reset), remove the entry under “Certificates” (bottom left).",
"Le certificat de cet appareil a changé (mémorisé {0}, maintenant {1}). Attaque possible (homme du milieu) – aucun mot de passe n'a été envoyé. Si le changement était voulu (nouveau certificat, remplacement de carte mère, réinitialisation usine), supprimez l'entrée sous « Certificats » (en bas à gauche).",
"S Zertifikat vo dem Grät hät sich gänderet (gmerkt {0}, jetzt {1}). Mögliche Aagriff (Man-in-the-Middle) – es isch kei Passwort gschickt worde. Isch de Wächsel gwollt gsi (neus Zertifikat, Mainboard-Tuusch, Werksreset), dä Iitrag under „Zertifikat“ (links une) entferne."
],
"  Warnung: Sicherung nicht schreibbar ({0}), es geht ohne weiter.": [
"  Warning: backup not writable ({0}), continuing without.",
"  Attention : sauvegarde non inscriptible ({0}), on continue sans.",
"  Warnig: Sicherig nöd schriibbar ({0}), es gaht ohni wiiter."
],
"GESPERRT: ": [
"BLOCKED: ",
"BLOQUÉ : ",
"GSPERRT: "
],
"  ... noch {0} von {1} Server(n) nicht erreichbar, warte weiter ({2}s vergangen)": [
"  ... {0} of {1} server(s) still not reachable, waiting ({2}s elapsed)",
"  ... encore {0} serveur(s) sur {1} injoignable(s), on attend ({2} s écoulées)",
"  ... no {0} vo {1} Server nöd erreichbar, warte wiiter ({2}s vergange)"
],
"Härtung: Kontosperre nach {0} Fehlversuchen für {1} min": [
"Hardening: lock account after {0} failed attempts for {1} min",
"Durcissement : verrouillage après {0} échecs pendant {1} min",
"Härtig: Kontosperri nach {0} Fehlversüech für {1} min"
],
"Härtung: Session-Zeitlimit {0} min": [
"Hardening: session timeout {0} min",
"Durcissement : délai de session {0} min",
"Härtig: Session-Zitlimit {0} min"
],
"FEHLER: Datei nicht lesbar: {0}": [
"ERROR: file not readable: {0}",
"ERREUR : fichier illisible : {0}",
"FEHLER: Datei nöd läsbar: {0}"
],
"FEHLER: Prüfsumme stimmt nicht mit dem Sollwert überein (erwartet {0}). Die Datei ist beschädigt oder nicht die richtige. Es wurde nichts eingespielt.": [
"ERROR: checksum does not match the expected value (expected {0}). The file is damaged or not the right one. Nothing was flashed.",
"ERREUR : la somme de contrôle ne correspond pas à la valeur attendue ({0}). Le fichier est endommagé ou n'est pas le bon. Rien n'a été flashé.",
"FEHLER: Prüefsumme stimmt nöd mit em Sollwärt überii (erwartet {0}). D Datei isch kaputt oder nöd di richtig. Es isch nüt iigspielt worde."
],
"keine brauchbare Antwort der XML-Schnittstelle (HTTP {0})": [
"no usable answer from the XML interface (HTTP {0})",
"aucune réponse exploitable de l'interface XML (HTTP {0})",
"kei bruuchbari Antwort vo de XML-Schnittstell (HTTP {0})"
],
"Anmeldung nicht möglich (HTTP {0})": [
"Login not possible (HTTP {0})",
"Connexion impossible (HTTP {0})",
"Aamäldig nöd möglich (HTTP {0})"
],
"Härtung: {0}": [
"Hardening: {0}",
"Durcissement : {0}",
"Härtig: {0}"
],
"Starte … %p %": [
"Starting … %p %",
"Démarrage … %p %",
"Start … %p %"
],
"In welche Spalte der Serverliste die Scans eingetragen werden, z. B. „Passwort“ oder „Seriennummer“. Lässt sich jederzeit umschalten, auch während der Scanner läuft - ab dem nächsten Scan gilt die neue Spalte.": [
"Which column of the server list the scans go into, e.g. “Password” or “Serial number”. Can be switched at any time, even while the scanner runs – the new column applies from the next scan.",
"Colonne de la liste où les scans sont saisis, p. ex. « Mot de passe » ou « Numéro de série ». Modifiable à tout moment, même pendant le scan – la nouvelle colonne s'applique dès le scan suivant.",
"I weli Spalte vo de Serverlischte d Scans iitreit wärded, z. B. „Passwort“ oder „Seriennummere“. Lat sich jederzit umschalte, au während de Scanner lauft – ab em nächschte Scan gilt di neu Spalte."
],
"erneut prüfen": [
"check again",
"revérifier",
"nomal prüefe"
],
"Nach dem Installieren eines Pakets erneut prüfen – ohne Neustart.": [
"Check again after installing a package – no restart needed.",
"Revérifier après l'installation d'un paquet – sans redémarrage.",
"Nach em Installiere vomene Paket nomal prüefe – ohni Neustart."
],
"Das Handy zeigt nur die Kamera. Bilder werden nicht auf dem Handy gespeichert: Der PC liest den Text und verwirft das Bild sofort. Der Wert landet im unten gewählten Zielfeld der markierten Zeile (sonst der ersten offenen Zeile, in der dieses Feld noch leer ist); danach springt die Markierung eine Zeile weiter.": [
"The phone only shows the camera. Images are not saved on the phone: the PC reads the text and discards the image at once. The value goes into the target field chosen below in the selected row (otherwise the first open row where this field is still empty); the selection then moves down one row.",
"Le téléphone affiche seulement la caméra. Les images ne sont pas enregistrées sur le téléphone : le PC lit le texte et jette l'image aussitôt. La valeur va dans le champ cible choisi ci-dessous de la ligne sélectionnée (sinon la première ligne ouverte où ce champ est vide) ; la sélection descend ensuite d'une ligne.",
"S Handy zeigt nume d Kamera. Bilder wärded nöd uf em Handy gspicheret: De PC list de Text und verwirft s Bild sofort. De Wärt landet im une gwählte Zielfäld vo de markierte Zile (suscht de erschte offene Zile, wo das Fäld no leer isch); dänn springt d Markierig e Zile wiiter."
],
"Zwischenbilder mitspeichern (zeigen das Passwort!)": [
"Keep intermediate images (they show the password!)",
"Conserver les images intermédiaires (elles montrent le mot de passe !)",
"Zwüschebilder mitspichere (zeiged s Passwort!)"
],
"Das Protokoll enthält nie Passwörter, nur Länge und Form (A groß, a klein, 9 Ziffer, # Rest).\nAn: Zusätzlich liegen die Bilder der Erkennung im Export. Die zeigen das Passwort lesbar. Nur zur Fehlersuche einschalten und die ZIP danach löschen.": [
"The log never contains passwords, only length and shape (A upper, a lower, 9 digit, # other).\nOn: the recognition images are added to the export as well. They show the password legibly. Switch on only for troubleshooting and delete the ZIP afterwards.",
"Le journal ne contient jamais de mots de passe, seulement la longueur et la forme (A majuscule, a minuscule, 9 chiffre, # autre).\nActivé : les images de reconnaissance sont ajoutées à l'export. Elles montrent le mot de passe lisiblement. N'activer que pour le dépannage et supprimer le ZIP ensuite.",
"S Protokoll enthaltet nie Passwörter, nume Längi und Form (A gross, a chli, 9 Ziffer, # Rescht).\nAa: Zuesätzlich ligged d Bilder vo de Erkännig im Export. Die zeiged s Passwort läsbar. Nume zur Fehlersuech iischalte und d ZIP dänn lösche."
],
"Log exportieren …": [
"Export log …",
"Exporter le journal …",
"Log exportiere …"
],
"HTTPS-Zertifikat (zum Starten nötig)": [
"HTTPS certificate (needed to start)",
"Certificat HTTPS (nécessaire au démarrage)",
"HTTPS-Zertifikat (zum Starte nötig)"
],
"Barcode (Paket „zbar“)": [
"Barcode (package “zbar”)",
"Code-barres (paquet « zbar »)",
"Barcode (Paket „zbar“)"
],
"Texterkennung (OCR)": [
"Text recognition (OCR)",
"Reconnaissance de texte (OCR)",
"Texterkännig (OCR)"
],
"QR-Code zum Verbinden": [
"QR code to connect",
"Code QR pour se connecter",
"QR-Code zum Verbinde"
],
"Handy verbunden": [
"Phone connected",
"Téléphone connecté",
"Handy verbunde"
],
"Log exportieren": [
"Export log",
"Exporter le journal",
"Log exportiere"
],
"ZIP (*.zip)": [
"ZIP (*.zip)",
"ZIP (*.zip)",
"ZIP (*.zip)"
],
"Bereit – QR-Code mit dem Handy scannen": [
"Ready – scan the QR code with the phone",
"Prêt – scannez le code QR avec le téléphone",
"Parat – QR-Code mit em Handy scanne"
],
"Zielfeld": [
"Target field",
"Champ cible",
"Zielfäld"
],
"Log exportiert: {0}": [
"Log exported: {0}",
"Journal exporté : {0}",
"Log exportiert: {0}"
],
"Hilfsprogramme am PC": [
"Helper programs on the PC",
"Programmes auxiliaires sur le PC",
"Hilfsprogramm am PC"
],
"Fehlt etwas? Unter CachyOS:": [
"Something missing? On CachyOS:",
"Il manque quelque chose ? Sous CachyOS :",
"Fählt öppis? Under CachyOS:"
],
"{0} in Zeile {1} eingetragen": [
"{0} entered in row {1}",
"{0} saisi en ligne {1}",
"{0} i Zile {1} iitreit"
],
"Virtuelle Dell/HPE/Lenovo/Supermicro-Server, die sich wie echte BMCs verhalten - zum Testen und Vorführen ohne Hardware. Im Modus Konfigraum oder Router bekommt jeder Server eine eigene Netzwerkkarte mit MAC, holt sich per DHCP eine Adresse (oder hat eine feste Werks-IP) und zieht beim Umstellen wirklich um.": [
"Virtual Dell/HPE/Lenovo/Supermicro servers that behave like real BMCs – for testing and demos without hardware. In Config room or Router mode each server gets its own network card with a MAC, obtains an address via DHCP (or has a static factory IP) and really moves when reconfigured.",
"Serveurs virtuels Dell/HPE/Lenovo/Supermicro qui se comportent comme de vrais BMC – pour tester et présenter sans matériel. En mode Salle de config ou Routeur, chaque serveur a sa propre carte réseau avec MAC, obtient une adresse par DHCP (ou a une IP d'usine fixe) et déménage réellement lors de la reconfiguration.",
"Virtuelli Dell/HPE/Lenovo/Supermicro-Server, wo sich wie ächti BMCs verhalted – zum Teschte und Vorfüehre ohni Hardware. Im Modus Konfigruum oder Router überchunnt jede Server e eigeti Netzwärkcharte mit MAC, holt sich per DHCP e Adrässe (oder hät e feschti Werks-IP) und zieht bim Umstelle würkli um."
],
"Netz": [
"Network",
"Réseau",
"Netz"
],
"Loopback: läuft ohne Root nur auf diesem PC, Adressen als IP:PORT.\nKonfigraum: eigener virtueller Staging-Switch, DHCP über „DHCP-Server“.\nRouter: die Server hängen im echten LAN und erscheinen im Router.\nKonfigraum und Router brauchen beim Start einmal Root (pkexec).": [
"Loopback: runs without root on this PC only, addresses as IP:PORT.\nConfig room: own virtual staging switch, DHCP via “DHCP server”.\nRouter: the servers sit on the real LAN and appear in the router.\nConfig room and Router need root once at start (pkexec).",
"Loopback : fonctionne sans root sur ce PC uniquement, adresses en IP:PORT.\nSalle de config : propre switch de staging virtuel, DHCP via « Serveur DHCP ».\nRouteur : les serveurs sont sur le vrai LAN et apparaissent dans le routeur.\nSalle de config et Routeur demandent root une fois au démarrage (pkexec).",
"Loopback: lauft ohni Root nume uf dem PC, Adrässe als IP:PORT.\nKonfigruum: eigete virtuelle Staging-Switch, DHCP über „DHCP-Server“.\nRouter: d Server hanged im ächte LAN und erschiined im Router.\nKonfigruum und Router bruuched bim Start eimal Root (pkexec)."
],
"Modus": [
"Mode",
"Mode",
"Modus"
],
"Adresse(n) dieses PCs am Staging-Switch, ohne Gateway - wie die Staging-Karte im Konfigraum. Mehrere durch Komma, z. B. 192.168.0.1/24, 10.20.30.1/24, damit auch die neuen Adressen im Zielnetz erreichbar sind.": [
"Address(es) of this PC on the staging switch, without gateway – like the staging card in the config room. Several separated by commas, e.g. 192.168.0.1/24, 10.20.30.1/24, so the new addresses in the target network are reachable too.",
"Adresse(s) de ce PC sur le switch de staging, sans passerelle – comme la carte de staging en salle de config. Plusieurs séparées par des virgules, p. ex. 192.168.0.1/24, 10.20.30.1/24, pour que les nouvelles adresses du réseau cible soient aussi joignables.",
"Adrässe vo dem PC am Staging-Switch, ohni Gateway – wie d Staging-Charte im Konfigruum. Mehreri mit Komma, z. B. 192.168.0.1/24, 10.20.30.1/24, damit au di neue Adrässe im Zielnetz erreichbar sind."
],
"Dieser PC": [
"This PC",
"Ce PC",
"Dä PC"
],
"Nach dem Start ist in „DHCP-Server“ die Karte „bmclab0“ schon gewählt: dort den DHCP-Server starten (wie am Staging-Switch). Server mit fester Werks-IP sind sofort da - zwei mit derselben IP stören sich wie in echt.": [
"After the start, the card “bmclab0” is already selected in “DHCP server”: start the DHCP server there (as on the staging switch). Servers with a static factory IP are there at once – two with the same IP interfere as in real life.",
"Après le démarrage, la carte « bmclab0 » est déjà choisie dans « Serveur DHCP » : lancez-y le serveur DHCP (comme sur le switch de staging). Les serveurs avec IP d'usine fixe sont là tout de suite – deux avec la même IP se gênent comme en vrai.",
"Nach em Start isch i „DHCP-Server“ d Charte „bmclab0“ scho gwählt: det dä DHCP-Server starte (wie am Staging-Switch). Server mit feschter Werks-IP sind sofort da – zwei mit de gliiche IP störed sich wie i echt."
],
"Kabel-Netzwerkkarte, die am Router hängt. WLAN geht nicht: ein WLAN-Router nimmt von einem Client keine zusätzlichen MAC-Adressen an.": [
"Wired network card connected to the router. Wi-Fi does not work: a Wi-Fi router does not accept additional MAC addresses from one client.",
"Carte réseau filaire reliée au routeur. Le Wi-Fi ne fonctionne pas : un routeur Wi-Fi n'accepte pas d'adresses MAC supplémentaires d'un client.",
"Kabel-Netzwärkcharte, wo am Router hanget. WLAN gaht nöd: En WLAN-Router nimmt vo eim Client kei zuesätzlichi MAC-Adrässe aa."
],
"Jeder Server bekommt eine eigene MAC (Hersteller-Kennung) und holt sich seine Adresse vom Router; er erscheint dort als eigenes Gerät. Dieser PC erreicht die Server über einen eigenen Labor-Anschluss „bmcshim“ (bekommt ebenfalls eine Adresse vom Router).": [
"Each server gets its own MAC (vendor prefix) and obtains its address from the router; it appears there as a device of its own. This PC reaches the servers through its own lab port “bmcshim” (which also gets an address from the router).",
"Chaque serveur reçoit sa propre MAC (préfixe fabricant) et obtient son adresse du routeur ; il y apparaît comme appareil distinct. Ce PC joint les serveurs par son propre accès labo « bmcshim » (qui reçoit aussi une adresse du routeur).",
"Jede Server überchunnt e eigeti MAC (Herställer-Kennig) und holt sich sini Adrässe vom Router; er erschiint det als eiges Grät. Dä PC erreicht d Server über en eigete Labor-Aaschluss „bmcshim“ (überchunnt au e Adrässe vom Router)."
],
"Hersteller des virtuellen Servers": [
"Vendor of the virtual server",
"Fabricant du serveur virtuel",
"Herställer vom virtuelle Server"
],
"Firmware-Generation. „iDRAC8, Firmware zu alt“ und iLO 3/4 prüfen die Sonderwege von BMC-Steward.": [
"Firmware generation. “iDRAC8, firmware too old” and iLO 3/4 test BMC-Steward's special paths.",
"Génération de firmware. « iDRAC8, firmware trop ancien » et iLO 3/4 testent les chemins particuliers de BMC-Steward.",
"Firmware-Generation. „iDRAC8, Firmware z alt“ und iLO 3/4 prüefed d Sonderwäg vo BMC-Steward."
],
"Ab Werk per DHCP oder mit fester Werks-IP (z. B. Dell 192.168.0.120). Nur Konfigraum/Router.": [
"Factory default via DHCP or with a static factory IP (e.g. Dell 192.168.0.120). Config room/Router only.",
"D'usine par DHCP ou avec une IP d'usine fixe (p. ex. Dell 192.168.0.120). Salle de config/Routeur uniquement.",
"Ab Wärk per DHCP oder mit feschter Werks-IP (z. B. Dell 192.168.0.120). Nume Konfigruum/Router."
],
"Feste Werks-IP (nur bei „Feste IP“)": [
"Static factory IP (only with “Static IP”)",
"IP d'usine fixe (seulement avec « IP fixe »)",
"Feschti Werks-IP (nume bi „Feschti IP“)"
],
"Werksbenutzer, mit dem BMC-Steward sich anmeldet": [
"Factory user BMC-Steward logs in with",
"Utilisateur d'usine avec lequel BMC-Steward se connecte",
"Werksbenutzer, mit dem sich BMC-Steward aamäldet"
],
"Werkspasswort (wie auf dem Aufkleber). Steht beim Übernehmen je Zeile in „Neuware“.": [
"Factory password (as on the sticker). Is entered per row in “New hardware” when added.",
"Mot de passe d'usine (comme sur l'étiquette). Saisi par ligne dans « Nouveau matériel » lors de la reprise.",
"Werkspasswort (wie uf em Chläber). Staht bim Übernäh je Zile i „Neuwar“."
],
"Leer = Standardmodell der Generation": [
"Empty = default model of the generation",
"Vide = modèle par défaut de la génération",
"Leer = Standardmodell vo de Generation"
],
"Bestimmt auch die MAC-Adresse: gleiche Seriennummer = gleiche MAC = gleiche DHCP-Adresse.": [
"Also determines the MAC address: same serial number = same MAC = same DHCP address.",
"Détermine aussi l'adresse MAC : même numéro de série = même MAC = même adresse DHCP.",
"Bestimmt au d MAC-Adrässe: gliichi Seriennummere = gliichi MAC = gliichi DHCP-Adrässe."
],
"Nur Dell: dieser User-Slot ist schon von einem anderen Benutzer belegt (z. B. 3). BMC-Steward darf ihn nicht überschreiben.": [
"Dell only: this user slot is already taken by another user (e.g. 3). BMC-Steward must not overwrite it.",
"Dell uniquement : cet emplacement utilisateur est déjà occupé par un autre utilisateur (p. ex. 3). BMC-Steward ne doit pas l'écraser.",
"Nume Dell: dä User-Slot isch scho vomene andere Benutzer beleit (z. B. 3). BMC-Steward darf en nöd überschriibe."
],
"Je Hersteller einer": [
"One per vendor",
"Un par fabricant",
"Je Herställer eine"
],
"Entfernen": [
"Remove",
"Supprimer",
"Entferne"
],
"Zufällige Werkspasswörter": [
"Random factory passwords",
"Mots de passe d'usine aléatoires",
"Zuefälligi Werkspasswörter"
],
"Setzt für die markierten Zeilen (ohne Markierung: alle) zufällige Passwörter im Stil der Aufkleber, wie bei neuen Geräten.": [
"Sets random sticker-style passwords for the selected rows (no selection: all), as on new devices.",
"Définit des mots de passe aléatoires de style étiquette pour les lignes sélectionnées (sans sélection : toutes), comme sur les appareils neufs.",
"Setzt für di markierte Zile (ohni Markierig: alli) zuefälligi Passwörter im Stil vo de Chläber, wie bi neue Grät."
],
"Szenario laden …": [
"Load scenario …",
"Charger un scénario …",
"Szenario lade …"
],
"Szenario speichern …": [
"Save scenario …",
"Enregistrer le scénario …",
"Szenario spichere …"
],
"Speichert Modus, Netz und alle Server (inkl. Seriennummern und Werkspasswörter) als JSON, um den Aufbau genau so nachzustellen.": [
"Saves mode, network and all servers (incl. serial numbers and factory passwords) as JSON to recreate exactly this setup.",
"Enregistre le mode, le réseau et tous les serveurs (y compris numéros de série et mots de passe d'usine) en JSON pour recréer exactement ce montage.",
"Spicheret Modus, Netz und alli Server (inkl. Seriennummere und Werkspasswörter) als JSON, zum dä Ufbau genau so nachestelle."
],
"Laufende Server": [
"Running servers",
"Serveurs en marche",
"Laufendi Server"
],
"In Serverliste übernehmen („Neuware“)": [
"Add to server list (“New hardware”)",
"Reprendre dans la liste (« Nouveau matériel »)",
"I Serverlischte übernäh („Neuwar“)"
],
"Trägt die markierten (ohne Markierung: alle) laufenden Server mit ihrer aktuellen Adresse und ihrem Werks-Passwort in die Serverliste ein und schlägt neue Adressen vor.": [
"Enters the selected (no selection: all) running servers with their current address and factory password into the server list and suggests new addresses.",
"Saisit les serveurs en marche sélectionnés (sans sélection : tous) avec leur adresse actuelle et leur mot de passe d'usine dans la liste et propose de nouvelles adresses.",
"Treit di markierte (ohni Markierig: alli) laufende Server mit ihrer aktuelle Adrässe und ihrem Werkspasswort i d Serverlischte ii und schlaat neui Adrässe vor."
],
"Nur im Loopback-Modus. Echtes Netz: Gateway/Subnetz werden erkannt und die neue IP mit deinem Präfix vorgefüllt (nur Trockenlauf). Loopback: die neue IP bleibt auf 127.x:Port, damit auch ein scharfer Lauf grün durchläuft.": [
"Loopback mode only. Real network: gateway/subnet are detected and the new IP is prefilled with your prefix (dry run only). Loopback: the new IP stays on 127.x:port so that a live run also completes green.",
"Mode loopback uniquement. Réseau réel : passerelle/sous-réseau détectés et nouvelle IP préremplie avec votre préfixe (simulation seulement). Loopback : la nouvelle IP reste sur 127.x:port pour qu'une exécution réelle passe aussi au vert.",
"Nume im Loopback-Modus. Ächts Netz: Gateway/Subnetz wärded erkännt und di neu IP mit dim Präfix vorgfüllt (nume Trochelauf). Loopback: di neu IP blibt uf 127.x:Port, damit au en schaarfe Lauf grüen durelauft."
],
"Szenario speichern": [
"Save scenario",
"Enregistrer le scénario",
"Szenario spichere"
],
"Szenario (*.json)": [
"Scenario (*.json)",
"Scénario (*.json)",
"Szenario (*.json)"
],
"Bitte mindestens einen Server hinzufügen (oder ein Szenario laden).": [
"Please add at least one server (or load a scenario).",
"Veuillez ajouter au moins un serveur (ou charger un scénario).",
"Bitte mindeschtens ein Server dezuefüege (oder es Szenario lade)."
],
"Router: bitte die Kabel-Netzwerkkarte wählen.": [
"Router: please choose the wired network card.",
"Routeur : veuillez choisir la carte réseau filaire.",
"Router: bitte d Kabel-Netzwärkcharte wähle."
],
"Cluster wird gestoppt (Adressen werden freigegeben) …": [
"Stopping cluster (releasing addresses) …",
"Arrêt du cluster (libération des adresses) …",
"Cluster wird gstoppt (Adrässe wärded freigäh) …"
],
"Generation": [
"Generation",
"Génération",
"Generation"
],
"Werks-Netz": [
"Factory network",
"Réseau d'usine",
"Werks-Netz"
],
"Werks-IP": [
"Factory IP",
"IP d'usine",
"Werks-IP"
],
"Werks-Benutzer": [
"Factory user",
"Utilisateur d'usine",
"Werks-Benutzer"
],
"Werks-Passwort": [
"Factory password",
"Mot de passe d'usine",
"Werks-Passwort"
],
"Modell": [
"Model",
"Modèle",
"Modell"
],
"Belegter Slot": [
"Occupied slot",
"Emplacement occupé",
"Beleite Slot"
],
"Einen virtuellen Server dieses Herstellers hinzufügen": [
"Add a virtual server of this vendor",
"Ajouter un serveur virtuel de ce fabricant",
"En virtuelle Server vo dem Herställer dezuefüege"
],
"Nr": [
"No.",
"N°",
"Nr"
],
"Modell · Seriennummer": [
"Model · serial number",
"Modèle · numéro de série",
"Modell · Seriennummere"
],
"MAC": [
"MAC",
"MAC",
"MAC"
],
"Zustand": [
"State",
"État",
"Zuestand"
],
"Werks-Login": [
"Factory login",
"Identifiant d'usine",
"Werks-Login"
],
"Kabel": [
"Cable",
"Câble",
"Kabel"
],
"Bereit. Server hinzufügen (oder Szenario laden) und „Cluster starten“.": [
"Ready. Add servers (or load a scenario) and “Start cluster”.",
"Prêt. Ajoutez des serveurs (ou chargez un scénario) et « Démarrer le cluster ».",
"Parat. Server dezuefüege (oder Szenario lade) und „Cluster starte“."
],
"Szenario laden": [
"Load scenario",
"Charger un scénario",
"Szenario lade"
],
"Szenario (*.json);;Alle Dateien (*)": [
"Scenario (*.json);;All files (*)",
"Scénario (*.json);;Tous les fichiers (*)",
"Szenario (*.json);;Alli Dateie (*)"
],
"Szenario geladen: {0} Server": [
"Scenario loaded: {0} servers",
"Scénario chargé : {0} serveurs",
"Szenario glade: {0} Server"
],
"Konfigraum: Adresse dieses PCs fehlt, z. B. 192.168.0.1/24.": [
"Config room: address of this PC missing, e.g. 192.168.0.1/24.",
"Salle de config : adresse de ce PC manquante, p. ex. 192.168.0.1/24.",
"Konfigruum: Adrässe vo dem PC fählt, z. B. 192.168.0.1/24."
],
"Für diesen Modus sind Root-Rechte nötig; pkexec ist nicht verfügbar. Bitte im Terminal starten:": [
"This mode needs root rights; pkexec is not available. Please start in a terminal:",
"Ce mode nécessite les droits root ; pkexec n'est pas disponible. Veuillez lancer dans un terminal :",
"Für dä Modus bruuchts Root-Rächt; pkexec isch nöd verfüegbar. Bitte im Terminal starte:"
],
"Cluster startet …": [
"Cluster starting …",
"Démarrage du cluster …",
"Cluster startet …"
],
"Cluster startet … (Rechteabfrage bestätigen)": [
"Cluster starting … (confirm the privilege prompt)",
"Démarrage du cluster … (confirmez la demande de droits)",
"Cluster startet … (Rächteabfrog bestätige)"
],
"Rechteabfrage abgebrochen, der Cluster wurde nicht gestartet.": [
"Privilege prompt cancelled, the cluster was not started.",
"Demande de droits annulée, le cluster n'a pas démarré.",
"Rächteabfrog abbroche, de Cluster isch nöd gstartet worde."
],
"Neue IP im Staging-Netz vorgeschlagen; Gateway des Zielnetzes noch eintragen.": [
"New IP suggested in the staging network; still enter the gateway of the target network.",
"Nouvelle IP proposée dans le réseau de staging ; saisissez encore la passerelle du réseau cible.",
"Neui IP im Staging-Netz vorgschlage; Gateway vom Zielnetz no iitrage."
],
"keine IPv4": [
"no IPv4",
"pas d'IPv4",
"kei IPv4"
],
"(kein Kabel)": [
"(no cable)",
"(pas de câble)",
"(kei Kabel)"
],
"Szenario gespeichert: {0}": [
"Scenario saved: {0}",
"Scénario enregistré : {0}",
"Szenario gspicheret: {0}"
],
"Protokoll geöffnet: {0}": [
"Log opened: {0}",
"Journal ouvert : {0}",
"Protokoll göffnet: {0}"
],
"(geändert)": [
"(changed)",
"(modifié)",
"(gänderet)"
],
"({0} unbekannte Einträge übersprungen)": [
"({0} unknown entries skipped)",
"({0} entrées inconnues ignorées)",
"({0} unbekannti Iiträg übersprunge)"
],
"Kein Standardprogramm für Textdateien gefunden.\nDatei liegt unter {0}": [
"No default program for text files found.\nThe file is at {0}",
"Aucun programme par défaut pour les fichiers texte.\nLe fichier se trouve sous {0}",
"Kei Standardprogramm für Textdateie gfunde.\nD Datei ligt under {0}"
],
"Kabel ziehen": [
"Unplug cable",
"Débrancher le câble",
"Kabel zieh"
],
"Kabel stecken": [
"Plug in cable",
"Brancher le câble",
"Kabel stecke"
],
"Netz erkannt (Gateway {0}, Maske {1}); bei „Neue IP“ nur noch die letzte Ziffer eintragen. Ein scharfer Lauf geht in diesem Übungsmodus nicht.": [
"Network detected (gateway {0}, mask {1}); for “New IP” just enter the last digit. A live run is not possible in this practice mode.",
"Réseau détecté (passerelle {0}, masque {1}) ; pour « Nouvelle IP », saisissez juste le dernier chiffre. Une exécution réelle n'est pas possible dans ce mode d'exercice.",
"Netz erkännt (Gateway {0}, Maske {1}); bi „Neui IP“ nume no di letscht Ziffer iitrage. En schaarfe Lauf gaht i dem Üebigsmodus nöd."
],
"Router-Netz übernommen (Gateway {0}); bei „Neue IP“ eine freie letzte Ziffer außerhalb des DHCP-Bereichs des Routers eintragen.": [
"Router network applied (gateway {0}); for “New IP” enter a free last digit outside the router's DHCP range.",
"Réseau du routeur repris (passerelle {0}) ; pour « Nouvelle IP », saisissez un dernier chiffre libre hors de la plage DHCP du routeur.",
"Router-Netz übernoh (Gateway {0}); bi „Neui IP“ e freii letschti Ziffer usserhalb vom DHCP-Beriich vom Router iitrage."
],
"Zeile {0}: Werks-Passwort fehlt.": [
"Row {0}: factory password missing.",
"Ligne {0} : mot de passe d'usine manquant.",
"Zile {0}: Werks-Passwort fählt."
],
"Zeile {0}: Seriennummer {1} kommt doppelt vor (sie bestimmt die MAC-Adresse).": [
"Row {0}: serial number {1} occurs twice (it determines the MAC address).",
"Ligne {0} : le numéro de série {1} apparaît deux fois (il détermine l'adresse MAC).",
"Zile {0}: Seriennummere {1} chunnt doppelt vor (si bestimmt d MAC-Adrässe)."
],
"Cluster beendet mit Fehler: {0}": [
"Cluster ended with error: {0}",
"Cluster terminé avec une erreur : {0}",
"Cluster beändet mit Fehler: {0}"
],
"Cluster läuft: {0} von {1} Servern haben eine Adresse. Die übrigen warten auf DHCP - in „DHCP-Server“ auf „bmclab0“ den DHCP-Server starten.": [
"Cluster running: {0} of {1} servers have an address. The others wait for DHCP – start the DHCP server on “bmclab0” in “DHCP server”.",
"Cluster en marche : {0} serveurs sur {1} ont une adresse. Les autres attendent le DHCP – lancez le serveur DHCP sur « bmclab0 » dans « Serveur DHCP ».",
"Cluster lauft: {0} vo {1} Server händ e Adrässe. Di übrige warted uf DHCP – i „DHCP-Server“ uf „bmclab0“ dä DHCP-Server starte."
],
"{0} Server übernommen, Werks-Passwörter stehen schon in der Liste.": [
"{0} servers added, factory passwords are already in the list.",
"{0} serveurs repris, les mots de passe d'usine sont déjà dans la liste.",
"{0} Server übernoh, Werks-Passwörter stönd scho i de Lischte."
],
"Konfigraum, Adresse dieses PCs: {0}": [
"Config room, address of this PC: {0}",
"Salle de config, adresse de ce PC : {0}",
"Konfigruum, Adrässe vo dem PC: {0}"
],
"Cluster läuft: {0} von {1} Servern haben eine Adresse, die übrigen warten auf DHCP.": [
"Cluster running: {0} of {1} servers have an address, the others wait for DHCP.",
"Cluster en marche : {0} serveurs sur {1} ont une adresse, les autres attendent le DHCP.",
"Cluster lauft: {0} vo {1} Server händ e Adrässe, di übrige warted uf DHCP."
],
"Cluster läuft: {0} Server erreichbar. Übernehmen in „Neuware“ oder dort selbst eintragen.": [
"Cluster running: {0} servers reachable. Add them in “New hardware” or enter them there yourself.",
"Cluster en marche : {0} serveurs joignables. Reprenez-les dans « Nouveau matériel » ou saisissez-les vous-même.",
"Cluster lauft: {0} Server erreichbar. Übernäh i „Neuwar“ oder det sälber iitrage."
],
"Zeile {0}: Belegter Slot „{1}“ - erlaubt sind 3 bis 16.": [
"Row {0}: occupied slot “{1}” – allowed are 3 to 16.",
"Ligne {0} : emplacement occupé « {1} » – autorisé de 3 à 16.",
"Zile {0}: Beleite Slot „{1}“ – erlaubt sind 3 bis 16."
],
"Zeile {0}: Werks-IP „{1}“ ist keine gültige IPv4-Adresse.": [
"Row {0}: factory IP “{1}” is not a valid IPv4 address.",
"Ligne {0} : l'IP d'usine « {1} » n'est pas une adresse IPv4 valide.",
"Zile {0}: Werks-IP „{1}“ isch kei gültigi IPv4-Adrässe."
],
"  GESPERRT (Eigenschutz): Adresse {0} steht in der Schutzliste. Nichts geändert.": [
"  BLOCKED (self-protection): address {0} is on the protection list. Nothing changed.",
"  BLOQUÉ (auto-protection) : l'adresse {0} figure dans la liste de protection. Rien n'a été modifié.",
"  GSPERRT (Eigeschutz): Adrässe {0} staht i de Schutzlischte. Nüt gänderet."
],
"  Eigenschutz: Identität im Trockenlauf nicht geprüft (Gerät nicht erreichbar).": [
"  Self-protection: identity not checked in dry run (device not reachable).",
"  Auto-protection : identité non vérifiée en simulation (appareil injoignable).",
"  Eigeschutz: Identität im Trochelauf nöd prüeft (Grät nöd erreichbar)."
],
"  Eigenschutz: Identität nicht lesbar; im scharfen Lauf würde dieser Server übersprungen.": [
"  Self-protection: identity not readable; a live run would skip this server.",
"  Auto-protection : identité illisible ; une exécution réelle ignorerait ce serveur.",
"  Eigeschutz: Identität nöd läsbar; im schaarfe Lauf würd dä Server übersprunge."
],
"  GESPERRT (Eigenschutz): Identität des Servers nicht lesbar, aus Sicherheitsgründen übersprungen.": [
"  BLOCKED (self-protection): server identity not readable, skipped for safety.",
"  BLOQUÉ (auto-protection) : identité du serveur illisible, ignoré par sécurité.",
"  GSPERRT (Eigeschutz): Identität vom Server nöd läsbar, us Sicherheitsgründ übersprunge."
],
"  GESPERRT (Eigenschutz): {0} ist der Rechner, auf dem BMC-Steward läuft (Treffer: {1}). Nichts geändert.": [
"  BLOCKED (self-protection): {0} is the machine BMC-Steward runs on (match: {1}). Nothing changed.",
"  BLOQUÉ (auto-protection) : {0} est la machine qui exécute BMC-Steward (correspondance : {1}). Rien n'a été modifié.",
"  GSPERRT (Eigeschutz): {0} isch dä Rächner, wo BMC-Steward druf lauft (Träffer: {1}). Nüt gänderet."
],
"  Warnung: BMC-Steward läuft in einer VM ({0}), die Schutzliste ist leer. Der eigene Host wird nicht erkannt.": [
"  Warning: BMC-Steward runs in a VM ({0}) and the protection list is empty. Its own host cannot be recognised.",
"  Attention : BMC-Steward tourne dans une VM ({0}) et la liste de protection est vide. Son propre hôte n'est pas reconnu.",
"  Warnig: BMC-Steward lauft innere VM ({0}), d Schutzlischte isch leer. Dä eiget Host wird nöd erkennt."
],
"Eigenschutz: Neustart gesperrt.": [
"Self-protection: restart blocked.",
"Auto-protection : redémarrage bloqué.",
"Eigeschutz: Neustart gsperrt."
],
"⚠ Eigenschutz: Schutzliste leer": [
"⚠ Self-protection: list empty",
"⚠ Auto-protection : liste vide",
"⚠ Eigeschutz: Schutzlischte leer"
],
"BMC-Steward läuft in einer VM ({0}). Den Host dieser VM in die Schutzliste eintragen, sonst kann er versehentlich neu gestartet werden.": [
"BMC-Steward runs in a VM ({0}). Add this VM's host to the protection list, otherwise it could be restarted by mistake.",
"BMC-Steward tourne dans une VM ({0}). Ajoutez l'hôte de cette VM à la liste de protection, sinon il pourrait être redémarré par erreur.",
"BMC-Steward lauft innere VM ({0}). Dä Host vo dere VM i d Schutzlischte iitrage, suscht chan er us Versehe neu gstartet werde."
],
"Eigenschutz ({0})": [
"Self-protection ({0})",
"Auto-protection ({0})",
"Eigeschutz ({0})"
],
"Server, die BMC-Steward nie neu startet oder mit Firmware bespielt.": [
"Servers that BMC-Steward never restarts or flashes.",
"Serveurs que BMC-Steward ne redémarre ni ne flashe jamais.",
"Server, wo BMC-Steward nie neu startet oder mit Firmware bespielt."
],
"ACHTUNG: BMC-Steward läuft in einer VM ({0}) und die Schutzliste ist leer. Steht der Host dieser VM in der Liste, schaltet sich das Tool mitten im Lauf selbst ab. Vorher unter „Eigenschutz“ eintragen.": [
"WARNING: BMC-Steward runs in a VM ({0}) and the protection list is empty. If this VM's host is in the list, the tool shuts itself down in the middle of the run. Add it under “Self-protection” first.",
"ATTENTION : BMC-Steward tourne dans une VM ({0}) et la liste de protection est vide. Si l'hôte de cette VM est dans la liste, l'outil s'arrête lui-même en pleine exécution. Ajoutez-le d'abord sous « Auto-protection ».",
"ACHTIG: BMC-Steward lauft innere VM ({0}) und d Schutzlischte isch leer. Staht dä Host vo dere VM i de Lischte, schaltet sich s Tool mitts im Lauf sälber ab. Vorher under „Eigeschutz“ iitrage."
],
"Eigenschutz": [
"Self-protection",
"Auto-protection",
"Eigeschutz"
],
"BMC-Steward läuft in einer virtuellen Maschine ({0}). Die VM kennt ihren Host nicht: Tragen Sie hier alle Hosts ein, auf denen diese VM laufen kann (z. B. den ganzen Management-Cluster).": [
"BMC-Steward runs in a virtual machine ({0}). The VM does not know its host: enter every host this VM can run on (e.g. the whole management cluster).",
"BMC-Steward tourne dans une machine virtuelle ({0}). La VM ne connaît pas son hôte : saisissez tous les hôtes sur lesquels elle peut tourner (p. ex. tout le cluster de gestion).",
"BMC-Steward lauft innere virtuelle Maschine ({0}). D VM kennt ihre Host nöd: Trag da alli Hosts ii, wo die VM druf laufe chan (z. B. dä ganz Management-Cluster)."
],
"BMC-Steward läuft auf echter Hardware; die eigene Kennung wurde erkannt und ist automatisch geschützt ({0} Werte).": [
"BMC-Steward runs on real hardware; its own identity was detected and is protected automatically ({0} values).",
"BMC-Steward tourne sur du matériel réel ; sa propre identité a été détectée et est protégée automatiquement ({0} valeurs).",
"BMC-Steward lauft uf ächter Hardware; di eigeti Kennig isch erkennt und automatisch gschützt ({0} Wärt)."
],
"Eigene Kennung nicht lesbar (unter Linux braucht das Root-Rechte). Läuft BMC-Steward auf einem Server, dessen BMC in der Liste stehen könnte, diesen hier eintragen.": [
"Own identity not readable (on Linux this needs root). If BMC-Steward runs on a server whose BMC could be in the list, enter it here.",
"Identité propre illisible (sous Linux, il faut les droits root). Si BMC-Steward tourne sur un serveur dont le BMC pourrait être dans la liste, saisissez-le ici.",
"Eigeti Kennig nöd läsbar (under Linux bruchts Root-Rächt). Lauft BMC-Steward uf emne Server, wo sin BMC i de Lischte chönnt stah, dä da iitrage."
],
"Je Zeile ein Eintrag: Seriennummer bzw. Service-Tag, System-UUID oder BMC-Adresse. Diese Server werden nie neu gestartet, bekommen keine UEFI-Änderungen und keine Firmware. Im scharfen Lauf wird außerdem jeder Server übersprungen, dessen Identität nicht lesbar ist.": [
"One entry per line: serial number or service tag, system UUID or BMC address. These servers are never restarted and get no UEFI changes and no firmware. In a live run, every server whose identity cannot be read is skipped as well.",
"Une entrée par ligne : numéro de série ou service tag, UUID système ou adresse BMC. Ces serveurs ne sont jamais redémarrés et ne reçoivent ni modification UEFI ni firmware. En exécution réelle, tout serveur dont l'identité est illisible est également ignoré.",
"Pro Zile en Iitrag: Seriennummere bzw. Service-Tag, System-UUID oder BMC-Adrässe. Die Server wärded nie neu gstartet, überchömed kei UEFI-Änderige und kei Firmware. Im schaarfe Lauf wird usserdem jede Server übersprunge, wo sini Identität nöd läsbar isch."
],
"{0} Server übernommen. Netz erkannt (Gateway {1}, Maske {2}); bei „Neue IP“ nur noch die letzte Ziffer eintragen. Werks-Login(s): {3}.": [
"{0} servers added. Network detected (gateway {1}, mask {2}); for “New IP” just enter the last digit. Factory login(s): {3}.",
"{0} serveurs ajoutés. Réseau détecté (passerelle {1}, masque {2}) ; pour « Nouvelle IP », saisissez juste le dernier chiffre. Identifiant(s) d'usine : {3}.",
"{0} Server übernoh. Netz erkennt (Gateway {1}, Maske {2}); bi „Neui IP“ nume no di letscht Ziffer iitrage. Werks-Login(s): {3}."
],
"{0} Server übernommen (Loopback). Oben neuen Benutzer/Passwort setzen. Werks-Login(s): {1}.": [
"{0} servers added (loopback). Set the new user/password above. Factory login(s): {1}.",
"{0} serveurs ajoutés (loopback). Renseignez le nouvel utilisateur/mot de passe ci-dessus. Identifiant(s) d'usine : {1}.",
"{0} Server übernoh (Loopback). Obe de neu Benutzer/Passwort setze. Werks-Login(s): {1}."
],
"Neue IP: echtes Netz (Router) – zum Üben": [
"New IP: real network (router) – for practice",
"Nouvelle IP : réseau réel (routeur) – pour s'entraîner",
"Neui IP: echts Netz (Router) – zum Üebe"
],
"Neue IP: Loopback – scharfe Migration läuft grün": [
"New IP: loopback – live migration passes green",
"Nouvelle IP : loopback – la migration réelle passe au vert",
"Neui IP: Loopback – scharfi Migration lauft grüen"
],
"Echtes Netz: Gateway/Subnetz werden erkannt und die neue IP mit deinem Präfix vorgefüllt (ideal für Trockenlauf). Loopback: die neue IP bleibt auf 127.x:Port, damit auch ein scharfer Lauf grün durchläuft.": [
"Real network: gateway/subnet are detected and the new IP is prefilled with your prefix (ideal for a dry run). Loopback: the new IP stays on 127.x:port so a live run also passes green.",
"Réseau réel : la passerelle/le sous-réseau sont détectés et la nouvelle IP est préremplie avec votre préfixe (idéal pour une simulation). Loopback : la nouvelle IP reste sur 127.x:port pour qu'un vrai run passe aussi au vert.",
"Echts Netz: Gateway/Subnetz werdet erkennt und d neui IP wird mit dim Präfix vorgfüllt (ideal für de Troggelauf). Loopback: d neui IP bliibt uf 127.x:Port, dass au en scharfe Lauf grüen durelauft."
],
"{0} Server übernommen. Netz erkannt (Gateway {1}, Maske {2}); bei „Neue IP“ nur noch die letzte Ziffer eintragen und oben Benutzer/Passwort setzen.": [
"{0} servers added. Network detected (gateway {1}, mask {2}); for “New IP” just enter the last digit and set user/password above.",
"{0} serveurs ajoutés. Réseau détecté (passerelle {1}, masque {2}) ; pour « Nouvelle IP », saisissez juste le dernier chiffre et renseignez l'utilisateur/mot de passe ci-dessus.",
"{0} Server übernoh. Netz erkennt (Gateway {1}, Maske {2}); bi „Neui IP“ nume no di letscht Ziffer iitrage und obe Benutzer/Passwort setze."
],
"{0} Server übernommen (kein echtes Netz erkannt, Loopback genutzt). Oben Benutzer/Passwort setzen und testen.": [
"{0} servers added (no real network detected, using loopback). Set user/password above and test.",
"{0} serveurs ajoutés (aucun réseau réel détecté, loopback utilisé). Renseignez l'utilisateur/mot de passe ci-dessus et testez.",
"{0} Server übernoh (kei echts Netz erkennt, Loopback bruucht). Obe Benutzer/Passwort setze und teste."
],
"Adressen in Serverliste übernehmen („Neuware“)": ["Add addresses to the server list (“New hardware”)", "Ajouter les adresses à la liste des serveurs (« Matériel neuf »)", "Adrässe i d Serverliste übernäh („Neuware“)"],
"Trägt die laufenden virtuellen Server mit Adresse (inkl. :Port) und Werks-Passwort in die Serverliste ein und schlägt neue Adressen vor.": [
"Enters the running virtual servers into the server list with their address (incl. :port) and factory password, and suggests new addresses.",
"Ajoute les serveurs virtuels en cours à la liste avec leur adresse (y c. :port) et le mot de passe d'usine, et propose de nouvelles adresses.",
"Treit di laufende virtuelle Server mit Adrässe (inkl. :Port) und Werks-Passwort i d Liste ii und schlaht neui Adrässe vor."
],
"{0} virtuelle Server übernommen. Oben Benutzer/Passwort setzen und mit Trockenlauf testen.": [
"{0} virtual servers added. Set user/password above and test with a dry run.",
"{0} serveurs virtuels ajoutés. Renseignez l'utilisateur/mot de passe ci-dessus et testez en simulation.",
"{0} virtuelli Server übernoh. Obe Benutzer/Passwort setze und mit em Troggelauf teste."
],
"Keine gültige IPv4-Adresse (optional mit :Port)": [
"Not a valid IPv4 address (optional :port)",
"Adresse IPv4 non valide (port facultatif après « : »)",
"Kei gültigi IPv4-Adrässe (optional mit :Port)"
],
# --- Firmware- und Testlabor-Reiter ---
"Das Update läuft über alle Server der Serverliste („Neuware“); grüne unter ihrer neuen Adresse. Angemeldet wird mit dem Werksbenutzer.": ["The update runs on every server in the list (“New hardware”); green ones at their new address. Login uses the factory user.", "La mise à jour s'exécute sur tous les serveurs de la liste (« Matériel neuf ») ; les verts à leur nouvelle adresse. Connexion avec l'utilisateur d'usine.", "Es Update lauft über alli Server vo de Liste („Neuware“); grüeni under ihrer neue Adrässe. Aagmäldet wird mit em Werks-Benutzer."],
"Firmware-Inventar (Referenzgerät)": [
"Firmware inventory (reference device)",
"Inventaire du firmware (appareil de référence)",
"Firmware-Inventar (Referänzgrät)"
],
"Liest nur lesend die vorhandenen Komponenten und Versionen eines Geräts. Eine Auswahl in der Tabelle bestimmt, welche Komponenten das Update treffen soll (nichts markiert = der Hersteller entscheidet selbst).": [
"Reads, read-only, the components and versions present on a device. A selection in the table decides which components the update targets (nothing selected = the vendor decides).",
"Lit, en lecture seule, les composants et versions présents sur un appareil. Une sélection dans le tableau détermine les composants visés par la mise à jour (rien de sélectionné = le fabricant décide).",
"Liest nume läsend d Komponänte und Versione vomene Grät. E Uswahl i de Tabälle bestimmt, weli Komponänte s Update trifft (nüt markiert = de Herschteller entscheidet sälber)."
],
"Adresse eines Geräts zum Auslesen. Vorschläge aus der Serverliste („Neuware“).": ["Address of a device to read from. Suggestions from the server list (“New hardware”).", "Adresse d'un appareil à lire. Suggestions depuis la liste des serveurs (« Matériel neuf »).", "Adrässe vomene Grät zum Uslääse. Vorschläg us de Serverliste („Neuware“)."],
"Inventar lesen": [
"Read inventory",
"Lire l'inventaire",
"Inventar läse"
],
"Firmware-Quelle": [
"Firmware source",
"Source du firmware",
"Firmware-Quälle"
],
"Datei hochladen (Multipart-/HTTP-Push)": [
"Upload file (multipart/HTTP push)",
"Téléverser un fichier (push multipart/HTTP)",
"Datei ufelade (Multipart-/HTTP-Push)"
],
"Von URL laden (SimpleUpdate)": [
"Load from URL (SimpleUpdate)",
"Charger depuis une URL (SimpleUpdate)",
"Vo URL lade (SimpleUpdate)"
],
"Firmware-Datei wählen …": [
"Choose firmware file …",
"Choisir un fichier firmware …",
"Firmware-Datei uswähle …"
],
"z. B. http://10.20.0.9/firmware/idrac_7.10.exe": [
"e.g. http://10.20.0.9/firmware/idrac_7.10.exe",
"p. ex. http://10.20.0.9/firmware/idrac_7.10.exe",
"z. B. http://10.20.0.9/firmware/idrac_7.10.exe"
],
"URL": [
"URL",
"URL",
"URL"
],
"Der BMC lädt die Datei selbst von dieser Adresse.": [
"The BMC downloads the file from this address itself.",
"Le BMC télécharge lui-même le fichier depuis cette adresse.",
"De BMC lädt d Datei sälber vo dere Adrässe."
],
"Protokoll": [
"Log",
"Journal",
"Protokoll"
],
"Übertragungsweg, mit dem der BMC die Datei holt.": [
"Transfer method the BMC uses to fetch the file.",
"Méthode de transfert utilisée par le BMC pour récupérer le fichier.",
"Übertragigswäg, mit dem de BMC d Datei holt."
],
"Sofort übernehmen (BMC startet ggf. neu)": [
"Apply immediately (BMC may restart)",
"Appliquer immédiatement (le BMC peut redémarrer)",
"Sofort übernäh (BMC startet eventuell neu)"
],
"Erst beim nächsten Neustart": [
"Only on the next restart",
"Seulement au prochain redémarrage",
"Erst bim nächste Neustart"
],
"Übernahme": [
"Apply",
"Application",
"Übernahm"
],
"Sofort: der BMC flasht und startet ggf. neu. Beim Neustart: wird nur hochgeladen und erst beim nächsten Neustart wirksam.": [
"Immediately: the BMC flashes and may restart. On restart: only uploaded, takes effect at the next restart.",
"Immédiatement : le BMC flashe et peut redémarrer. Au redémarrage : seulement téléversé, actif au prochain redémarrage.",
"Sofort: de BMC flasht und startet eventuell neu. Bim Neustart: wird nume ufeglade und erst bim nächste Neustart wirksam."
],
"Achtung: Firmware-Updates sind heikel. Ein unterbrochener BMC-Flash kann einen Server unbrauchbar machen. Nur passende Pakete des Herstellers verwenden, Strom sichern und immer zuerst ein einzelnes Gerät testen.": [
"Caution: firmware updates are delicate. An interrupted BMC flash can brick a server. Use only matching vendor packages, secure the power, and always test a single device first.",
"Attention : les mises à jour de firmware sont délicates. Un flash du BMC interrompu peut rendre un serveur inutilisable. N'utilisez que des paquets adaptés du fabricant, sécurisez l'alimentation et testez toujours un seul appareil d'abord.",
"Achtung: Firmware-Updates sind heikel. En underbrochene BMC-Flash cha en Server unbruuchbar mache. Nume passendi Pakete vom Herschteller näh, de Strom absichere und immer zerscht ei einzigs Grät teste."
],
"Firmware-Datei wählen": [
"Choose firmware file",
"Choisir un fichier firmware",
"Firmware-Datei uswähle"
],
"Firmware (*.exe *.bin *.fwpkg *.rom *.d7 *.pm *.pldm *.img);;Alle Dateien (*)": [
"Firmware (*.exe *.bin *.fwpkg *.rom *.d7 *.pm *.pldm *.img);;All files (*)",
"Firmware (*.exe *.bin *.fwpkg *.rom *.d7 *.pm *.pldm *.img);;Tous les fichiers (*)",
"Firmware (*.exe *.bin *.fwpkg *.rom *.d7 *.pm *.pldm *.img);;Alli Dateie (*)"
],
"Lese Inventar …": [
"Reading inventory …",
"Lecture de l'inventaire …",
"Lise s Inventar …"
],
"Komponente": [
"Component",
"Composant",
"Komponänte"
],
"Version": [
"Version",
"Version",
"Version"
],
"Aktualisierbar": [
"Updateable",
"Actualisable",
"Aktualisierbar"
],
"Scharf: Firmware wirklich einspielen": [
"Live: really flash firmware",
"Réel : flasher vraiment le firmware",
"Scharf: Firmware würkli iispiele"
],
"Firmware": [
"Firmware",
"Firmware",
"Firmware"
],
"{0} Komponenten gelesen ({1}). Zeilen markieren, um das Update auf sie zu begrenzen.": [
"{0} components read ({1}). Select rows to limit the update to them.",
"{0} composants lus ({1}). Sélectionnez des lignes pour y limiter la mise à jour.",
"{0} Komponänte gläse ({1}). Zeile markiere, zum s Update uf si beschränke."
],
"Bitte eine Firmware-Datei wählen.": [
"Please choose a firmware file.",
"Veuillez choisir un fichier firmware.",
"Bitte e Firmware-Datei uswähle."
],
"Bitte eine URL eintragen.": [
"Please enter a URL.",
"Veuillez saisir une URL.",
"Bitte e URL iitrage."
],
"Datei {0}": [
"file {0}",
"fichier {0}",
"Datei {0}"
],
"URL {0}": [
"URL {0}",
"URL {0}",
"URL {0}"
],
"Es wird Firmware auf die BMCs geschrieben. Ein unterbrochener Flash kann einen Server unbrauchbar machen. Quelle: {0}, Übernahme: {1}.": [
"Firmware is written to the BMCs. An interrupted flash can brick a server. Source: {0}, apply: {1}.",
"Du firmware est écrit sur les BMC. Un flash interrompu peut rendre un serveur inutilisable. Source : {0}, application : {1}.",
"Es wird Firmware uf d BMCs gschribe. En underbrochene Flash cha en Server unbruuchbar mache. Quälle: {0}, Übernahm: {1}."
],
"sofort": [
"immediately",
"immédiatement",
"sofort"
],
"beim Neustart": [
"on restart",
"au redémarrage",
"bim Neustart"
],
"Firmware wirklich einspielen?": [
"Really flash firmware?",
"Vraiment flasher le firmware ?",
"Firmware würkli iispiele?"
],
"Fehler: {0}": [
"Error: {0}",
"Erreur : {0}",
"Fähler: {0}"
],
"Auf {0} Servern wird jetzt wirklich Firmware eingespielt.": [
"Firmware will now really be flashed on {0} servers.",
"Le firmware va maintenant réellement être flashé sur {0} serveurs.",
"Uf {0} Server wird jetzt würkli Firmware iigspielt."
],
"Startet virtuelle Dell/HPE/Lenovo/Supermicro-Server, die sich wie echte BMCs verhalten - zum Testen und Vorführen ohne Hardware. Die hier gezeigten Adressen trägt man im Haupt-Reiter als IP:PORT ein.": [
"Starts virtual Dell/HPE/Lenovo/Supermicro servers that behave like real BMCs - for testing and demos without hardware. Enter the addresses shown here in the main tab as IP:PORT.",
"Démarre des serveurs virtuels Dell/HPE/Lenovo/Supermicro qui se comportent comme de vrais BMC - pour tester et faire des démos sans matériel. Saisissez les adresses affichées ici dans l'onglet principal sous la forme IP:PORT.",
"Startet virtuelli Dell/HPE/Lenovo/Supermicro-Server, wo sich wie echti BMCs verhalte - zum Teste und Vorführe ohni Hardware. D Adrässe wo da aazeigt werdet, treit me im Haupt-Reiter als IP:PORT ii."
],
"Cluster zusammenstellen": [
"Compose cluster",
"Composer le cluster",
"Cluster zämestelle"
],
"Wie viele Server je angehaktem Hersteller.": [
"How many servers per ticked vendor.",
"Combien de serveurs par fabricant coché.",
"Wie vill Server pro aaghäggte Herschteller."
],
"Anzahl je Hersteller": [
"Count per vendor",
"Nombre par fabricant",
"Aazahl pro Herschteller"
],
"Basis für die automatischen Loopback-Adressen, z. B. 127.9.0 -> 127.9.0.11, .12, ...": [
"Base for the automatic loopback addresses, e.g. 127.9.0 -> 127.9.0.11, .12, ...",
"Base pour les adresses loopback automatiques, p. ex. 127.9.0 -> 127.9.0.11, .12, ...",
"Basis für di automatische Loopback-Adrässe, z. B. 127.9.0 -> 127.9.0.11, .12, ..."
],
"Basis-Adresse": [
"Base address",
"Adresse de base",
"Basis-Adrässe"
],
"Redfish-Port. Port 443 bräuchte Root; mit 8443 geht es ohne.": [
"Redfish port. Port 443 would need root; 8443 works without.",
"Port Redfish. Le port 443 nécessiterait root ; 8443 fonctionne sans.",
"Redfish-Port. Port 443 bruichti Root; mit 8443 goht's ohni."
],
"Redfish-Port": [
"Redfish port",
"Port Redfish",
"Redfish-Port"
],
"UEFI-Konsole aktivieren (echtes SSH + IPMI, braucht Root)": [
"Enable UEFI console (real SSH + IPMI, needs root)",
"Activer la console UEFI (SSH + IPMI réels, nécessite root)",
"UEFI-Konsole aktiviere (echts SSH + IPMI, bruucht Root)"
],
"Zusätzlich SSH (22), IPMI-SOL (623) und Redfish auf 443. Diese Ports verlangen Root-Rechte und die Pakete paramiko + pyghmi. Der Start läuft dann über eine Rechteabfrage (pkexec).": [
"Additionally SSH (22), IPMI-SOL (623) and Redfish on 443. These ports need root rights and the packages paramiko + pyghmi. The start then goes through a privilege prompt (pkexec).",
"En plus SSH (22), IPMI-SOL (623) et Redfish sur 443. Ces ports nécessitent des droits root et les paquets paramiko + pyghmi. Le démarrage passe alors par une demande de privilèges (pkexec).",
"Zuesätzlich SSH (22), IPMI-SOL (623) und Redfish uf 443. Die Ports bruuche Root-Rächt und d Pakete paramiko + pyghmi. De Start lauft denn über e Rächtefrog (pkexec)."
],
"Cluster starten": [
"Start cluster",
"Démarrer le cluster",
"Cluster starte"
],
"Cluster stoppen": [
"Stop cluster",
"Arrêter le cluster",
"Cluster stoppe"
],
"Ausgabe des Clusters (Adressen, Werks-Logins, Statusmeldungen) …": [
"Cluster output (addresses, factory logins, status messages) …",
"Sortie du cluster (adresses, identifiants d'usine, messages d'état) …",
"Usgab vom Cluster (Adrässe, Werks-Logins, Status-Mäldige) …"
],
"Cluster läuft. Adressen stehen unten; mit „Cluster stoppen“ beenden.": [
"Cluster is running. Addresses are below; stop with “Stop cluster”.",
"Le cluster tourne. Les adresses sont ci-dessous ; arrêtez avec « Arrêter le cluster ».",
"De Cluster lauft. D Adrässe stönd unde; mit „Cluster stoppe“ beände."
],
"Cluster gestoppt.": [
"Cluster stopped.",
"Cluster arrêté.",
"Cluster gstoppt."
],
"tools/vbmc/vbmc.py nicht gefunden. Der Reiter funktioniert nur, wenn BMC-Steward aus dem Quellcode läuft.": [
"tools/vbmc/vbmc.py not found. This tab only works when BMC-Steward runs from source.",
"tools/vbmc/vbmc.py introuvable. Cet onglet ne fonctionne que si BMC-Steward s'exécute depuis les sources.",
"tools/vbmc/vbmc.py nöd gfunde. De Reiter funktioniert nume, wenn BMC-Steward us em Quällcode lauft."
],
"Bereit.": [
"Ready.",
"Prêt.",
"Parat."
],
"Bitte mindestens einen Hersteller anhaken.": [
"Please tick at least one vendor.",
"Veuillez cocher au moins un fabricant.",
"Bitte mindestens ei Herschteller aahögge."
],
"Für die Konsole sind Root-Rechte nötig; pkexec ist nicht verfügbar. Bitte im Terminal starten:": [
"The console needs root rights; pkexec is not available. Please start it in a terminal:",
"La console nécessite des droits root ; pkexec n'est pas disponible. Veuillez démarrer dans un terminal :",
"Für d Konsole bruucht's Root-Rächt; pkexec isch nöd verfüegbar. Bitte im Terminal starte:"
],
"automatisch": [
"automatic",
"automatique",
"automatisch"
],
"Update starten": [
"Start update",
"Démarrer la mise à jour",
"Update starte"
],
"UpdateService lesen": [
"Read UpdateService",
"Lire UpdateService",
"UpdateService läse"
],
"  FEHLER: Zeitüberschreitung beim Update": [
"  ERROR: timeout during update",
"  ERREUR : délai dépassé pendant la mise à jour",
"  FÄHLER: Zitüberschritig bim Update"
],
"  {0} wird eingespielt (Übernahme: {1}) …": [
"  {0} is being applied (apply: {1}) …",
"  {0} en cours d'application (application : {1}) …",
"  {0} wird iigspielt (Übernahm: {1}) …"
],
"  Task: {0}": [
"  Task: {0}",
"  Tâche : {0}",
"  Task: {0}"
],
"{0}: {1} → {2}": [
"{0}: {1} → {2}",
"{0} : {1} → {2}",
"{0}: {1} → {2}"
],
"  Hochgeladen, wird beim nächsten Neustart übernommen.": [
"  Uploaded, will be applied at the next restart.",
"  Téléversé, sera appliqué au prochain redémarrage.",
"  Ufeglade, wird bim nächste Neustart übernoh."
],
"  Update abgeschlossen.": [
"  Update completed.",
"  Mise à jour terminée.",
"  Update abgschlosse."
],
"  iLO 3/4: Firmware-Update hier nicht unterstützt (RIBCL), übersprungen.": [
"  iLO 3/4: firmware update not supported here (RIBCL), skipped.",
"  iLO 3/4 : mise à jour du firmware non prise en charge ici (RIBCL), ignorée.",
"  iLO 3/4: Firmware-Update da nöd understützt (RIBCL), übersprunge."
],
"  Trockenlauf: würde {0} einspielen (Ziel: {1}, Übernahme: {2})": [
"  Dry run: would apply {0} (target: {1}, apply: {2})",
"  Simulation : appliquerait {0} (cible : {1}, application : {2})",
"  Troggelauf: würd {0} iispiele (Ziel: {1}, Übernahm: {2})"
],
"  Firmware aktualisiert: {0}": [
"  Firmware updated: {0}",
"  Firmware mis à jour : {0}",
"  Firmware aktualisiert: {0}"
],
"    {0} … {1}%": [
"    {0} … {1}%",
"    {0} … {1}%",
"    {0} … {1}%"
],
"  FEHLER: Task nicht mehr lesbar (HTTP {0})": [
"  ERROR: task no longer readable (HTTP {0})",
"  ERREUR : tâche illisible (HTTP {0})",
"  FÄHLER: Task nüme läsbar (HTTP {0})"
],
"  FEHLER: Datei nicht lesbar: {0}": [
"  ERROR: file not readable: {0}",
"  ERREUR : fichier illisible : {0}",
"  FÄHLER: Datei nöd läsbar: {0}"
],
"  FEHLER: Update fehlgeschlagen ({0})": [
"  ERROR: update failed ({0})",
"  ERREUR : échec de la mise à jour ({0})",
"  FÄHLER: Update fählgschlage ({0})"
],
"iLO 3/4: Firmware-Update wird hier nicht unterstützt.": [
"iLO 3/4: firmware update is not supported here.",
"iLO 3/4 : la mise à jour du firmware n'est pas prise en charge ici.",
"iLO 3/4: Firmware-Update wird da nöd understützt."
],
"Firmware-Inventar lesen": [
"Read firmware inventory",
"Lire l'inventaire du firmware",
"Firmware-Inventar läse"
],
"UEFI-Konsole": ["UEFI console", "Console UEFI", "UEFI-Konsole"],
"Hilfe": ["Help", "Aide", "Hilf"],
"Testlabor": [
"Test lab",
"Labo de test",
"Teschtlabor"
],
"Tesseract brauchte zu lange.": [
"Tesseract took too long.",
"Tesseract a pris trop de temps.",
"Tesseract hät z lang bruucht."
],
"Tesseract meldet: {0} (Sprachdaten fehlen? sudo pacman -S tesseract-data-eng)": [
"Tesseract reports: {0} (language data missing? sudo pacman -S tesseract-data-eng)",
"Tesseract signale : {0} (données de langue manquantes ? sudo pacman -S tesseract-data-eng)",
"Tesseract meldet: {0} (Sprachdate fähled? sudo pacman -S tesseract-data-eng)"
],
"Das Bild konnte nicht gelesen werden.": [
"The image could not be read.",
"L'image n'a pas pu être lue.",
"S Bild het nöd chöne gläse wärde."
],
"Bild {0}×{1} px.": [
"Image {0}×{1} px.",
"Image {0}×{1} px.",
"Bild {0}×{1} px."
],
"Verbindungsweg": [
"Connection method",
"Mode de connexion",
"Verbindigswäg"
],
"Vorhandenes Netz (selbes WLAN/LAN)": [
"Existing network (same Wi-Fi/LAN)",
"Réseau existant (même Wi-Fi/LAN)",
"Vorhandes Netz (gliiche WLAN/LAN)"
],
"USB-Tethering (Handy per Kabel)": [
"USB tethering (phone by cable)",
"Partage USB (téléphone par câble)",
"USB-Tethering (Handy per Chabel)"
],
"Eigener WLAN-Hotspot dieses PCs": [
"Own Wi-Fi hotspot of this PC",
"Point d'accès Wi-Fi propre de ce PC",
"Eigete WLAN-Hotspot vo dem PC"
],
"Wie das Handy den PC erreicht:\nVorhandenes Netz: Handy und PC sind im selben WLAN/LAN.\nUSB-Tethering: Handy per Kabel anstecken und dort „USB-Tethering“ einschalten.\nHotspot: Der PC macht ein eigenes WLAN auf (NetworkManager); der WLAN-Adapter ist dann belegt.": [
"How the phone reaches the PC:\nExisting network: phone and PC are on the same Wi-Fi/LAN.\nUSB tethering: connect the phone by cable and switch on “USB tethering” there.\nHotspot: the PC opens its own Wi-Fi (NetworkManager); the Wi-Fi adapter is then occupied.",
"Comment le téléphone atteint le PC :\nRéseau existant : téléphone et PC sont sur le même Wi-Fi/LAN.\nPartage USB : brancher le téléphone par câble et y activer « Partage USB ».\nPoint d'accès : le PC ouvre son propre Wi-Fi (NetworkManager) ; l'adaptateur Wi-Fi est alors occupé.",
"Wie s Handy de PC erreicht:\nVorhandes Netz: Handy und PC sind im gliiche WLAN/LAN.\nUSB-Tethering: Handy per Chabel aastecke und det „USB-Tethering“ iischalte.\nHotspot: De PC macht es eigets WLAN uuf (NetworkManager); de WLAN-Adapter isch dänn bsetzt."
],
"Adressen neu einlesen, z. B. nachdem das Handy angesteckt wurde.": [
"Read addresses again, e.g. after the phone was connected.",
"Relire les adresses, p. ex. après avoir branché le téléphone.",
"Adrässe neu iilese, z. B. nachdem s Handy aagsteckt worde isch."
],
"(kein WLAN-Adapter gefunden)": [
"(no Wi-Fi adapter found)",
"(aucun adaptateur Wi-Fi trouvé)",
"(kein WLAN-Adapter gfunde)"
],
"WLAN-Adapter, der zum Hotspot wird.": [
"Wi-Fi adapter that becomes the hotspot.",
"Adaptateur Wi-Fi qui devient le point d'accès.",
"WLAN-Adapter, wo zum Hotspot wird."
],
"(kein USB-Tethering erkannt, Handy anstecken und Tethering einschalten)": [
"(no USB tethering detected, connect the phone and switch tethering on)",
"(aucun partage USB détecté, brancher le téléphone et activer le partage)",
"(kei USB-Tethering erchännt, Handy aastecke und Tethering iischalte)"
],
"Adresse dieses PCs auf der USB-Verbindung zum Handy.": [
"Address of this PC on the USB connection to the phone.",
"Adresse de ce PC sur la connexion USB vers le téléphone.",
"Adrässe vo dem PC uf de USB-Verbindig zum Handy."
],
"Kein WLAN-Adapter gefunden.": [
"No Wi-Fi adapter found.",
"Aucun adaptateur Wi-Fi trouvé.",
"Kei WLAN-Adapter gfunde."
],
"Hotspot starten?": [
"Start hotspot?",
"Démarrer le point d'accès ?",
"Hotspot starte?"
],
"Der WLAN-Adapter {0} wird zum Hotspot. Eine bestehende WLAN-Verbindung dieses PCs wird dabei getrennt. Fortfahren?": [
"The Wi-Fi adapter {0} becomes a hotspot. An existing Wi-Fi connection of this PC is dropped. Continue?",
"L'adaptateur Wi-Fi {0} devient un point d'accès. Une connexion Wi-Fi existante de ce PC est coupée. Continuer ?",
"De WLAN-Adapter {0} wird zum Hotspot. E bestehendi WLAN-Verbindig vo dem PC wird trännt. Wiiterfahre?"
],
"Hotspot fehlgeschlagen: {0}": [
"Hotspot failed: {0}",
"Échec du point d'accès : {0}",
"Hotspot fählgschlage: {0}"
],
"Der Hotspot hat keine Adresse bekommen.": [
"The hotspot did not get an address.",
"Le point d'accès n'a pas reçu d'adresse.",
"De Hotspot hät kei Adrässe übercho."
],
"Dieser Rechner hat keine passende Netzwerkadresse.": [
"This computer has no suitable network address.",
"Cet ordinateur n'a pas d'adresse réseau adaptée.",
"Dä Rächner hät kei passendi Netzwerkadrässe."
],
"1. Mit dem WLAN verbinden: {0}, Passwort {1}": [
"1. Connect to the Wi-Fi: {0}, password {1}",
"1. Se connecter au Wi-Fi : {0}, mot de passe {1}",
"1. Mit em WLAN verbinde: {0}, Passwort {1}"
],
"Handy-Scanner …": [
"Phone scanner …",
"Scanner mobile …",
"Handy-Scanner …"
],
"Passwörter vom Aufkleber/Anhänger mit der Handy-Kamera abfotografieren und direkt in die\nSpalte „Passwort“ eintragen. Das Handy speichert keine Bilder.": [
"Capture passwords from the sticker/tag with the phone camera and enter them straight into the\n“Password” column. The phone does not store any images.",
"Photographier les mots de passe de l'étiquette avec la caméra du téléphone et saisis-les directement dans la\ncolonne « Mot de passe ». Le téléphone n'enregistre aucune image.",
"Passwörter vom Chläber/Aahänger mit de Handy-Kamera abfotografiere und diräkt id\nSpalte „Passwort“ iiträge. S Handy speicheret kei Bilder."
],
"Handy-Scanner (Passwort)": [
"Phone scanner (password)",
"Scanner mobile (mot de passe)",
"Handy-Scanner (Passwort)"
],
"Handy-Scanner": [
"Phone scanner",
"Scanner mobile",
"Handy-Scanner"
],
"Eigene Adresse, unter der das Handy diesen Rechner erreicht (selbes WLAN/Netz).": [
"Own address through which the phone reaches this computer (same Wi-Fi/network).",
"Adresse propre par laquelle le téléphone atteint cet ordinateur (même Wi-Fi/réseau).",
"Eigeni Adrässe, under dere s Handy dä Rächner erreicht (gliiche WLAN/Netz)."
],
"Starten": [
"Start",
"Démarrer",
"Starte"
],
"Hilfsprogramme am PC: Barcode braucht zbar, Texterkennung braucht tesseract.": [
"Helper programs on the PC: barcode needs zbar, text recognition needs tesseract.",
"Programmes auxiliaires sur le PC : le code-barres demande zbar, la reconnaissance de texte tesseract.",
"Hilfsprogramm am PC: Barcode bruucht zbar, Texterkännig bruucht tesseract."
],
"Es fehlt: {0}": [
"Missing: {0}",
"Manque : {0}",
"S fählt: {0}"
],
"Das Handy zeigt nur die Kamera. Bilder werden nicht auf dem Handy gespeichert: Der PC liest den Text und verwirft das Bild sofort. Der Wert landet im Feld „Passwort“ der markierten Zeile (sonst der ersten ohne Passwort); danach springt die Markierung eine Zeile weiter.": [
"The phone only shows the camera. Images are not stored on the phone: the PC reads the text and discards the image at once. The value goes into the “Password” field of the selected row (otherwise the first one without a password); the selection then moves down one row.",
"Le téléphone n'affiche que la caméra. Les images ne sont pas enregistrées sur le téléphone : le PC lit le texte et supprime l'image aussitôt. La valeur arrive dans le champ « Mot de passe » de la ligne sélectionnée (sinon la première sans mot de passe) ; la sélection avance ensuite d'une ligne.",
"S Handy zeigt nume d Kamera. Bilder wärded nöd uf em Handy gspeicheret: De PC liest de Text und verwirft s Bild sofort. De Wärt landet im Fäld „Passwort“ vo de markierte Ziile (susch vo de erschte ohni Passwort); dänn springt d Markierig e Ziile wiiter."
],
"Dieser Rechner hat keine Netzwerkadresse.": [
"This computer has no network address.",
"Cet ordinateur n'a pas d'adresse réseau.",
"Dä Rächner hät kei Netzwerkadrässe."
],
"Start fehlgeschlagen: {0}": [
"Start failed: {0}",
"Échec du démarrage : {0}",
"Start fählgschlage: {0}"
],
"Läuft. Link am Handy öffnen (QR-Code scannen), die Zertifikatswarnung einmal bestätigen, Kamera erlauben. Bei aktiver Firewall den Port {0} freigeben (ufw: sudo ufw allow {0}/tcp).": [
"Running. Open the link on the phone (scan the QR code), confirm the certificate warning once, allow the camera. With an active firewall open port {0} (ufw: sudo ufw allow {0}/tcp).",
"En cours. Ouvrir le lien sur le téléphone (scanner le code QR), confirmer l'avertissement de certificat une fois, autoriser la caméra. Pare-feu actif : ouvrir le port {0} (ufw : sudo ufw allow {0}/tcp).",
"Lauft. Link am Handy öffne (QR-Code scanne), d Zertifikatswarnig eimal bestätige, Kamera erlaube. Bi aktiver Firewall de Port {0} freigäh (ufw: sudo ufw allow {0}/tcp)."
],
"(Für einen QR-Code: sudo pacman -S qrencode)": [
"(For a QR code: sudo pacman -S qrencode)",
"(Pour un code QR : sudo pacman -S qrencode)",
"(Für en QR-Code: sudo pacman -S qrencode)"
],
"Scanner gestartet auf {0}": [
"Scanner started on {0}",
"Scanner démarré sur {0}",
"Scanner gstartet uf {0}"
],
"Passwort in Zeile {0} eingetragen": [
"Password entered in row {0}",
"Mot de passe saisi à la ligne {0}",
"Passwort i Ziile {0} iitrage"
],
"Kein freies Feld, nichts eingetragen": [
"No free field, nothing entered",
"Aucun champ libre, rien saisi",
"Kei freis Fäld, nüt iitrage"
],
"zbarimg fehlt am PC. Unter CachyOS: sudo pacman -S zbar": [
"zbarimg is missing on the PC. On CachyOS: sudo pacman -S zbar",
"zbarimg manque sur le PC. Sous CachyOS : sudo pacman -S zbar",
"zbarimg fählt am PC. Under CachyOS: sudo pacman -S zbar"
],
"tesseract fehlt am PC. Unter CachyOS: sudo pacman -S tesseract tesseract-data-eng": [
"tesseract is missing on the PC. On CachyOS: sudo pacman -S tesseract tesseract-data-eng",
"tesseract manque sur le PC. Sous CachyOS : sudo pacman -S tesseract tesseract-data-eng",
"tesseract fählt am PC. Under CachyOS: sudo pacman -S tesseract tesseract-data-eng"
],
"Erkennung fehlgeschlagen: {0}": [
"Recognition failed: {0}",
"Échec de la reconnaissance : {0}",
"Erkännig fählgschlage: {0}"
],
"openssl fehlt am PC (wird für das Zertifikat gebraucht).": [
"openssl is missing on the PC (needed for the certificate).",
"openssl manque sur le PC (nécessaire pour le certificat).",
"openssl fählt am PC (wird für s Zertifikat bruucht)."
],
"Seriennummer {0}": [
"serial number {0}",
"numéro de série {0}",
"Serienummere {0}"
],
"Service-Tag/SKU {0}": [
"service tag/SKU {0}",
"service tag/SKU {0}",
"Service-Tag/SKU {0}"
],
"Inventar (neue IP, Modell, Seriennummer):": [
"Inventory (new IP, model, serial number):",
"Inventaire (nouvelle IP, modèle, numéro de série) :",
"Inventar (neui IP, Modäll, Serienummere):"
],
"(kein Profil)": [
"(no profile)",
"(aucun profil)",
"(kei Profil)"
],
"automatisch je Hersteller": [
"automatic per vendor",
"automatique selon le constructeur",
"automatisch je Hersteller"
],
"Werkspasswort": [
"factory password",
"mot de passe d'usine",
"Werkspasswort"
],
"ab Werk calvin oder laut Service-Tag-Auszug": [
"factory default calvin, or as on the service tag sheet",
"calvin par défaut, ou selon la fiche du Service Tag",
"ab Werk calvin oder so wie's uf em Service-Tag-Uuszug staht"
],
"steht auf dem Anhänger am Gerät": [
"printed on the pull-out tag on the device",
"indiqué sur l'étiquette amovible de l'appareil",
"staht uf em Aahänger am Grät"
],
"steht auf dem Aufkleber am Gerät": [
"printed on the sticker on the device",
"indiqué sur l'autocollant de l'appareil",
"staht uf em Chläber am Grät"
],
"Geprüft: Dell iDRAC9 · HPE iLO 5/6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. Andere Generationen zuerst an einem einzelnen Gerät testen.": [
"Tested: Dell iDRAC9 · HPE iLO 5/6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. Try other generations on a single device first.",
"Testé : Dell iDRAC9 · HPE iLO 5/6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. Essayer d'abord les autres générations sur un seul appareil.",
"Prüeft: Dell iDRAC9 · HPE iLO 5/6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. Anderi Generatione zerscht a eim einzelne Grät teste."
],
"iDRAC9, 14. bis 16. Generation (z. B. R6625). iDRAC10 (17. Generation) ist nicht geprüft, einzelne Aufrufe können dort abweichen.": [
"iDRAC9, 14th to 16th generation (e.g. R6625). iDRAC10 (17th generation) is untested; individual calls may differ there.",
"iDRAC9, 14e à 16e génération (p. ex. R6625). iDRAC10 (17e génération) n'est pas testé, certains appels peuvent y différer.",
"iDRAC9, 14. bis 16. Generation (z. B. R6625). iDRAC10 (17. Generation) isch nöd prüeft, einzelni Uufrüef chönd det abwiiche."
],
"iLO 5 und 6 (Gen10, Gen10 Plus, Gen11, z. B. DL345/DL365). iLO 7 (Gen12) ist nicht geprüft.": [
"iLO 5 and 6 (Gen10, Gen10 Plus, Gen11, e.g. DL345/DL365). iLO 7 (Gen12) is untested.",
"iLO 5 et 6 (Gen10, Gen10 Plus, Gen11, p. ex. DL345/DL365). iLO 7 (Gen12) n'est pas testé.",
"iLO 5 und 6 (Gen10, Gen10 Plus, Gen11, z. B. DL345/DL365). iLO 7 (Gen12) isch nöd prüeft."
],
"Aktuelle X12/X13-Boards mit Redfish 1.x, auch baugleiche Nutanix-NX-Nodes. Bei älteren Boards können einzelne Redfish-Funktionen eine Lizenz erfordern.": [
"Current X12/X13 boards with Redfish 1.x, including identical Nutanix NX nodes. On older boards some Redfish functions may need a licence.",
"Cartes X12/X13 actuelles avec Redfish 1.x, y compris les nœuds Nutanix NX identiques. Sur les cartes plus anciennes, certaines fonctions Redfish peuvent exiger une licence.",
"Aktuelli X12/X13-Boards mit Redfish 1.x, au baugliichi Nutanix-NX-Nodes. Bi ältere Boards chönd einzelni Redfish-Funktione e Lizänz bruuche."
],
"ThinkSystem mit XCC2/XCC3 (z. B. SR6xx, SR8xx). Ältere Systeme mit XCC1 sind nicht geprüft.": [
"ThinkSystem with XCC2/XCC3 (e.g. SR6xx, SR8xx). Older systems with XCC1 are untested.",
"ThinkSystem avec XCC2/XCC3 (p. ex. SR6xx, SR8xx). Les systèmes plus anciens avec XCC1 ne sont pas testés.",
"ThinkSystem mit XCC2/XCC3 (z. B. SR6xx, SR8xx). Älteri System mit XCC1 sind nöd prüeft."
],
"Adresse, unter der der BMC jetzt erreichbar ist, z. B. die per DHCP vergebene.": [
"Address where the BMC can be reached right now, e.g. the one assigned by DHCP.",
"Adresse à laquelle le BMC est joignable actuellement, p. ex. celle attribuée par DHCP.",
"Adrässe, under dere de BMC jetzt erreichbar isch, z. B. die vom DHCP."
],
"Aktuelles Passwort dieses Geräts. Nur nötig, wenn es vom Werkspasswort oben abweicht\n(bei HPE, Supermicro und Lenovo ist es pro Gerät verschieden).": [
"Current password of this device. Only needed if it differs from the factory password above\n(on HPE, Supermicro and Lenovo it differs per device).",
"Mot de passe actuel de cet appareil. Nécessaire seulement s'il diffère du mot de passe d'usine ci-dessus\n(chez HPE, Supermicro et Lenovo, il est différent pour chaque appareil).",
"Aktuells Passwort vo dem Grät. Nur nötig, wenn's vom Werkspasswort obe abwiicht\n(bi HPE, Supermicro und Lenovo isch's pro Grät verschide)."
],
"Feste Adresse, die der BMC bekommen soll.": [
"Static address the BMC should get.",
"Adresse fixe que le BMC doit recevoir.",
"Feschti Adrässe, wo de BMC söll übercho."
],
"Subnetzmaske zur neuen Adresse, z. B. 255.255.255.0. Eingabe als /24 wird umgewandelt.": [
"Subnet mask for the new address, e.g. 255.255.255.0. Input like /24 is converted.",
"Masque de sous-réseau de la nouvelle adresse, p. ex. 255.255.255.0. Une saisie comme /24 est convertie.",
"Subnetzmaske zu de neue Adrässe, z. B. 255.255.255.0. E Iigab wie /24 wird umgwandlet."
],
"Gateway im Netz der neuen Adresse.": [
"Gateway in the network of the new address.",
"Passerelle dans le réseau de la nouvelle adresse.",
"Gateway im Netz vo de neue Adrässe."
],
"Name, den der BMC bekommt (iDRAC-, iLO- bzw. BMC-Hostname), nicht der Name des Betriebssystems.": [
"Name the BMC gets (iDRAC, iLO or BMC hostname), not the name of the operating system.",
"Nom donné au BMC (nom d'hôte iDRAC, iLO ou BMC), pas celui du système d'exploitation.",
"Name, wo de BMC überchunnt (iDRAC-, iLO- bzw. BMC-Hostname), nöd de Name vom Betriebssystem."
],
"Aktuelle IP": [
"Current IP",
"IP actuelle",
"Aktuelli IP"
],
"Passwort": [
"Password",
"Mot de passe",
"Passwort"
],
"Neue IP": [
"New IP",
"Nouvelle IP",
"Neui IP"
],
"Subnetzmaske": [
"Subnet mask",
"Masque de sous-réseau",
"Subnetzmaske"
],
"Gateway": [
"Gateway",
"Passerelle",
"Gateway"
],
"Hostname": [
"Hostname",
"Nom d'hôte",
"Hostname"
],
"Seriennummer": [
"Serial number",
"Numéro de série",
"Säriänummer"
],
"Hersteller/Typ": [
"Vendor/model",
"Fabricant/modèle",
"Harschteller/Typ"
],
"Keine gültige IPv4-Adresse": [
"Not a valid IPv4 address",
"Adresse IPv4 non valide",
"Kei gültigi IPv4-Adrässe"
],
"Kommas im Passwort werden nicht unterstützt": [
"Commas in the password are not supported",
"Les virgules dans le mot de passe ne sont pas prises en charge",
"Komma im Passwort gönd nöd"
],
"Keine gültige Subnetzmaske (z. B. 255.255.255.0 oder /24)": [
"Not a valid subnet mask (e.g. 255.255.255.0 or /24)",
"Masque de sous-réseau non valide (p. ex. 255.255.255.0 ou /24)",
"Kei gültigi Subnetzmaske (z. B. 255.255.255.0 oder /24)"
],
"Gateway und neue IP sind identisch": [
"Gateway and new IP are identical",
"La passerelle et la nouvelle IP sont identiques",
"Gateway und neui IP sind gliich"
],
"Nur Buchstaben, Ziffern und Bindestrich (nicht am Anfang/Ende), höchstens 63 Zeichen": [
"Only letters, digits and hyphen (not at the start or end), at most 63 characters",
"Uniquement lettres, chiffres et tiret (pas au début ni à la fin), 63 caractères au maximum",
"Nur Buechstabe, Ziffere und Bindestrich (nöd am Aafang/Änd), höchschtens 63 Zeiche"
],
"Fertig: {0} konfiguriert, {1} Fehler": [
"Done: {0} configured, {1} errors",
"Terminé : {0} configurés, {1} erreurs",
"Fertig: {0} konfiguriert, {1} Fähler"
],
"Fertig: {0} Server ohne Fehler, {1} mit Fehlern": [
"Done: {0} servers without errors, {1} with errors",
"Terminé : {0} serveurs sans erreur, {1} avec erreurs",
"Fertig: {0} Server ohni Fähler, {1} mit Fähler"
],
"Aus diesem Bereich bekommen die Geräte vorübergehend eine Adresse,\ndamit das Tool sie erreichen kann. Er muss im Netz der gewählten Karte liegen\nund groß genug für alle Geräte sein.": [
"Devices temporarily get an address from this range\nso the tool can reach them. It must lie in the network of the selected card\nand be large enough for all devices.",
"Les appareils reçoivent temporairement une adresse de cette plage\npour que l'outil puisse les joindre. Elle doit se trouver dans le réseau de la carte choisie\net être assez grande pour tous les appareils.",
"Us dem Beriich überchömed d Grät vorübergehend e Adrässe,\ndamit s Tool sie erreicht. Er mues im Netz vo de gwählte Charte ligge\nund gross gnueg für alli Grät sii."
],
"Automatisch erkennen": [
"Detect automatically",
"Détecter automatiquement",
"Automatisch erchänne"
],
"Dell iDRAC": [
"Dell iDRAC",
"Dell iDRAC",
"Dell iDRAC"
],
"HPE iLO": [
"HPE iLO",
"HPE iLO",
"HPE iLO"
],
"Lenovo XCC": [
"Lenovo XCC",
"Lenovo XCC",
"Lenovo XCC"
],
"Netz- oder Broadcast-Adresse von {0}": [
"Network or broadcast address of {0}",
"Adresse de réseau ou de diffusion de {0}",
"Netz- oder Broadcast-Adrässe vo {0}"
],
"Gateway liegt nicht im Netz der neuen IP ({0})": [
"Gateway is not in the network of the new IP ({0})",
"La passerelle n'est pas dans le réseau de la nouvelle IP ({0})",
"Gateway liit nöd im Netz vo de neue IP ({0})"
],
"keine Verbindung": [
"no connection",
"pas de connexion",
"kei Verbindig"
],
"Benutzer": [
"User",
"Utilisateur",
"Benutzer"
],
"  Benutzer {0} existierte schon, Passwort und Rolle gesetzt": [
"  User {0} already existed, password and role set",
"  L'utilisateur {0} existait déjà, mot de passe et rôle définis",
"  Benutzer {0} hät's scho gäh, Passwort und Rolle gsetzt"
],
"  Benutzer {0} ok": [
"  User {0} ok",
"  Utilisateur {0} ok",
"  Benutzer {0} ok"
],
"  Hostname ok": [
"  Hostname ok",
"  Nom d'hôte ok",
"  Hostname ok"
],
"Netzwerk": [
"Network",
"Réseau",
"Netzwerk"
],
"  Netzwerk gesetzt": [
"  Network set",
"  Réseau configuré",
"  Netzwerk gsetzt"
],
"  iLO-Neustart ausgelöst": [
"  iLO restart triggered",
"  Redémarrage de l'iLO lancé",
"  iLO-Neustart uusglöst"
],
"Neustart": [
"Restart",
"Redémarrage",
"Neustart"
],
"DHCP-Server gestoppt": [
"DHCP server stopped",
"Serveur DHCP arrêté",
"DHCP-Server gstoppt"
],
"Passwort für alle Server übernehmen": [
"Apply password to all servers",
"Appliquer le mot de passe à tous les serveurs",
"Passwort für alli Server übernäh"
],
"Trägt das Passwort von oben in die Spalte „Passwort“ aller Zeilen ein\nund überschreibt dort vorhandene Einträge.": [
"Enters the password from above into the “Password” column of all rows\nand overwrites existing entries there.",
"Inscrit le mot de passe ci-dessus dans la colonne « Mot de passe » de toutes les lignes\net y écrase les entrées existantes.",
"Treit s Passwort vo obe i d Spalte „Passwort“ vo allne Ziile ii\nund überschriibt det was scho staht."
],
"Aktueller Login (Auslieferungszustand)": [
"Current login (factory state)",
"Connexion actuelle (état de livraison)",
"Aktuells Login (Uuslieferigszuestand)"
],
"Mit diesen Daten meldet sich das Tool an den Geräten an, um sie umzustellen.": [
"The tool uses these credentials to log in to the devices and reconfigure them.",
"L'outil utilise ces données pour se connecter aux appareils et les reconfigurer.",
"Mit dene Date mäldet sich s Tool a de Grät aa, zum sie umstelle."
],
"Hersteller": [
"Vendor",
"Constructeur",
"Hersteller"
],
"„Automatisch erkennen“ fragt jedes Gerät nach seinem Hersteller, gemischte Listen sind möglich.\nEine feste Wahl überspringt die Erkennung und gilt für alle Zeilen. Nützlich, wenn die\nErkennung danebenliegt oder der Trockenlauf den richtigen Ablauf zeigen soll.": [
"“Detect automatically” asks each device for its vendor; mixed lists are possible.\nA fixed choice skips detection and applies to all rows. Useful if detection\ngets it wrong or the dry run should show the right sequence.",
"« Détecter automatiquement » demande son constructeur à chaque appareil ; les listes mixtes sont possibles.\nUn choix fixe saute la détection et vaut pour toutes les lignes. Utile si la détection\nse trompe ou si la simulation doit montrer le bon déroulement.",
"„Automatisch erchänne“ fröget jedes Grät nach sim Hersteller, gmischti Lischte gönd.\nE feschti Wahl überspringt d Erchännig und gilt für alli Ziile. Praktisch, wenn d\nErchännig danäbe liit oder de Trocheluuf de richtig Ablauf söll zeige."
],
"Benutzer, mit dem sich das Tool jetzt am BMC anmeldet, also der Werksbenutzer.\nLeer lassen: Dann wird je Hersteller root, Administrator oder ADMIN verwendet.": [
"User the tool logs in with at the BMC right now, i.e. the factory user.\nLeave empty: root, Administrator, ADMIN or USERID is used depending on the vendor.",
"Utilisateur avec lequel l'outil se connecte maintenant au BMC, donc l'utilisateur d'usine.\nLaisser vide : root, Administrator, ADMIN ou USERID est utilisé selon le constructeur.",
"Benutzer, mit dem sich s Tool jetzt am BMC aamäldet, also de Werksbenutzer.\nLäär laa: Denn wird je nach Hersteller root, Administrator, ADMIN oder USERID gnoh."
],
"Passwort des Werksbenutzers. Es gilt für alle Zeilen, deren Spalte „Passwort“ leer ist,\nund wird bei neuen Zeilen vorbelegt. Hat jedes Gerät ein eigenes Passwort\n(HPE, Supermicro, Lenovo), dieses in der Tabelle je Zeile eintragen.": [
"Password of the factory user. It applies to all rows whose “Password” column is empty\nand is pre-filled in new rows. If every device has its own password\n(HPE, Supermicro, Lenovo), enter it per row in the table.",
"Mot de passe de l'utilisateur d'usine. Il vaut pour toutes les lignes dont la colonne « Mot de passe » est vide\net est prérempli dans les nouvelles lignes. Si chaque appareil a son propre mot de passe\n(HPE, Supermicro, Lenovo), le saisir ligne par ligne dans le tableau.",
"Passwort vom Werksbenutzer. Es gilt für alli Ziile, wo d Spalte „Passwort“ läär isch,\nund wird bi neue Ziile vorbeleit. Hät jedes Grät es eigets Passwort\n(HPE, Supermicro, Lenovo), das i de Tabälle pro Ziile iiträge."
],
"Neuer Administrator": [
"New administrator",
"Nouvel administrateur",
"Neue Administrator"
],
"Dieses Konto wird auf jedem Gerät der Liste eingerichtet.": [
"This account is set up on every device in the list.",
"Ce compte est créé sur chaque appareil de la liste.",
"Das Konto wird uf jedem Grät vo de Lischte iigrichtet."
],
"Name des Administrator-Kontos, das auf allen Geräten angelegt wird.\nExistiert der Name bei HPE, Supermicro oder Lenovo schon, werden nur Passwort und Rolle gesetzt.": [
"Name of the administrator account created on all devices.\nIf the name already exists on HPE, Supermicro or Lenovo, only password and role are set.",
"Nom du compte administrateur créé sur tous les appareils.\nSi le nom existe déjà chez HPE, Supermicro ou Lenovo, seuls le mot de passe et le rôle sont définis.",
"Name vom Administrator-Konto, wo uf allne Grät aagleit wird.\nGit's de Name bi HPE, Supermicro oder Lenovo scho, wärded nur Passwort und Rolle gsetzt."
],
"Passwort des neuen Administrators, auf allen Geräten gleich.\nDie Geräte prüfen ihre eigenen Regeln, iLO verlangt z. B. mindestens 8 Zeichen.\nVor dem scharfen Lauf über das Auge kontrollieren.": [
"Password of the new administrator, the same on all devices.\nThe devices check their own rules; iLO, for example, requires at least 8 characters.\nCheck it with the eye button before a live run.",
"Mot de passe du nouvel administrateur, identique sur tous les appareils.\nLes appareils appliquent leurs propres règles ; iLO exige p. ex. au moins 8 caractères.\nLe vérifier avec l'œil avant une exécution réelle.",
"Passwort vom neue Administrator, uf allne Grät gliich.\nD Grät prüefed ihri eigete Regle, iLO verlangt z. B. mindeschtens 8 Zeiche.\nVor em scharfe Luuf über s Aug kontrolliere."
],
"User-Slot (nur Dell)": [
"User slot (Dell only)",
"Emplacement utilisateur (Dell uniquement)",
"User-Slot (nur Dell)"
],
"Nur Dell: Der iDRAC hat 16 feste Benutzerplätze, Platz 2 ist root.\nDer neue Administrator wird in diesen Platz geschrieben. Ist der Platz schon\nvon einem anderen Benutzer belegt, wird nichts überschrieben. Standard ist Platz 3.": [
"Dell only: the iDRAC has 16 fixed user slots, slot 2 is root.\nThe new administrator is written into this slot. If the slot is already\nused by another user, nothing is overwritten. Default is slot 3.",
"Dell uniquement : l'iDRAC a 16 emplacements utilisateur fixes, le 2 est root.\nLe nouvel administrateur est écrit dans cet emplacement. S'il est déjà\noccupé par un autre utilisateur, rien n'est écrasé. Par défaut : emplacement 3.",
"Nur Dell: De iDRAC hät 16 feschti Benutzerplätz, Platz 2 isch root.\nDe neu Administrator wird i dä Platz gschribe. Isch de Platz scho\nvo eme andere Benutzer bsetzt, wird nüt überschribe. Standard isch Platz 3."
],
"z. B. 255.255.255.0 oder /24": [
"e.g. 255.255.255.0 or /24",
"p. ex. 255.255.255.0 ou /24",
"z. B. 255.255.255.0 oder /24"
],
"Vorgaben für neue Zeilen": [
"Defaults for new rows",
"Valeurs par défaut des nouvelles lignes",
"Vorgabe für neui Ziile"
],
"Wird bei jeder neuen Zeile der Serverliste vorbelegt, auch bei Geräten aus der DHCP-Liste.\nLeer lassen: Dann wird der Wert der vorherigen Zeile übernommen.": [
"Pre-filled in every new row of the server list, also for devices from the DHCP list.\nLeave empty: the value of the previous row is used.",
"Prérempli dans chaque nouvelle ligne de la liste, y compris pour les appareils de la liste DHCP.\nLaisser vide : la valeur de la ligne précédente est reprise.",
"Wird bi jedere neue Ziile vo de Serverlischte vorbeleit, au bi Grät us de DHCP-Lischte.\nLäär laa: Denn wird de Wärt vo de vorherige Ziile übernoh."
],
"Wird bei jeder neuen Zeile der Serverliste vorbelegt.\nLeer lassen: Dann wird der Wert der vorherigen Zeile übernommen.": [
"Pre-filled in every new row of the server list.\nLeave empty: the value of the previous row is used.",
"Prérempli dans chaque nouvelle ligne de la liste.\nLaisser vide : la valeur de la ligne précédente est reprise.",
"Wird bi jedere neue Ziile vo de Serverlischte vorbeleit.\nLäär laa: Denn wird de Wärt vo de vorherige Ziile übernoh."
],
"+ Server": [
"+ Server",
"+ Serveur",
"+ Server"
],
"Neue Zeile. Passwort, Subnetzmaske und Gateway werden vorbelegt.": [
"New row. Password, subnet mask and gateway are pre-filled.",
"Nouvelle ligne. Mot de passe, masque et passerelle sont préremplis.",
"Neui Ziile. Passwort, Subnetzmaske und Gateway wärded vorbeleit."
],
"– Entfernen": [
"– Remove",
"– Retirer",
"– Ewägnäh"
],
"Entfernt die markierten Zeilen.": [
"Removes the selected rows.",
"Retire les lignes sélectionnées.",
"Nimmt di markierte Ziile ewäg."
],
"Erneut bearbeiten": [
"Process again",
"Traiter à nouveau",
"Nomal bearbeite"
],
"Nimmt bei den markierten Zeilen die grüne Fertig-Markierung zurück,\ndamit sie beim nächsten Start wieder bearbeitet werden.": [
"Removes the green “done” mark from the selected rows\nso they are processed again at the next start.",
"Retire la marque verte « terminé » des lignes sélectionnées\npour qu'elles soient retraitées au prochain démarrage.",
"Nimmt bi de markierte Ziile di grüen Fertig-Markierig zrugg,\ndamit sie bim nächschte Start wider bearbeitet wärded."
],
"Liste laden …": [
"Load list …",
"Charger la liste …",
"Lischte lade …"
],
"Lädt eine gespeicherte Liste oder eine CSV-Datei, z. B. aus Excel:\naktuelle_ip,passwort,neue_ip,subnetzmaske,gateway,hostname": [
"Loads a saved list or a CSV file, e.g. from Excel:\ncurrent_ip,password,new_ip,subnet_mask,gateway,hostname",
"Charge une liste enregistrée ou un fichier CSV, p. ex. depuis Excel :\nip_actuelle,mot_de_passe,nouvelle_ip,masque,passerelle,nom_hote",
"Ladt e gspeichereti Lischte oder e CSV-Datei, z. B. us em Excel:\naktuelli_ip,passwort,neui_ip,subnetzmaske,gateway,hostname"
],
"Liste speichern …": [
"Save list …",
"Enregistrer la liste …",
"Lischte speichere …"
],
"Speichert die Liste in eine Datei. Das Tool speichert nie von selbst.\nBeim Speichern wird gefragt, ob die Passwörter mit in die Datei sollen.": [
"Saves the list to a file. The tool never saves on its own.\nWhen saving you are asked whether the passwords should go into the file.",
"Enregistre la liste dans un fichier. L'outil n'enregistre jamais de lui-même.\nÀ l'enregistrement, il demande si les mots de passe doivent figurer dans le fichier.",
"Speicheret d Lischte i e Datei. S Tool speicheret nie vo sälber.\nBim Speichere wird gfröget, öb d Passwörter mit i d Datei sölled."
],
"Serverliste": [
"Server list",
"Liste des serveurs",
"Serverlischte"
],
"Trockenlauf (nichts ändern)": [
"Dry run (change nothing)",
"Simulation (ne rien modifier)",
"Trocheluuf (nüt ändere)"
],
"Mit Haken wird nichts geändert: Das Protokoll zeigt nur, welche Aufrufe\nan welches Gerät gingen. Ohne Haken werden die Geräte wirklich umgestellt.": [
"When ticked, nothing is changed: the log only shows which calls\nwould go to which device. Unticked, the devices are really reconfigured.",
"Coché, rien n'est modifié : le journal montre seulement quels appels\niraient à quel appareil. Décoché, les appareils sont réellement reconfigurés.",
"Mit Häägli wird nüt gänderet: S Protokoll zeiget nur, weli Uufrüef\na weles Grät giengted. Ohni Häägli wärded d Grät würkli umgstellt."
],
"Start": [
"Start",
"Démarrer",
"Start"
],
"Prüft die Eingaben und bearbeitet dann alle noch offenen Zeilen der Reihe nach:\nBenutzer anlegen, Hostname setzen, feste Adresse setzen.\nFertige (grüne) Zeilen werden übersprungen. Vor einem scharfen Lauf\nwird nachgefragt.": [
"Checks the input and then processes all open rows one after the other:\ncreate user, set hostname, set static address.\nDone (green) rows are skipped. Before a live run\nyou are asked to confirm.",
"Vérifie la saisie puis traite toutes les lignes encore ouvertes, l'une après l'autre :\ncréer l'utilisateur, définir le nom d'hôte, définir l'adresse fixe.\nLes lignes terminées (vertes) sont ignorées. Avant une exécution réelle,\nune confirmation est demandée.",
"Prüeft d Iigabe und bearbeitet denn alli no offene Ziile de Reihe naa:\nBenutzer aalegge, Hostname setze, feschti Adrässe setze.\nFertigi (grüeni) Ziile wärded überspronge. Vor eme scharfe Luuf\nwird nagfröget."
],
"Abbrechen": [
"Cancel",
"Annuler",
"Abbräche"
],
"Bricht nach dem Server ab, der gerade bearbeitet wird": [
"Stops after the server currently being processed",
"S'arrête après le serveur en cours de traitement",
"Bricht nach em Server ab, wo grad bearbeitet wird"
],
"Bereit": [
"Ready",
"Prêt",
"Parat. De Steward wartet uf Arbet."
],
"Neuware": ["New hardware", "Matériel neuf", "Neuware"],
"4 · Hilfe": [
"4 · Help",
"4 · Aide",
"4 · Hilf"
],
"Ein Profil merkt sich die Einstellungen dieser Maske: Hersteller, Werksbenutzer, neuen\nAdministrator, User-Slot, Vorgaben für neue Zeilen und die DHCP-Einstellungen.\nPasswörter und die Serverliste gehören nicht dazu.": [
"A profile remembers the settings of this form: vendor, factory user, new\nadministrator, user slot, defaults for new rows and the DHCP settings.\nPasswords and the server list are not part of it.",
"Un profil mémorise les réglages de ce formulaire : constructeur, utilisateur d'usine, nouvel\nadministrateur, emplacement utilisateur, valeurs par défaut et réglages DHCP.\nLes mots de passe et la liste des serveurs n'en font pas partie.",
"Es Profil merkt sich d Iistellige vo dere Maske: Hersteller, Werksbenutzer, neue\nAdministrator, User-Slot, Vorgabe für neui Ziile und d DHCP-Iistellige.\nPasswörter und d Serverlischte ghöred nöd dezue."
],
"Profil": [
"Profile",
"Profil",
"Profil"
],
"Name des Profils:": [
"Profile name:",
"Nom du profil :",
"Name vom Profil:"
],
"Neues Profil": [
"New profile",
"Nouveau profil",
"Neus Profil"
],
"Profil umbenennen": [
"Rename profile",
"Renommer le profil",
"Profil umbenänne"
],
"Liste laden": [
"Load list",
"Charger la liste",
"Lischte lade"
],
"CSV (*.csv);;Alle Dateien (*)": [
"CSV (*.csv);;All files (*)",
"CSV (*.csv);;Tous les fichiers (*)",
"CSV (*.csv);;Alli Dateie (*)"
],
"Liste speichern": [
"Save list",
"Enregistrer la liste",
"Lischte speichere"
],
"CSV (*.csv)": [
"CSV (*.csv)",
"CSV (*.csv)",
"CSV (*.csv)"
],
"Sollen die Passwörter mit in die Datei?": [
"Should the passwords go into the file?",
"Faut-il inclure les mots de passe dans le fichier ?",
"Sölled d Passwörter mit i d Datei?"
],
"Ohne Passwörter enthält die Datei nur Adressen, Hostnamen und Einstellungen. Mit Passwörtern stehen diese im Klartext in der Datei.": [
"Without passwords the file only contains addresses, hostnames and settings. With passwords, they are stored in the file in plain text.",
"Sans mots de passe, le fichier ne contient que les adresses, noms d'hôte et réglages. Avec, ils y figurent en clair.",
"Ohni Passwörter hät d Datei nur Adrässe, Hostnäme und Iistellige dine. Mit Passwörter stönd die im Klartext i de Datei."
],
"Ohne Passwörter": [
"Without passwords",
"Sans mots de passe",
"Ohni Passwörter"
],
"Mit Passwörtern": [
"With passwords",
"Avec mots de passe",
"Mit Passwörter"
],
"Neuen Benutzer und Passwort ausfüllen.": [
"Fill in the new user and password.",
"Renseigner le nouvel utilisateur et le mot de passe.",
"Neue Benutzer und Passwort uusfülle."
],
"Neuer Benutzer: nur Buchstaben, Ziffern, Punkt, Bindestrich und Unterstrich, höchstens 16 Zeichen.": [
"New user: only letters, digits, dot, hyphen and underscore, at most 16 characters.",
"Nouvel utilisateur : uniquement lettres, chiffres, point, tiret et tiret bas, 16 caractères au maximum.",
"Neue Benutzer: nur Buechstabe, Ziffere, Punkt, Bindestrich und Understrich, höchschtens 16 Zeiche."
],
"Neues Passwort: mindestens 8 Zeichen. Kürzere lehnen die meisten BMCs ab.": [
"New password: at least 8 characters. Most BMCs reject shorter ones.",
"Nouveau mot de passe : au moins 8 caractères. La plupart des BMC refusent les plus courts.",
"Neus Passwort: mindeschtens 8 Zeiche. Chürzeri lehned di meischte BMCs ab."
],
"Die Serverliste ist leer.": [
"The server list is empty.",
"La liste des serveurs est vide.",
"D Serverlischte isch läär."
],
"Zeile {0}, {1}: {2}": [
"Row {0}, {1}: {2}",
"Ligne {0}, {1} : {2}",
"Ziile {0}, {1}: {2}"
],
"Adressen aus der Serverliste („Neuware“) neu einlesen": ["Re-read the addresses from the server list (“New hardware”)", "Relire les adresses de la liste des serveurs (« Matériel neuf »)", "Adrässe us de Serverlischte („Neuware“) neu iiläse"],
"Adresse des Geräts, von dem die Attribute gelesen werden.\nVorschläge kommen aus der Serverliste in „Neuware“, es geht aber jede\nerreichbare Adresse.": ["Address of the device the attributes are read from.\nSuggestions come from the server list in “New hardware”, but any\nreachable address works.", "Adresse de l'appareil dont les attributs sont lus.\nLes suggestions viennent de la liste de « Matériel neuf », mais toute\nadresse joignable convient.", "Adrässe vom Grät, vo dem d Attribut gläse wärded.\nVorschläg chömed us de Serverlischte in „Neuware“, es gaht aber jedi\nerreichbari Adrässe."],
"Attribute lesen": [
"Read attributes",
"Lire les attributs",
"Attribut läse"
],
"Liest die tatsächlich vorhandenen UEFI-Attribute von diesem einen\nGerät. Nur Lesen, es wird nichts verändert.": [
"Reads the UEFI attributes that actually exist on this one\ndevice. Read only, nothing is changed.",
"Lit les attributs UEFI réellement présents sur cet\nappareil. Lecture seule, rien n'est modifié.",
"Liest d UEFI-Attribut, wo's uf dem einte Grät würkli git.\nNur läse, es wird nüt gänderet."
],
"Referenzgerät (nur zum Lesen)": [
"Reference device (read only)",
"Appareil de référence (lecture seule)",
"Referänzgrät (nur zum Läse)"
],
"Da sich UEFI-Attribute je Modell unterscheiden, zeigt das Tool nur die\ntatsächlich vorhandenen eines echten Geräts an, statt sie zu erraten.": [
"Because UEFI attributes differ per model, the tool only shows those\nthat really exist on a real device instead of guessing them.",
"Comme les attributs UEFI diffèrent selon le modèle, l'outil n'affiche que ceux\nréellement présents sur un vrai appareil, au lieu de les deviner.",
"Will sich UEFI-Attribut je nach Modäll underscheided, zeiget s Tool nur die,\nwo's uf eme ächte Grät würkli git, statt sie z errate."
],
"Feste Wahl überspringt die Erkennung. „Automatisch erkennen“ ermittelt den\nHersteller dieses einen Geräts selbst.": [
"A fixed choice skips detection. “Detect automatically” determines the\nvendor of this one device itself.",
"Un choix fixe saute la détection. « Détecter automatiquement » détermine\nle constructeur de cet appareil.",
"E feschti Wahl überspringt d Erchännig. „Automatisch erchänne“ findet de\nHersteller vo dem einte Grät sälber use."
],
"Leer lassen: Dann wird je Hersteller der Werksbenutzer verwendet\n(root/Administrator/ADMIN/USERID).": [
"Leave empty: the factory user of the vendor is used\n(root/Administrator/ADMIN/USERID).",
"Laisser vide : l'utilisateur d'usine du constructeur est utilisé\n(root/Administrator/ADMIN/USERID).",
"Läär laa: Denn wird je nach Hersteller de Werksbenutzer gnoh\n(root/Administrator/ADMIN/USERID)."
],
"Gilt zum Lesen des Referenzgeräts und, wenn die Vorlage später angewendet wird,\nfür alle Zeilen der Serverliste ohne eigenes Passwort.": [
"Used to read the reference device and, when the template is applied later,\nfor all rows of the server list without a password of their own.",
"Sert à lire l'appareil de référence et, quand le modèle est appliqué ensuite,\npour toutes les lignes de la liste sans mot de passe propre.",
"Gilt zum Läse vom Referänzgrät und, wenn d Vorlag spöter aagwändet wird,\nfür alli Ziile vo de Serverlischte ohni eigets Passwort."
],
"Adresse": [
"Address",
"Adresse",
"Adrässe"
],
"Attribut suchen …": [
"Search attribute …",
"Rechercher un attribut …",
"Attribut sueche …"
],
"Blendet Zeilen aus, deren Name den Suchtext nicht enthält.": [
"Hides rows whose name does not contain the search text.",
"Masque les lignes dont le nom ne contient pas le texte recherché.",
"Bländet Ziile uus, wo de Name de Suechtext nöd dine hät."
],
"Nur die Spalte „Neuer Wert“ ist bearbeitbar. Leer lassen, um ein\nAttribut unverändert zu lassen. Graue Zeilen (Passwörter, TPM,\nLöschfunktionen) sind gesperrt.": [
"Only the “New value” column can be edited. Leave empty to keep an\nattribute unchanged. Grey rows (passwords, TPM,\nerase functions) are locked.",
"Seule la colonne « Nouvelle valeur » est modifiable. Laisser vide pour ne pas\nchanger un attribut. Les lignes grises (mots de passe, TPM,\nfonctions d'effacement) sont verrouillées.",
"Nur d Spalte „Neue Wärt“ cha me bearbeite. Läär laa, zum es\nAttribut nöd z ändere. Graui Ziile (Passwörter, TPM,\nLöschfunktione) sind gsperrt."
],
"UEFI/BIOS-Attribute": [
"UEFI/BIOS attributes",
"Attributs UEFI/BIOS",
"UEFI/BIOS-Attribut"
],
"Mit Haken wird nichts gesendet, das Protokoll zeigt nur den Ablauf.\nOhne Haken werden die ausgefüllten „Neuer Wert“-Felder auf jedem Server\nder Liste („Neuware“) gesetzt, der das jeweilige Attribut tatsächlich hat.": ["When ticked, nothing is sent; the log only shows the sequence.\nUnticked, the filled-in “New value” fields are set on every server\nof the list (“New hardware”) that actually has the attribute.", "Coché, rien n'est envoyé ; le journal montre seulement le déroulement.\nDécoché, les champs « Nouvelle valeur » remplis sont appliqués sur chaque serveur\nde la liste (« Matériel neuf ») qui possède réellement l'attribut.", "Mit Häägli wird nüt gschickt, s Protokoll zeiget nur de Ablauf.\nOhni Häägli wärded di uusgfüllte „Neue Wärt“-Fälder uf jedem Server\nvo de Lischte („Neuware“) gsetzt, wo das Attribut würkli hät."],
"Danach hart neu starten": [
"Then hard restart",
"Puis redémarrage forcé",
"Dänn hert neu starte"
],
"UEFI-Einstellungen werden erst nach einem Neustart aktiv. Ohne Haken\nwerden sie nur vorgemerkt, es wird kein Neustart ausgelöst.\nMit Haken wird ein laufender Server hart neu gestartet (wie mit der\nReset-Taste), ein ausgeschalteter wird eingeschaltet. Nur für Server\nohne laufendes Betriebssystem verwenden.": [
"UEFI settings only take effect after a restart. Unticked,\nthey are only staged and no restart is triggered.\nTicked, a running server is hard restarted (like pressing the\nreset button) and a powered-off one is switched on. Use only for\nservers without a running operating system.",
"Les réglages UEFI ne prennent effet qu'après un redémarrage. Décoché,\nils sont seulement mis en attente, sans redémarrage.\nCoché, un serveur en marche est redémarré de force (comme avec le\nbouton Reset) et un serveur éteint est allumé. À n'utiliser que pour\ndes serveurs sans système d'exploitation en cours d'exécution.",
"UEFI-Iistellige wärded erscht nach eme Neustart aktiv. Ohni Häägli\nwärded sie nur vorgmerkt, es wird kein Neustart uusglöst.\nMit Häägli wird en laufende Server hert neu gstartet (wie mit de\nReset-Taschte), en abgstellte wird iigschaltet. Nur für Server\nohni laufends Betriebssystem bruuche."
],
"Auf allen Servern der Liste setzen": [
"Set on all servers in the list",
"Appliquer à tous les serveurs de la liste",
"Uf allne Server vo de Lischte setze"
],
"Wendet jedes ausgefüllte „Neuer Wert“-Feld auf jede Zeile der\nServerliste („Neuware“) an, der Reihe nach, mit Login wie oben.": ["Applies every filled-in “New value” field to every row of the\nserver list (“New hardware”), one after the other, with the login above.", "Applique chaque champ « Nouvelle valeur » rempli à chaque ligne de la\nliste des serveurs (« Matériel neuf »), l'une après l'autre, avec la connexion ci-dessus.", "Wändet jedes uusgfüllte „Neue Wärt“-Fäld uf jedi Ziile vo de\nServerlischte („Neuware“) aa, de Reihe naa, mit em Login wie obe."],
"Lese …": [
"Reading …",
"Lecture …",
"Am Läse …"
],
"{0} Attribute ({1})": [
"{0} attributes ({1})",
"{0} attributs ({1})",
"{0} Attribut ({1})"
],
"Eine Vorlage merkt sich Hersteller und die ausgefüllten „Neuer Wert“-Felder (Attribut ->\nWert). Eigener Bereich, getrennt von den Profilen in „Neuware“ und ohne Passwörter.": ["A template remembers the vendor and the filled-in “New value” fields (attribute ->\nvalue). A separate area, apart from the profiles in “New hardware” and without passwords.", "Un modèle mémorise le constructeur et les champs « Nouvelle valeur » remplis (attribut ->\nvaleur). Domaine à part, séparé des profils de « Matériel neuf » et sans mots de passe.", "E Vorlag merkt sich de Hersteller und di uusgfüllte „Neue Wärt“-Fälder (Attribut ->\nWärt). Eigene Beriich, trännt vo de Profil in „Neuware“ und ohni Passwörter."],
"UEFI-Vorlage": [
"UEFI template",
"Modèle UEFI",
"UEFI-Vorlag"
],
"Neue UEFI-Vorlage": [
"New UEFI template",
"Nouveau modèle UEFI",
"Neui UEFI-Vorlag"
],
"UEFI-Vorlage umbenennen": [
"Rename UEFI template",
"Renommer le modèle UEFI",
"UEFI-Vorlag umbenänne"
],
"Trockenlauf": [
"Dry run",
"Simulation",
"Trocheluuf"
],
"Scharfer Lauf": [
"Live run",
"Exécution réelle",
"Scharfe Luuf"
],
"— {0}, {1} Server —": [
"— {0}, {1} servers —",
"— {0}, {1} serveurs —",
"— {0}, {1} Server —"
],
"Läuft …": [
"Running …",
"En cours …",
"De Steward isch dra …"
],
"Netzwerkkarten neu einlesen": [
"Re-read network cards",
"Relire les cartes réseau",
"Netzwerkcharte neu iiläse"
],
"Netzwerkkarte dieses Rechners, die am Staging-Switch hängt.\nNur auf dieser Karte werden Adressen vergeben. Sie braucht eine feste IP.": [
"Network card of this computer that is connected to the staging switch.\nAddresses are only handed out on this card. It needs a static IP.",
"Carte réseau de cet ordinateur reliée au switch de staging.\nLes adresses ne sont attribuées que sur cette carte. Elle doit avoir une IP fixe.",
"Netzwerkcharte vo dem Rächner, wo am Staging-Switch hanget.\nNur uf dere Charte wärded Adrässe vergäh. Sie bruucht e feschti IP."
],
"DHCP starten": [
"Start DHCP",
"Démarrer DHCP",
"DHCP starte"
],
"Startet bzw. stoppt die Adressvergabe auf der gewählten Karte.\nNur an einem getrennten Staging-Switch verwenden.": [
"Starts or stops handing out addresses on the selected card.\nUse only on a separate staging switch.",
"Démarre ou arrête l'attribution d'adresses sur la carte choisie.\nÀ n'utiliser que sur un switch de staging séparé.",
"Startet bzw. stoppt d Adrässvergab uf de gwählte Charte.\nNur a eme trännte Staging-Switch bruuche."
],
"Gestoppt": [
"Stopped",
"Arrêté",
"Gstoppt"
],
"DHCP-Server für das Staging-Netz": [
"DHCP server for the staging network",
"Serveur DHCP pour le réseau de staging",
"DHCP-Server fürs Staging-Netz"
],
"Netzwerkkarte": [
"Network card",
"Carte réseau",
"Netzwerkcharte"
],
"Adressbereich": [
"Address range",
"Plage d'adresses",
"Adrässberiich"
],
"Geräte, die eine Adresse bekommen haben. Der gemeldete Name hilft bei der Zuordnung,\nz. B. „idrac-“ plus Service-Tag oder „ILO“ plus Seriennummer.": [
"Devices that received an address. The reported name helps to identify them,\ne.g. “idrac-” plus service tag or “ILO” plus serial number.",
"Appareils ayant reçu une adresse. Le nom annoncé aide à les identifier,\np. ex. « idrac- » plus Service Tag ou « ILO » plus numéro de série.",
"Grät, wo e Adrässe übercho händ. De gmäldet Name hilft bi de Zueordnig,\nz. B. „idrac-“ plus Service-Tag oder „ILO“ plus Serienummere."
],
"In Serverliste übernehmen": [
"Add to server list",
"Reprendre dans la liste des serveurs",
"I d Serverlischte übernäh"
],
"Trägt die vergebenen Adressen als „Aktuelle IP“ in die Serverliste ein.\nMit Markierung nur die markierten Geräte, sonst alle.": [
"Enters the assigned addresses as “Current IP” in the server list.\nWith a selection only the selected devices, otherwise all.",
"Inscrit les adresses attribuées comme « IP actuelle » dans la liste des serveurs.\nAvec une sélection, seulement les appareils sélectionnés, sinon tous.",
"Treit di vergäbne Adrässe als „Aktuelli IP“ i d Serverlischte ii.\nMit Markierig nur di markierte Grät, susch alli."
],
"0 Geräte": [
"0 devices",
"0 appareil",
"0 Grät"
],
"Gefundene Geräte": [
"Devices found",
"Appareils trouvés",
"Gfundeni Grät"
],
"Ende liegt vor dem Anfang des Bereichs": [
"End is before the start of the range",
"La fin précède le début de la plage",
"S Änd liit vor em Aafang vom Beriich"
],
"Keine Netzwerkkarte gefunden.": [
"No network card found.",
"Aucune carte réseau trouvée.",
"Kei Netzwerkcharte gfunde."
],
"Die Karte hat eine öffentliche Adresse ({0}). Ein Staging-Netz nutzt private Adressen, deshalb wird hier kein DHCP-Server gestartet.": [
"The card has a public address ({0}). A staging network uses private addresses, so no DHCP server is started here.",
"La carte a une adresse publique ({0}). Un réseau de staging utilise des adresses privées ; aucun serveur DHCP n'est donc démarré ici.",
"D Charte hät e öffentlichi Adrässe ({0}). Es Staging-Netz bruucht privati Adrässe, drum wird da kein DHCP-Server gstartet."
],
"Kein anderer DHCP-Server hat innerhalb von 1,5 Sekunden geantwortet.": [
"No other DHCP server answered within 1.5 seconds.",
"Aucun autre serveur DHCP n'a répondu en 1,5 seconde.",
"Kein andere DHCP-Server hät innert 1,5 Sekunde Antwort gäh."
],
"Ob dort schon ein DHCP-Server läuft, ließ sich nicht prüfen (Port 68 braucht Root-Rechte).": [
"Whether a DHCP server is already running there could not be checked (port 68 needs root rights).",
"Impossible de vérifier si un serveur DHCP y fonctionne déjà (le port 68 exige les droits root).",
"Öb det scho en DHCP-Server lauft, hät sich nöd la prüefe (Port 68 bruucht Root-Rächt)."
],
"Ein zweiter DHCP-Server in einem Produktivnetz stört dort alle Geräte.\n\nHängt diese Karte wirklich an einem getrennten Staging-Switch?": [
"A second DHCP server in a production network disrupts all devices there.\n\nIs this card really connected to a separate staging switch?",
"Un second serveur DHCP dans un réseau de production y perturbe tous les appareils.\n\nCette carte est-elle vraiment reliée à un switch de staging séparé ?",
"En zweite DHCP-Server im Produktivnetz stört det alli Grät.\n\nHanget die Charte würkli a eme trännte Staging-Switch?"
],
"DHCP stoppen": [
"Stop DHCP",
"Arrêter DHCP",
"DHCP stoppe"
],
"<b style='color:#3fa45b'>Läuft</b> auf {0}, {1} bis {2}": [
"<b style='color:#3fa45b'>Running</b> on {0}, {1} to {2}",
"<b style='color:#3fa45b'>Actif</b> sur {0}, {1} à {2}",
"<b style='color:#3fa45b'>Lauft</b> uf {0}, {1} bis {2}"
],
"Anmeldung abgebrochen": [
"Authentication cancelled",
"Authentification annulée",
"Aamäldig abbroche"
],
"Start nicht möglich oder Anmeldung fehlgeschlagen": [
"Could not start, or authentication failed",
"Démarrage impossible ou échec de l'authentification",
"Start nöd möglich oder Aamäldig fählgschlage"
],
"Port 67 belegt oder Karte nicht bereit, siehe Protokoll": [
"Port 67 in use or card not ready, see log",
"Port 67 occupé ou carte non prête, voir le journal",
"Port 67 bsetzt oder Charte nöd parat, lueg im Protokoll"
],
"{0} Geräte": [
"{0} devices",
"{0} appareils",
"{0} Grät"
],
"{0} Geräte übernommen: jetzt neue IP und Hostname eintragen": [
"{0} devices added: now enter new IP and hostname",
"{0} appareils repris : saisir maintenant la nouvelle IP et le nom d'hôte",
"{0} Grät übernoh: jetzt neui IP und Hostname iiträge"
],
"Automatisch übernehmen": [
"Add automatically",
"Ajouter automatiquement",
"Automatisch übernäh"
],
"Trägt neu gefundene Geräte von selbst in die Serverliste ein (Aktuelle IP).\nEs wird nichts konfiguriert: Neue IP, Hostname und der Start bleiben Handarbeit.\nEin Gerät kommt nur einmal in die Liste; wer eine Zeile löscht, bekommt sie nicht\nvon selbst zurück. Während eines Laufs wartet die Übernahme bis zum Ende.\nOhne Haken übernimmt nur der Knopf rechts.": [
"Adds newly found devices to the server list by itself (Current IP).\nNothing gets configured: new IP, hostname and the start stay manual.\nA device is added only once; if you delete a row, it does not come back\non its own. During a run, adding waits until the run is over.\nWithout the tick, only the button on the right adds devices.",
"Ajoute automatiquement les appareils nouvellement trouvés à la liste des serveurs (IP actuelle).\nRien n'est configuré : nouvelle IP, nom d'hôte et démarrage restent manuels.\nUn appareil n'est ajouté qu'une seule fois ; une ligne supprimée ne revient pas\ntoute seule. Pendant une exécution, l'ajout attend la fin.\nSans coche, seul le bouton de droite ajoute des appareils.",
"Träit neu gfundeni Grät vo sälber i d Serverlischte ii (Aktuelli IP).\nEs wird nüt konfiguriert: Neui IP, Hostname und de Start bliibed Handarbeit.\nEs Grät chunnt nume eimal i d Lischte; wer e Ziile löscht, überchunnt sie nöd\nvo sälber zrugg. Während emene Luuf wartet d Übernahm bis zum Ändi.\nOhni Häägli übernimmt nume de Chnopf rächts."
],
"{0} Geräte automatisch übernommen: jetzt neue IP und Hostname eintragen": [
"{0} devices added automatically: now enter new IP and hostname",
"{0} appareils repris automatiquement : saisir maintenant la nouvelle IP et le nom d'hôte",
"{0} Grät automatisch übernoh: jetzt neui IP und Hostname iiträge"
],
"Ja": [
"Yes",
"Oui",
"Ja"
],
"Zur Bestätigung die Anzahl der betroffenen Server eintippen:": [
"To confirm, type the number of servers affected:",
"Pour confirmer, saisir le nombre de serveurs concernés :",
"Zum Bestätige d Aazahl vo de betroffene Server iitippe:"
],
"  FEHLER: {0} (HTTP {1})": [
"  ERROR: {0} (HTTP {1})",
"  ERREUR : {0} (HTTP {1})",
"  FÄHLER: {0} (HTTP {1})"
],
"Benutzer (vorhandenes Konto)": [
"User (existing account)",
"Utilisateur (compte existant)",
"Benutzer (Konto git's scho)"
],
"Login/Erreichbarkeit": [
"Login/reachability",
"Connexion/accessibilité",
"Login/Erreichbarkeit"
],
"User-Slot lesen": [
"Read user slot",
"Lecture de l'emplacement utilisateur",
"User-Slot läse"
],
"  FEHLER: User-Slot {0} ist schon belegt (Benutzer {1}). Nichts überschrieben, bitte einen freien Slot wählen.": [
"  ERROR: user slot {0} is already in use (user {1}). Nothing overwritten, please choose a free slot.",
"  ERREUR : l'emplacement utilisateur {0} est déjà occupé (utilisateur {1}). Rien n'a été écrasé, choisir un emplacement libre.",
"  FÄHLER: User-Slot {0} isch scho bsetzt (Benutzer {1}). Nüt überschribe, bitte en freie Slot wähle."
],
"DHCP abschalten": [
"Disable DHCP",
"Désactivation du DHCP",
"DHCP abschalte"
],
"Statische IP": [
"Static IP",
"IP fixe",
"Statischi IP"
],
"iLO-Neustart": [
"iLO restart",
"Redémarrage de l'iLO",
"iLO-Neustart"
],
"  Hersteller: {0}, Anmeldung als {1}": [
"  Vendor: {0}, logging in as {1}",
"  Constructeur : {0}, connexion en tant que {1}",
"  Hersteller: {0}, Aamäldig als {1}"
],
"Kontrolle der neuen Adressen (bis zu {0}s)...": [
"Checking the new addresses (up to {0}s)...",
"Contrôle des nouvelles adresses (jusqu'à {0} s)...",
"Kontrolle vo de neue Adrässe (bis zu {0}s)..."
],
"  Trockenlauf: Attribute nicht wirklich gelesen, zeige den Ablauf ungeprüft": [
"  Dry run: attributes not actually read, showing the sequence unchecked",
"  Simulation : attributs non lus réellement, déroulement affiché sans vérification",
"  Trocheluuf: Attribut nöd würkli gläse, zeige de Ablauf unprüeft"
],
"  nichts zu ändern, kein Neustart": [
"  nothing to change, no restart",
"  rien à modifier, pas de redémarrage",
"  nüt z ändere, kein Neustart"
],
"BIOS-Attribute setzen": [
"Set BIOS attributes",
"Écriture des attributs BIOS",
"BIOS-Attribut setze"
],
"  gesetzt: ": [
"  set: ",
"  défini : ",
"  gsetzt: "
],
"  Hinweis: wird erst beim nächsten Neustart übernommen (kein Neustart ausgelöst)": [
"  Note: takes effect at the next restart (no restart triggered)",
"  Remarque : pris en compte au prochain redémarrage (aucun redémarrage lancé)",
"  Hiiwiis: wird erscht bim nächschte Neustart übernoh (kein Neustart uusglöst)"
],
"  Hinweis: Betriebszustand unbekannt, kein Neustart ausgelöst. Bitte von Hand neu starten.": [
"  Note: power state unknown, no restart triggered. Please restart by hand.",
"  Remarque : état d'alimentation inconnu, aucun redémarrage lancé. Redémarrer à la main.",
"  Hiiwiis: Betriebszuestand unbekannt, kein Neustart uusglöst. Bitte vo Hand neu starte."
],
"  Neustart ausgelöst ({0}); die Attribute greifen danach": [
"  Restart triggered ({0}); the attributes apply afterwards",
"  Redémarrage lancé ({0}) ; les attributs s'appliquent ensuite",
"  Neustart uusglöst ({0}); d Attribut griifed dänn"
],
"  Hersteller: {0}": [
"  Vendor: {0}",
"  Constructeur : {0}",
"  Hersteller: {0}"
],
"Angebot   {0}  an {1} {2}": [
"Offer     {0}  to {1} {2}",
"Offre     {0}  à {1} {2}",
"Aagebot   {0}  a {1} {2}"
],
"Vergeben  {0}  an {1} {2}": [
"Assigned  {0}  to {1} {2}",
"Attribué  {0}  à {1} {2}",
"Vergäh    {0}  a {1} {2}"
],
"DHCP-Server läuft auf {0}, Bereich {1} bis {2}": [
"DHCP server running on {0}, range {1} to {2}",
"Serveur DHCP actif sur {0}, plage {1} à {2}",
"DHCP-Server lauft uf {0}, Beriich {1} bis {2}"
],
"Speichern": [
"Save",
"Enregistrer",
"Speichere"
],
"Speichert die aktuellen Einstellungen in das gewählte Profil.\nOhne gewähltes Profil wird ein neues angelegt.": [
"Saves the current settings to the selected profile.\nWithout a selected profile a new one is created.",
"Enregistre les réglages actuels dans le profil choisi.\nSans profil choisi, un nouveau est créé.",
"Speicheret di aktuelle Iistellige is gwählte Profil.\nOhni gwählts Profil wird es neus aagleit."
],
"Neu …": [
"New …",
"Nouveau …",
"Neu …"
],
"Legt aus den aktuellen Einstellungen ein neues Profil an.": [
"Creates a new profile from the current settings.",
"Crée un nouveau profil à partir des réglages actuels.",
"Leit us de aktuelle Iistellige es neus Profil aa."
],
"Umbenennen …": [
"Rename …",
"Renommer …",
"Umbenänne …"
],
"Gibt dem gewählten Profil einen anderen Namen.": [
"Gives the selected profile another name.",
"Donne un autre nom au profil choisi.",
"Git em gwählte Profil en andere Name."
],
"Löschen": [
"Delete",
"Supprimer",
"Lösche"
],
"Löscht das gewählte Profil. Die Einstellungen in der Maske bleiben stehen.": [
"Deletes the selected profile. The settings in the form stay as they are.",
"Supprime le profil choisi. Les réglages du formulaire restent en place.",
"Löscht s gwählte Profil. D Iistellige i de Maske bliibed stah."
],
"Profil „{0}“ geladen": [
"Profile “{0}” loaded",
"Profil « {0} » chargé",
"Profil „{0}“ glade"
],
"Profil „{0}“ gespeichert (ohne Passwörter)": [
"Profile “{0}” saved (without passwords)",
"Profil « {0} » enregistré (sans mots de passe)",
"Profil „{0}“ gspeicheret (ohni Passwörter)"
],
"Dieser Name ist reserviert.": [
"This name is reserved.",
"Ce nom est réservé.",
"Dä Name isch reserviert."
],
"Kein Profil gewählt": [
"No profile selected",
"Aucun profil choisi",
"Kei Profil gwählt"
],
"Das Profil „{0}“ gibt es schon.": [
"The profile “{0}” already exists.",
"Le profil « {0} » existe déjà.",
"S Profil „{0}“ git's scho."
],
"Profil „{0}“ heißt jetzt „{1}“": [
"Profile “{0}” is now called “{1}”",
"Le profil « {0} » s'appelle maintenant « {1} »",
"Profil „{0}“ heisst jetzt „{1}“"
],
"Profil löschen": [
"Delete profile",
"Supprimer le profil",
"Profil lösche"
],
"Profil „{0}“ wirklich löschen?": [
"Really delete profile “{0}”?",
"Vraiment supprimer le profil « {0} » ?",
"Profil „{0}“ würkli lösche?"
],
"Profil „{0}“ gelöscht": [
"Profile “{0}” deleted",
"Profil « {0} » supprimé",
"Profil „{0}“ glöscht"
],
"<small>Leer lassen für: Dell root · HPE Administrator · Supermicro ADMIN · Lenovo USERID</small>": [
"<small>Leave empty for: Dell root · HPE Administrator · Supermicro ADMIN · Lenovo USERID</small>",
"<small>Laisser vide pour : Dell root · HPE Administrator · Supermicro ADMIN · Lenovo USERID</small>",
"<small>Läär laa für: Dell root · HPE Administrator · Supermicro ADMIN · Lenovo USERID</small>"
],
"<small>Leer lassen für den Werksbenutzer {0}</small>": [
"<small>Leave empty for the factory user {0}</small>",
"<small>Laisser vide pour l'utilisateur d'usine {0}</small>",
"<small>Läär laa für de Werksbenutzer {0}</small>"
],
"Es sind schon Einträge vorhanden.": [
"There are already entries.",
"Il y a déjà des entrées.",
"Es hät scho Iiträg i de Lischte."
],
"Ersetzen": [
"Replace",
"Remplacer",
"Ersetze"
],
"Anhängen": [
"Append",
"Ajouter",
"Aahänke"
],
"Geladen: {0}": [
"Loaded: {0}",
"Chargé : {0}",
"Glade: {0}"
],
"Gespeichert in {0}": [
"Saved to {0}",
"Enregistré dans {0}",
"Gspeicheret i {0}"
],
"Diese Adresse ist die aktuelle IP von Zeile {0}": [
"This address is the current IP of row {0}",
"Cette adresse est l'IP actuelle de la ligne {0}",
"Die Adrässe isch di aktuell IP vo Ziile {0}"
],
"fehlt": [
"missing",
"manquant",
"fählt"
],
"Zeile {0}: kein Passwort (weder in der Zeile noch oben).": [
"Row {0}: no password (neither in the row nor above).",
"Ligne {0} : pas de mot de passe (ni dans la ligne ni en haut).",
"Ziile {0}: kei Passwort (weder i de Ziile no obe)."
],
"Zeile {0}: Die neue IP ist eine Adresse dieses Rechners.": [
"Row {0}: the new IP is an address of this computer.",
"Ligne {0} : la nouvelle IP est une adresse de cet ordinateur.",
"Ziile {0}: Di neu IP isch e Adrässe vo dem Rächner."
],
"Zeile {0}: Die neue IP liegt im Bereich des laufenden DHCP-Servers ({1} bis {2}). Dort könnte sie ein zweites Mal vergeben werden.": [
"Row {0}: the new IP lies in the range of the running DHCP server ({1} to {2}). It could be handed out a second time there.",
"Ligne {0} : la nouvelle IP est dans la plage du serveur DHCP actif ({1} à {2}). Elle pourrait y être attribuée une seconde fois.",
"Ziile {0}: Di neu IP liit im Beriich vom laufende DHCP-Server ({1} bis {2}). Det chönnt sie es zweits Mal vergäh wärde."
],
"Lauf aktiv": [
"Run active",
"Exécution en cours",
"Luuf aktiv"
],
"Es läuft noch eine Konfiguration. Bitte warten oder erst abbrechen.": [
"A configuration is still running. Please wait or cancel it first.",
"Une configuration est encore en cours. Patienter ou l'annuler d'abord.",
"De Steward isch no am Schaffe. Bitte warte oder zerscht abbräche."
],
"Beenden": [
"Quit",
"Quitter",
"Beände"
],
"Die Serverliste wurde nicht gespeichert.": [
"The server list has not been saved.",
"La liste des serveurs n'a pas été enregistrée.",
"D Serverlischte isch nöd gspeicheret worde."
],
"Das Tool speichert nichts von selbst. Ohne Speichern ist die Liste nach dem Schließen weg.": [
"The tool saves nothing on its own. Without saving, the list is gone after closing.",
"L'outil n'enregistre rien de lui-même. Sans enregistrement, la liste est perdue à la fermeture.",
"S Tool speicheret nüt vo sälber. Ohni Speichere isch d Lischte nach em Zuemache futsch."
],
"Speichern …": [
"Save …",
"Enregistrer …",
"Speichere …"
],
"Verwerfen": [
"Discard",
"Abandonner",
"Verwärfe"
],
"Attribut": [
"Attribute",
"Attribut",
"Attribut"
],
"Aktueller Wert": [
"Current value",
"Valeur actuelle",
"Aktuelle Wärt"
],
"Neuer Wert": [
"New value",
"Nouvelle valeur",
"Neue Wärt"
],
"Bitte eine Adresse eintragen oder aus der Serverliste wählen.": [
"Please enter an address or pick one from the server list.",
"Saisir une adresse ou en choisir une dans la liste des serveurs.",
"Bitte e Adrässe iiträge oder us de Serverlischte wähle."
],
"Speichert die aktuellen „Neuer Wert“-Felder in die gewählte Vorlage.\nOhne gewählte Vorlage wird eine neue angelegt.": [
"Saves the current “New value” fields to the selected template.\nWithout a selected template a new one is created.",
"Enregistre les champs « Nouvelle valeur » actuels dans le modèle choisi.\nSans modèle choisi, un nouveau est créé.",
"Speicheret di aktuelle „Neue Wärt“-Fälder i di gwählti Vorlag.\nOhni gwählti Vorlag wird e neui aagleit."
],
"Legt aus den aktuellen Feldern eine neue Vorlage an.": [
"Creates a new template from the current fields.",
"Crée un nouveau modèle à partir des champs actuels.",
"Leit us de aktuelle Fälder e neui Vorlag aa."
],
"Gibt der gewählten Vorlage einen anderen Namen.": [
"Gives the selected template another name.",
"Donne un autre nom au modèle choisi.",
"Git de gwählte Vorlag en andere Name."
],
"Löscht die gewählte Vorlage.": [
"Deletes the selected template.",
"Supprime le modèle choisi.",
"Löscht di gwählti Vorlag."
],
"Vorlage „{0}“ geladen": [
"Template “{0}” loaded",
"Modèle « {0} » chargé",
"Vorlag „{0}“ glade"
],
"Vorlage „{0}“ gespeichert": [
"Template “{0}” saved",
"Modèle « {0} » enregistré",
"Vorlag „{0}“ gspeicheret"
],
"Keine Vorlage gewählt": [
"No template selected",
"Aucun modèle choisi",
"Kei Vorlag gwählt"
],
"Die Vorlage „{0}“ gibt es schon.": [
"The template “{0}” already exists.",
"Le modèle « {0} » existe déjà.",
"D Vorlag „{0}“ git's scho."
],
"Vorlage „{0}“ heißt jetzt „{1}“": [
"Template “{0}” is now called “{1}”",
"Le modèle « {0} » s'appelle maintenant « {1} »",
"Vorlag „{0}“ heisst jetzt „{1}“"
],
"UEFI-Vorlage löschen": [
"Delete UEFI template",
"Supprimer le modèle UEFI",
"UEFI-Vorlag lösche"
],
"Vorlage „{0}“ wirklich löschen?": [
"Really delete template “{0}”?",
"Vraiment supprimer le modèle « {0} » ?",
"Vorlag „{0}“ würkli lösche?"
],
"Vorlage „{0}“ gelöscht": [
"Template “{0}” deleted",
"Modèle « {0} » supprimé",
"Vorlag „{0}“ glöscht"
],
"UEFI-Einstellungen": [
"UEFI settings",
"Réglages UEFI",
"UEFI-Iistellige"
],
"Keine „Neuer Wert“-Felder ausgefüllt.": [
"No “New value” fields filled in.",
"Aucun champ « Nouvelle valeur » rempli.",
"Kei „Neue Wärt“-Fälder uusgfüllt. Ohni Uuftrag macht de Steward nüt."
],
"Die Serverliste („Neuware“) ist leer.": ["The server list (“New hardware”) is empty.", "La liste des serveurs (« Matériel neuf ») est vide.", "D Serverlischte („Neuware“) isch läär."],
"Falsche UEFI-Werte können dazu führen, dass ein Server nicht mehr startet.": [
"Wrong UEFI values can stop a server from booting.",
"De mauvaises valeurs UEFI peuvent empêcher un serveur de démarrer.",
"Falschi UEFI-Wärt chönd dezue füehre, dass en Server nüme startet."
],
"Wird nach dem aktuellen Server abgebrochen …": [
"Stopping after the current server …",
"Arrêt après le serveur en cours …",
"Nach em aktuelle Server isch Schluss …"
],
"Fertig ohne Fehler": [
"Finished without errors",
"Terminé sans erreur",
"Fertig, alles tipptopp."
],
"Beendet mit Fehlern (Code {0})": [
"Finished with errors (code {0})",
"Terminé avec des erreurs (code {0})",
"Fertig, aber nöd ganz suuber: Es hät Fähler gäh (Code {0})"
],
"bis": [
"to",
"à",
"bis"
],
"<small><b>Nur an einem getrennten Staging-Switch starten</b>, nie im Produktivnetz.</small>": [
"<small><b>Start only on a separate staging switch</b>, never in the production network.</small>",
"<small><b>Démarrer uniquement sur un switch de staging séparé</b>, jamais dans le réseau de production.</small>",
"<small><b>Nur a eme trännte Staging-Switch starte</b>, nie im Produktivnetz.</small>"
],
"Vergebene IP": [
"Assigned IP",
"IP attribuée",
"Vergäbni IP"
],
"MAC-Adresse": [
"MAC address",
"Adresse MAC",
"MAC-Adrässe"
],
"Gemeldeter Name": [
"Reported name",
"Nom annoncé",
"Gmäldete Name"
],
"Die Karte „{0}“ hat keine IPv4-Adresse.\n\nIn den Adaptereinstellungen (ncpa.cpl) bei dieser Karte unter „Internetprotokoll, Version 4“ eine feste Adresse eintragen, z. B. 192.168.0.1 mit Maske 255.255.255.0, Gateway leer lassen.\n\nDanach hier auf ↻ klicken.": [
"The card “{0}” has no IPv4 address.\n\nIn the adapter settings (ncpa.cpl), enter a static address for this card under “Internet Protocol Version 4”, e.g. 192.168.0.1 with mask 255.255.255.0, and leave the gateway empty.\n\nThen click ↻ here.",
"La carte « {0} » n'a pas d'adresse IPv4.\n\nDans les paramètres de la carte (ncpa.cpl), saisir une adresse fixe sous « Protocole Internet version 4 », p. ex. 192.168.0.1 avec le masque 255.255.255.0, et laisser la passerelle vide.\n\nCliquer ensuite ici sur ↻.",
"D Charte „{0}“ hät kei IPv4-Adrässe.\n\nI de Adapteriistellige (ncpa.cpl) bi dere Charte under „Internetprotokoll, Version 4“ e feschti Adrässe iiträge, z. B. 192.168.0.1 mit Maske 255.255.255.0, Gateway läär laa.\n\nDänn da uf ↻ klicke."
],
"DHCP-Server": [
"DHCP server",
"Serveur DHCP",
"DHCP-Server"
],
"Über diese Karte läuft die Standardroute dieses Rechners (Internet bzw. Firmennetz). Das ist kein getrenntes Staging-Netz, deshalb wird hier kein DHCP-Server gestartet.\n\nFür das Staging die Karte mit fester Adresse und ohne Gateway einrichten.": [
"This computer's default route (internet or company network) runs over this card. That is not a separate staging network, so no DHCP server is started here.\n\nFor staging, set the card up with a static address and without a gateway.",
"La route par défaut de cet ordinateur (Internet ou réseau d'entreprise) passe par cette carte. Ce n'est pas un réseau de staging séparé ; aucun serveur DHCP n'est donc démarré ici.\n\nPour le staging, configurer la carte avec une adresse fixe et sans passerelle.",
"Über die Charte lauft d Standardroute vo dem Rächner (Internet bzw. Firmenetz). Das isch kei trännts Staging-Netz, drum wird da kein DHCP-Server gstartet.\n\nFürs Staging d Charte mit feschter Adrässe und ohni Gateway iirichte."
],
"DHCP-Server nicht gestartet": [
"DHCP server not started",
"Serveur DHCP non démarré",
"DHCP-Server nöd gstartet"
],
"DHCP-Server starten?": [
"Start DHCP server?",
"Démarrer le serveur DHCP ?",
"DHCP-Server starte?"
],
"Auf {0} ({1}) werden Adressen von {2} bis {3} vergeben.": [
"On {0} ({1}), addresses from {2} to {3} will be handed out.",
"Sur {0} ({1}), des adresses de {2} à {3} seront attribuées.",
"Uf {0} ({1}) wärded Adrässe vo {2} bis {3} vergäh."
],
"Keine neuen Geräte zum Übernehmen": [
"No new devices to add",
"Aucun nouvel appareil à reprendre",
"Kei neui Grät ume"
],
"Eingabe prüfen": [
"Check input",
"Vérifier la saisie",
"Iigab prüefe"
],
"Nichts zu tun": [
"Nothing to do",
"Rien à faire",
"Nüt z tue"
],
"Alle Server der Liste sind schon umgestellt.\n\nNeue Zeilen eintragen.": [
"All servers in the list have already been reconfigured.\n\nEnter new rows.",
"Tous les serveurs de la liste sont déjà reconfigurés.\n\nSaisir de nouvelles lignes.",
"Alli Server vo de Lischte sind scho umgstellt. De Steward hät Fiirabig.\n\nNeui Ziile iiträge."
],
"Die Zahl stimmt nicht. Es wurde nichts ausgeführt.": [
"The number does not match. Nothing was executed.",
"Le nombre ne correspond pas. Rien n'a été exécuté.",
"D Zahl stimmt nöd. Sicher isch sicher: Es isch nüt uusgfüehrt worde."
],
"DHCP-Server gestoppt: Über diese Karte läuft jetzt die Standardroute, das ist kein getrenntes Staging-Netz mehr.": [
"DHCP server stopped: the default route now runs over this card, so it is no longer a separate staging network.",
"Serveur DHCP arrêté : la route par défaut passe désormais par cette carte, ce n'est plus un réseau de staging séparé.",
"DHCP-Server gstoppt: Über die Charte lauft jetzt d Standardroute, das isch kei trännts Staging-Netz meh."
],
"DHCP-Server gestoppt: Im Staging-Netz ist während des Laufs ein weiterer DHCP-Server aufgetaucht ({0}). Zwei DHCP-Server im selben Netz würden Geräte stören.": [
"DHCP server stopped: another DHCP server ({0}) appeared on the staging network while running. Two DHCP servers on the same network would disrupt devices.",
"Serveur DHCP arrêté : un autre serveur DHCP ({0}) est apparu sur le réseau de staging pendant l'exécution. Deux serveurs DHCP sur le même réseau perturberaient les appareils.",
"DHCP-Server gstoppt: Im Staging-Netz isch während em Luuf en anderi DHCP-Server ufgtaucht ({0}). Zwei DHCP-Server im gliiche Netz würded d Gschirr störe."
],
"Sprache": [
"Language",
"Langue",
"Sproch"
],
"Die Sprache lässt sich nur wechseln, solange kein Lauf aktiv ist und der DHCP-Server gestoppt ist.": [
"The language can only be changed while no run is active and the DHCP server is stopped.",
"La langue ne peut être changée que si aucune exécution n'est en cours et que le serveur DHCP est arrêté.",
"D Sproch laht sich nur wächsle, solang kein Luuf aktiv isch und de DHCP-Server gstoppt isch."
],
"    Hinweis: bei HPE heißt der Werksbenutzer meist Administrator": [
"    Note: on HPE the factory user is usually Administrator",
"    Remarque : chez HPE, l'utilisateur d'usine est généralement Administrator",
"    Hiiwiis: bi HPE heisst de Werksbenutzer meischtens Administrator"
],
"    Hinweis: bei Supermicro heißt der Werksbenutzer meist ADMIN": [
"    Note: on Supermicro the factory user is usually ADMIN",
"    Remarque : chez Supermicro, l'utilisateur d'usine est généralement ADMIN",
"    Hiiwiis: bi Supermicro heisst de Werksbenutzer meischtens ADMIN"
],
"    Hinweis: bei Lenovo heißt der Werksbenutzer meist USERID": [
"    Note: on Lenovo the factory user is usually USERID",
"    Remarque : chez Lenovo, l'utilisateur d'usine est généralement USERID",
"    Hiiwiis: bi Lenovo heisst de Werksbenutzer meischtens USERID"
],
"== Abgebrochen, übrige Server nicht bearbeitet": [
"== Cancelled, remaining servers not processed",
"== Annulé, serveurs restants non traités",
"== Abbroche, übrigi Server nöd bearbeitet"
],
"Abbruch": [
"cancelled",
"annulé",
"Abbruch"
],
"== {0}: kein Passwort (weder in der Zeile noch oben)": [
"== {0}: no password (neither in the row nor above)",
"== {0} : pas de mot de passe (ni dans la ligne ni en haut)",
"== {0}: kei Passwort (weder i de Ziile no obe)"
],
"  Trockenlauf: Hersteller nicht erkennbar ({0}), zeige den Dell-Ablauf": [
"  Dry run: vendor not detectable ({0}), showing the Dell sequence",
"  Simulation : constructeur non détectable ({0}), affichage du déroulement Dell",
"  Trocheluuf: Hersteller nöd erchännbar ({0}), zeige de Dell-Ablauf"
],
"  FEHLER: nicht erreichbar": [
"  ERROR: not reachable",
"  ERREUR : injoignable",
"  FÄHLER: nöd erreichbar"
],
"  FEHLER: Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC)": [
"  ERROR: vendor not recognised (neither iDRAC, iLO, Supermicro nor XCC)",
"  ERREUR : constructeur non reconnu (ni iDRAC, ni iLO, ni Supermicro, ni XCC)",
"  FÄHLER: Hersteller nöd erchännt (weder iDRAC, iLO, Supermicro no XCC)"
],
"  FEHLER: Die neue IP {0} ist schon vergeben, dort antwortet bereits ein Gerät. Nichts geändert.": [
"  ERROR: the new IP {0} is already taken, a device answers there. Nothing changed.",
"  ERREUR : la nouvelle IP {0} est déjà prise, un appareil y répond. Rien n'a été modifié.",
"  FÄHLER: Di neu IP {0} isch scho vergäh, det antwortet scho es Grät. Nüt gänderet."
],
"  FEHLT   {0}": [
"  MISSING {0}",
"  ABSENT  {0}",
"  FÄHLT   {0}"
],
"BIOS-Attribute lesen": [
"Read BIOS attributes",
"Lecture des attributs BIOS",
"BIOS-Attribut läse"
],
"  FEHLER: gesperrt, wird nie gesetzt (Passwort-, TPM- und Lösch-Attribute): ": [
"  ERROR: locked, never set (password, TPM and erase attributes): ",
"  ERREUR : verrouillé, jamais défini (attributs de mot de passe, TPM et effacement) : ",
"  FÄHLER: gsperrt, wird nie gsetzt (Passwort-, TPM- und Lösch-Attribut): "
],
"  FEHLER: Wert passt nicht zum Attribut (Zahl bzw. Ein/Aus erwartet), nicht gesetzt: ": [
"  ERROR: value does not fit the attribute (number or on/off expected), not set: ",
"  ERREUR : la valeur ne convient pas à l'attribut (nombre ou activé/désactivé attendu), non défini : ",
"  FÄHLER: Wärt passt nöd zum Attribut (Zahl bzw. Ii/Uus erwartet), nöd gsetzt: "
],
"  nicht auf diesem Gerät vorhanden, übersprungen: ": [
"  not present on this device, skipped: ",
"  absent de cet appareil, ignoré : ",
"  uf dem Grät nöd vorhande, überspronge: "
],
"  schon so eingestellt: ": [
"  already set this way: ",
"  déjà réglé ainsi : ",
"  scho so iigstellt: "
],
"== {0}: kein Passwort": [
"== {0}: no password",
"== {0} : pas de mot de passe",
"== {0}: kei Passwort"
],
"Abgelehnt {0}  von {1}": [
"Refused   {0}  from {1}",
"Refusé    {0}  de {1}",
"Abglehnt  {0}  vo {1}"
],
"  FEHLER: unerwarteter Abbruch: {0!r}": [
"  ERROR: unexpected abort: {0!r}",
"  ERREUR : arrêt inattendu : {0!r}",
"  FÄHLER: unerwartete Abbruch: {0!r}"
],
"Gerät nicht erreichbar.": [
"Device not reachable.",
"Appareil injoignable.",
"Grät nöd erreichbar."
],
"Hersteller nicht erkannt (weder iDRAC, iLO, Supermicro noch XCC).": [
"Vendor not recognised (neither iDRAC, iLO, Supermicro nor XCC).",
"Constructeur non reconnu (ni iDRAC, ni iLO, ni Supermicro, ni XCC).",
"Hersteller nöd erchännt (weder iDRAC, iLO, Supermicro no XCC)."
],
"Unerwarteter Fehler: {0!r}": [
"Unexpected error: {0!r}",
"Erreur inattendue : {0!r}",
"Unerwartete Fähler: {0!r}"
],
"Ein DHCP-Server aus einem früheren, abgebrochenen Lauf lief noch und wurde beendet.": [
"A DHCP server from an earlier, aborted run was still running and has been stopped.",
"Un serveur DHCP d'une exécution précédente interrompue tournait encore et a été arrêté.",
"En DHCP-Server us eme früehnere, abbrochne Luuf isch no umegeischteret und isch beändet worde."
],
"Profile": [
"Profiles",
"Profils",
"Profil"
],
"{0} nicht schreibbar:\n{1}": [
"{0} cannot be written:\n{1}",
"Impossible d'écrire {0} :\n{1}",
"{0} laht sich nöd schriibe:\n{1}"
],
"Das Profil „{0}“ gibt es schon. Überschreiben?": [
"The profile “{0}” already exists. Overwrite?",
"Le profil « {0} » existe déjà. L'écraser ?",
"S Profil „{0}“ git's scho. Überschriibe?"
],
"{0} Server": [
"{0} servers",
"{0} serveurs",
"{0} Server"
],
" · <span style='color:#d9534f'>{0} mit Fehlern</span>": [
" · <span style='color:#d9534f'>{0} with errors</span>",
" · <span style='color:#d9534f'>{0} avec erreurs</span>",
" · <span style='color:#d9534f'>{0} mit Fähler</span>"
],
"Datei nicht lesbar:\n{0}": [
"File cannot be read:\n{0}",
"Fichier illisible :\n{0}",
"Datei nöd läsbar:\n{0}"
],
"Datei nicht schreibbar:\n{0}": [
"File cannot be written:\n{0}",
"Impossible d'écrire le fichier :\n{0}",
"Datei nöd schriibbar:\n{0}"
],
" (mit Passwörtern im Klartext)": [
" (with passwords in plain text)",
" (avec mots de passe en clair)",
" (mit Passwörter im Klartext)"
],
" (ohne Passwörter)": [
" (without passwords)",
" (sans mots de passe)",
" (ohni Passwörter)"
],
"{0} kommt mehrfach vor (Zeilen {1})": [
"{0} occurs more than once (rows {1})",
"{0} apparaît plusieurs fois (lignes {1})",
"{0} chunnt mehrfach vor (Ziile {1})"
],
"({0} weitere markierte Felder)": [
"({0} more marked fields)",
"({0} autres champs marqués)",
"({0} wiiteri markierti Fälder)"
],
"Attribute konnten nicht gelesen werden.": [
"Attributes could not be read.",
"Les attributs n'ont pas pu être lus.",
"D Attribut händ sich nöd la läse."
],
"UEFI-Vorlagen": [
"UEFI templates",
"Modèles UEFI",
"UEFI-Vorlage"
],
"Die Vorlage „{0}“ gibt es schon. Überschreiben?": [
"The template “{0}” already exists. Overwrite?",
"Le modèle « {0} » existe déjà. L'écraser ?",
"D Vorlag „{0}“ git's scho. Überschriibe?"
],
"… und {0} weitere": [
"… and {0} more",
"… et {0} autres",
"… und {0} wiiteri"
],
"Danach wird jeder Server hart neu gestartet, wie mit der Reset-Taste. Ein laufendes Betriebssystem wird dabei nicht sauber heruntergefahren.": [
"Afterwards every server is hard restarted, like pressing the reset button. A running operating system is not shut down cleanly.",
"Ensuite, chaque serveur est redémarré de force, comme avec le bouton Reset. Un système d'exploitation en marche n'est pas arrêté proprement.",
"Dänn wird jede Server hert neu gstartet, wie mit de Reset-Taschte. Es laufends Betriebssystem wird debii nöd suuber abegfahre."
],
"Es wird kein Neustart ausgelöst; die Änderung wirkt erst beim nächsten Neustart.": [
"No restart is triggered; the change takes effect at the next restart.",
"Aucun redémarrage n'est lancé ; la modification prend effet au prochain redémarrage.",
"Es wird kein Neustart uusglöst; d Änderig würkt erscht bim nächschte Neustart."
],
"Scharf ausführen?": [
"Run for real?",
"Exécuter réellement ?",
"Scharf uusfüehre?"
],
"Auf {0} Servern werden jetzt wirklich UEFI-Einstellungen geändert.": [
"UEFI settings will now really be changed on {0} servers.",
"Les réglages UEFI vont maintenant être réellement modifiés sur {0} serveurs.",
"Uf {0} Server wärded jetzt würkli UEFI-Iistellige gänderet."
],
"{0}  –  keine IPv4-Adresse": [
"{0}  –  no IPv4 address",
"{0}  –  pas d'adresse IPv4",
"{0}  –  kei IPv4-Adrässe"
],
"Liegt nicht im Netz der Karte ({0})": [
"Not in the network of the card ({0})",
"Hors du réseau de la carte ({0})",
"Liit nöd im Netz vo de Charte ({0})"
],
"Die Karte {0} hat keine IPv4-Adresse. Feste Adresse setzen, z. B. im Terminal:\n\nnmcli con add type ethernet ifname {1} con-name staging ipv4.method manual ipv4.addresses 192.168.0.1/24\nnmcli con up staging\n\nDanach hier auf ↻ klicken.": [
"The card {0} has no IPv4 address. Set a static address, e.g. in the terminal:\n\nnmcli con add type ethernet ifname {1} con-name staging ipv4.method manual ipv4.addresses 192.168.0.1/24\nnmcli con up staging\n\nThen click ↻ here.",
"La carte {0} n'a pas d'adresse IPv4. Définir une adresse fixe, p. ex. dans le terminal :\n\nnmcli con add type ethernet ifname {1} con-name staging ipv4.method manual ipv4.addresses 192.168.0.1/24\nnmcli con up staging\n\nCliquer ensuite ici sur ↻.",
"D Charte {0} hät kei IPv4-Adrässe. Feschti Adrässe setze, z. B. im Terminal:\n\nnmcli con add type ethernet ifname {1} con-name staging ipv4.method manual ipv4.addresses 192.168.0.1/24\nnmcli con up staging\n\nDänn da uf ↻ klicke."
],
"Diese Karte hat ihre eigene Adresse per DHCP bekommen. In diesem Netz läuft also schon ein DHCP-Server, ein zweiter würde dort alle Geräte stören. Deshalb wird hier keiner gestartet.\n\nFür das Staging der Karte eine feste Adresse geben.": [
"This card received its own address via DHCP. So a DHCP server is already running in this network, and a second one would disrupt all devices there. That is why none is started here.\n\nFor staging, give the card a static address.",
"Cette carte a reçu sa propre adresse par DHCP. Un serveur DHCP fonctionne donc déjà dans ce réseau, et un second y perturberait tous les appareils. C'est pourquoi aucun n'est démarré ici.\n\nPour le staging, donner une adresse fixe à la carte.",
"Die Charte hät ihri eigeti Adrässe per DHCP übercho. I dem Netz lauft also scho en DHCP-Server, en zweite würd det alli Grät störe. Drum wird da kein gstartet.\n\nFürs Staging de Charte e feschti Adrässe gäh."
],
"<b style='color:#d9534f'>Beendet (Code {0})</b>: ": [
"<b style='color:#d9534f'>Ended (code {0})</b>: ",
"<b style='color:#d9534f'>Terminé (code {0})</b> : ",
"<b style='color:#d9534f'>Beändet (Code {0})</b>: "
],
"⚠ Die aktuelle IP liegt in keinem Netz, an dem dieser Rechner direkt hängt. Für ein Staging-Gerät ist das ungewöhnlich. Ist es wirklich das richtige Gerät?": [
"⚠ The current IP is not in any network this computer is directly connected to. That is unusual for a staging device. Is it really the right device?",
"⚠ L'IP actuelle n'est dans aucun réseau auquel cet ordinateur est directement relié. C'est inhabituel pour un appareil en staging. Est-ce vraiment le bon appareil ?",
"⚠ Di aktuell IP liit i keim Netz, wo dä Rächner diräkt draa hanget. Für es Staging-Grät isch das ungwöhnlich. Isch es würkli s richtige Grät?"
],
"{0} Server werden jetzt wirklich umgestellt (Benutzer, Hostname, feste IP).": [
"{0} servers will now really be reconfigured (user, hostname, static IP).",
"{0} serveurs vont maintenant être réellement reconfigurés (utilisateur, nom d'hôte, IP fixe).",
"{0} Server wärded jetzt würkli umgstellt (Benutzer, Hostname, feschti IP)."
],
"— {0}, {1} Server": [
"— {0}, {1} servers",
"— {0}, {1} serveurs",
"— {0}, {1} Server"
],
" (ohne Zugriffsarten angelegt: Web-Anmeldung bitte einmal prüfen)": [
" (created without access types: please check the web login once)",
" (créé sans types d'accès : vérifier une fois la connexion web)",
" (ohni Zuegriffsarte aagleit: Web-Aamäldig bitte eimal prüefe)"
],
"Adressbereich erschöpft, keine Adresse mehr frei für ": [
"Address range exhausted, no address left for ",
"Plage d'adresses épuisée, plus d'adresse libre pour ",
"Adrässberiich uufbruucht, kei Adrässe meh frei für "
],
"Port 67 lässt sich nicht öffnen: {0}": [
"Port 67 cannot be opened: {0}",
"Impossible d'ouvrir le port 67 : {0}",
"Port 67 laht sich nöd uuftue: {0}"
],
" · <span style='color:#3fa45b'>{0} fertig</span>": [
" · <span style='color:#3fa45b'>{0} done</span>",
" · <span style='color:#3fa45b'>{0} terminés</span>",
" · <span style='color:#3fa45b'>{0} fertig</span>"
],
"Gesperrt: Passwort-, TPM- und Lösch-Attribute ändert das Tool nie. Ein Fehler dort kann den Server aussperren oder Daten kosten.": [
"Locked: the tool never changes password, TPM and erase attributes. A mistake there can lock you out of the server or cost data.",
"Verrouillé : l'outil ne modifie jamais les attributs de mot de passe, TPM et effacement. Une erreur peut bloquer l'accès au serveur ou coûter des données.",
"Gsperrt: Passwort-, TPM- und Lösch-Attribut änderet s Tool nie. En Fähler det cha de Server uussperre oder Date choschte."
],
"Netz- oder Broadcast-Adresse": [
"Network or broadcast address",
"Adresse de réseau ou de diffusion",
"Netz- oder Broadcast-Adrässe"
],
"Auf dieser Karte antwortet bereits ein DHCP-Server ({0}). Ein zweiter würde dort alle Geräte stören, deshalb wird hier keiner gestartet.": [
"A DHCP server already answers on this card ({0}). A second one would disrupt all devices there, so none is started here.",
"Un serveur DHCP répond déjà sur cette carte ({0}). Un second y perturberait tous les appareils ; aucun n'est donc démarré ici.",
"Uf dere Charte antwortet scho en DHCP-Server ({0}). En zweite würd det alli Grät störe, drum wird da kein gstartet."
],
"siehe Protokoll": [
"see log",
"voir le journal",
"lueg im Protokoll"
],
", {0} fertige übersprungen": [
", {0} done ones skipped",
", {0} terminés ignorés",
", {0} fertigi überspronge"
],
"  OK      {0}": [
"  OK      {0}",
"  OK      {0}",
"  OK      {0}"
],
"Paket verworfen: {0}": [
"Packet discarded: {0}",
"Paquet rejeté : {0}",
"Paket verworfe: {0}"
],
"Fertig umgestellt, wird beim nächsten Start übersprungen.\nSoll das Gerät noch einmal bearbeitet werden: Zeile markieren und „Erneut bearbeiten“ klicken.": [
"Reconfigured; skipped at the next start.\nTo process the device again: select the row and click “Process again”.",
"Reconfiguré ; ignoré au prochain démarrage.\nPour retraiter l'appareil : sélectionner la ligne et cliquer sur « Traiter à nouveau ».",
"Fertig umgstellt, wird bim nächschte Start überspronge.\nSöll s Grät nomal bearbeitet wärde: Ziile markiere und „Nomal bearbeite“ klicke."
],
"dnsmasq ist nicht installiert:\n\nsudo pacman -S dnsmasq": [
"dnsmasq is not installed:\n\nsudo pacman -S dnsmasq",
"dnsmasq n'est pas installé :\n\nsudo pacman -S dnsmasq",
"dnsmasq isch nöd installiert:\n\nsudo pacman -S dnsmasq"
],
"Pflichtfeld, noch leer.": [
"Required field, still empty.",
"Champ obligatoire, encore vide.",
"Pflichtfäld, no läär."
],
"Adressbereich: ": [
"Address range: ",
"Plage d'adresses : ",
"Adrässberiich: "
],
"pkexec (polkit) fehlt, damit kann der DHCP-Server nicht mit Root-Rechten starten.": [
"pkexec (polkit) is missing, so the DHCP server cannot start with root rights.",
"pkexec (polkit) est absent ; le serveur DHCP ne peut donc pas démarrer avec les droits root.",
"pkexec (polkit) fählt, drum cha de DHCP-Server nöd mit Root-Rächt starte."
],
"z. B. 10.20.0.1": [
"e.g. 10.20.0.1",
"p. ex. 10.20.0.1",
"z. B. 10.20.0.1"
],
"Passwort wiederholen": [
"Repeat password",
"Répéter le mot de passe",
"Passwort nomal"
],
"Das Passwort zur Sicherheit ein zweites Mal eingeben.\nStimmen die beiden Eingaben nicht überein, startet kein Lauf.": [
"Enter the password a second time to be safe.\nIf the two entries differ, no run starts.",
"Saisir le mot de passe une seconde fois par sécurité.\nSi les deux saisies diffèrent, rien ne démarre.",
"S Passwort zur Sicherheit es zweits Mal iitippe.\nStimmed di beide Iigabe nöd überii, startet kein Luuf."
],
"Neues Passwort: Die beiden Eingaben stimmen nicht überein.": [
"New password: the two entries do not match.",
"Nouveau mot de passe : les deux saisies ne correspondent pas.",
"Neus Passwort: Di beide Iigabe stimmed nöd überii."
],
"Zeigt, was vor dem Start noch auszufüllen ist. Die betroffenen Felder sind\ngelb (fehlt noch) oder rot (fehlerhaft) markiert.": [
"Shows what still has to be filled in before starting. The fields concerned are\nmarked yellow (still missing) or red (wrong).",
"Indique ce qu'il reste à remplir avant de démarrer. Les champs concernés sont\nmarqués en jaune (manquant) ou en rouge (erroné).",
"Zeiget, was vor em Start no uuszfülle isch. Di betroffene Fälder sind\ngäl (fählt no) oder rot (fählerhaft) markiert."
],
"fehlt noch": [
"still missing",
"manquant",
"fählt no"
],
"fehlerhaft": [
"wrong",
"erroné",
"fählerhaft"
],
"fertig": [
"done",
"terminé",
"fertig"
],
"Neuer Administrator: Benutzer": [
"New administrator: user",
"Nouvel administrateur : utilisateur",
"Neue Administrator: Benutzer"
],
"Neuer Administrator: Passwort": [
"New administrator: password",
"Nouvel administrateur : mot de passe",
"Neue Administrator: Passwort"
],
"mindestens ein Server in der Liste": [
"at least one server in the list",
"au moins un serveur dans la liste",
"mindeschtens ein Server i de Lischte"
],
"{0} leere Pflichtfelder in der Serverliste": [
"{0} empty required fields in the server list",
"{0} champs obligatoires vides dans la liste des serveurs",
"{0} lääri Pflichtfälder i de Serverlischte"
],
"{0} fehlerhafte Felder in der Serverliste": [
"{0} wrong fields in the server list",
"{0} champs erronés dans la liste des serveurs",
"{0} fählerhafti Fälder i de Serverlischte"
],
"Es fehlt noch:": [
"Still missing:",
"Il manque encore :",
"Es fählt no:"
],
"Alle Server der Liste sind fertig.": [
"All servers in the list are done.",
"Tous les serveurs de la liste sont terminés.",
"Alli Server sind fertig. Ziit für es Kafi."
],
"Alles ausgefüllt. Jetzt den Trockenlauf starten.": [
"Everything is filled in. Now start the dry run.",
"Tout est rempli. Lancer maintenant la simulation.",
"Alles uusgfüllt, suuber. Jetzt de Trocheluuf starte."
],
"Alles ausgefüllt. Der nächste Start stellt die Geräte wirklich um.": [
"Everything is filled in. The next start really reconfigures the devices.",
"Tout est rempli. Le prochain démarrage reconfigure réellement les appareils.",
"Alles uusgfüllt. Jetzt gilt's ernscht: De nächscht Start stellt d Grät würkli um."
],
"Trockenlauf starten": [
"Start dry run",
"Lancer la simulation",
"Trocheluuf starte"
],
"Scharf starten": [
"Start live run",
"Lancer l'exécution réelle",
"Scharf starte"
],
"Trockenlauf für alle Server der Liste": [
"Dry run for all servers in the list",
"Simulation pour tous les serveurs de la liste",
"Trocheluuf für alli Server vo de Lischte"
],
"Scharf auf allen Servern der Liste setzen": [
"Set for real on all servers in the list",
"Appliquer réellement à tous les serveurs de la liste",
"Scharf uf allne Server vo de Lischte setze"
],
"  FEHLER: Die Verbindung brach ab, aber das Gerät antwortet weiter unter der alten Adresse. Netzwerk nicht umgestellt.": [
"  ERROR: the connection dropped, but the device still answers on the old address. Network not changed.",
"  ERREUR : la connexion a été coupée, mais l'appareil répond toujours à l'ancienne adresse. Réseau non modifié.",
"  FÄHLER: D Verbindig isch abgrisse, aber s Grät antwortet wiiter under de alte Adrässe. Netzwerk nöd umgstellt."
],
"Ausgelegt auf: Dell iDRAC9, iDRAC7/8 · HPE iLO 3 bis 6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. Andere Generationen zuerst an einem einzelnen Gerät testen.": [
"Designed for: Dell iDRAC9, iDRAC7/8 · HPE iLO 3 to 6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. Try other generations on a single device first.",
"Conçu pour : Dell iDRAC9, iDRAC7/8 · HPE iLO 3 à 6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. Essayer d'abord les autres générations sur un seul appareil.",
"Uusgleit uf: Dell iDRAC9, iDRAC7/8 · HPE iLO 3 bis 6 · Supermicro X12/X13 · Lenovo XCC2/XCC3. Anderi Generatione zerscht a eim einzelne Grät teste."
],
"iDRAC9 (14. bis 16. Generation, z. B. R6625) und iDRAC7/8 (12./13. Generation) ab Firmware 2.50.50.50. iDRAC10 ist nicht geprüft, iDRAC6 wird nicht unterstützt.": [
"iDRAC9 (14th to 16th generation, e.g. R6625) and iDRAC7/8 (12th/13th generation) from firmware 2.50.50.50. iDRAC10 is untested, iDRAC6 is not supported.",
"iDRAC9 (14e à 16e génération, p. ex. R6625) et iDRAC7/8 (12e/13e génération) à partir du firmware 2.50.50.50. iDRAC10 n'est pas testé, iDRAC6 n'est pas pris en charge.",
"iDRAC9 (14. bis 16. Generation, z. B. R6625) und iDRAC7/8 (12./13. Generation) ab Firmware 2.50.50.50. iDRAC10 isch nöd prüeft, iDRAC6 wird nöd understützt."
],
"iLO 3 (G7) und iLO 4 (Gen8, Gen9), angesprochen über die XML-Schnittstelle RIBCL. iLO 2 und älter werden nicht unterstützt.": [
"iLO 3 (G7) and iLO 4 (Gen8, Gen9), addressed through the RIBCL XML interface. iLO 2 and older are not supported.",
"iLO 3 (G7) et iLO 4 (Gen8, Gen9), pilotés via l'interface XML RIBCL. iLO 2 et versions antérieures ne sont pas pris en charge.",
"iLO 3 (G7) und iLO 4 (Gen8, Gen9), aagsproche über d XML-Schnittstell RIBCL. iLO 2 und älter wärded nöd understützt."
],
"Keine gültige Antwort der XML-Schnittstelle (RIBCL)": [
"No valid answer from the XML interface (RIBCL)",
"Pas de réponse valide de l'interface XML (RIBCL)",
"Kei gültigi Antwort vo de XML-Schnittstell (RIBCL)"
],
"    Erkannt: {0}. Dieses Gerät wird nicht unterstützt.": [
"    Detected: {0}. This device is not supported.",
"    Détecté : {0}. Cet appareil n'est pas pris en charge.",
"    Erchännt: {0}. Das Grät wird nöd understützt."
],
"    Hinweis: Geräte ohne Redfish (iDRAC6, iLO 2, sehr alte Firmware) werden nicht unterstützt.": [
"    Note: devices without Redfish (iDRAC6, iLO 2, very old firmware) are not supported.",
"    Remarque : les appareils sans Redfish (iDRAC6, iLO 2, firmware très ancien) ne sont pas pris en charge.",
"    Hiiwiis: Grät ohni Redfish (iDRAC6, iLO 2, uralti Firmware) wärded nöd understützt."
],
"  Firmware {0}": [
"  Firmware {0}",
"  Firmware {0}",
"  Firmware {0}"
],
"  FEHLER: Diese iDRAC-Firmware ist zu alt für das Tool (iDRAC7/8 erst ab 2.50.50.50). Bitte zuerst die Firmware aktualisieren. Nichts geändert.": [
"  ERROR: this iDRAC firmware is too old for the tool (iDRAC7/8 only from 2.50.50.50). Please update the firmware first. Nothing changed.",
"  ERREUR : ce firmware iDRAC est trop ancien pour l'outil (iDRAC7/8 seulement à partir de 2.50.50.50). Mettre d'abord le firmware à jour. Rien n'a été modifié.",
"  FÄHLER: Die iDRAC-Firmware isch z alt fürs Tool (iDRAC7/8 erscht ab 2.50.50.50). Bitte zerscht d Firmware aktualisiere. Nüt gänderet."
],
"  FEHLER: Hostname zu lang, iLO 3/4 erlaubt höchstens 49 Zeichen. Nichts geändert.": [
"  ERROR: hostname too long, iLO 3/4 allows at most 49 characters. Nothing changed.",
"  ERREUR : nom d'hôte trop long, iLO 3/4 accepte au plus 49 caractères. Rien n'a été modifié.",
"  FÄHLER: Hostname z lang, iLO 3/4 erlaubt höchschtens 49 Zeiche. Nüt gänderet."
],
"    Hinweis: iLO 3 und 4 werden über die XML-Schnittstelle (RIBCL) konfiguriert. iLO 2 und älter werden nicht unterstützt.": [
"    Note: iLO 3 and 4 are configured through the XML interface (RIBCL). iLO 2 and older are not supported.",
"    Remarque : iLO 3 et 4 sont configurés via l'interface XML (RIBCL). iLO 2 et versions antérieures ne sont pas pris en charge.",
"    Hiiwiis: iLO 3 und 4 wärded über d XML-Schnittstell (RIBCL) konfiguriert. iLO 2 und älter wärded nöd understützt."
],
"  Gerät: {0}, Firmware {1}": [
"  Device: {0}, firmware {1}",
"  Appareil : {0}, firmware {1}",
"  Grät: {0}, Firmware {1}"
],
"Benutzer lesen": [
"Read users",
"Lecture des utilisateurs",
"Benutzer läse"
],
"  Netzwerk und Hostname gesetzt, das iLO startet von selbst neu": [
"  Network and hostname set, the iLO restarts by itself",
"  Réseau et nom d'hôte définis, l'iLO redémarre de lui-même",
"  Netzwerk und Hostname gsetzt, s iLO startet vo sälber neu"
],
"Netzwerk und Hostname": [
"Network and hostname",
"Réseau et nom d'hôte",
"Netzwerk und Hostname"
],
"  Hinweis: altes Gerät, die Verbindung nutzt veraltete Verschlüsselung (TLS 1.0/1.1).": [
"  Note: old device, the connection uses outdated encryption (TLS 1.0/1.1).",
"  Remarque : appareil ancien, la connexion utilise un chiffrement obsolète (TLS 1.0/1.1).",
"  Hiiwiis: alts Grät, d Verbindig bruucht veralteti Verschlüsselig (TLS 1.0/1.1)."
],
"  Hinweis: iLO 3 kann keine UEFI-Einstellungen über Redfish. Bei iLO 4 geht es nur ab Gen9 mit Firmware 2.30 und ist ungeprüft.": [
"  Note: iLO 3 cannot do UEFI settings via Redfish. With iLO 4 it only works from Gen9 with firmware 2.30 and is untested.",
"  Remarque : iLO 3 ne gère pas les réglages UEFI via Redfish. Avec iLO 4, cela ne fonctionne qu'à partir de Gen9 avec le firmware 2.30 et n'est pas testé.",
"  Hiiwiis: iLO 3 cha kei UEFI-Iistellige über Redfish. Bi iLO 4 gaht's nume ab Gen9 mit Firmware 2.30 und isch nöd prüeft."
],
"LDAP": [
"LDAP",
"LDAP",
"LDAP"
],
"NTP": [
"NTP",
"NTP",
"NTP"
],
"Syslog": [
"Syslog",
"Syslog",
"Syslog"
],
"HTTPS-Zertifikat": [
"HTTPS certificate",
"Certificat HTTPS",
"HTTPS-Zertifikat"
],
"5 · Hilfe": [
"5 · Help",
"5 · Aide",
"5 · Hilf"
],
"Login (Werksbenutzer, gilt für alle Server der Liste)": [
"Login (factory user, used for every server in the list)",
"Connexion (utilisateur d'usine, pour tous les serveurs de la liste)",
"Login (Wärksbenutzer, gilt für alli Server vo de Lischte)"
],
"Die Dienste werden auf allen Servern der Serverliste („Neuware“) eingerichtet.\nFertige (grüne) Server werden unter ihrer neuen Adresse angesprochen.\nAngemeldet wird mit dem Werksbenutzer; der Werks-Administrator bleibt bestehen.": ["The services are set up on every server in the list (“New hardware”).\nFinished (green) servers are reached at their new address.\nLogin uses the factory user; the factory administrator stays in place.", "Les services sont configurés sur tous les serveurs de la liste (« Matériel neuf »).\nLes serveurs terminés (verts) sont joints à leur nouvelle adresse.\nLa connexion utilise l'utilisateur d'usine ; l'administrateur d'usine reste en place.", "D Dienscht wärded uf allne Server vo de Lischte („Neuware“) iigrichtet.\nFertigi (grüeni) Server wärded under ihrer neue Adrässe aagsproche.\nAagmäldet wird mit em Wärksbenutzer; de Wärks-Administrator bliibt bestah."],
"Feste Wahl überspringt die Erkennung. „Automatisch erkennen“ fragt jedes Gerät.": [
"A fixed choice skips detection. “Automatic” asks each device.",
"Un choix fixe saute la détection. « Automatique » interroge chaque appareil.",
"E feschti Wahl überspringt d Erkennig. „Automatisch“ fragt jedes Grät."
],
"Leer lassen: Dann wird je Hersteller der Werksbenutzer verwendet.": [
"Leave empty: the factory user is used per vendor.",
"Laisser vide : l'utilisateur d'usine est utilisé selon le constructeur.",
"Läär laa: Denn wird je Hersteller de Wärksbenutzer gnoh."
],
"Fällt für Zeilen ein, deren Spalte „Passwort“ in der Serverliste leer ist.": [
"Used for rows whose “Password” column in the server list is empty.",
"Utilisé pour les lignes dont la colonne « Mot de passe » est vide.",
"Gilt für Ziile, wo d Spalte „Passwort“ i de Serverlischte läär isch."
],
"NTP (Zeitserver)": [
"NTP (time servers)",
"NTP (serveurs de temps)",
"NTP (Ziitserver)"
],
"z. B. 10.20.0.1 oder pool.ntp.org": [
"e.g. 10.20.0.1 or pool.ntp.org",
"p. ex. 10.20.0.1 ou pool.ntp.org",
"z. B. 10.20.0.1 oder pool.ntp.org"
],
"optional": [
"optional",
"facultatif",
"optional"
],
"nur Dell, z. B. Europe/Berlin": [
"Dell only, e.g. Europe/Berlin",
"Dell uniquement, p. ex. Europe/Berlin",
"nur Dell, z. B. Europe/Berlin"
],
"NTP-Server 1": [
"NTP server 1",
"Serveur NTP 1",
"NTP-Server 1"
],
"Pflicht, wenn NTP eingerichtet wird.": [
"Required when setting up NTP.",
"Obligatoire si NTP est configuré.",
"Pflicht, wenn NTP iigrichtet wird."
],
"NTP-Server 2": [
"NTP server 2",
"Serveur NTP 2",
"NTP-Server 2"
],
"Optional, zweiter Zeitserver.": [
"Optional, second time server.",
"Facultatif, deuxième serveur de temps.",
"Optional, zweite Ziitserver."
],
"Zeitzone": [
"Time zone",
"Fuseau horaire",
"Ziitzone"
],
"Nur bei Dell gesetzt. Bei HPE, Supermicro und Lenovo bitte in der Oberfläche wählen.": [
"Set on Dell only. On HPE, Supermicro and Lenovo please choose it in the web interface.",
"Défini uniquement sur Dell. Sur HPE, Supermicro et Lenovo, le choisir dans l'interface.",
"Nur bi Dell gsetzt. Bi HPE, Supermicro und Lenovo bitte i de Oberflächi wähle."
],
"Nur Dell und HPE. Bei Supermicro und Lenovo wird Syslog übersprungen.": [
"Dell and HPE only. On Supermicro and Lenovo syslog is skipped.",
"Dell et HPE uniquement. Sur Supermicro et Lenovo, syslog est ignoré.",
"Nur Dell und HPE. Bi Supermicro und Lenovo wird Syslog übersprunge."
],
"z. B. 10.20.0.9": [
"e.g. 10.20.0.9",
"p. ex. 10.20.0.9",
"z. B. 10.20.0.9"
],
"Server": [
"Server",
"Serveur",
"Server"
],
"Adresse des Syslog-Empfängers.": [
"Address of the syslog receiver.",
"Adresse du récepteur syslog.",
"Adrässe vom Syslog-Empfänger."
],
"Port": [
"Port",
"Port",
"Port"
],
"Standard 514.": [
"Default 514.",
"Par défaut 514.",
"Standard 514."
],
"LDAP / Active Directory": [
"LDAP / Active Directory",
"LDAP / Active Directory",
"LDAP / Active Directory"
],
"z. B. ad.example.com": [
"e.g. ad.example.com",
"p. ex. ad.example.com",
"z. B. ad.example.com"
],
"optional, z. B. cn=svc,dc=example,dc=com": [
"optional, e.g. cn=svc,dc=example,dc=com",
"facultatif, p. ex. cn=svc,dc=example,dc=com",
"optional, z. B. cn=svc,dc=example,dc=com"
],
"Adresse des LDAP-/AD-Servers.": [
"Address of the LDAP/AD server.",
"Adresse du serveur LDAP/AD.",
"Adrässe vom LDAP-/AD-Server."
],
"389 unverschlüsselt, 636 für LDAPS.": [
"389 plain, 636 for LDAPS.",
"389 en clair, 636 pour LDAPS.",
"389 unverschlüsselet, 636 für LDAPS."
],
"Base-DN": [
"Base DN",
"Base DN",
"Base-DN"
],
"Ab wo gesucht wird, z. B. dc=example,dc=com.": [
"Where the search starts, e.g. dc=example,dc=com.",
"Point de départ de la recherche, p. ex. dc=example,dc=com.",
"Ab wo gsuecht wird, z. B. dc=example,dc=com."
],
"Bind-DN": [
"Bind DN",
"Bind DN",
"Bind-DN"
],
"Dienstkonto zum Suchen. Leer lassen, wenn der Server anonyme Suche erlaubt.": [
"Service account for searching. Leave empty if the server allows anonymous search.",
"Compte de service pour la recherche. Laisser vide si la recherche anonyme est permise.",
"Dienstkonto zum Sueche. Läär laa, wenn de Server anonymi Suech erlaubt."
],
"Bind-Passwort": [
"Bind password",
"Mot de passe du bind",
"Bind-Passwort"
],
"Passwort des Bind-DN.": [
"Password of the bind DN.",
"Mot de passe du bind DN.",
"Passwort vom Bind-DN."
],
"Benutzer-Attribut": [
"User attribute",
"Attribut utilisateur",
"Benutzer-Attribut"
],
"Active Directory: sAMAccountName. OpenLDAP oft uid.": [
"Active Directory: sAMAccountName. OpenLDAP often uid.",
"Active Directory : sAMAccountName. OpenLDAP souvent uid.",
"Active Directory: sAMAccountName. OpenLDAP of uid."
],
"Gruppen-Attribut": [
"Group attribute",
"Attribut de groupe",
"Gruppe-Attribut"
],
"Meist memberOf.": [
"Usually memberOf.",
"En général memberOf.",
"Meischtens memberOf."
],
"Ordnet LDAP-Gruppen einer Rolle zu: Administrator, Operator oder ReadOnly.\nLeer lassen, wenn keine Gruppen-Zuordnung gebraucht wird.": [
"Maps LDAP groups to a role: Administrator, Operator or ReadOnly.\nLeave empty if no group mapping is needed.",
"Associe des groupes LDAP à un rôle : Administrator, Operator ou ReadOnly.\nLaisser vide si aucun mappage de groupe n'est nécessaire.",
"Ordnet LDAP-Gruppe ere Rolle zue: Administrator, Operator oder ReadOnly.\nLäär laa, wenn kei Gruppe-Zueordnig bruucht wird."
],
"+ Gruppe": [
"+ Group",
"+ Groupe",
"+ Gruppe"
],
"– Gruppe": [
"– Group",
"– Groupe",
"– Gruppe"
],
"Experimentell und je Hersteller verschieden. Importiert ein von einer CA signiertes\nZertifikat (PEM). Bei HPE muss der passende Schlüssel aus einer auf dem iLO erzeugten\nZertifikatsanforderung (CSR) stammen, sonst scheitert der Import.": [
"Experimental and vendor-specific. Imports a CA-signed certificate (PEM). On HPE the\nmatching key must come from a CSR generated on the iLO, otherwise the import fails.",
"Expérimental et propre au constructeur. Importe un certificat signé par une CA (PEM).\nSur HPE, la clé correspondante doit provenir d'une CSR générée sur l'iLO, sinon l'import échoue.",
"Experimentell und je Hersteller andersch. Importiert es vonere CA signiets Zertifikat (PEM).\nBi HPE mues de passend Schlüssel us ere uf em iLO erzüügte CSR cho, susch schiitert de Import."
],
"keine Datei gewählt": [
"no file chosen",
"aucun fichier choisi",
"kei Datei gwählt"
],
"PEM-Datei wählen …": [
"Choose PEM file …",
"Choisir un fichier PEM …",
"PEM-Datei wähle …"
],
"Mit Haken wird nichts geändert; das Protokoll zeigt nur die Aufrufe.": [
"When ticked nothing is changed; the log only shows the calls.",
"Si coché, rien n'est modifié ; le journal montre seulement les appels.",
"Mit Haagge wird nüt gänderet; s Protokoll zeigt nur d Uffruef."
],
"Zertifikat wählen": [
"Choose certificate",
"Choisir un certificat",
"Zertifikat wähle"
],
"Zertifikat (*.pem *.crt *.cer);;Alle Dateien (*)": [
"Certificate (*.pem *.crt *.cer);;All files (*)",
"Certificat (*.pem *.crt *.cer);;Tous les fichiers (*)",
"Zertifikat (*.pem *.crt *.cer);;Alli Datei (*)"
],
"LDAP-Gruppe": [
"LDAP group",
"Groupe LDAP",
"LDAP-Gruppe"
],
"Rolle": [
"Role",
"Rôle",
"Rolle"
],
"Gruppen → Rolle": [
"Groups → role",
"Groupes → rôle",
"Gruppe → Rolle"
],
"Zertifikat": [
"Certificate",
"Certificat",
"Zertifikat"
],
"Die Datei enthält kein PEM-Zertifikat (erwartet „BEGIN CERTIFICATE“).": [
"The file contains no PEM certificate (expected “BEGIN CERTIFICATE”).",
"Le fichier ne contient pas de certificat PEM (« BEGIN CERTIFICATE » attendu).",
"D Datei hät kei PEM-Zertifikat (erwartet „BEGIN CERTIFICATE“)."
],
"Dienste": [
"Services",
"Services",
"Dienscht"
],
"Kein Abschnitt angehakt. Oben mindestens einen aktivieren.": [
"No section ticked. Enable at least one above.",
"Aucune section cochée. En activer au moins une ci-dessus.",
"Kei Abschnitt aaghaaggt. Obe mindeschtens eine aktiviere."
],
"Es werden Verwaltungseinstellungen der BMCs geändert. Eine falsche LDAP- oder Zertifikatseinstellung kann die Anmeldung erschweren; der lokale Administrator bleibt aber bestehen.": [
"Management settings of the BMCs are changed. A wrong LDAP or certificate setting can make signing in harder; the local administrator stays in place, though.",
"Les réglages de gestion des BMC sont modifiés. Un mauvais réglage LDAP ou de certificat peut compliquer la connexion ; l'administrateur local reste toutefois en place.",
"Es wärded Verwaltigs-Iistellige vo de BMC gänderet. E falschi LDAP- oder Zertifikats-Iistellig cha s Aamälde erschwere; de lokal Administrator bliibt aber bestah."
],
"    Zeitzone bei HPE bitte in der iLO-Oberfläche setzen (hier nicht gesetzt).": [
"    Please set the time zone on HPE in the iLO interface (not set here).",
"    Veuillez définir le fuseau horaire HPE dans l'interface iLO (non défini ici).",
"    Ziitzone bi HPE bitte i de iLO-Oberflächi setze (da nöd gsetzt)."
],
"    Zeitzone bei diesem Hersteller bitte in der Oberfläche setzen (hier nicht gesetzt).": [
"    Please set the time zone for this vendor in its interface (not set here).",
"    Veuillez définir le fuseau horaire de ce constructeur dans son interface (non défini ici).",
"    Ziitzone bi dem Hersteller bitte i de Oberflächi setze (da nöd gsetzt)."
],
"  iLO 3/4: Dienste werden hier nicht unterstützt, übersprungen.": [
"  iLO 3/4: services are not supported here, skipped.",
"  iLO 3/4 : services non pris en charge ici, ignoré.",
"  iLO 3/4: Dienscht wärded da nöd understützt, übersprunge."
],
"Nur angehakte Abschnitte werden angewendet. Alles wird auf den Servern der Liste in „Neuware“ gesetzt, grüne unter ihrer neuen Adresse.": ["Only ticked sections are applied. Everything is set on the servers in the list on “New hardware”, green ones at their new address.", "Seules les sections cochées sont appliquées. Tout est défini sur les serveurs de la liste de « Matériel neuf », les verts à leur nouvelle adresse.", "Nur aaghaaggti Abschnitt wärded aagwändet. Alles wird uf de Server vo de Lischte i „Neuware“ gsetzt, grüeni under ihrer neue Adrässe."],
"NTP: mindestens den ersten Server eintragen.": [
"NTP: enter at least the first server.",
"NTP : saisir au moins le premier serveur.",
"NTP: mindeschtens de erscht Server iiträge."
],
"Syslog: Server eintragen.": [
"Syslog: enter the server.",
"Syslog : saisir le serveur.",
"Syslog: de Server iiträge."
],
"Port muss eine Zahl von 1 bis 65535 sein.": [
"Port must be a number from 1 to 65535.",
"Le port doit être un nombre de 1 à 65535.",
"Port mues e Zahl vo 1 bis 65535 si."
],
"LDAP: Server und Base-DN eintragen.": [
"LDAP: enter server and base DN.",
"LDAP : saisir le serveur et le base DN.",
"LDAP: Server und Base-DN iiträge."
],
"HTTPS-Zertifikat: erst eine PEM-Datei wählen.": [
"HTTPS certificate: choose a PEM file first.",
"Certificat HTTPS : choisir d'abord un fichier PEM.",
"HTTPS-Zertifikat: zerscht e PEM-Datei wähle."
],
"— {0}, {1} Server, {2} —": [
"— {0}, {1} servers, {2} —",
"— {0}, {1} serveurs, {2} —",
"— {0}, {1} Server, {2} —"
],
"  {0}: gesendet, die Verbindung brach dabei ab (der BMC startet oft neu).": [
"  {0}: sent, the connection dropped (the BMC often restarts).",
"  {0} : envoyé, la connexion a été coupée (le BMC redémarre souvent).",
"  {0}: gschickt, d Verbindig isch abgrisse (de BMC startet of neu)."
],
"  {0}: gesetzt": [
"  {0}: set",
"  {0} : défini",
"  {0}: gsetzt"
],
"Dienste: {0}": [
"Services: {0}",
"Services : {0}",
"Dienscht: {0}"
],
"Auf {0} Servern werden jetzt wirklich Dienste eingerichtet.": [
"Services will now really be set up on {0} servers.",
"Les services vont maintenant vraiment être configurés sur {0} serveurs.",
"Uf {0} Server wärded jetz würklich Dienscht iigrichtet."
],
"  {0}: auf diesem Hersteller nicht unterstützt, übersprungen": [
"  {0}: not supported on this vendor, skipped",
"  {0} : non pris en charge sur ce constructeur, ignoré",
"  {0}: uf dem Hersteller nöd understützt, übersprunge"
],
"Rolle muss Administrator, Operator oder ReadOnly sein (Gruppe {0}).": [
"Role must be Administrator, Operator or ReadOnly (group {0}).",
"Le rôle doit être Administrator, Operator ou ReadOnly (groupe {0}).",
"D Rolle mues Administrator, Operator oder ReadOnly si (Gruppe {0})."
],
"Adresse des BMC. Vorschläge aus der Serverliste („Neuware“), es geht aber jede\nerreichbare Adresse.": ["Address of the BMC. Suggestions come from the server list (“New hardware”), but any\nreachable address works.", "Adresse du BMC. Suggestions issues de la liste des serveurs (« Matériel neuf »), mais toute\nadresse joignable convient.", "Adrässe vom BMC. Vorschläg chömed us de Serverlischte („Neuware“), aber jedi\nerreichbari Adrässe goht."],
"Bild: Web-Konsole des BMC (eingebettet)": ["Picture: BMC web console (embedded)", "Image : console web du BMC (intégrée)", "Bild: Web-Konsole vom BMC (iibettet)"],
"Text: serielle Konsole (SOL/SSH)": ["Text: serial console (SOL/SSH)", "Texte : console série (SOL/SSH)", "Text: serielli Konsole (SOL/SSH)"],
"Bild: im Standardbrowser öffnen": ["Picture: open in default browser", "Image : ouvrir dans le navigateur par défaut", "Bild: im Standardbrowser uftue"],
"Automatisch": ["Automatic", "Automatique", "Automatisch"],
"Verbindung": ["Connection", "Connexion", "Verbindig"],
"Feste Wahl überspringt die Erkennung. „Automatisch erkennen“ ermittelt den Hersteller\ndieses einen Geräts selbst.": ["A fixed choice skips detection. \"Detect automatically\" determines the vendor\nof this one device itself.", "Un choix fixe saute la détection. « Détecter automatiquement » détermine le fabricant\nde cet appareil lui-même.", "E feschti Wahl überspringt d Erkennig. „Automatisch erkenne“ findet de Hersteller\nvo däm einte Grät sälber use."],
"Wird nur für diese Verbindung verwendet und nirgends gespeichert.": ["Used only for this connection and never stored.", "Utilisé uniquement pour cette connexion et jamais enregistré.", "Wird nume für die Verbindig bruucht und nienemer gspeicheret."],
"Anzeige": ["Display", "Affichage", "Aazeig"],
"Bild: echtes Konsolenbild mit Maus und Tastatur wie am Original (HTML5-Konsole des BMC).\nText: serielle Umleitung; geht nur, wenn im UEFI „Console Redirection“ aktiv ist.\nBrowser: öffnet die BMC-Seite im Standardbrowser, ohne Auto-Anmeldung.": ["Picture: real console image with mouse and keyboard like on the original (HTML5 console of the BMC).\nText: serial redirection; works only if \"Console Redirection\" is enabled in the UEFI.\nBrowser: opens the BMC page in the default browser, without automatic login.", "Image : vraie image de la console avec souris et clavier comme sur l'original (console HTML5 du BMC).\nTexte : redirection série ; ne fonctionne que si « Console Redirection » est activé dans l'UEFI.\nNavigateur : ouvre la page du BMC dans le navigateur par défaut, sans connexion automatique.", "Bild: ächts Konsolebild mit Muus und Tastatur wie am Original (HTML5-Konsole vom BMC).\nText: serielli Umleitig; goht nume, wenn im UEFI „Console Redirection“ aktiv isch.\nBrowser: macht d BMC-Siite im Standardbrowser uf, ohni Auto-Aamäldig."],
"Verfahren": ["Method", "Méthode", "Verfahre"],
"Automatisch: Supermicro über IPMI-SOL (pyghmi), Dell/HPE/Lenovo über SSH (paramiko), sonst IPMI-SOL.\nIPMI über LAN muss im BMC aktiviert sein (bei Dell ab Werk aus).": ["Automatic: Supermicro via IPMI-SOL (pyghmi), Dell/HPE/Lenovo via SSH (paramiko), otherwise IPMI-SOL.\nIPMI over LAN must be enabled in the BMC (off by default on Dell).", "Automatique : Supermicro via IPMI-SOL (pyghmi), Dell/HPE/Lenovo via SSH (paramiko), sinon IPMI-SOL.\nIPMI sur LAN doit être activé dans le BMC (désactivé par défaut chez Dell).", "Automatisch: Supermicro über IPMI-SOL (pyghmi), Dell/HPE/Lenovo über SSH (paramiko), susch IPMI-SOL.\nIPMI über LAN muess im BMC aktiviert sii (bi Dell ab Werk uus)."],
"Terminaltyp": ["Terminal type", "Type de terminal", "Terminaltyp"],
"Muss zur Einstellung „Terminal Type“ im UEFI passen: VT100/VT-UTF8 → UTF-8,\nANSI → CP437 (Rahmenzeichen).": ["Must match the \"Terminal Type\" setting in the UEFI: VT100/VT-UTF8 → UTF-8,\nANSI → CP437 (box characters).", "Doit correspondre au réglage « Terminal Type » de l'UEFI : VT100/VT-UTF8 → UTF-8,\nANSI → CP437 (caractères de cadre).", "Muess zur Iistellig „Terminal Type“ im UEFI passe: VT100/VT-UTF8 → UTF-8,\nANSI → CP437 (Rahmezeiche)."],
"Größe": ["Size", "Taille", "Grössi"],
"Zeichen × Zeilen; muss zur Einstellung im UEFI passen (meist 80 × 24 oder 80 × 25).": ["Characters × lines; must match the UEFI setting (usually 80 × 24 or 80 × 25).", "Caractères × lignes ; doit correspondre au réglage de l'UEFI (souvent 80 × 24 ou 80 × 25).", "Zeiche × Zile; muess zur Iistellig im UEFI passe (meischtens 80 × 24 oder 80 × 25)."],
"Verbinden": ["Connect", "Connecter", "Verbinde"],
"Trennen": ["Disconnect", "Déconnecter", "Trenne"],
"Konsole starten": ["Start console", "Lancer la console", "Konsole starte"],
"Seriennummern auslesen": ["Read serial numbers", "Lire les numéros de série", "Seriennummere uslääse"],
"Liest für alle Zeilen ohne Seriennummer die Seriennummer (Dell: Service-Tag) aus dem BMC.\nNur lesend, ein Anmeldeversuch je Gerät mit dem Passwort der Zeile (iLO 3/4: ohne Anmeldung).\nDas geschieht auch automatisch beim Eintragen einer Aktuellen IP. Eingetragenes wird nie überschrieben.": ["Reads the serial number (Dell: service tag) from the BMC for every row without one.\nRead-only, one login attempt per device using the row's password (iLO 3/4: no login).\nThis also happens automatically when a current IP is entered. Existing entries are never overwritten.", "Lit le numéro de série (Dell : service tag) du BMC pour chaque ligne qui n'en a pas.\nLecture seule, une seule tentative de connexion par appareil avec le mot de passe de la ligne (iLO 3/4 : sans connexion).\nCela se fait aussi automatiquement à la saisie d'une IP actuelle. Les saisies existantes ne sont jamais écrasées.", "Liest für alli Zile ohni Seriennummere d Seriennummere (Dell: Service-Tag) us em BMC.\nNume läse, en Aamäldeversuech pro Grät mit em Passwort vo de Zile (iLO 3/4: ohni Aamäldig).\nDas passiert au automatisch bim Iitrage vo ere Aktuelle IP. Iigtragnigs wird nie überschriebe."],
"Seriennummern werden ausgelesen: {0} Adresse(n). Die Spalte füllt sich, sobald die Geräte antworten.": ["Reading serial numbers: {0} address(es). The column fills in as the devices answer.", "Lecture des numéros de série : {0} adresse(s). La colonne se remplit dès que les appareils répondent.", "Seriennummere wärde uusgläse: {0} Adrässe. D Spalte füllt sich, sobald d Grät antworte."],
"Keine Zeile ohne Seriennummer und mit gültiger Aktueller IP.": ["No row without a serial number and with a valid current IP.", "Aucune ligne sans numéro de série et avec une IP actuelle valide.", "Kei Zile ohni Seriennummere und mit gültiger Aktuelle IP."],
"Simuliert das Netzwerkkabel des Servers (nur in den Modi „Konfigraum“ und „Zuhause/Router“; im Loopback-Modus gibt es keine Netzwerkkarten).\n„Kabel ziehen“ trennt die Netzwerkkarte des Servers vom virtuellen Switch bzw. vom Router: Der Server läuft weiter, ist im Netz aber nicht mehr erreichbar und bekommt keine Adresse per DHCP.\n„Kabel stecken“ verbindet ihn wieder: Er ist wieder erreichbar und kann sich per DHCP eine Adresse holen.\nZum Üben: Server nacheinander anschließen oder einen Kabelbruch bzw. Portausfall mitten in der Umstellung nachstellen.": ["Simulates the server's network cable (only in the \"Config room\" and \"Home/Router\" modes; loopback mode has no network cards).\n\"Unplug cable\" disconnects the server's network card from the virtual switch or router: the server keeps running but is no longer reachable on the network and gets no DHCP address.\n\"Plug in cable\" reconnects it: it is reachable again and can obtain an address via DHCP.\nUse it to practise connecting servers one after another, or to simulate a broken cable or switch port during a migration.", "Simule le câble réseau du serveur (uniquement dans les modes « Salle de config » et « Maison/Routeur » ; le mode loopback n'a pas de carte réseau).\n« Débrancher » déconnecte la carte réseau du serveur du switch virtuel ou du routeur : le serveur continue de tourner mais n'est plus joignable sur le réseau et n'obtient pas d'adresse DHCP.\n« Rebrancher » le reconnecte : il est de nouveau joignable et peut obtenir une adresse par DHCP.\nSert à s'exercer à connecter les serveurs l'un après l'autre ou à simuler un câble ou un port de switch défectueux pendant une migration.", "Simuliert s Netzwerkchabel vom Server (nume i de Modi „Konfigraum“ und „Zuhause/Router“; im Loopback-Modus git es kei Netzwerkcharte).\n„Kabel ziehen“ trennt d Netzwerkcharte vom Server vom virtuelle Switch bzw. vom Router: De Server lauft wiiter, isch im Netz aber nümme erreichbar und überchunnt kei Adrässe per DHCP.\n„Kabel stecken“ verbindet en wieder: Er isch wieder erreichbar und cha sich per DHCP e Adrässe hole.\nZum Üebe: Server nacheinand aaschliesse oder en Chabelbruch bzw. Portusfall mitts i de Umstellig nachestelle."],
"Zieht das Netzwerkkabel dieses Servers: Der Server läuft weiter, ist im Netz aber nicht mehr erreichbar (wie bei einem Kabelbruch oder abgeschaltetem Switch-Port).": ["Unplugs this server's network cable: the server keeps running but is no longer reachable on the network (like a broken cable or a switched-off port).", "Débranche le câble réseau de ce serveur : il continue de tourner mais n'est plus joignable sur le réseau (comme un câble coupé ou un port désactivé).", "Zieht s Netzwerkchabel vo däm Server: De Server lauft wiiter, isch im Netz aber nümme erreichbar (wie bi me Chabelbruch oder abgschaltetem Switch-Port)."],
"Steckt das Netzwerkkabel dieses Servers wieder an: Er ist wieder erreichbar und kann sich per DHCP eine Adresse holen.": ["Plugs this server's network cable back in: it is reachable again and can obtain an address via DHCP.", "Rebranche le câble réseau de ce serveur : il est de nouveau joignable et peut obtenir une adresse par DHCP.", "Stecht s Netzwerkchabel vo däm Server wieder a: Er isch wieder erreichbar und cha sich per DHCP e Adrässe hole."],
"Die virtuellen Server laufen nur auf diesem PC und lauschen auf 127.x-Adressen mit eigenem Port. Es gibt kein echtes Netz und kein DHCP, Root ist nicht nötig.\nBeispiel: Server 1 ist unter 127.9.0.11:8443 erreichbar; in „Neuware“ trägt man als Aktuelle IP 127.9.0.11:8443 ein. Gut, um Oberfläche und Abläufe schnell auszuprobieren.": ["The virtual servers run only on this PC and listen on 127.x addresses with their own port. There is no real network and no DHCP; root is not needed.\nExample: server 1 is reachable at 127.9.0.11:8443; in \"New hardware\" you enter 127.9.0.11:8443 as the current IP. Good for trying out the interface and the workflows quickly.", "Les serveurs virtuels ne tournent que sur ce PC et écoutent sur des adresses 127.x avec leur propre port. Il n'y a ni réseau réel ni DHCP, et pas besoin de root.\nExemple : le serveur 1 est joignable en 127.9.0.11:8443 ; dans « Matériel neuf », on saisit 127.9.0.11:8443 comme IP actuelle. Pratique pour essayer rapidement l'interface et les procédures.", "D virtuelle Server laufe nume uf dem PC und lose uf 127.x-Adrässe mit eigenem Port. Es git kei ächts Netz und kei DHCP, Root bruucht es nöd.\nBispiil: Server 1 isch under 127.9.0.11:8443 erreichbar; i „Neuware“ trait mer als Aktuelle IP 127.9.0.11:8443 y. Guet, zum d Oberflächi und d Abläuf schnäll uuszprobiere."],
"Ein virtueller, vom restlichen Netz getrennter Staging-Switch „bmclab0“ mit eigener Netzwerkkarte je Server, wie im Konfigraum. Die Server holen sich ihre Adresse vom DHCP-Server des Tools oder haben eine feste Werks-IP.\nBeispiel: Ein Dell mit fester Werks-IP 192.168.0.120 ist sofort unter https://192.168.0.120 erreichbar, ein HPE bekommt nach dem Start des DHCP-Servers z. B. 192.168.0.101. Braucht beim Start einmal Root.": ["A virtual staging switch \"bmclab0\", isolated from the rest of the network, with its own network card per server, like in a configuration room. The servers get their address from the tool's DHCP server or have a fixed factory IP.\nExample: a Dell with the fixed factory IP 192.168.0.120 is reachable right away at https://192.168.0.120; an HPE gets e.g. 192.168.0.101 once the DHCP server is started. Needs root once at start.", "Un switch de staging virtuel « bmclab0 », isolé du reste du réseau, avec une carte réseau par serveur, comme dans une salle de configuration. Les serveurs obtiennent leur adresse du serveur DHCP de l'outil ou ont une IP d'usine fixe.\nExemple : un Dell avec l'IP d'usine fixe 192.168.0.120 est joignable tout de suite en https://192.168.0.120 ; un HPE obtient p. ex. 192.168.0.101 une fois le serveur DHCP démarré. Nécessite root une fois au démarrage.", "En virtuelle, vom restliche Netz trennte Staging-Switch „bmclab0“ mit eigener Netzwerkcharte pro Server, wie im Konfigraum. D Server hole sich ihri Adrässe vom DHCP-Server vom Tool oder händ e feschti Werks-IP.\nBispiil: En Dell mit de feschte Werks-IP 192.168.0.120 isch sofort under https://192.168.0.120 erreichbar, en HPE überchunnt nach em Start vom DHCP-Server z. B. 192.168.0.101. Bruucht bim Start einisch Root."],
"Die Server hängen mit eigener MAC am echten LAN (Kabel-Netzwerkkarte) und bekommen ihre Adresse vom Router, z. B. einer FritzBox, wo sie als eigene Geräte erscheinen.\nBeispiel: Ein HPE meldet sich als „ILOCZ12345“ beim Router und bekommt 192.168.178.45; das Umstellen auf eine feste Adresse wie 192.168.178.201 läuft wirklich durch. Braucht beim Start einmal Root, WLAN geht nicht.": ["The servers sit on the real LAN (wired network card) with their own MAC and get their address from the router, e.g. a FritzBox, where they appear as separate devices.\nExample: an HPE registers as \"ILOCZ12345\" with the router and gets 192.168.178.45; switching it to a fixed address such as 192.168.178.201 really goes through. Needs root once at start; Wi-Fi does not work.", "Les serveurs sont sur le vrai réseau local (carte réseau filaire) avec leur propre MAC et obtiennent leur adresse du routeur, p. ex. une FritzBox, où ils apparaissent comme des appareils distincts.\nExemple : un HPE se présente comme « ILOCZ12345 » auprès du routeur et obtient 192.168.178.45 ; le passage à une adresse fixe comme 192.168.178.201 fonctionne réellement. Nécessite root une fois au démarrage ; le Wi-Fi ne marche pas.", "D Server hänge mit eigener MAC am ächte LAN (Chabel-Netzwerkcharte) und überchömed ihri Adrässe vom Router, z. B. ere FritzBox, wo si als eigeni Grät erschiine.\nBispiil: En HPE meldet sich als „ILOCZ12345“ bim Router und überchunnt 192.168.178.45; s Umstelle uf e feschti Adrässe wie 192.168.178.201 louft würkli düre. Bruucht bim Start einisch Root, WLAN gaht nöd."],
"Cluster-Konfiguration": ["Cluster configuration", "Configuration du cluster", "Cluster-Konfiguration"],
"Jede Zeile ist ein virtueller Server. „Cluster starten“ startet alle Zeilen zusammen.": ["Each row is one virtual server. \"Start cluster\" starts all rows together.", "Chaque ligne est un serveur virtuel. « Démarrer le cluster » démarre toutes les lignes ensemble.", "Jedi Ziile isch en virtuelle Server. „Cluster starten“ startet alli Ziile zäme."],
"Lizenz": ["License", "Licence", "Lizänz"],
"Lizenziert": ["Licensed", "Sous licence", "Lizänziert"],
"Ohne Lizenz": ["Unlicensed", "Sans licence", "Ohni Lizänz"],
"„Ohne Lizenz“: Virtual Media und Remote-Konsole des virtuellen Servers sind gesperrt, bis ein Lizenzschlüssel auf seiner Lizenzseite installiert wird.": ["\"Unlicensed\": virtual media and the remote console of the virtual server are locked until a license key is installed on its license page.", "« Sans licence » : les médias virtuels et la console distante du serveur virtuel sont verrouillés tant qu'une clé de licence n'est pas installée sur sa page de licence.", "„Ohni Lizänz“: Virtual Media und Remote-Konsole vom virtuelle Server sind gsperrt, bis en Lizänzschlüssel uf sinere Lizänzsiite installiert isch."],
"Öffnet die Konsole des BMC in einem eigenen Reiter.": ["Opens the BMC console in its own tab.", "Ouvre la console du BMC dans un onglet séparé.", "Macht d Konsole vom BMC i some eigene Reiter uf."],
"Verbunden mit {0}. Anmeldung läuft; danach bleibt die Verwaltungsseite des BMC offen.": ["Connected to {0}. Logging in; the BMC management page then stays open.", "Connecté à {0}. Connexion en cours ; la page de gestion du BMC reste ensuite ouverte.", "Verbunde mit {0}. D Aamäldig lauft; danach blibt d Verwaltigssiite vom BMC offe."],
"Angemeldet. Die Verwaltungsseite des BMC ist offen; „Konsole starten“ öffnet die Konsole in einem eigenen Reiter.": ["Logged in. The BMC management page is open; \"Start console\" opens the console in its own tab.", "Connecté. La page de gestion du BMC est ouverte ; « Lancer la console » ouvre la console dans un onglet séparé.", "Aagmäldet. D Verwaltigssiite vom BMC isch offe; „Konsole starte“ macht d Konsole i some eigene Reiter uf."],
"Auf der Verwaltungsseite „Remote Console“ wählen; die Konsole öffnet sich dann in einem eigenen Reiter.": ["Choose \"Remote Console\" on the management page; the console then opens in its own tab.", "Choisissez « Remote Console » sur la page de gestion ; la console s'ouvre alors dans un onglet séparé.", "Uf de Verwaltigssiite „Remote Console“ wähle; d Konsole goht dänn i some eigene Reiter uf."],
"Springt nach der Anmeldung zur Konsolen-Seite des BMC.": ["Jumps to the console page of the BMC after login.", "Passe à la page de console du BMC après la connexion.", "Springt nach de Aamäldig zur Konsole-Siite vom BMC."],
"Ins UEFI-Setup booten …": ["Boot into UEFI setup …", "Démarrer dans la configuration UEFI …", "Is UEFI-Setup boote …"],
"Merkt „beim nächsten Start ins UEFI-Setup“ vor und startet den Server hart neu\n(per Redfish). Nur mit Rückfrage.": ["Sets \"enter UEFI setup on next boot\" and hard-restarts the server\n(via Redfish). Only after confirmation.", "Prépare « démarrer dans la configuration UEFI au prochain démarrage » et redémarre le serveur\nde force (via Redfish). Seulement après confirmation.", "Merkt sich „bim nöchschte Start is UEFI-Setup“ und startet de Server hert neu\n(per Redfish). Nume mit Rückfrag."],
"Sendet die Taste an den Server. „Esc+1“ usw. ersetzen F1 usw. auf Konsolen,\ndie nur Escape-Folgen verstehen.": ["Sends the key to the server. \"Esc+1\" etc. replace F1 etc. on consoles\nthat only understand escape sequences.", "Envoie la touche au serveur. « Esc+1 » etc. remplacent F1 etc. sur les consoles\nqui ne comprennent que les séquences d'échappement.", "Schickt d Taschte an Server. „Esc+1“ usw. ersetzed F1 usw. uf Konsole,\ndie nume Escape-Folge verstönd."],
"Passwort kopieren (30 s)": ["Copy password (30 s)", "Copier le mot de passe (30 s)", "Passwort kopiere (30 s)"],
"Achtung: Tasten gehen direkt an den Server. Dieser Reiter ändert von sich aus nichts, alles Weitere geschieht in der UEFI-Oberfläche selbst.": ["Caution: keys go directly to the server. This tab changes nothing by itself; everything else happens in the UEFI interface itself.", "Attention : les touches vont directement au serveur. Cet onglet ne modifie rien de lui-même ; tout le reste se passe dans l'interface UEFI.", "Achtung: D Taschte gönd diräkt zum Server. Dä Reiter ändered vo sich us nüt, alles anderi gschiet i de UEFI-Oberflächi sälber."],
"Passwort liegt 30 Sekunden in der Zwischenablage.": ["The password stays in the clipboard for 30 seconds.", "Le mot de passe reste 30 secondes dans le presse-papiers.", "S Passwort liit 30 Sekunde i de Zwüscheablag."],
"Konsole": ["Console", "Console", "Konsole"],
"Getrennt.": ["Disconnected.", "Déconnecté.", "Trennt."],
"Ins UEFI-Setup booten": ["Boot into UEFI setup", "Démarrer dans la configuration UEFI", "Is UEFI-Setup boote"],
"Ein laufendes Betriebssystem wird ohne Herunterfahren beendet (wie die Reset-Taste), nicht gespeicherte Daten gehen verloren. Nur für Server ohne laufenden Betrieb verwenden.": ["A running operating system is terminated without shutting down (like the reset button); unsaved data is lost. Use only for servers that are not in operation.", "Un système d'exploitation en cours est arrêté sans extinction propre (comme le bouton reset) ; les données non enregistrées sont perdues. À utiliser uniquement sur des serveurs hors production.", "Es lauffends Betriebssystem wird ohni Abefahre beändet (wie d Reset-Taschte), nöd gspeicheret Date gönd verlore. Nume für Server ohni lauffende Betrieb bruuche."],
"Hart neu starten": ["Hard restart", "Redémarrer de force", "Hert neu starte"],
"Vorgemerkt, Server startet neu und landet im UEFI-Setup. Das Bild erscheint nach etwa ein bis zwei Minuten.": ["Scheduled; the server restarts and lands in UEFI setup. The picture appears after about one to two minutes.", "Préparé ; le serveur redémarre et arrive dans la configuration UEFI. L'image apparaît après une à deux minutes environ.", "Vorgmerkt, de Server startet neu und landet im UEFI-Setup. S Bild chunnt nach öppe ein bis zwei Minute."],
"Tasten:": ["Keys:", "Touches :", "Taschte:"],
"„Verbinden“ öffnet die Anmeldeseite des BMC im Standardbrowser. Die Anmeldung erfolgt dort von Hand;\ndie HTML5-Konsole wird im BMC über „Remote Console“ bzw. „Virtual Console“ gestartet.": ["\"Connect\" opens the BMC login page in the default browser. You log in there by hand;\nthe HTML5 console is started in the BMC via \"Remote Console\" or \"Virtual Console\".", "« Connecter » ouvre la page de connexion du BMC dans le navigateur par défaut. La connexion se fait à la main ;\nla console HTML5 se lance dans le BMC via « Remote Console » ou « Virtual Console ».", "„Verbinde“ macht d Aamäldsiite vom BMC im Standardbrowser uf. D Aamäldig machsch det vo Hand;\nd HTML5-Konsole wird im BMC über „Remote Console“ bzw. „Virtual Console“ gstartet."],
"Bitte eine Adresse eintragen.": ["Please enter an address.", "Veuillez saisir une adresse.", "Bitte e Adrässe iitrage."],
"QtWebEngine fehlt, siehe Hinweis unten. Alternativ „Text“ oder „Standardbrowser“ wählen.": ["QtWebEngine is missing, see the note below. Alternatively choose \"Text\" or \"default browser\".", "QtWebEngine est absent, voir l'indication ci-dessous. Sinon choisir « Texte » ou « navigateur par défaut ».", "QtWebEngine fählt, lueg dr Hiiwiis unde. Alternativ „Text“ oder „Standardbrowser“ wähle."],
"Dieser BMC kann nur TLS 1.0/1.1 (z. B. iLO 3/4, iDRAC7/8 mit alter Firmware). Die eingebettete Konsole geht damit nicht, bitte „Text“ wählen.": ["This BMC only speaks TLS 1.0/1.1 (e.g. iLO 3/4, iDRAC7/8 with old firmware). The embedded console cannot connect; please choose \"Text\".", "Ce BMC ne gère que TLS 1.0/1.1 (p. ex. iLO 3/4, iDRAC7/8 avec ancien firmware). La console intégrée ne fonctionne pas ; veuillez choisir « Texte ».", "Dä BMC cha nume TLS 1.0/1.1 (z. B. iLO 3/4, iDRAC7/8 mit alter Firmware). D iibettet Konsole goht demit nöd, bitte „Text“ wähle."],
"Anmeldeseite nicht erkannt. Bitte im Fenster von Hand anmelden.": ["Login page not recognised. Please log in by hand in the window.", "Page de connexion non reconnue. Veuillez vous connecter à la main dans la fenêtre.", "Aamäldsiite nöd erkannt. Bitte im Fenschter vo Hand aamälde."],
"Für die Textkonsole fehlt „pyte“: sudo pacman -S python-pyte  (Arch/CachyOS) bzw. pip install pyte": ["The text console needs \"pyte\": sudo pacman -S python-pyte  (Arch/CachyOS) or pip install pyte", "La console texte nécessite « pyte » : sudo pacman -S python-pyte  (Arch/CachyOS) ou pip install pyte", "Für d Textkonsole fählt „pyte“: sudo pacman -S python-pyte  (Arch/CachyOS) bzw. pip install pyte"],
"Für diesen Hersteller gibt es kein SSH-Verfahren, bitte IPMI-SOL wählen.": ["There is no SSH method for this vendor, please choose IPMI-SOL.", "Il n'y a pas de méthode SSH pour ce fabricant, veuillez choisir IPMI-SOL.", "Für dä Hersteller gits kei SSH-Verfahre, bitte IPMI-SOL wähle."],
"Dieser Hersteller bzw. diese Generation kann das nicht per Redfish. Bitte im UEFI-Bild F2/F9/Entf selbst drücken.": ["This vendor or generation cannot do this via Redfish. Please press F2/F9/Del yourself in the console picture.", "Ce fabricant ou cette génération ne le permet pas via Redfish. Veuillez appuyer vous-même sur F2/F9/Suppr dans l'image de la console.", "Dä Hersteller bzw. die Generation cha das nöd per Redfish. Bitte im Konsolebild sälber F2/F9/Entf drücke."],
"Prüfe {0} …": ["Checking {0} …", "Vérification de {0} …", "Prüef {0} …"],
"Verbunden mit {0}. Anmeldung läuft; danach öffnet sich die Konsole.": ["Connected to {0}. Logging in; the console opens afterwards.", "Connecté à {0}. Connexion en cours ; la console s'ouvre ensuite.", "Verbunde mit {0}. D Aamäldig lauft; danach goht d Konsole uf."],
"Textkonsole verbunden ({0}). In das schwarze Feld klicken, dann tippen. Bleibt es schwarz, ist im UEFI die „Console Redirection“ aus oder die Größe falsch.": ["Text console connected ({0}). Click into the black area, then type. If it stays black, \"Console Redirection\" is off in the UEFI or the size is wrong.", "Console texte connectée ({0}). Cliquer dans la zone noire, puis taper. Si elle reste noire, « Console Redirection » est désactivé dans l'UEFI ou la taille est incorrecte.", "Textkonsole verbunde ({0}). is schwarz Fäld klicke, denn tippe. Bliibt s schwarz, isch im UEFI d „Console Redirection“ uus oder d Grössi falsch."],
"Textkonsole beendet (Code {0}).": ["Text console ended (code {0}).", "Console texte terminée (code {0}).", "Textkonsole beändet (Code {0})."],
"Server {0} wird jetzt HART neu gestartet.": ["Server {0} will now be HARD restarted.", "Le serveur {0} va maintenant être redémarré DE FORCE.", "De Server {0} wird jetzt HERT neu gstartet."],
"Sende an {0} …": ["Sending to {0} …", "Envoi à {0} …", "Schicke a {0} …"],
"Die eingebettete Web-Konsole braucht QtWebEngine. Installation auf dem Rechner mit diesem Tool:": ["The embedded web console needs QtWebEngine. Installation on the computer running this tool:", "La console web intégrée nécessite QtWebEngine. Installation sur l'ordinateur qui exécute cet outil :", "D iibettet Web-Konsole bruucht QtWebEngine. Installation uf em Rechner mit däm Tool:"],
"Es fehlt: {0}. Installation: sudo pacman -S {1}  (Arch/CachyOS)": ["Missing: {0}. Installation: sudo pacman -S {1}  (Arch/CachyOS)", "Manquant : {0}. Installation : sudo pacman -S {1}  (Arch/CachyOS)", "Es fählt: {0}. Installation: sudo pacman -S {1}  (Arch/CachyOS)"],
"Start ins UEFI-Setup vormerken": ["Schedule boot into UEFI setup", "Préparer le démarrage dans la configuration UEFI", "Start is UEFI-Setup vormerke"],
"Neustart auslösen": ["Trigger restart", "Déclencher le redémarrage", "Neustart uslöse"],
"Im Browser geöffnet: {0}": ["Opened in browser: {0}", "Ouvert dans le navigateur : {0}", "Im Browser ufgmacht: {0}"],
"Start fehlgeschlagen: {0}": ["Start failed: {0}", "Échec du démarrage : {0}", "Start fehlgschlage: {0}"],
"5 · UEFI-Konsole": ["5 · UEFI console", "5 · Console UEFI", "5 · UEFI-Konsole"],
"6 · Hilfe": ["6 · Help", "6 · Aide", "6 · Hilf"],
"Benötigte Pakete": ["Required packages", "Paquets requis", "Nötigi Pakete"],
"installiert": ["installed", "installé", "installiert"],
"nicht installiert, Paket: {0}": ["not installed, package: {0}", "non installé, paquet : {0}", "nöd installiert, Paket: {0}"],
"Fehlendes nachinstallieren, auf dem Rechner mit diesem Tool (Terminal):": ["Install what is missing, on the computer running this tool (terminal):", "Installer ce qui manque, sur l'ordinateur qui exécute cet outil (terminal) :", "Fählendi nachinstalliere, uf em Rechner mit däm Tool (Terminal):"],
"✔ installiert, ❗ fehlt. Je nach gewähltem Weg werden nicht alle gebraucht:\nBild: QtWebEngine · Text: pyte + pyghmi (Supermicro-SOL) bzw. paramiko (SSH, Dell/HPE/Lenovo).": ["✔ installed, ❗ missing. Not all are needed, depending on the chosen way:\nPicture: QtWebEngine · Text: pyte + pyghmi (Supermicro SOL) or paramiko (SSH, Dell/HPE/Lenovo).", "✔ installé, ❗ manquant. Tous ne sont pas nécessaires selon la voie choisie :\nImage : QtWebEngine · Texte : pyte + pyghmi (Supermicro SOL) ou paramiko (SSH, Dell/HPE/Lenovo).", "✔ installiert, ❗ fählt. Je nach gwählte Wääg bruucht me nöd alli:\nBild: QtWebEngine · Text: pyte + pyghmi (Supermicro-SOL) bzw. paramiko (SSH, Dell/HPE/Lenovo)."],
"Bestandsserver": ["Existing servers", "Serveurs existants", "Bestehendi Server"],
"Adresse (BMC)": ["Address (BMC)", "Adresse (BMC)", "Adrässe (BMC)"],
"Status": ["Status", "Statut", "Status"],
"wartet …": ["waiting …", "en attente …", "wartet …"],
"OK": ["OK", "OK", "OK"],
"Fehler": ["Error", "Erreur", "Fähler"],
"kein Admin": ["not admin", "pas admin", "kei Admin"],
"Verbindung testen": ["Test connection", "Tester la connexion", "Verbindig teste"],
"Übernehmen in Neuware, UEFI, Dienste": ["Use in New hardware, UEFI, Services", "Reprendre dans Matériel neuf, UEFI, Services", "Übernäh i Neuware, UEFI, Dienscht"],
"Aus Serverliste holen": ["Get from server list", "Prendre dans la liste", "Us de Serverlischte hole"],
"Für alle Zeilen übernehmen": ["Apply to all rows", "Appliquer à toutes les lignes", "Für alli Zile übernäh"],
"Bereich einfügen": ["Insert range", "Insérer la plage", "Bereich iifüege"],
"Zugangsdaten und Adressen schnell eintragen": ["Enter credentials and addresses quickly", "Saisie rapide des accès et adresses", "Zuegangsdate und Adrässe schnäll iitrage"],
"Für Server, die schon konfiguriert sind: erst die Verbindung prüfen, dann die erfolgreichen Server in „Neuware“, „UEFI-Einstellungen“ und „Dienste“ übernehmen. Das Tool speichert nichts.": ["For servers that are already configured: check the connection first, then use the successful servers in “New hardware”, “UEFI settings” and “Services”. The tool stores nothing.", "Pour les serveurs déjà configurés : vérifier d'abord la connexion, puis reprendre les serveurs réussis dans « Matériel neuf », « Réglages UEFI » et « Services ». L'outil n'enregistre rien.", "Für Server, wo scho konfiguriert sind: zerscht d Verbindig prüefe, dänn d erfolgriichä Server i d „Neuware“, „UEFI-Iistellige“ und „Dienscht“ übernäh. S Tool speicheret nüt."],
"— Verbindungstest, {0} Server, nur lesend —": ["— Connection test, {0} servers, read-only —", "— Test de connexion, {0} serveurs, lecture seule —", "— Verbindigstest, {0} Server, nume läse —"],
"Fertig: {0} von {1} Servern erreichbar und angemeldet": ["Done: {0} of {1} servers reachable and logged in", "Terminé : {0} serveurs sur {1} joignables et connectés", "Fertig: {0} vo {1} Server erreichbar und aagmäldet"],
"Port {0} auf {1} nicht erreichbar ({2})": ["Port {0} on {1} not reachable ({2})", "Port {0} sur {1} injoignable ({2})", "Port {0} uf {1} nöd erreichbar ({2})"],
"  Port {0}: offen": ["Port {0}: open", "Port {0} : ouvert", "Port {0}: offe"],
"  Anmeldung: ok": ["Login: ok", "Connexion : ok", "Aamäldig: ok"],
"  BMC: {0}, Firmware {1}": ["BMC: {0}, firmware {1}", "BMC : {0}, firmware {1}", "BMC: {0}, Firmware {1}"],
"  Server: {0}, Stromzustand {1}": ["Server: {0}, power state {1}", "Serveur : {0}, état d'alimentation {1}", "Server: {0}, Stromzuestand {1}"],
"  Verschlüsselung: {0}": ["Encryption: {0}", "Chiffrement : {0}", "Verschlüsselig: {0}"],
"TLS 1.2 oder neuer": ["TLS 1.2 or newer", "TLS 1.2 ou plus récent", "TLS 1.2 oder neuer"],
"veraltet (TLS 1.0/1.1 möglich)": ["outdated (TLS 1.0/1.1 possible)", "obsolète (TLS 1.0/1.1 possible)", "veralteti (TLS 1.0/1.1 möglich)"],
"nur TLS 1.0/1.1": ["TLS 1.0/1.1 only", "TLS 1.0/1.1 uniquement", "nume TLS 1.0/1.1"],
"Anmeldung abgelehnt (Benutzer oder Passwort falsch)": ["Login rejected (wrong user or password)", "Connexion refusée (utilisateur ou mot de passe incorrect)", "Aamäldig abglehnt (Benutzer oder Passwort falsch)"],
"Anmeldung abgelehnt (Benutzer oder Passwort falsch, oder Konto gesperrt)": ["Login rejected (wrong user or password, or account locked)", "Connexion refusée (utilisateur ou mot de passe incorrect, ou compte verrouillé)", "Aamäldig abglehnt (Benutzer oder Passwort falsch, oder Konto gsperrt)"],
"  Hinweis: Das Konto hat keine Administratorrechte, Konfigurieren wird scheitern.": ["Note: the account has no administrator rights, configuring will fail.", "Remarque : le compte n'a pas de droits d'administrateur, la configuration échouera.", "Hiiwiis: S Konto het kei Administratorrächt, s Konfiguriere wird schiitere."],
"Noch kein erfolgreich getesteter Server. Erst „Verbindung testen“.": ["No successfully tested server yet. Run “Test connection” first.", "Aucun serveur testé avec succès. Lancez d'abord « Tester la connexion ».", "No kei erfolgriich tested Server. Zerscht „Verbindig teste“."],
"Die Liste ist leer.": ["The list is empty.", "La liste est vide.", "D Lischte isch leer."],
"Passwort fehlt": ["password missing", "mot de passe manquant", "Passwort fehlt"],
"z. B. 10.20.0.11": ["e.g. 10.20.0.11", "p. ex. 10.20.0.11", "z. B. 10.20.0.11"],
"z. B. 10.20.0.30": ["e.g. 10.20.0.30", "p. ex. 10.20.0.30", "z. B. 10.20.0.30"],
"Monitor": ["Monitor", "Moniteur", "Monitor"],
"Menü": ["Menu", "Menu", "Menü"],
"kein Signal": ["no signal", "aucun signal", "kei Signal"],
"Standbild": ["frozen frame", "image figée", "Standbild"],
"Text": ["Text", "Texte", "Text"],
"Kein Signal. Ein Klick öffnet die UEFI-Konsole, dort lässt sich eine Verbindung herstellen.": ["No signal. Click to open the UEFI console, where you can connect to a server.", "Aucun signal. Un clic ouvre la console UEFI, où vous pouvez établir une connexion.", "Kei Signal. Mit emene Klick gaht d UEFI-Konsole uf, det cha mer e Verbindig ufboue."],
"Live-Vorschau der Server-Konsole. Ein Klick öffnet sie in voller Größe.": ["Live preview of the server console. Click to open it full size.", "Aperçu en direct de la console du serveur. Un clic l'ouvre en taille réelle.", "Live-Vorschau vo de Server-Konsole. Mit emene Klick gaht si i voller Grössi uf."],
"Benötigtes Paket fehlt, siehe Hinweis auf der Seite „UEFI-Konsole“.": ["A required package is missing, see the note on the “UEFI console” page.", "Un paquet requis manque, voir la remarque sur la page « Console UEFI ».", "S nötige Paket fehlt, lueg dr Hiiwiis uf de Siite „UEFI-Konsole“."],
"Bild: eingebettete HTML5-Konsole des BMC, Maus und Tastatur wie am Original.": ["Picture: embedded HTML5 console of the BMC, mouse and keyboard as on the original.", "Image : console HTML5 intégrée du BMC, souris et clavier comme sur l'original.", "Bild: igbetteti HTML5-Konsole vom BMC, Muus und Tastatur wie am Original."],
"Text: serielle Konsole über IPMI-SOL oder SSH. Nur wenn im UEFI „Console Redirection“ aktiv ist.": ["Text: serial console via IPMI SOL or SSH. Only works if “Console Redirection” is enabled in the UEFI.", "Texte : console série via IPMI-SOL ou SSH. Uniquement si la « Console Redirection » est active dans l'UEFI.", "Text: serielli Konsole über IPMI-SOL oder SSH. Nume wenn im UEFI „Console Redirection“ aktiv isch."],
"Bild: BMC-Seite im Standardbrowser öffnen, ohne Auto-Anmeldung.": ["Picture: open the BMC page in the default browser, without automatic login.", "Image : ouvre la page du BMC dans le navigateur par défaut, sans connexion automatique.", "Bild: BMC-Siite im Standardbrowser ufmache, ohni Auto-Aamäldig."],
"▲ Einstellungen ausblenden": ["▲ Hide settings", "▲ Masquer les réglages", "▲ Iistellige verstecke"],
"▼ Einstellungen einblenden": ["▼ Show settings", "▼ Afficher les réglages", "▼ Iistellige zeige"],
"Blendet die Verbindungsangaben aus, damit die Konsole den ganzen Platz bekommt.": ["Hides the connection fields so the console gets all the space.", "Masque les champs de connexion pour donner toute la place à la console.", "Versteckt d Verbindigsaagabe, damit d Konsole de ganz Platz überchunnt."],
"⛶ Vollbild": ["⛶ Full screen", "⛶ Plein écran", "⛶ Vollbild"],
"✕ Vollbild beenden": ["✕ Exit full screen", "✕ Quitter le plein écran", "✕ Vollbild beände"],
"Die Konsole füllt den ganzen Bildschirm. Beenden mit dem Knopf oben „Vollbild beenden“,\ndenn Esc und F11 gehören dem Server.": ["The console fills the whole screen. Exit with the “Exit full screen” button at the top,\nbecause Esc and F11 belong to the server.", "La console remplit tout l'écran. Quitter avec le bouton « Quitter le plein écran » en haut,\ncar Échap et F11 appartiennent au serveur.", "D Konsole füllt de ganz Bildschirm. Beände mit em Chnopf obe „Vollbild beände“,\nwil Esc und F11 em Server ghöred."]
}



HELP = {
"de": "<h2>BMC-Steward</h2>\n<p>Der BMC-Steward bedient jeden Server der Reihe nach: Er legt den neuen Administrator an, setzt den\nHostnamen und stellt den BMC auf eine feste Adresse um. Die Server müssen nicht alle auf einmal\neingetragen werden, sie können nach und nach dazukommen.</p>\n\n<h2>Schutz vor Fehlbedienung</h2>\n<p>Das Tool ist so gebaut, dass ein Fehlgriff möglichst nichts kaputt macht:</p>\n<ul>\n<li><b>Trockenlauf ist voreingestellt.</b> Solange der Haken gesetzt ist, wird nichts geändert.</li>\n<li><b>Jeder scharfe Lauf wird vorher gezeigt</b> (alte IP → neue IP, Hostname). In allen Abfragen ist\n    „Abbrechen“ vorausgewählt, Enter bricht also ab.</li>\n<li><b>Ungewöhnliche Fälle verlangen das Eintippen der Server-Anzahl</b>, z. B. wenn ein Gerät nicht im\n    eigenen Staging-Netz liegt, und immer bei UEFI-Änderungen.</li>\n<li><b>Adressen werden geprüft:</b> doppelte IPs oder Hostnamen, Gateway außerhalb des Netzes, eine\n    Adresse dieses Rechners, eine Adresse aus dem laufenden DHCP-Bereich und die aktuelle IP eines\n    anderen Geräts werden abgelehnt. Vor dem Umstellen prüft das Tool außerdem, ob unter der neuen IP\n    schon ein Gerät antwortet, und lässt das Gerät dann unverändert.</li>\n<li><b>Dell:</b> Ein schon belegter User-Slot wird nie überschrieben.</li>\n<li><b>Das neue Passwort wird zweimal eingegeben.</b> Stimmen die Eingaben nicht überein, startet kein\n    Lauf.</li>\n<li><b>Der Adresswechsel wird geprüft:</b> Reißt beim Umstellen nur die Verbindung ab, gilt ein Gerät\n    erst als fertig, wenn es unter der alten Adresse wirklich nicht mehr antwortet.</li>\n<li><b>DHCP-Server:</b> startet nicht auf einer Karte mit öffentlicher Adresse, mit der Standardroute\n    (Internet/Firmennetz), mit einer per DHCP bezogenen Adresse oder wenn dort schon ein DHCP-Server\n    antwortet. Er stoppt von selbst, wenn die Karte später zur Standardroute wird. Geräte, die ihre\n    Adresse von einem anderen Server haben, werden nie abgewiesen. Ein nach einem Absturz\n    übrig gebliebener DHCP-Server wird beim nächsten Start beendet.</li>\n<li><b>UEFI:</b> Passwort-, TPM- und Lösch-Attribute sind gesperrt. Werte, die nicht zum Typ passen,\n    werden nicht gesetzt. Ein Neustart ist nicht vorausgewählt.</li>\n<li><b>Passwörter</b> bleiben im Arbeitsspeicher und landen nur auf ausdrücklichen Wunsch in einer\n    Datei.</li>\n<li><b>Eigenschutz:</b> UEFI-Änderungen, Firmware und „Ins UEFI-Setup booten“ prüfen vorher die Identität des Servers. Der Rechner, auf dem BMC-Steward läuft, und alle Server der Schutzliste (Knopf „Eigenschutz“ links unten) werden nie angefasst. Läuft BMC-Steward in einer VM (z. B. VMware), dort alle Hosts eintragen, auf denen die VM laufen kann – die VM kennt ihren Host nicht. Ist die Identität im scharfen Lauf nicht lesbar, wird der Server übersprungen.</li>\n<li><b>Zertifikate:</b> Beim ersten Kontakt merkt sich das Tool das Zertifikat jedes BMC (je Gerät, nicht je Adresse). Zeigt dasselbe Gerät später ein anderes, wird es gesperrt, bevor ein Passwort gesendet wird – Schutz gegen Mitlesen im Netz. Gewollte Wechsel unter „Zertifikate …“ links unten freigeben.</li>\n<li><b>Anmeldung per Session:</b> Das Passwort geht je Gerät einmal über die Leitung; am Ende meldet sich das Tool ab. Eine abgelehnte Anmeldung wird nie wiederholt.</li>\n<li><b>Sicherung und Revisionsprotokoll:</b> Vor jeder Änderung werden die wichtigsten Einstellungen des Geräts als JSON gesichert, jeder ändernde Aufruf eines scharfen Laufs wird protokolliert (ohne Passwörter). Beides unter „Sicherungen/Protokolle“.</li>\n<li><b>Firmware:</b> Die Datei wird einmal gelesen und per SHA-256 geprüft (Sollwert vom Hersteller eintragen); „Bei Fehler anhalten“ ist voreingestellt.</li>\n<li><b>Werkskonto:</b> Nach erfolgreicher Kontrolle meldet sich das Tool mit dem neuen Administrator unter der neuen Adresse an und sperrt das Werkskonto (Haken „Werkskonto danach sperren“, voreingestellt). Kann ein BMC Konten nicht abschalten (z. B. iLO 3/4), bekommt es ein nirgends gespeichertes Zufallspasswort.</li>\n</ul>\n\n<h2>Ablauf</h2>\n<ol>\n<li><b>DHCP-Server</b>: Die Netzwerkkarte am getrennten Staging-Switch braucht eine feste\n    Adresse ohne Gateway. DHCP starten, die Geräte holen sich eine Adresse und erscheinen in der Liste,\n    von dort „In Serverliste übernehmen“. Mit dem Haken „Automatisch übernehmen“ (voreingestellt) trägt das Tool\n    neue Geräte von selbst in die Serverliste ein; ohne Haken geht es nur per Knopf. Unter Linux läuft dafür dnsmasq.</li>\n<li><b>Neuware</b>: neue IP, Subnetzmaske, Gateway und Hostname eintragen. Was noch fehlt,\n    ist <b>gelb</b> markiert, Fehlerhaftes <b>rot</b>, Fertiges <b>grün</b>. Über dem Start-Knopf steht in\n    einer Zeile, was vor dem Lauf noch auszufüllen ist; der Grund zu jedem Feld steht im Tooltip.</li>\n<li><b>Trockenlauf</b>: zeigt im Protokoll, was gesendet würde.</li>\n<li><b>Scharfer Lauf</b>: Haken bei „Trockenlauf“ entfernen. Der Start-Knopf wird rot und heißt dann\n    „Scharf starten“. Klicken und die Abfrage bestätigen.</li>\n<li><b>Kontrolle</b>: Am Ende prüft das Tool, ob jede neue Adresse mit dem neuen Benutzer antwortet. Das\n    klappt nur, wenn dieser Rechner das neue Netz erreicht; sonst steht dort „FEHLT“, obwohl die\n    Umstellung geklappt haben kann.</li>\n</ol>\n\n<h2>Server nacheinander konfigurieren</h2>\n<ul>\n<li>Man kann einen Server konfigurieren, Start drücken, den nächsten anschließen und wieder Start\n    drücken, beliebig oft. Der DHCP-Server läuft dabei weiter.</li>\n<li>Erfolgreich umgestellte Zeilen werden <b>grün</b> markiert, bleiben in der Liste stehen und werden\n    beim nächsten Start übersprungen. Fehlgeschlagene werden erneut versucht.</li>\n<li>Soll ein grünes Gerät noch einmal bearbeitet werden: Zeile markieren, „Erneut bearbeiten“ und die\n    „Aktuelle IP“ auf seine jetzige Adresse setzen.</li>\n<li>Der Trockenlauf markiert nie etwas als fertig.</li>\n<li>„Liste speichern“ schreibt die grüne Markierung mit in die Datei (Spalte „fertig“), nach\n    „Liste laden“ sind die Zeilen wieder grün. So lässt sich ein Rollout auch an einem anderen Tag\n    fortsetzen.</li>\n</ul>\n\n<h2>Handy-Scanner (Passwort per Kamera)</h2>\n<ul>\n<li>In „Neuware“ öffnet „Handy-Scanner …“ ein Fenster. Adresse wählen, <b>Starten</b>, am Handy den QR-Code scannen (Handy und PC im selben Netz). Die Zertifikatswarnung einmal bestätigen und die Kamera erlauben.</li>\n<li>Am Handy zwischen <b>Barcode</b> und <b>Text (OCR)</b> umschalten, <b>Aufnehmen</b> tippen, den erkannten Wert prüfen und übernehmen. Er landet im Feld „Passwort“ der markierten Zeile (sonst der ersten ohne Passwort), danach rückt die Markierung eine Zeile weiter.</li>\n<li>Es wird kein Foto auf dem Handy gespeichert: Die Seite zeigt nur das Kamerabild, der PC liest den Text und verwirft das Bild sofort. Der Link ist geheim und gilt nur, solange der Scanner läuft.</li>\n<li>Am PC nötig: <code>openssl</code>, für Barcodes <code>zbar</code>, für Text <code>tesseract</code>, für den QR-Code <code>qrencode</code>. CachyOS: <code>sudo pacman -S zbar tesseract tesseract-data-eng qrencode</code>. Bei aktiver Firewall den Port freigeben (ufw: <code>sudo ufw allow 8443/tcp</code>).</li>\n<li>Texterkennung verwechselt gern 0/O und 1/l/I. Den Wert am Handy vor dem Übernehmen kontrollieren; er lässt sich dort korrigieren.</li>\n<li><b>Verbindungsweg:</b> Im Fenster wählst du, wie das Handy den PC erreicht. <i>Vorhandenes Netz</i>: beide im selben WLAN/LAN. <i>USB-Tethering</i>: Handy per Kabel anstecken und dort „USB-Tethering“ einschalten, dann erscheint die Adresse in der Liste (↻ liest neu ein). <i>Eigener WLAN-Hotspot</i> (nur Linux mit NetworkManager): Der PC öffnet ein WLAN mit zufälligem Namen und Passwort, zwei QR-Codes führen durch die Verbindung. Der WLAN-Adapter ist dabei belegt, eine bestehende WLAN-Verbindung des PCs wird getrennt. Beim Beenden wird der Hotspot samt Passwort wieder entfernt.</li>\n<li><b>Log-Export:</b> Am Handy ein paar Aufnahmen machen, dann im Scanner-Fenster <b>Log exportieren …</b>. Die ZIP-Datei enthält <code>log.txt</code> (Version, System, Tesseract/zbar, Handy-Angaben, jeder Erkennungsschritt). <b>Passwörter stehen nie im Protokoll</b>, nur Länge und Form (A groß, a klein, 9 Ziffer, # Rest). Mit dem Haken <i>Zwischenbilder mitspeichern</i> liegen zusätzlich die Bilder der Erkennung in der ZIP; die zeigen das Passwort lesbar, deshalb nur zur Fehlersuche nutzen und die ZIP danach löschen.</li>\n</ul>\n<h2>Profile</h2>\n<ul>\n<li>Ein Profil merkt sich die Einstellungen der Maske: Hersteller, Werksbenutzer, neuen Administrator,\n    User-Slot, Vorgaben für neue Zeilen und die DHCP-Einstellungen. Passwörter und die Serverliste\n    gehören nie dazu.</li>\n<li>Die Profile liegen in <code>bmc-profile.json</code> neben dem Tool; beim Start werden das zuletzt\n    genutzte Profil und die zuletzt gewählte Sprache geladen.</li>\n</ul>\n\n<h2>Bestandsserver</h2>\n<p>Für BMCs, die schon konfiguriert sind. Je Zeile Adresse, Port (Standard 443), Benutzer und Passwort eintragen, dann <b>Verbindung testen</b>: Das Tool prüft nur lesend Port, Verschlüsselung, Hersteller, Anmeldung, Modell, Firmware und Administratorrechte. Jeder Server bekommt genau einen Anmeldeversuch, damit ein falsches Passwort kein Konto sperrt. Erfolgreiche Server übernimmt <b>Übernehmen in Neuware, UEFI, Dienste</b> in die Serverliste; dort stellen Sie Netzwerk, UEFI und Dienste ein. „Neuware“, „UEFI-Einstellungen“ und „Dienste“ kennen einen Benutzer für alle Server, abweichende Benutzer in einem zweiten Durchgang. Der Reiter speichert nichts.</p>\n<h2>UEFI-Einstellungen</h2>\n<ul>\n<li>UEFI-Attribute unterscheiden sich je Modell. Das Tool liest deshalb die tatsächlich vorhandenen\n    Attribute von einem <b>Referenzgerät</b>, statt sie zu raten. Lesen ändert nichts.</li>\n<li><b>Ändern:</b> bei den gewünschten Zeilen „Neuer Wert“ ausfüllen, die Suche grenzt die Liste ein.\n    Leere Felder bleiben unverändert. Graue Zeilen (Passwörter, TPM, Löschfunktionen) sind gesperrt.</li>\n<li><b>Anwenden:</b> „Auf allen Servern der Liste setzen“ nutzt die Serverliste aus „Neuware“; grüne\n    Zeilen werden unter ihrer neuen IP angesprochen. Jedes Gerät bekommt nur Attribute, die es auch\n    hat. Werte, die schon stimmen, werden übersprungen.</li>\n<li><b>Neustart:</b> UEFI-Werte wirken erst nach einem Neustart. Mit dem Haken „Danach hart neu\n    starten“ wird ein laufender Server hart neu gestartet (wie mit der Reset-Taste), ein\n    ausgeschalteter eingeschaltet. Nur bei Servern ohne laufendes Betriebssystem verwenden.</li>\n<li><b>UEFI-Vorlagen</b> merken sich Hersteller und „Neuer Wert“-Felder und liegen getrennt in\n    <code>bmc-bios-profile.json</code>.</li>\n</ul>\n\n<h2>Dienste: NTP, Syslog, LDAP, Zertifikat</h2>\n<p>„Dienste“ richtet auf allen Servern der Liste gemeinsame Verwaltungsdienste ein. Nur angehakte\nAbschnitte werden angewendet; grüne (fertige) Server werden unter ihrer neuen Adresse angesprochen.\nAngemeldet wird mit dem Werksbenutzer, der Werks-Administrator bleibt bestehen. Der Trockenlauf ist\nvoreingestellt und zeigt nur, was gesendet würde; Passwörter und der Zertifikatsinhalt stehen dabei\nnicht im Protokoll.</p>\n<ul>\n<li><b>NTP:</b> ein bis zwei Zeitserver. Die Zeitzone wird nur bei Dell gesetzt; bei HPE, Supermicro\n    und Lenovo bitte in der Oberfläche wählen.</li>\n<li><b>Syslog:</b> nur Dell und HPE. Bei Supermicro und Lenovo gibt es keinen einheitlichen Weg, der\n    Abschnitt wird dort übersprungen.</li>\n<li><b>LDAP / Active Directory:</b> Server, Base-DN, optional ein Bind-Konto, Benutzer- und\n    Gruppen-Attribut. Unter „Gruppen → Rolle“ werden LDAP-Gruppen den Rollen Administrator, Operator\n    oder ReadOnly zugeordnet. Funktioniert bei allen vier Herstellern über den Standard-Redfish-Weg.</li>\n<li><b>Härtung:</b> schaltet IPMI über LAN, SSDP und auf Wunsch SNMP ab und setzt Kontosperre und Session-Zeitlimit über Standard-Redfish. Was ein BMC nicht kennt, wird übersprungen. Ohne IPMI geht die Textkonsole (SOL) bei Supermicro nicht mehr.</li>\n<li><b>HTTPS-Zertifikat:</b> importiert ein von einer CA signiertes PEM-Zertifikat. Das ist\n    experimentell und je Hersteller verschieden. Bei HPE muss der passende Schlüssel aus einer auf dem\n    iLO erzeugten Zertifikatsanforderung (CSR) stammen, sonst scheitert der Import. Beim Import kann der\n    BMC neu starten.</li>\n</ul>\n<p>Für jeden Dienst gilt: Ein Hersteller, der ihn nicht kennt, wird übersprungen, nicht geraten.\niLO 3/4 (über RIBCL) werden hier nicht bedient. Die Abläufe sind bisher nur gegen simulierte\nGegenstellen geprüft; vor einem großen Lauf immer zuerst ein einzelnes Gerät.</p>\n<h2>UEFI-Konsole</h2>\n<p>Zeigt das UEFI/BIOS eines Servers über den BMC an. Drei Wege: <b>Bild</b> (eingebettete HTML5-Konsole des BMC, Maus und Tastatur wie am Original, Anmeldung automatisch), <b>Text</b> (serielle Konsole über IPMI-SOL oder SSH, läuft unter Linux, braucht <code>pyte</code>, <code>paramiko</code> und <code>pyghmi</code>) und <b>Standardbrowser</b>. „Ins UEFI-Setup booten“ merkt per Redfish den Start ins Setup vor und startet den Server hart neu. Der Reiter speichert nichts. Alte BMCs mit nur TLS 1.0/1.1 gehen nur per Textkonsole. Oben links im Fenster zeigt der <b>Monitor</b> dezentes Rauschen, solange nichts verbunden ist; darin baut sich alle acht Sekunden das Logo mit der Kugel auf und wieder ab; zum Häkchen leuchtet es kurz grün. Mit Verbindung erscheint dort die Konsole als Miniatur, ein Klick darauf öffnet sie in voller Größe. Der Schalter unter dem Monitor wählt den Weg (HTML5, Text oder Browser). Ist die Web-Ansicht gerade verdeckt, bleibt im Monitor das letzte Bild als Standbild stehen. Der Knopf <b>Vollbild</b> lässt die Konsole den ganzen Bildschirm füllen; die Textkonsole vergrößert dazu ihre Schrift. Beendet wird mit „Vollbild beenden“ in der Leiste oben, denn Esc und F11 gehören dem Server.</p>\n<h2>Testlabor</h2>\n<p>Virtuelle Dell/HPE/Lenovo/Supermicro-Server zum Üben ohne Hardware. Jede Zeile des Clusters ist ein Server mit eigener Firmware-Generation, eigenem Werks-Login, Modell/Seriennummer und bei Dell optional einem belegten User-Slot. <b>Szenario speichern/laden</b> stellt denselben Aufbau wieder her (gleiche Seriennummer = gleiche MAC = gleiche DHCP-Adresse); Beispiele liegen in <code>tools/vbmc/szenarien</code>.</p>\n<ul>\n<li><b>Loopback:</b> ohne Root, nur dieser PC. Adressen als <code>IP:PORT</code> (127.x:8443).</li>\n<li><b>Konfigraum:</b> ein virtueller Staging-Switch <code>bmclab0</code>, an dem dieser PC mit fester Adresse hängt. Die Server holen sich ihre Adresse über den DHCP-Reiter (Karte bmclab0) oder haben eine feste Werks-IP, z. B. Dell 192.168.0.120 - zwei davon stören sich wie in echt. „Kabel ziehen/stecken“ schließt sie nacheinander an.</li>\n<li><b>Zuhause/Router:</b> die Server hängen an der Kabel-Netzwerkkarte, bekommen ihre Adresse vom Router und erscheinen dort als eigene Geräte; ein Umzug auf eine feste Adresse im LAN läuft wirklich durch. WLAN geht nicht.</li>\n</ul>\n<p>Konfigraum und Router brauchen beim Start einmal Root (pkexec); beim Stoppen werden Adressen freigegeben und alle Labor-Netzwerkgeräte wieder entfernt. Die Server antworten dort auf 443/22/623, also ohne <code>:Port</code>.</p>\n<h2>Hersteller im Vergleich</h2>\n<table border=\"1\" cellspacing=\"0\" cellpadding=\"6\">\n<tr><th></th><th>Dell iDRAC</th><th>HPE iLO</th><th>Supermicro</th><th>Lenovo XCC</th></tr>\n<tr><td><b>Werksbenutzer</b></td><td>root</td><td>Administrator</td><td>ADMIN</td><td>USERID</td></tr>\n<tr><td><b>Werkspasswort</b></td><td>calvin oder individuell (Service-Tag-Auszug)</td><td>individuell,\n    Anhänger am Gerät</td><td>individuell auf dem Aufkleber, alte Boards ADMIN</td><td>individuell,\n    Anhänger am Gerät</td></tr>\n<tr><td><b>Neuer Benutzer</b></td><td>in den User-Slot, nur wenn frei</td><td>neu angelegt</td>\n    <td>neu angelegt</td><td>neu angelegt</td></tr>\n<tr><td><b>Adresswechsel</b></td><td>sofort</td><td>nach iLO-Neustart (löst das Tool aus)</td>\n    <td>sofort</td><td>sofort</td></tr>\n<tr><td><b>Bis erreichbar</b></td><td>Sekunden</td><td>1 bis 3 Minuten</td><td>Sekunden</td>\n    <td>Sekunden</td></tr>\n</table>\n<p>Bleibt „Benutzer“ beim aktuellen Login leer, wird je Hersteller der Werksbenutzer verwendet; so gehen\nauch gemischte Listen. „Automatisch erkennen“ ermittelt den Hersteller pro Gerät, eine feste Wahl gilt\nfür alle Zeilen.</p>\n\n<h2>Dell iDRAC</h2>\n<ul>\n<li><b>User-Slot:</b> Standard 3 (Slot 2 ist root). Ist der Slot von einem anderen Benutzer belegt,\n    bricht das Tool für dieses Gerät ab und ändert nichts.</li>\n<li><b>Hostname:</b> gesetzt wird der DNS-Name des iDRAC, nicht der des Betriebssystems.</li>\n<li><b>Auslieferung:</b> je nach Bestellung DHCP oder feste Adresse 192.168.0.120. Mit fester Adresse\n    erscheint der iDRAC nicht in der DHCP-Liste; mehrere solche Geräte am selben Switch kollidieren,\n    dann einzeln anschließen.</li>\n<li><b>Generationen:</b> iDRAC9 (14. bis 16. Generation) und iDRAC7/8 (12. und 13. Generation). iDRAC7/8\n    brauchen mindestens Firmware 2.50.50.50; ältere erkennt das Tool, meldet es und ändert nichts.\n    iDRAC10 zuerst an einem Gerät testen. iDRAC6 wird nicht unterstützt.</li>\n</ul>\n\n<h2>HPE iLO</h2>\n<ul>\n<li><b>Passwort pro Gerät:</b> steht auf dem Anhänger, je Zeile in die Spalte „Passwort“ eintragen.</li>\n<li><b>Zuordnung:</b> in der DHCP-Liste meldet sich ein iLO meist als „ILO“ plus Seriennummer.</li>\n<li><b>Neustart:</b> Hostname und Adresse werden erst nach dem iLO-Neustart aktiv; der Server selbst\n    läuft weiter.</li>\n<li><b>Port:</b> konfiguriert wird der dedizierte iLO-Port. Neue Passwörter brauchen mindestens 8\n    Zeichen.</li>\n<li><b>Generationen:</b> iLO 5 und 6 (Gen10 bis Gen11) über Redfish. iLO 7 ist nicht geprüft. Für iLO 3\n    und 4 siehe den nächsten Abschnitt.</li>\n</ul>\n\n<h2>HPE iLO 3 und iLO 4 (ältere Server)</h2>\n<ul>\n<li><b>Geräte:</b> iLO 3 steckt in G7-Servern, iLO 4 in Gen8 und Gen9. Das Tool erkennt sie von selbst;\n    wer den Hersteller fest wählt, nimmt „HPE iLO 3/4“.</li>\n<li><b>Anderer Weg:</b> iLO 3 kennt kein Redfish. Das Tool nutzt deshalb für iLO 3 und iLO 4 die\n    XML-Schnittstelle RIBCL, die beide ab Werk mitbringen. Im Trockenlauf stehen die XML-Befehle im\n    Protokoll, Passwörter sind dort durch *** ersetzt.</li>\n<li><b>Neustart:</b> Nach dem Setzen von Adresse und Hostname startet das iLO von selbst neu; der Server\n    läuft weiter. Bis es unter der neuen Adresse antwortet, vergehen 1 bis 3 Minuten.</li>\n<li><b>Alte Verschlüsselung:</b> iLO 3 kann nur TLS 1.0/1.1. Das Tool schaltet dafür je Gerät von selbst\n    auf die alte Verschlüsselung um und schreibt einen Hinweis ins Protokoll. Solche Geräte gehören in\n    ein abgeschottetes Verwaltungsnetz.</li>\n<li><b>Grenzen:</b> Hostname höchstens 49 Zeichen. UEFI-Einstellungen („UEFI-Einstellungen“) gehen bei iLO 3 nicht.\n    iLO 2 und älter werden nicht unterstützt.</li>\n</ul>\n\n<h2>Supermicro</h2>\n<ul>\n<li><b>Passwort pro Gerät:</b> steht auf dem Aufkleber, je Zeile eintragen.</li>\n<li><b>Zugriffsarten:</b> das Tool vergibt Web, IPMI und Konsole mit. Steht im Protokoll „ohne\n    Zugriffsarten angelegt“, die Web-Anmeldung einmal prüfen.</li>\n<li><b>Lizenz:</b> bei älteren Boards können Redfish-Funktionen eine Lizenz brauchen.</li>\n<li><b>Nutanix NX</b> wird als Supermicro erkannt.</li>\n</ul>\n\n<h2>Lenovo XCC</h2>\n<ul>\n<li><b>Passwort pro Gerät:</b> steht auf dem Anhänger, je Zeile eintragen.</li>\n<li><b>Werksbenutzer:</b> USERID in Großbuchstaben.</li>\n<li><b>Port:</b> konfiguriert wird die Schnittstelle „NIC“, der dedizierte XCC-Port.</li>\n<li><b>Generationen:</b> XCC2/XCC3. XCC1 zuerst an einem Gerät testen.</li>\n</ul>\n\n<h2>Nutanix-Nodes (Dell XC, HPE DX, NX)</h2>\n<ul>\n<li>Das Tool setzt nur BMC-Adresse und BMC-Benutzer, nicht Hypervisor oder CVM.</li>\n<li>Nur <b>vor</b> der Cluster-Erstellung so umstellen. Bei einem laufenden Cluster das\n    Nutanix-Verfahren nutzen, weil der Cluster die BMC-Daten selbst verwendet.</li>\n</ul>\n\n<h2>Wenn etwas nicht klappt</h2>\n<ul>\n<li><b>DHCP startet nicht:</b> Die Meldung nennt den Grund. Meist hat die Karte ein Gateway oder ihre\n    Adresse per DHCP. Unter Linux z. B.:\n    <code>nmcli con add type ethernet ifname &lt;karte&gt; con-name staging ipv4.method manual\n    ipv4.addresses 192.168.0.1/24</code></li>\n<li><b>Keine Geräte in der DHCP-Liste:</b> Firewall (Linux mit ufw: UDP 67 freigeben) oder der BMC steht nicht auf DHCP.</li>\n<li><b>HTTP 401:</b> Benutzer oder Passwort falsch. Feld „Benutzer“ leeren, Passwort der Zeile\n    prüfen.</li>\n<li><b>Hersteller nicht erkannt:</b> Das Gerät antwortet weder per Redfish noch als iLO 3/4. iDRAC6,\n    iLO 2 und sehr alte Firmware werden nicht unterstützt. Ein iLO 3/4, bei dem die Geräteauskunft\n    abgeschaltet wurde, lässt sich über die feste Wahl „HPE iLO 3/4“ trotzdem ansprechen.</li>\n<li><b>Benutzer abgelehnt:</b> meist Passwortregeln. Die Meldung des BMC steht unter der\n    Fehlerzeile.</li>\n<li><b>„neue IP ist schon vergeben“:</b> Unter der Adresse antwortet bereits ein Gerät. Andere Adresse\n    wählen.</li>\n<li><b>Liste aus Excel oder einem anderen Tool:</b> Ohne Spalte „fertig“ sind alle Zeilen offen. Schon\n    umgestellte Geräte sind unter der alten Adresse nicht erreichbar, diese Zeilen entfernen oder\n    anpassen.</li>\n</ul>\n\n<h2>Daten und Grenzen</h2>\n<ul>\n<li>Das Tool speichert nichts von selbst. „Liste speichern“ fragt, ob Passwörter mit in die Datei\n    sollen (dann im Klartext). Eine solche Datei nach dem Rollout löschen.</li>\n<li>Die Abläufe für HPE (auch iLO 3/4), ältere iDRAC7/8, Supermicro, Lenovo und UEFI sind bisher nur gegen simulierte Gegenstellen geprüft. Vor einem großen Lauf immer zuerst ein einzelnes Gerät\n    konfigurieren.</li>\n<li>Das <b>Protokoll</b> unten ist für das ganze Programm dasselbe, auch im Testlabor. Es füllt sich schon beim Start\n    und enthält jede Aktion. „Protokoll“ öffnet es als Textdatei im Standard-Editor; „Info“ oder „Debug“ wählt\n    die Genauigkeit. Passwörter stehen nicht darin.</li>\n<li>Die Sprache lässt sich links unten unter dem Menü umstellen, solange kein Lauf aktiv ist und der DHCP-Server\n    gestoppt ist.</li>\n</ul>\n\n",
"en": "<h2>BMC-Steward</h2>\n<p>BMC-Steward serves one server after another: it creates the new administrator, sets the hostname and\nswitches the BMC to a static address. You don't have to enter all servers at once; they can be added\none by one.</p>\n\n<h2>Protection against mistakes</h2>\n<p>The tool is built so that a slip does as little damage as possible:</p>\n<ul>\n<li><b>Dry run is the default.</b> As long as it is ticked, nothing is changed.</li>\n<li><b>Every live run is shown first</b> (old IP → new IP, hostname). “Cancel” is preselected in every\n    prompt, so pressing Enter cancels.</li>\n<li><b>Unusual cases require typing the number of servers</b>, e.g. when a device is not in the\n    computer's own staging network, and always for UEFI changes.</li>\n<li><b>Addresses are checked:</b> duplicate IPs or hostnames, a gateway outside the network, an address\n    of this computer, an address from the running DHCP range and the current IP of another device are\n    rejected. Before reconfiguring, the tool also checks whether a device already answers on the new IP\n    and, if so, leaves the device unchanged.</li>\n<li><b>Dell:</b> an occupied user slot is never overwritten.</li>\n<li><b>The new password is entered twice.</b> If the entries differ, no run starts.</li>\n<li><b>The address change is verified:</b> if only the connection drops during the change, a device\n    counts as done only once it really no longer answers on the old address.</li>\n<li><b>DHCP server:</b> does not start on a card with a public address, with the default route\n    (internet/company network), with an address obtained via DHCP, or when a DHCP server already\n    answers there. It stops by itself if the card later becomes the default route. Devices that got\n    their address from another server are never refused. A DHCP server left over after a crash is\n    stopped at the next start.</li>\n<li><b>UEFI:</b> password, TPM and erase attributes are locked. Values that don't match the type are\n    not set. A restart is not preselected.</li>\n<li><b>Passwords</b> stay in memory and only go into a file if you explicitly ask for it.</li>\n<li><b>Self-protection:</b> UEFI changes, firmware and “Boot into UEFI setup” check the server's identity first. The machine BMC-Steward runs on and every server on the protection list (button “Self-protection” at the bottom left) are never touched. If BMC-Steward runs in a VM (e.g. VMware), enter every host the VM can run on there – the VM does not know its host. If the identity cannot be read in a live run, the server is skipped.</li>\n<li><b>Certificates:</b> On first contact the tool remembers each BMC's certificate (per device, not per address). If the same device later shows a different one, it is blocked before any password is sent – protection against eavesdropping on the network. Release intended changes under “Certificates …” at the bottom left.</li>\n<li><b>Session login:</b> The password crosses the wire once per device; at the end the tool logs out. A rejected login is never retried.</li>\n<li><b>Backup and audit log:</b> Before every change the device's main settings are saved as JSON, and every changing call of a live run is logged (without passwords). Both under “Backups/logs”.</li>\n<li><b>Firmware:</b> The file is read once and checked by SHA-256 (enter the vendor's value); “Stop on error” is preselected.</li>\n<li><b>Factory account:</b> After a successful check the tool logs in with the new administrator at the new address and locks the factory account (option “Lock factory account afterwards”, preselected). If a BMC cannot disable accounts (e.g. iLO 3/4), it gets a random password that is stored nowhere.</li>\n</ul>\n\n<h2>Procedure</h2>\n<ol>\n<li><b>DHCP server</b>: the network card on the separate staging switch needs a static address\n    without a gateway. Start DHCP; the devices get an address and appear in the list. From there,\n    “Add to server list”. With “Add automatically” ticked (the default) the tool enters new devices into the\n    server list by itself; untick it to add them by button only. On Linux dnsmasq is used.</li>\n<li><b>New hardware</b>: enter new IP, subnet mask, gateway and hostname. Whatever is still\n    missing is marked <b>yellow</b>, wrong entries <b>red</b>, finished ones <b>green</b>. A line above the\n    Start button says what still has to be filled in; the tooltip of each field gives the reason.</li>\n<li><b>Dry run</b>: the log shows what would be sent.</li>\n<li><b>Live run</b>: untick “Dry run”. The Start button turns red and reads “Start live run”. Click it\n    and confirm the prompt.</li>\n<li><b>Check</b>: at the end the tool checks whether each new address answers with the new user. That\n    only works if this computer can reach the new network; otherwise it shows “MISSING” even though\n    the change may have worked.</li>\n</ol>\n\n<h2>Configuring servers one after another</h2>\n<ul>\n<li>You can configure one server, press Start, connect the next one and press Start again, as often as\n    you like. The DHCP server keeps running.</li>\n<li>Successfully reconfigured rows turn <b>green</b>, stay in the list and are skipped at the next\n    start. Failed ones are tried again.</li>\n<li>To process a green device again: select the row, click “Process again” and set “Current IP” to its\n    present address.</li>\n<li>A dry run never marks anything as done.</li>\n<li>“Save list” writes the green mark into the file as well (column “fertig”); after “Load list” the\n    rows are green again. This lets you continue a rollout on another day.</li>\n</ul>\n\n<h2>Phone scanner (password by camera)</h2>\n<ul>\n<li>On “New hardware”, “Phone scanner …” opens a window. Choose the address, press <b>Start</b> and scan the QR code with the phone (phone and PC on the same network). Accept the certificate warning once and allow the camera.</li>\n<li>On the phone switch between <b>Barcode</b> and <b>Text (OCR)</b>, tap <b>Capture</b>, check the recognised value and send it. It goes into the “Password” field of the selected row (otherwise the first one without a password); the selection then moves down one row.</li>\n<li>No photo is saved on the phone: the page only shows the camera view, the PC reads the text and discards the image at once. The link is secret and only valid while the scanner runs.</li>\n<li>Needed on the PC: <code>openssl</code>, <code>zbar</code> for barcodes, <code>tesseract</code> for text, <code>qrencode</code> for the QR code. CachyOS: <code>sudo pacman -S zbar tesseract tesseract-data-eng qrencode</code>. With an active firewall open the port (ufw: <code>sudo ufw allow 8443/tcp</code>).</li>\n<li>Text recognition often confuses 0/O and 1/l/I. Check the value on the phone before sending; it can be corrected there.</li>\n<li><b>Connection method:</b> In the window you choose how the phone reaches the PC. <i>Existing network</i>: both on the same Wi-Fi/LAN. <i>USB tethering</i>: connect the phone by cable and switch on “USB tethering” there; the address then appears in the list (↻ reads again). <i>Own Wi-Fi hotspot</i> (Linux with NetworkManager only): the PC opens a Wi-Fi with a random name and password, two QR codes guide you through connecting. The Wi-Fi adapter is occupied and an existing Wi-Fi connection of the PC is dropped. On stop the hotspot and its password are removed again.</li>\n<li><b>Log export:</b> Take a few shots on the phone, then click <b>Export log …</b> in the scanner window. The ZIP holds <code>log.txt</code> (version, system, tesseract/zbar, phone details, every recognition step). <b>Passwords are never in the log</b>, only length and shape (A upper, a lower, 9 digit, # other). With <i>Keep intermediate images</i> ticked the recognition images are added to the ZIP; they show the password legibly, so use it only for troubleshooting and delete the ZIP afterwards.</li>\n</ul>\n<h2>Profiles</h2>\n<ul>\n<li>A profile remembers the settings of the form: vendor, factory user, new administrator, user slot,\n    defaults for new rows and the DHCP settings. Passwords and the server list are never included.</li>\n<li>Profiles are stored in <code>bmc-profile.json</code> next to the tool; at start-up the last used\n    profile and the last chosen language are loaded.</li>\n</ul>\n\n<h2>Existing servers</h2>\n<p>For BMCs that are already configured. Enter address, port (default 443), user and password per row, then <b>Test connection</b>: the tool checks read-only the port, encryption, vendor, login, model, firmware and administrator rights. Each server gets exactly one login attempt so a wrong password cannot lock an account. <b>Use in New hardware, UEFI, Services</b> copies the successful servers into the server list, where you set network, UEFI and services. “New hardware”, “UEFI settings” and “Services” know one user for all servers; use different users in a second pass. This tab stores nothing.</p>\n<h2>UEFI settings</h2>\n<ul>\n<li>UEFI attributes differ per model. The tool therefore reads the attributes that actually exist from\n    a <b>reference device</b> instead of guessing. Reading changes nothing.</li>\n<li><b>Change:</b> fill in “New value” in the rows you want; the search narrows the list. Empty fields\n    stay unchanged. Grey rows (passwords, TPM, erase functions) are locked.</li>\n<li><b>Apply:</b> “Set on all servers in the list” uses the server list from “New hardware”; green rows are\n    addressed at their new IP. Each device only gets attributes it actually has. Values that are\n    already correct are skipped.</li>\n<li><b>Restart:</b> UEFI values only take effect after a restart. With “Then hard restart” ticked, a\n    running server is hard restarted (like pressing reset) and a powered-off one is switched on. Use\n    only for servers without a running operating system.</li>\n<li><b>UEFI templates</b> remember the vendor and the “New value” fields and are stored separately in\n    <code>bmc-bios-profile.json</code>.</li>\n</ul>\n\n<h2>Services: NTP, syslog, LDAP, certificate</h2>\n<p>“Services” sets up shared management services on every server in the list. Only ticked sections are\napplied; green (finished) servers are reached at their new address. Login uses the factory user and the\nfactory administrator stays in place. The dry run is the default and only shows what would be sent;\npasswords and the certificate content do not appear in the log.</p>\n<ul>\n<li><b>NTP:</b> one or two time servers. The time zone is set on Dell only; on HPE, Supermicro and\n    Lenovo please choose it in the web interface.</li>\n<li><b>Syslog:</b> Dell and HPE only. There is no uniform way on Supermicro and Lenovo, so the section\n    is skipped there.</li>\n<li><b>LDAP / Active Directory:</b> server, base DN, an optional bind account, user and group attribute.\n    Under “Groups → role” LDAP groups are mapped to the roles Administrator, Operator or ReadOnly. Works\n    on all four vendors through the standard Redfish path.</li>\n<li><b>Hardening:</b> switches off IPMI over LAN, SSDP and optionally SNMP and sets account lockout and session timeout via standard Redfish. Whatever a BMC does not know is skipped. Without IPMI the text console (SOL) on Supermicro no longer works.</li>\n<li><b>HTTPS certificate:</b> imports a CA-signed PEM certificate. This is experimental and\n    vendor-specific. On HPE the matching key must come from a CSR generated on the iLO, otherwise the\n    import fails. The BMC may restart during the import.</li>\n</ul>\n<p>For each service a vendor that does not know it is skipped, not guessed. iLO 3/4 (via RIBCL) are not\nhandled here. The procedures have so far only been tested against simulated counterparts; always\nconfigure a single device first before a large run.</p>\n<h2>UEFI console</h2>\n<p>Shows a server's UEFI/BIOS through the BMC. Three ways: <b>Picture</b> (embedded HTML5 console of the BMC, mouse and keyboard like the original, automatic login), <b>Text</b> (serial console via IPMI-SOL or SSH, runs on Linux, needs <code>pyte</code>, <code>paramiko</code> and <code>pyghmi</code>) and <b>default browser</b>. \"Boot into UEFI setup\" schedules the next boot into setup via Redfish and hard-restarts the server. This tab stores nothing. Old BMCs with TLS 1.0/1.1 only work via the text console. At the top left of the window, the <b>monitor</b> shows subtle noise while nothing is connected; every eight seconds the logo with the sphere builds up in it and fades away again; with the check mark it briefly glows green. Once connected, the console appears there as a thumbnail; a click on it opens the console full size. The switch below the monitor picks the way (HTML5, Text or Browser). While the web view is hidden, the monitor keeps the last picture as a still frame. The <b>Full screen</b> button makes the console fill the whole screen; the text console enlarges its font to match. Exit with “Exit full screen” in the bar at the top, because Esc and F11 belong to the server.</p>\n<h2>Test lab</h2>\n<p>Virtual Dell/HPE/Lenovo/Supermicro servers for practising without hardware. Each row of the cluster is one server with its own firmware generation, factory login, model/serial number and, for Dell, optionally an occupied user slot. <b>Save/load scenario</b> restores the same setup (same serial number = same MAC = same DHCP address); examples are in <code>tools/vbmc/szenarien</code>.</p>\n<ul>\n<li><b>Loopback:</b> no root, this PC only. Addresses as <code>IP:PORT</code> (127.x:8443).</li>\n<li><b>Config room:</b> a virtual staging switch <code>bmclab0</code> to which this PC is attached with a static address. The servers get their address from the DHCP tab (card bmclab0) or have a static factory IP, e.g. Dell 192.168.0.120 - two of them collide like real ones. “Pull/plug cable” connects them one at a time.</li>\n<li><b>Home/router:</b> the servers hang on the wired network card, get their address from the router and appear there as devices of their own; moving to a static address in the LAN really works. Wi-Fi does not work.</li>\n</ul>\n<p>Config room and router need root once at start (pkexec); on stop the addresses are released and all lab network devices are removed. There the servers answer on 443/22/623, i.e. without <code>:port</code>.</p>\n<h2>Vendors compared</h2>\n<table border=\"1\" cellspacing=\"0\" cellpadding=\"6\">\n<tr><th></th><th>Dell iDRAC</th><th>HPE iLO</th><th>Supermicro</th><th>Lenovo XCC</th></tr>\n<tr><td><b>Factory user</b></td><td>root</td><td>Administrator</td><td>ADMIN</td><td>USERID</td></tr>\n<tr><td><b>Factory password</b></td><td>calvin or individual (service tag sheet)</td><td>individual,\n    tag on the device</td><td>individual on the sticker, old boards ADMIN</td><td>individual, tag on\n    the device</td></tr>\n<tr><td><b>New user</b></td><td>into the user slot, only if free</td><td>created</td><td>created</td>\n    <td>created</td></tr>\n<tr><td><b>Address change</b></td><td>immediate</td><td>after iLO restart (triggered by the tool)</td>\n    <td>immediate</td><td>immediate</td></tr>\n<tr><td><b>Until reachable</b></td><td>seconds</td><td>1 to 3 minutes</td><td>seconds</td>\n    <td>seconds</td></tr>\n</table>\n<p>If “User” in the current login stays empty, the vendor's factory user is used, so mixed lists work.\n“Detect automatically” determines the vendor per device; a fixed choice applies to all rows.</p>\n\n<h2>Dell iDRAC</h2>\n<ul>\n<li><b>User slot:</b> default 3 (slot 2 is root). If another user occupies the slot, the tool stops for\n    this device and changes nothing.</li>\n<li><b>Hostname:</b> the iDRAC DNS name is set, not the operating system's.</li>\n<li><b>As delivered:</b> DHCP or static 192.168.0.120, depending on the order. With a static address the\n    iDRAC does not appear in the DHCP list; several such devices on one switch collide, so connect\n    them one at a time.</li>\n<li><b>Generations:</b> iDRAC9 (14th to 16th generation) and iDRAC7/8 (12th and 13th generation). iDRAC7/8\n    need at least firmware 2.50.50.50; the tool detects older firmware, reports it and changes nothing.\n    Test iDRAC10 on one device first. iDRAC6 is not supported.</li>\n</ul>\n\n<h2>HPE iLO</h2>\n<ul>\n<li><b>Password per device:</b> on the tag; enter it per row in the “Password” column.</li>\n<li><b>Identification:</b> in the DHCP list an iLO usually reports as “ILO” plus serial number.</li>\n<li><b>Restart:</b> hostname and address only become active after the iLO restart; the server itself\n    keeps running.</li>\n<li><b>Port:</b> the dedicated iLO port is configured. New passwords need at least 8 characters.</li>\n<li><b>Generations:</b> iLO 5 and 6 (Gen10 to Gen11) via Redfish. iLO 7 is untested. For iLO 3 and 4 see\n    the next section.</li>\n</ul>\n\n<h2>HPE iLO 3 and iLO 4 (older servers)</h2>\n<ul>\n<li><b>Devices:</b> iLO 3 is found in G7 servers, iLO 4 in Gen8 and Gen9. The tool detects them by\n    itself; if you pick the vendor by hand, choose “HPE iLO 3/4”.</li>\n<li><b>A different route:</b> iLO 3 has no Redfish. For iLO 3 and iLO 4 the tool therefore uses the RIBCL\n    XML interface, which both ship with. In a dry run the XML commands appear in the log, with\n    passwords replaced by ***.</li>\n<li><b>Restart:</b> after the address and hostname are set, the iLO restarts by itself; the server keeps\n    running. It takes 1 to 3 minutes until it answers on the new address.</li>\n<li><b>Old encryption:</b> iLO 3 only speaks TLS 1.0/1.1. The tool switches to the old encryption per\n    device by itself and writes a note to the log. Such devices belong in an isolated management\n    network.</li>\n<li><b>Limits:</b> hostname at most 49 characters. UEFI settings (“UEFI settings”) do not work with iLO 3. iLO 2\n    and older are not supported.</li>\n</ul>\n\n<h2>Supermicro</h2>\n<ul>\n<li><b>Password per device:</b> on the sticker; enter it per row.</li>\n<li><b>Access types:</b> the tool also grants web, IPMI and console. If the log says “created without\n    access types”, check the web login once.</li>\n<li><b>Licence:</b> on older boards some Redfish functions may need a licence.</li>\n<li><b>Nutanix NX</b> is detected as Supermicro.</li>\n</ul>\n\n<h2>Lenovo XCC</h2>\n<ul>\n<li><b>Password per device:</b> on the tag; enter it per row.</li>\n<li><b>Factory user:</b> USERID in capitals.</li>\n<li><b>Port:</b> the “NIC” interface, i.e. the dedicated XCC port, is configured.</li>\n<li><b>Generations:</b> XCC2/XCC3. Test XCC1 on one device first.</li>\n</ul>\n\n<h2>Nutanix nodes (Dell XC, HPE DX, NX)</h2>\n<ul>\n<li>The tool only sets the BMC address and BMC user, not the hypervisor or CVM.</li>\n<li>Only change these <b>before</b> creating the cluster. On a running cluster use the Nutanix\n    procedure, because the cluster uses the BMC data itself.</li>\n</ul>\n\n<h2>Troubleshooting</h2>\n<ul>\n<li><b>DHCP does not start:</b> the message gives the reason. Usually the card has a gateway or got its\n    address via DHCP. On Linux, for example:\n    <code>nmcli con add type ethernet ifname &lt;card&gt; con-name staging ipv4.method manual\n    ipv4.addresses 192.168.0.1/24</code></li>\n<li><b>No devices in the DHCP list:</b> firewall (Linux with ufw: allow UDP 67) or the BMC is not set to DHCP.</li>\n<li><b>HTTP 401:</b> wrong user or password. Clear “User”, check the row's password.</li>\n<li><b>Vendor not recognised:</b> the device answers neither via Redfish nor as an iLO 3/4. iDRAC6, iLO 2\n    and very old firmware are not supported. An iLO 3/4 whose device information has been switched off\n    can still be addressed by choosing “HPE iLO 3/4” by hand.</li>\n<li><b>User refused:</b> usually password rules. The BMC's message appears below the error line.</li>\n<li><b>“new IP is already taken”:</b> a device already answers on that address. Choose another one.</li>\n<li><b>List from Excel or another tool:</b> without the “fertig” column all rows are open. Devices\n    already reconfigured cannot be reached at their old address; remove or adjust those rows.</li>\n</ul>\n\n<h2>Data and limits</h2>\n<ul>\n<li>The tool saves nothing on its own. “Save list” asks whether passwords go into the file (then in plain\n    text). Delete such a file after the rollout.</li>\n<li>The procedures for HPE (including iLO 3/4), older iDRAC7/8, Supermicro, Lenovo and UEFI have so far only been tested against simulated counterparts. Always configure a single device first before a large\n    run.</li>\n<li>The <b>log</b> below is the same for the whole program, including the test lab. It fills as soon as the program\n    starts and records every action. “Log” opens it as a text file in the default editor; “Info” or “Debug”\n    sets the level of detail. It contains no passwords.</li>\n<li>The language can be changed at the bottom left, below the menu while no run is active and the DHCP server is\n    stopped.</li>\n</ul>\n\n",
"fr": "<h2>BMC-Steward</h2>\n<p>BMC-Steward sert les serveurs l'un après l'autre : il crée le nouvel administrateur, définit le nom\nd'hôte et passe le BMC sur une adresse fixe. Inutile de saisir tous les serveurs d'un coup, ils peuvent\nêtre ajoutés au fur et à mesure.</p>\n\n<h2>Protection contre les erreurs de manipulation</h2>\n<p>L'outil est conçu pour qu'une fausse manœuvre cause le moins de dégâts possible :</p>\n<ul>\n<li><b>La simulation est activée par défaut.</b> Tant que la case est cochée, rien n'est modifié.</li>\n<li><b>Chaque exécution réelle est d'abord affichée</b> (ancienne IP → nouvelle IP, nom d'hôte).\n    « Annuler » est présélectionné dans toutes les questions : la touche Entrée annule.</li>\n<li><b>Les cas inhabituels exigent de saisir le nombre de serveurs</b>, p. ex. lorsqu'un appareil n'est\n    pas dans le réseau de staging de l'ordinateur, et toujours pour les modifications UEFI.</li>\n<li><b>Les adresses sont vérifiées :</b> IP ou noms d'hôte en double, passerelle hors du réseau, adresse\n    de cet ordinateur, adresse de la plage DHCP active et IP actuelle d'un autre appareil sont\n    refusées. Avant la reconfiguration, l'outil vérifie aussi si un appareil répond déjà sur la\n    nouvelle IP et, le cas échéant, ne modifie pas l'appareil.</li>\n<li><b>Dell :</b> un emplacement utilisateur occupé n'est jamais écrasé.</li>\n<li><b>Le nouveau mot de passe est saisi deux fois.</b> Si les saisies diffèrent, rien ne démarre.</li>\n<li><b>Le changement d'adresse est vérifié :</b> si seule la connexion est coupée pendant le\n    changement, un appareil n'est considéré comme terminé que lorsqu'il ne répond vraiment plus à\n    l'ancienne adresse.</li>\n<li><b>Serveur DHCP :</b> ne démarre pas sur une carte ayant une adresse publique, portant la route par\n    défaut (Internet/réseau d'entreprise), ayant obtenu son adresse par DHCP, ou si un serveur DHCP y\n    répond déjà. Il s'arrête de lui-même si la carte devient plus tard la route par défaut. Les\n    appareils ayant reçu leur adresse d'un autre serveur ne sont jamais refusés. Un serveur DHCP resté\n    actif après un plantage est arrêté au démarrage suivant.</li>\n<li><b>UEFI :</b> les attributs de mot de passe, TPM et effacement sont verrouillés. Les valeurs qui ne\n    correspondent pas au type ne sont pas appliquées. Le redémarrage n'est pas présélectionné.</li>\n<li><b>Les mots de passe</b> restent en mémoire et ne sont écrits dans un fichier que sur demande\n    explicite.</li>\n<li><b>Auto-protection :</b> les modifications UEFI, le firmware et « Démarrer dans le setup UEFI » vérifient d'abord l'identité du serveur. La machine qui exécute BMC-Steward et tous les serveurs de la liste de protection (bouton « Auto-protection » en bas à gauche) ne sont jamais touchés. Si BMC-Steward tourne dans une VM (p. ex. VMware), y saisir tous les hôtes sur lesquels la VM peut tourner – la VM ne connaît pas son hôte. Si l'identité est illisible lors d'une exécution réelle, le serveur est ignoré.</li>\n<li><b>Certificats :</b> Au premier contact, l'outil mémorise le certificat de chaque BMC (par appareil, pas par adresse). Si le même appareil en présente un autre plus tard, il est bloqué avant l'envoi de tout mot de passe – protection contre l'écoute sur le réseau. Autoriser les changements voulus sous « Certificats … » en bas à gauche.</li>\n<li><b>Connexion par session :</b> Le mot de passe ne passe qu'une fois par appareil ; à la fin, l'outil se déconnecte. Une connexion refusée n'est jamais répétée.</li>\n<li><b>Sauvegarde et journal d'audit :</b> Avant chaque modification, les principaux réglages de l'appareil sont sauvegardés en JSON, et chaque appel modifiant d'une exécution réelle est journalisé (sans mots de passe). Les deux sous « Sauvegardes/journaux ».</li>\n<li><b>Firmware :</b> Le fichier est lu une seule fois et vérifié par SHA-256 (saisir la valeur du fabricant) ; « Arrêter en cas d'erreur » est présélectionné.</li>\n<li><b>Compte d'usine :</b> Après un contrôle réussi, l'outil se connecte avec le nouvel administrateur à la nouvelle adresse et verrouille le compte d'usine (option « Verrouiller ensuite le compte d'usine », présélectionnée). Si un BMC ne peut pas désactiver de comptes (p. ex. iLO 3/4), il reçoit un mot de passe aléatoire enregistré nulle part.</li>\n</ul>\n\n<h2>Déroulement</h2>\n<ol>\n<li><b>Serveur DHCP</b> : la carte réseau reliée au switch de staging séparé doit avoir une\n    adresse fixe sans passerelle. Démarrer DHCP ; les appareils obtiennent une adresse et apparaissent\n    dans la liste, puis « Reprendre dans la liste des serveurs ». Avec la case « Ajouter automatiquement » (cochée par défaut), l'outil\n    inscrit lui-même les nouveaux appareils dans la liste ; décochée, seul le bouton le fait. Sous Linux, dnsmasq est utilisé.</li>\n<li><b>Matériel neuf</b> : saisir nouvelle IP, masque, passerelle et nom d'hôte. Ce qui\n    manque encore est marqué en <b>jaune</b>, ce qui est erroné en <b>rouge</b>, ce qui est terminé en\n    <b>vert</b>. Une ligne au-dessus du bouton Démarrer indique ce qu'il reste à remplir ; l'infobulle de\n    chaque champ donne la raison.</li>\n<li><b>Simulation</b> : le journal montre ce qui serait envoyé.</li>\n<li><b>Exécution réelle</b> : décocher « Simulation ». Le bouton devient rouge et s'intitule « Lancer\n    l'exécution réelle ». Cliquer puis confirmer.</li>\n<li><b>Contrôle</b> : à la fin, l'outil vérifie que chaque nouvelle adresse répond avec le nouvel\n    utilisateur. Cela ne fonctionne que si cet ordinateur atteint le nouveau réseau ; sinon « ABSENT »\n    s'affiche, même si la modification a pu réussir.</li>\n</ol>\n\n<h2>Configurer les serveurs l'un après l'autre</h2>\n<ul>\n<li>On peut configurer un serveur, cliquer sur Démarrer, brancher le suivant et recommencer, autant de\n    fois que nécessaire. Le serveur DHCP continue de tourner.</li>\n<li>Les lignes reconfigurées avec succès passent en <b>vert</b>, restent dans la liste et sont ignorées\n    au démarrage suivant. Celles en échec sont retentées.</li>\n<li>Pour retraiter un appareil vert : sélectionner la ligne, cliquer sur « Traiter à nouveau » et\n    mettre son adresse actuelle dans « IP actuelle ».</li>\n<li>La simulation ne marque jamais rien comme terminé.</li>\n<li>« Enregistrer la liste » écrit aussi la marque verte dans le fichier (colonne « fertig ») ;\n    après « Charger la liste », les lignes sont de nouveau vertes. Un déploiement peut ainsi se\n    poursuivre un autre jour.</li>\n</ul>\n\n<h2>Scanner mobile (mot de passe par caméra)</h2>\n<ul>\n<li>Dans « Matériel neuf », « Scanner mobile … » ouvre une fenêtre. Choisir l'adresse, <b>Démarrer</b>, scanner le code QR avec le téléphone (téléphone et PC sur le même réseau). Confirmer l'avertissement de certificat une fois et autoriser la caméra.</li>\n<li>Sur le téléphone, basculer entre <b>Code-barres</b> et <b>Texte (OCR)</b>, toucher <b>Capturer</b>, vérifier la valeur reconnue et l'envoyer. Elle arrive dans le champ « Mot de passe » de la ligne sélectionnée (sinon la première sans mot de passe) ; la sélection avance ensuite d'une ligne.</li>\n<li>Aucune photo n'est enregistrée sur le téléphone : la page n'affiche que l'image de la caméra, le PC lit le texte et supprime l'image aussitôt. Le lien est secret et valable seulement tant que le scanner tourne.</li>\n<li>Nécessaire sur le PC : <code>openssl</code>, <code>zbar</code> pour les codes-barres, <code>tesseract</code> pour le texte, <code>qrencode</code> pour le code QR. CachyOS : <code>sudo pacman -S zbar tesseract tesseract-data-eng qrencode</code>. Pare-feu actif : ouvrir le port (ufw : <code>sudo ufw allow 8443/tcp</code>).</li>\n<li>La reconnaissance de texte confond souvent 0/O et 1/l/I. Vérifier la valeur sur le téléphone avant l'envoi ; elle peut y être corrigée.</li>\n<li><b>Mode de connexion :</b> Dans la fenêtre, tu choisis comment le téléphone atteint le PC. <i>Réseau existant</i> : les deux sur le même Wi-Fi/LAN. <i>Partage USB</i> : brancher le téléphone par câble et y activer « Partage de connexion USB » ; l'adresse apparaît alors dans la liste (↻ relit). <i>Point d'accès Wi-Fi propre</i> (Linux avec NetworkManager uniquement) : le PC ouvre un Wi-Fi avec un nom et un mot de passe aléatoires, deux codes QR guident la connexion. L'adaptateur Wi-Fi est occupé et une connexion Wi-Fi existante du PC est coupée. À l'arrêt, le point d'accès et son mot de passe sont supprimés.</li>\n<li><b>Export du journal :</b> faire quelques prises de vue au téléphone, puis cliquer sur <b>Exporter le journal …</b> dans la fenêtre du scanner. Le ZIP contient <code>log.txt</code> (version, système, tesseract/zbar, informations du téléphone, chaque étape de reconnaissance). <b>Les mots de passe ne figurent jamais dans le journal</b>, seulement longueur et forme (A majuscule, a minuscule, 9 chiffre, # autre). Avec la case <i>Conserver les images intermédiaires</i>, les images de reconnaissance sont ajoutées au ZIP ; elles montrent le mot de passe lisiblement, à n'utiliser que pour le dépannage, puis supprimer le ZIP.</li>\n</ul>\n<h2>Profils</h2>\n<ul>\n<li>Un profil mémorise les réglages du formulaire : constructeur, utilisateur d'usine, nouvel\n    administrateur, emplacement utilisateur, valeurs par défaut et réglages DHCP. Les mots de passe et\n    la liste des serveurs n'en font jamais partie.</li>\n<li>Les profils sont dans <code>bmc-profile.json</code> à côté de l'outil ; au démarrage, le dernier\n    profil utilisé et la dernière langue choisie sont chargés.</li>\n</ul>\n\n<h2>Serveurs existants</h2>\n<p>Pour les BMC déjà configurés. Saisissez adresse, port (443 par défaut), utilisateur et mot de passe par ligne, puis <b>Tester la connexion</b> : l'outil vérifie en lecture seule le port, le chiffrement, le fabricant, la connexion, le modèle, le firmware et les droits d'administrateur. Chaque serveur n'a qu'une seule tentative de connexion pour qu'un mauvais mot de passe ne verrouille pas un compte. <b>Reprendre dans Matériel neuf, UEFI, Services</b> copie les serveurs réussis dans la liste, où vous réglez réseau, UEFI et services. « Matériel neuf », « Réglages UEFI » et « Services » ne connaissent qu'un utilisateur pour tous les serveurs ; les autres en deuxième passe. Cet onglet n'enregistre rien.</p>\n<h2>Réglages UEFI</h2>\n<ul>\n<li>Les attributs UEFI diffèrent selon le modèle. L'outil lit donc ceux qui existent réellement sur un\n    <b>appareil de référence</b> au lieu de les deviner. La lecture ne modifie rien.</li>\n<li><b>Modifier :</b> remplir « Nouvelle valeur » dans les lignes voulues ; la recherche filtre la\n    liste. Les champs vides restent inchangés. Les lignes grises (mots de passe, TPM, effacement) sont\n    verrouillées.</li>\n<li><b>Appliquer :</b> « Appliquer à tous les serveurs de la liste » utilise la liste de « Matériel neuf » ;\n    les lignes vertes sont jointes à leur nouvelle IP. Chaque appareil ne reçoit que les attributs\n    qu'il possède. Les valeurs déjà correctes sont ignorées.</li>\n<li><b>Redémarrage :</b> les valeurs UEFI ne prennent effet qu'après un redémarrage. Avec « Puis\n    redémarrage forcé », un serveur en marche est redémarré de force (comme avec Reset) et un serveur\n    éteint est allumé. À réserver aux serveurs sans système d'exploitation en cours d'exécution.</li>\n<li><b>Les modèles UEFI</b> mémorisent le constructeur et les champs « Nouvelle valeur » et sont\n    enregistrés à part dans <code>bmc-bios-profile.json</code>.</li>\n</ul>\n\n<h2>Services : NTP, syslog, LDAP, certificat</h2>\n<p>« Services » configure des services de gestion communs sur tous les serveurs de la liste. Seules les\nsections cochées sont appliquées ; les serveurs verts (terminés) sont joints à leur nouvelle adresse.\nLa connexion utilise l'utilisateur d'usine et l'administrateur d'usine reste en place. La simulation\nest activée par défaut et montre seulement ce qui serait envoyé ; les mots de passe et le contenu du\ncertificat n'apparaissent pas dans le journal.</p>\n<ul>\n<li><b>NTP :</b> un ou deux serveurs de temps. Le fuseau horaire n'est défini que sur Dell ; sur HPE,\n    Supermicro et Lenovo, le choisir dans l'interface.</li>\n<li><b>Syslog :</b> Dell et HPE uniquement. Il n'existe pas de voie uniforme sur Supermicro et Lenovo,\n    la section y est ignorée.</li>\n<li><b>LDAP / Active Directory :</b> serveur, base DN, un compte de bind facultatif, attribut\n    utilisateur et de groupe. Sous « Groupes → rôle », les groupes LDAP sont associés aux rôles\n    Administrator, Operator ou ReadOnly. Fonctionne sur les quatre constructeurs via la voie Redfish\n    standard.</li>\n<li><b>Durcissement :</b> désactive IPMI sur LAN, SSDP et sur demande SNMP, et règle le verrouillage de compte et le délai de session via Redfish standard. Ce qu'un BMC ne connaît pas est ignoré. Sans IPMI, la console texte (SOL) sur Supermicro ne fonctionne plus.</li>\n<li><b>Certificat HTTPS :</b> importe un certificat PEM signé par une CA. C'est expérimental et propre\n    au constructeur. Sur HPE, la clé correspondante doit provenir d'une CSR générée sur l'iLO, sinon\n    l'import échoue. Le BMC peut redémarrer pendant l'import.</li>\n</ul>\n<p>Pour chaque service, un constructeur qui ne le connaît pas est ignoré, rien n'est deviné. iLO 3/4\n(via RIBCL) ne sont pas gérés ici. Les procédures n'ont jusqu'ici été testées que contre des\néquipements simulés ; toujours configurer un seul appareil d'abord avant une grande série.</p>\n<h2>Console UEFI</h2>\n<p>Affiche l'UEFI/BIOS d'un serveur via le BMC. Trois voies : <b>Image</b> (console HTML5 intégrée du BMC, souris et clavier comme sur l'original, connexion automatique), <b>Texte</b> (console série via IPMI-SOL ou SSH, fonctionne sous Linux, nécessite <code>pyte</code>, <code>paramiko</code> et <code>pyghmi</code>) et <b>navigateur par défaut</b>. « Démarrer dans la configuration UEFI » prépare via Redfish le prochain démarrage dans la configuration et redémarre le serveur de force. Cet onglet n'enregistre rien. Les anciens BMC avec TLS 1.0/1.1 seulement ne fonctionnent qu'en console texte. En haut à gauche de la fenêtre, le <b>moniteur</b> affiche un bruit discret tant que rien n'est connecté ; toutes les huit secondes, le logo à la sphère s'y construit puis s'efface ; avec la coche, il brille brièvement en vert. Une fois connecté, la console y apparaît en miniature ; un clic l'ouvre en taille réelle. Le commutateur sous le moniteur choisit le mode (HTML5, Texte ou Browser). Tant que la vue web est masquée, le moniteur garde la dernière image figée. Le bouton <b>Plein écran</b> fait remplir tout l'écran par la console ; la console texte agrandit sa police en conséquence. On quitte avec « Quitter le plein écran » dans la barre du haut, car Échap et F11 appartiennent au serveur.</p>\n<h2>Laboratoire de test</h2>\n<p>Serveurs virtuels Dell/HPE/Lenovo/Supermicro pour s'entraîner sans matériel. Chaque ligne du cluster est un serveur avec sa propre génération de firmware, son identifiant d'usine, son modèle/numéro de série et, pour Dell, éventuellement un slot utilisateur occupé. <b>Enregistrer/charger un scénario</b> restaure le même montage (même numéro de série = même MAC = même adresse DHCP) ; des exemples se trouvent dans <code>tools/vbmc/szenarien</code>.</p>\n<ul>\n<li><b>Loopback :</b> sans root, uniquement ce PC. Adresses sous la forme <code>IP:PORT</code> (127.x:8443).</li>\n<li><b>Salle de configuration :</b> un switch de staging virtuel <code>bmclab0</code> auquel ce PC est relié avec une adresse fixe. Les serveurs obtiennent leur adresse via l'onglet DHCP (carte bmclab0) ou ont une IP d'usine fixe, p. ex. Dell 192.168.0.120 - deux d'entre eux entrent en conflit comme en vrai. « Débrancher/brancher le câble » les connecte l'un après l'autre.</li>\n<li><b>Domicile/routeur :</b> les serveurs sont reliés à la carte réseau filaire, obtiennent leur adresse du routeur et y apparaissent comme appareils à part entière ; le passage à une adresse fixe du LAN fonctionne réellement. Le Wi-Fi ne fonctionne pas.</li>\n</ul>\n<p>Salle de configuration et routeur nécessitent une fois les droits root au démarrage (pkexec) ; à l'arrêt, les adresses sont libérées et tous les périphériques réseau du laboratoire supprimés. Les serveurs y répondent sur 443/22/623, donc sans <code>:port</code>.</p>\n<h2>Comparaison des constructeurs</h2>\n<table border=\"1\" cellspacing=\"0\" cellpadding=\"6\">\n<tr><th></th><th>Dell iDRAC</th><th>HPE iLO</th><th>Supermicro</th><th>Lenovo XCC</th></tr>\n<tr><td><b>Utilisateur d'usine</b></td><td>root</td><td>Administrator</td><td>ADMIN</td><td>USERID</td></tr>\n<tr><td><b>Mot de passe d'usine</b></td><td>calvin ou individuel (fiche Service Tag)</td><td>individuel,\n    étiquette sur l'appareil</td><td>individuel sur l'autocollant, anciennes cartes ADMIN</td>\n    <td>individuel, étiquette sur l'appareil</td></tr>\n<tr><td><b>Nouvel utilisateur</b></td><td>dans l'emplacement, seulement s'il est libre</td><td>créé</td>\n    <td>créé</td><td>créé</td></tr>\n<tr><td><b>Changement d'adresse</b></td><td>immédiat</td><td>après redémarrage de l'iLO (lancé par\n    l'outil)</td><td>immédiat</td><td>immédiat</td></tr>\n<tr><td><b>Délai d'accès</b></td><td>secondes</td><td>1 à 3 minutes</td><td>secondes</td>\n    <td>secondes</td></tr>\n</table>\n<p>Si « Utilisateur » reste vide dans la connexion actuelle, l'utilisateur d'usine du constructeur est\nutilisé ; les listes mixtes fonctionnent donc. « Détecter automatiquement » identifie le constructeur de\nchaque appareil ; un choix fixe vaut pour toutes les lignes.</p>\n\n<h2>Dell iDRAC</h2>\n<ul>\n<li><b>Emplacement utilisateur :</b> 3 par défaut (le 2 est root). S'il est occupé par un autre\n    utilisateur, l'outil s'arrête pour cet appareil et ne modifie rien.</li>\n<li><b>Nom d'hôte :</b> c'est le nom DNS de l'iDRAC qui est défini, pas celui du système.</li>\n<li><b>À la livraison :</b> DHCP ou adresse fixe 192.168.0.120 selon la commande. Avec une adresse fixe,\n    l'iDRAC n'apparaît pas dans la liste DHCP ; plusieurs appareils de ce type sur le même switch\n    entrent en conflit, les brancher un par un.</li>\n<li><b>Générations :</b> iDRAC9 (14e à 16e génération) et iDRAC7/8 (12e et 13e génération). iDRAC7/8\n    exigent au moins le firmware 2.50.50.50 ; l'outil détecte un firmware plus ancien, le signale et ne\n    modifie rien. Tester d'abord iDRAC10 sur un seul appareil. iDRAC6 n'est pas pris en charge.</li>\n</ul>\n\n<h2>HPE iLO</h2>\n<ul>\n<li><b>Mot de passe par appareil :</b> sur l'étiquette ; le saisir ligne par ligne dans « Mot de\n    passe ».</li>\n<li><b>Identification :</b> dans la liste DHCP, un iLO s'annonce en général « ILO » plus numéro de\n    série.</li>\n<li><b>Redémarrage :</b> nom d'hôte et adresse ne sont actifs qu'après le redémarrage de l'iLO ; le\n    serveur lui-même continue de tourner.</li>\n<li><b>Port :</b> c'est le port iLO dédié qui est configuré. Les nouveaux mots de passe exigent au moins\n    8 caractères.</li>\n<li><b>Générations :</b> iLO 5 et 6 (Gen10 à Gen11) via Redfish. iLO 7 n'est pas testé. Pour iLO 3 et 4,\n    voir la section suivante.</li>\n</ul>\n\n<h2>HPE iLO 3 et iLO 4 (serveurs plus anciens)</h2>\n<ul>\n<li><b>Appareils :</b> iLO 3 équipe les serveurs G7, iLO 4 les Gen8 et Gen9. L'outil les reconnaît de\n    lui-même ; si le constructeur est choisi à la main, prendre « HPE iLO 3/4 ».</li>\n<li><b>Autre voie :</b> iLO 3 ne connaît pas Redfish. Pour iLO 3 et iLO 4, l'outil utilise donc\n    l'interface XML RIBCL, présente d'origine sur les deux. En simulation, les commandes XML figurent\n    dans le journal, les mots de passe y sont remplacés par ***.</li>\n<li><b>Redémarrage :</b> une fois l'adresse et le nom d'hôte définis, l'iLO redémarre de lui-même ; le\n    serveur continue de tourner. Il faut 1 à 3 minutes avant qu'il réponde à la nouvelle adresse.</li>\n<li><b>Chiffrement ancien :</b> iLO 3 ne gère que TLS 1.0/1.1. L'outil bascule de lui-même, appareil par\n    appareil, sur l'ancien chiffrement et l'indique dans le journal. Ces appareils ont leur place dans\n    un réseau d'administration isolé.</li>\n<li><b>Limites :</b> nom d'hôte de 49 caractères au plus. Les réglages UEFI (« Réglages UEFI ») ne fonctionnent pas\n    avec iLO 3. iLO 2 et versions antérieures ne sont pas pris en charge.</li>\n</ul>\n\n<h2>Supermicro</h2>\n<ul>\n<li><b>Mot de passe par appareil :</b> sur l'autocollant ; le saisir ligne par ligne.</li>\n<li><b>Types d'accès :</b> l'outil accorde aussi web, IPMI et console. Si le journal indique « créé sans\n    types d'accès », vérifier une fois la connexion web.</li>\n<li><b>Licence :</b> sur les cartes plus anciennes, certaines fonctions Redfish peuvent exiger une\n    licence.</li>\n<li><b>Nutanix NX</b> est reconnu comme Supermicro.</li>\n</ul>\n\n<h2>Lenovo XCC</h2>\n<ul>\n<li><b>Mot de passe par appareil :</b> sur l'étiquette ; le saisir ligne par ligne.</li>\n<li><b>Utilisateur d'usine :</b> USERID en majuscules.</li>\n<li><b>Port :</b> c'est l'interface « NIC », le port XCC dédié, qui est configurée.</li>\n<li><b>Générations :</b> XCC2/XCC3. Tester d'abord XCC1 sur un seul appareil.</li>\n</ul>\n\n<h2>Nœuds Nutanix (Dell XC, HPE DX, NX)</h2>\n<ul>\n<li>L'outil ne définit que l'adresse et l'utilisateur du BMC, ni l'hyperviseur ni la CVM.</li>\n<li>Ne faire ce changement qu'<b>avant</b> la création du cluster. Sur un cluster en service, utiliser la\n    procédure Nutanix, car le cluster utilise lui-même les données du BMC.</li>\n</ul>\n\n<h2>En cas de problème</h2>\n<ul>\n<li><b>DHCP ne démarre pas :</b> le message indique la raison. Le plus souvent, la carte a une\n    passerelle ou a obtenu son adresse par DHCP. Sous Linux par exemple :\n    <code>nmcli con add type ethernet ifname &lt;carte&gt; con-name staging ipv4.method manual\n    ipv4.addresses 192.168.0.1/24</code></li>\n<li><b>Aucun appareil dans la liste DHCP :</b> pare-feu (Linux avec ufw : autoriser UDP 67) ou le BMC n'est pas en DHCP.</li>\n<li><b>HTTP 401 :</b> utilisateur ou mot de passe erroné. Vider « Utilisateur », vérifier le mot de\n    passe de la ligne.</li>\n<li><b>Constructeur non reconnu :</b> l'appareil ne répond ni en Redfish ni comme iLO 3/4. iDRAC6, iLO 2\n    et les firmwares très anciens ne sont pas pris en charge. Un iLO 3/4 dont les informations\n    d'appareil ont été désactivées reste accessible en choisissant « HPE iLO 3/4 » à la main.</li>\n<li><b>Utilisateur refusé :</b> le plus souvent les règles de mot de passe. Le message du BMC figure\n    sous la ligne d'erreur.</li>\n<li><b>« la nouvelle IP est déjà prise » :</b> un appareil répond déjà à cette adresse. En choisir une\n    autre.</li>\n<li><b>Liste issue d'Excel ou d'un autre outil :</b> sans colonne « fertig », toutes les lignes sont\n    ouvertes. Les appareils déjà reconfigurés ne sont plus joignables à leur ancienne adresse ; retirer\n    ou corriger ces lignes.</li>\n</ul>\n\n<h2>Données et limites</h2>\n<ul>\n<li>L'outil n'enregistre rien de lui-même. « Enregistrer la liste » demande si les mots de passe doivent\n    figurer dans le fichier (alors en clair). Supprimer un tel fichier après le déploiement.</li>\n<li>Les procédures HPE (iLO 3/4 compris), iDRAC7/8 anciens, Supermicro, Lenovo et UEFI n'ont été testés jusqu'ici que contre des équipements simulés. Toujours configurer d'abord un seul appareil avant\n    une grande série.</li>\n<li>Le <b>journal</b> du bas est le même pour tout le programme, labo de test compris. Il se remplit dès le\n    démarrage et note chaque action. « Journal » l'ouvre comme fichier texte dans l'éditeur par défaut ; « Info »\n    ou « Debug » règle le niveau de détail. Il ne contient aucun mot de passe.</li>\n<li>La langue se change en bas à gauche, sous le menu, tant qu'aucune exécution n'est en cours et que le serveur DHCP\n    est arrêté.</li>\n</ul>\n\n",
"gsw": "<h2>BMC-Steward</h2>\n<p>Grüezi! De BMC-Steward bedient jede Server schön de Reihe naa, und das ganz ohni Trinkgäld: Er leit de neu Administrator aa, setzt de Hostname\nund stellt de BMC uf e feschti Adrässe um. Mer mues nöd alli Server ufs Mal iiträge, sie chönd eis\nnach em andere dezuechoo.</p>\n\n<h2>Schutz gäge Fählbedienig</h2>\n<p>S Tool isch so baut, dass en Fählgriff möglichscht nüt kaputt macht:</p>\n<ul>\n<li><b>De Trocheluuf isch voriigstellt.</b> Solang s Häägli gsetzt isch, wird nüt gänderet.</li>\n<li><b>Jede scharfe Luuf wird vorher zeigt</b> (alti IP → neui IP, Hostname). I allne Abfrage isch\n    „Abbräche“ voruusgwählt, Enter bricht also ab.</li>\n<li><b>Bi ungwöhnliche Fäll mues mer d Aazahl Server iitippe</b>, z. B. wenn es Grät nöd im eigete\n    Staging-Netz liit, und immer bi UEFI-Änderige.</li>\n<li><b>D Adrässe wärded prüeft:</b> doppleti IPs oder Hostnäme, es Gateway usserhalb vom Netz, e\n    Adrässe vo dem Rächner, e Adrässe us em laufende DHCP-Beriich und di aktuell IP vo eme andere Grät\n    wärded abglehnt. Vor em Umstelle prüeft s Tool zuesätzlich, öb under de neue IP scho es Grät\n    antwortet, und laht s Grät dänn wie's isch.</li>\n<li><b>Dell:</b> En bsetzte User-Slot wird nie überschribe.</li>\n<li><b>S neu Passwort wird zweimal iitippt.</b> Stimmed d Iigabe nöd überii, startet kein Luuf.</li>\n<li><b>De Adrässwächsel wird prüeft:</b> Riisst bim Umstelle nur d Verbindig ab, gilt es Grät erscht als\n    fertig, wenn's under de alte Adrässe würkli nüme antwortet.</li>\n<li><b>DHCP-Server:</b> startet nöd uf ere Charte mit öffentlicher Adrässe, mit de Standardroute\n    (Internet/Firmenetz), mit ere per DHCP übercho Adrässe oder wenn det scho en DHCP-Server antwortet.\n    Er stoppt vo sälber, wenn d Charte spöter zur Standardroute wird. Grät, wo ihri Adrässe vo eme\n    andere Server händ, wärded nie abgwise. En DHCP-Server, wo nach eme Absturz no lauft, wird bim\n    nächschte Start beändet.</li>\n<li><b>UEFI:</b> Passwort-, TPM- und Lösch-Attribut sind gsperrt. Wärt, wo nöd zum Typ passed, wärded\n    nöd gsetzt. En Neustart isch nöd voruusgwählt.</li>\n<li><b>Passwörter</b> bliibed im Arbeitsspeicher und landed nur uf usdrückliche Wunsch i ere\n    Datei.</li>\n<li><b>Eigeschutz:</b> UEFI-Änderige, Firmware und „Is UEFI-Setup boote“ prüefed vorher d Identität vom Server. Dä Rächner, wo BMC-Steward druf lauft, und alli Server vo de Schutzlischte (Chnopf „Eigeschutz“ links une) wärded nie aagrüehrt. Lauft BMC-Steward innere VM (z. B. VMware), det alli Hosts iitrage, wo d VM druf laufe chan – d VM kennt ihre Host nöd. Isch d Identität im schaarfe Lauf nöd läsbar, wird dä Server übersprunge.</li>\n<li><b>Zertifikat:</b> Bim erschte Kontakt merkt sich s Tool s Zertifikat vo jedem BMC (je Grät, nöd je Adrässe). Zeigt s gliiche Grät spöter es anders, wird es gsperrt, bevor es Passwort gschickt wird – Schutz gäge Mitläse im Netz. Gwollti Wächsel under „Zertifikat …“ links une freigäh.</li>\n<li><b>Aamäldig per Session:</b> S Passwort gaht je Grät eimal über d Leitig; am Schluss mäldet sich s Tool ab. E abglehnti Aamäldig wird nie widerholt.</li>\n<li><b>Sicherig und Revisionsprotokoll:</b> Vor jeder Änderig wärded di wichtigschte Iistellige vom Grät als JSON gsicheret, jede ändernde Ufruef vomene schaarfe Lauf wird protokolliert (ohni Passwörter). Beides under „Sicherige/Protokoll“.</li>\n<li><b>Firmware:</b> D Datei wird eimal gläse und per SHA-256 prüeft (Sollwärt vom Herställer iitrage); „Bi Fehler aahalte“ isch voriigstellt.</li>\n<li><b>Werkskonto:</b> Nach erfolgriicher Kontrolle mäldet sich s Tool mit em neue Administrator under de neue Adrässe aa und sperrt s Werkskonto (Haken „Werkskonto dänn sperre“, voriigstellt). Chan en BMC Konte nöd abschalte (z. B. iLO 3/4), überchunnt es es nienet gspicherets Zuefallspasswort.</li>\n</ul>\n\n<h2>Ablauf</h2>\n<ol>\n<li><b>DHCP-Server</b>: D Netzwerkcharte am trännte Staging-Switch bruucht e feschti Adrässe\n    ohni Gateway. DHCP starte, d Grät holed sich e Adrässe und tauched i de Lischte uf, vo det „I d\n    Serverlischte übernäh“. Mit em Häägli „Automatisch übernäh“ (voriigstellt) träit s Tool\n    neui Grät vo sälber i d Serverlischte ii; ohni Häägli gaht s nume per Chnopf. Under Linux lauft defür dnsmasq.</li>\n<li><b>Neuware</b>: neui IP, Subnetzmaske, Gateway und Hostname iiträge. Was no fählt, isch\n    <b>gäl</b> markiert, Fählerhafts <b>rot</b>, Fertigs <b>grüen</b>. Über em Start-Chnopf staht i einere\n    Ziile, was vor em Luuf no uuszfülle isch; de Grund zu jedem Fäld staht im Tooltip.</li>\n<li><b>Trocheluuf</b>: zeiget im Protokoll, was gschickt würd.</li>\n<li><b>Scharfe Luuf</b>: Häägli bi „Trocheluuf“ ewägnäh. De Start-Chnopf wird rot und heisst dänn\n    „Scharf starte“. Klicke und d Abfrag bestätige.</li>\n<li><b>Kontrolle</b>: Am Schluss prüeft s Tool, öb jedi neui Adrässe mit em neue Benutzer antwortet. Das\n    gaht nur, wenn dä Rächner s neu Netz erreicht; susch staht det „FÄHLT“, obwohl d Umstellig\n    cha klappt ha.</li>\n</ol>\n\n<h2>Server eis nach em andere konfiguriere</h2>\n<ul>\n<li>Mer cha en Server konfiguriere, Start drücke, de nächscht aaschliesse und wider Start drücke, so\n    oft wie mer will. De DHCP-Server lauft debii wiiter.</li>\n<li>Erfolgriich umgstellti Ziile wärded <b>grüen</b> markiert, bliibed i de Lischte stah und wärded bim\n    nächschte Start überspronge. Fählgschlageni wärded nomal probiert.</li>\n<li>Söll es grüens Grät nomal bearbeitet wärde: Ziile markiere, „Nomal bearbeite“ und d „Aktuelli IP“\n    uf sini jetzigi Adrässe setze.</li>\n<li>De Trocheluuf markiert nie öppis als fertig.</li>\n<li>„Lischte speichere“ schriibt d grüen Markierig mit i d Datei (Spalte „fertig“), nach „Lischte\n    lade“ sind d Ziile wider grüen. So cha mer en Rollout au amene andere Tag wiitermache.</li>\n</ul>\n\n<h2>Handy-Scanner (Passwort per Kamera)</h2>\n<ul>\n<li>In „Neuware“ öffnet „Handy-Scanner …“ es Fänschter. Adrässe wähle, <b>Starte</b>, mit em Handy de QR-Code scanne (Handy und PC im gliiche Netz). D Zertifikatswarnig eimal bestätige und d Kamera erlaube.</li>\n<li>Am Handy zwüsche <b>Barcode</b> und <b>Text (OCR)</b> umschalte, <b>Ufnäh</b> tippe, de erchännt Wärt prüefe und übernäh. Er landet im Fäld „Passwort“ vo de markierte Ziile (susch vo de erschte ohni Passwort), dänn rückt d Markierig e Ziile wiiter.</li>\n<li>S wird keis Foti uf em Handy gspeicheret: D Siite zeigt nume s Kamerabild, de PC liest de Text und verwirft s Bild sofort. De Link isch gheim und gilt nume, solang de Scanner lauft.</li>\n<li>Am PC nötig: <code>openssl</code>, für Barcodes <code>zbar</code>, für Text <code>tesseract</code>, für de QR-Code <code>qrencode</code>. CachyOS: <code>sudo pacman -S zbar tesseract tesseract-data-eng qrencode</code>. Bi aktiver Firewall de Port freigäh (ufw: <code>sudo ufw allow 8443/tcp</code>).</li>\n<li>D Texterkennig verwächslet gärn 0/O und 1/l/I. De Wärt am Handy vor em Übernäh kontrolliere; er cha det korrigiert wärde.</li>\n<li><b>Verbindigswäg:</b> Im Fänschter wählsch, wie s Handy de PC erreicht. <i>Vorhandes Netz</i>: beidi im gliiche WLAN/LAN. <i>USB-Tethering</i>: Handy per Chabel aastecke und det „USB-Tethering“ iischalte, dänn erschiint d Adrässe i de Lischte (↻ liest neu ii). <i>Eigete WLAN-Hotspot</i> (nur Linux mit NetworkManager): De PC macht es WLAN mit zuefällige Name und Passwort uuf, zwei QR-Code füehred dur d Verbindig. De WLAN-Adapter isch debii bsetzt, e bestehendi WLAN-Verbindig vom PC wird trännt. Bim Beände wird de Hotspot samt Passwort wider entfernt.</li>\n<li><b>Log-Export:</b> Am Handy es paar Ufnahme mache, denn im Scanner-Fänschter <b>Log exportiere …</b>. D ZIP-Datei het <code>log.txt</code> (Version, System, Tesseract/zbar, Handy-Aagabe, jede Erkennigsschritt). <b>Passwörter staht nie im Log</b>, nume Läng und Form (A gross, a chli, 9 Ziffer, # Rest). Mit em Haag <i>Zwüschebilder mitspeichere</i> chömed zuesätzlich d Bilder i d ZIP; die zeiged s Passwort läsbar, drum nume zur Fehlersuech bruuche und d ZIP nachhär lösche.</li>\n</ul>\n<h2>Profil</h2>\n<ul>\n<li>Es Profil merkt sich d Iistellige vo de Maske: Hersteller, Werksbenutzer, neue Administrator,\n    User-Slot, Vorgabe für neui Ziile und d DHCP-Iistellige. Passwörter und d Serverlischte ghöred nie\n    dezue.</li>\n<li>D Profil ligged i <code>bmc-profile.json</code> näbem Tool; bim Start wärded s zletscht bruuchte\n    Profil und di zletscht gwählti Sproch glade.</li>\n</ul>\n\n<h2>Bestehendi Server</h2>\n<p>Für BMCs, wo scho konfiguriert sind. Pro Zile Adrässe, Port (Standard 443), Benutzer und Passwort iitrage, dänn <b>Verbindig teste</b>: S Tool prüeft nume läsend Port, Verschlüsselig, Hersteller, Aamäldig, Modell, Firmware und Administratorrächt. Jede Server überchunnt gnau en Aamäldversuech, dass es falsches Passwort kei Konto sperrt. <b>Übernäh i Neuware, UEFI, Dienscht</b> kopiert d erfolgriichä Server i d Serverlischte; det stellsch Netzwärk, UEFI und Dienscht i. D „Neuware“, „UEFI-Iistellige“ und „Dienscht“ kännd eimal Benutzer für alli Server, anderi i emene zweite Durchgang. De Reiter speicheret nüt.</p>\n<h2>UEFI-Iistellige</h2>\n<ul>\n<li>UEFI-Attribut sind je nach Modäll andersch. S Tool liest drum die, wo's würkli git, vo eme\n    <b>Referänzgrät</b>, statt sie z errate. Läse änderet nüt.</li>\n<li><b>Ändere:</b> bi de gwünschte Ziile „Neue Wärt“ uusfülle, d Suechi grenzt d Lischte ii. Lääri\n    Fälder bliibed wie sie sind. Graui Ziile (Passwörter, TPM, Löschfunktione) sind gsperrt.</li>\n<li><b>Aawände:</b> „Uf allne Server vo de Lischte setze“ nimmt d Serverlischte us „Neuware“; grüeni\n    Ziile wärded under ihrer neue IP aagsproche. Jedes Grät überchunnt nur Attribut, wo's au hät.\n    Wärt, wo scho stimmed, wärded überspronge.</li>\n<li><b>Neustart:</b> UEFI-Wärt würked erscht nach eme Neustart. Mit em Häägli „Dänn hert neu starte“\n    wird en laufende Server hert neu gstartet (wie mit de Reset-Taschte), en abgstellte iigschaltet.\n    Nur bi Server ohni laufends Betriebssystem bruuche.</li>\n<li><b>UEFI-Vorlage</b> merked sich Hersteller und „Neue Wärt“-Fälder und ligged trännt i\n    <code>bmc-bios-profile.json</code>.</li>\n</ul>\n\n<h2>Dienscht: NTP, Syslog, LDAP, Zertifikat</h2>\n<p>„Dienscht“ richtet uf allne Server vo de Lischte gmeinsami Verwaltigsdienscht ii. Nur aaghaaggti\nAbschnitt wärded aagwändet; grüeni (fertigi) Server wärded under ihrer neue Adrässe aagsproche.\nAagmäldet wird mit em Wärksbenutzer, de Wärks-Administrator bliibt bestah. De Trocheluuf isch\nvoriigstellt und zeigt nur, was gschickt wür; Passwörter und de Zertifikatsinhalt stönd debii nöd im\nProtokoll.</p>\n<ul>\n<li><b>NTP:</b> ein bis zwee Ziitserver. D Ziitzone wird nur bi Dell gsetzt; bi HPE, Supermicro und\n    Lenovo bitte i de Oberflächi wähle.</li>\n<li><b>Syslog:</b> nur Dell und HPE. Bi Supermicro und Lenovo gits kei einheitliche Wäg, de Abschnitt\n    wird dört übersprunge.</li>\n<li><b>LDAP / Active Directory:</b> Server, Base-DN, optional es Bind-Konto, Benutzer- und\n    Gruppe-Attribut. Under „Gruppe → Rolle“ wärded LDAP-Gruppe de Rolle Administrator, Operator oder\n    ReadOnly zuegordnet. Gaht bi allne vier Hersteller über de Standard-Redfish-Wäg.</li>\n<li><b>Härtig:</b> schaltet IPMI über LAN, SSDP und uf Wunsch SNMP ab und setzt Kontosperri und Session-Zitlimit über Standard-Redfish. Was en BMC nöd kennt, wird übersprunge. Ohni IPMI gaht d Textkonsole (SOL) bi Supermicro nümm.</li>\n<li><b>HTTPS-Zertifikat:</b> importiert es vonere CA signiets PEM-Zertifikat. Das isch experimentell und\n    je Hersteller andersch. Bi HPE mues de passend Schlüssel us ere uf em iLO erzüügte CSR cho, susch\n    schiitert de Import. Bim Import cha de BMC neu starte.</li>\n</ul>\n<p>Für jede Dienscht gilt: En Hersteller, wo ne nöd kännt, wird übersprunge, nöd grate. iLO 3/4 (über\nRIBCL) wärded da nöd bedient. D Abläuf sind bis jetz nur gäge simulierti Gägestelle prüeft; vor eme\ngrosse Luuf immer zerscht eis einzelns Grät.</p>\n<h2>UEFI-Konsole</h2>\n<p>Zeigt s UEFI/BIOS vomene Server über de BMC aa. Drü Wääg: <b>Bild</b> (iibettet HTML5-Konsole vom BMC, Muus und Tastatur wie am Original, Aamäldig automatisch), <b>Text</b> (serielli Konsole über IPMI-SOL oder SSH, lauft under Linux, bruucht <code>pyte</code>, <code>paramiko</code> und <code>pyghmi</code>) und <b>Standardbrowser</b>. „Is UEFI-Setup boote“ merkt per Redfish de Start is Setup vor und startet de Server hert neu. De Reiter speicheret nüt. Alti BMCs mit nume TLS 1.0/1.1 gönd nume über d Textkonsole. Obe links im Fenschter zeigt de <b>Monitor</b> dezents Rusche, solang nüt verbunde isch; drin boued sich alli acht Sekunde s Logo mit de Kugle uf und wider ab; zum Häägli lüüchtets churz grüen. Mit Verbindig erschiint det d Konsole als Miniatur, mit emene Klick druf gaht si i voller Grössi uf. De Schalter under em Monitor wählt de Wäg (HTML5, Text oder Browser). Isch d Web-Aasicht grad verdeckt, blibt im Monitor s letschte Bild als Standbild stah. De Chnopf <b>Vollbild</b> lat d Konsole de ganz Bildschirm fülle; d Textkonsole macht derfür d Schrift grösser. Beände tuet mer mit „Vollbild beände“ i de Leischte obe, wil Esc und F11 em Server ghöred.</p>\n<h2>Testlabor</h2>\n<p>Virtuelli Dell/HPE/Lenovo/Supermicro-Server zum Üebe ohni Hardware. Jedi Ziile vom Cluster isch en Server mit eigener Firmware-Generation, eigenem Wärks-Login, Modell/Seriennummere und bi Dell optional emne bsetzte User-Slot. <b>Szenario spichere/lade</b> stellt dä gliich Ufbau wieder her (gliichi Seriennummere = gliichi MAC = gliichi DHCP-Adrässe); Biispil liged i <code>tools/vbmc/szenarien</code>.</p>\n<ul>\n<li><b>Loopback:</b> ohni Root, nume dä PC. Adrässe als <code>IP:PORT</code> (127.x:8443).</li>\n<li><b>Konfigruum:</b> en virtuelle Staging-Switch <code>bmclab0</code>, wo dä PC mit fixer Adrässe dra hängt. D Server holed sich d Adrässe über de DHCP-Reiter (Charte bmclab0) oder händ e fixi Wärks-IP, z. B. Dell 192.168.0.120 - zwei devo störed sich wie in echt. „Kabel zieh/stecke“ schliesst si eis nach em andere a.</li>\n<li><b>Dihei/Router:</b> d Server hänged a de Kabel-Netzwerkcharte, überchömed d Adrässe vom Router und erschiined det als eigeni Grät; en Umzug uf e fixi Adrässe im LAN lauft würkli dure. WLAN gaht nöd.</li>\n</ul>\n<p>Konfigruum und Router bruuched bim Start eimal Root (pkexec); bim Stoppe wärded d Adrässe freigä und alli Labor-Netzwerkgrät wieder entfernt. D Server antwortet det uf 443/22/623, also ohni <code>:Port</code>.</p>\n<h2>Hersteller im Vergliich</h2>\n<table border=\"1\" cellspacing=\"0\" cellpadding=\"6\">\n<tr><th></th><th>Dell iDRAC</th><th>HPE iLO</th><th>Supermicro</th><th>Lenovo XCC</th></tr>\n<tr><td><b>Werksbenutzer</b></td><td>root</td><td>Administrator</td><td>ADMIN</td><td>USERID</td></tr>\n<tr><td><b>Werkspasswort</b></td><td>calvin oder individuell (Service-Tag-Uuszug)</td><td>individuell,\n    Aahänger am Grät</td><td>individuell uf em Chläber, alti Boards ADMIN</td><td>individuell,\n    Aahänger am Grät</td></tr>\n<tr><td><b>Neue Benutzer</b></td><td>in User-Slot, nur wenn frei</td><td>neu aagleit</td>\n    <td>neu aagleit</td><td>neu aagleit</td></tr>\n<tr><td><b>Adrässwächsel</b></td><td>sofort</td><td>nach em iLO-Neustart (löst s Tool uus)</td>\n    <td>sofort</td><td>sofort</td></tr>\n<tr><td><b>Bis erreichbar</b></td><td>Sekunde</td><td>1 bis 3 Minute</td><td>Sekunde</td>\n    <td>Sekunde</td></tr>\n</table>\n<p>Bliibt „Benutzer“ bim aktuelle Login läär, wird je nach Hersteller de Werksbenutzer gnoh; so gönd au\ngmischti Lischte. „Automatisch erchänne“ findet de Hersteller pro Grät use, e feschti Wahl gilt für alli\nZiile.</p>\n\n<h2>Dell iDRAC</h2>\n<ul>\n<li><b>User-Slot:</b> Standard 3 (Slot 2 isch root). Isch de Slot vo eme andere Benutzer bsetzt, bricht\n    s Tool für das Grät ab und änderet nüt.</li>\n<li><b>Hostname:</b> gsetzt wird de DNS-Name vom iDRAC, nöd dä vom Betriebssystem.</li>\n<li><b>Uuslieferig:</b> je nach Bstellig DHCP oder feschti Adrässe 192.168.0.120. Mit feschter Adrässe\n    chunnt de iDRAC nöd i d DHCP-Lischte; mehreri settigi Grät am gliiche Switch chömed sich i d Quere,\n    dänn eis nach em andere aaschliesse.</li>\n<li><b>Generatione:</b> iDRAC9 (14. bis 16. Generation) und iDRAC7/8 (12. und 13. Generation). iDRAC7/8\n    bruuched mindeschtens Firmware 2.50.50.50; älteri erchännt s Tool, mäldet's und änderet nüt.\n    iDRAC10 zerscht a eim Grät teste. iDRAC6 wird nöd understützt.</li>\n</ul>\n\n<h2>HPE iLO</h2>\n<ul>\n<li><b>Passwort pro Grät:</b> staht uf em Aahänger, pro Ziile i d Spalte „Passwort“ iiträge.</li>\n<li><b>Zueordnig:</b> i de DHCP-Lischte mäldet sich en iLO meischtens als „ILO“ plus Serienummere.</li>\n<li><b>Neustart:</b> Hostname und Adrässe wärded erscht nach em iLO-Neustart aktiv; de Server sälber\n    lauft wiiter.</li>\n<li><b>Port:</b> konfiguriert wird de dediziert iLO-Port. Neui Passwörter bruuched mindeschtens 8\n    Zeiche.</li>\n<li><b>Generatione:</b> iLO 5 und 6 (Gen10 bis Gen11) über Redfish. iLO 7 isch nöd prüeft. Für iLO 3 und\n    4 lueg im nächschte Abschnitt.</li>\n</ul>\n\n<h2>HPE iLO 3 und iLO 4 (di ältere Semeschter)</h2>\n<ul>\n<li><b>Grät:</b> iLO 3 steckt i G7-Server, iLO 4 i Gen8 und Gen9. S Tool erchännt si vo sälber; wär de\n    Hersteller fescht wählt, nimmt „HPE iLO 3/4“.</li>\n<li><b>En andere Wäg:</b> iLO 3 kännt kei Redfish. S Tool bruucht drum für iLO 3 und iLO 4 d\n    XML-Schnittstell RIBCL, wo beidi ab Wärk debii händ. Im Trocheluuf stönd d XML-Befähl im Protokoll,\n    Passwörter sind dört dur *** ersetzt.</li>\n<li><b>Neustart:</b> Nach em Setze vo Adrässe und Hostname startet s iLO vo sälber neu; de Server lauft\n    wiiter. Bis es under de neue Adrässe antwortet, gaht's 1 bis 3 Minute.</li>\n<li><b>Alti Verschlüsselig:</b> iLO 3 cha nume TLS 1.0/1.1. S Tool schaltet defür pro Grät vo sälber uf\n    di alt Verschlüsselig um und schriibt en Hiiwiis is Protokoll. Söttigi Grät ghöred in es\n    abgschottets Verwaltigsnetz.</li>\n<li><b>Gränze:</b> Hostname höchschtens 49 Zeiche. UEFI-Iistellige („UEFI-Iistellige“) gönd bi iLO 3 nöd. iLO 2\n    und älter wärded nöd understützt.</li>\n</ul>\n\n<h2>Supermicro</h2>\n<ul>\n<li><b>Passwort pro Grät:</b> staht uf em Chläber, pro Ziile iiträge.</li>\n<li><b>Zuegriffsarte:</b> s Tool git Web, IPMI und Konsole mit. Staht im Protokoll „ohni Zuegriffsarte\n    aagleit“, d Web-Aamäldig eimal prüefe.</li>\n<li><b>Lizänz:</b> bi ältere Boards chönd Redfish-Funktione e Lizänz bruuche.</li>\n<li><b>Nutanix NX</b> wird als Supermicro erchännt.</li>\n</ul>\n\n<h2>Lenovo XCC</h2>\n<ul>\n<li><b>Passwort pro Grät:</b> staht uf em Aahänger, pro Ziile iiträge.</li>\n<li><b>Werksbenutzer:</b> USERID i Grossbuechstabe.</li>\n<li><b>Port:</b> konfiguriert wird d Schnittstell „NIC“, de dediziert XCC-Port.</li>\n<li><b>Generatione:</b> XCC2/XCC3. XCC1 zerscht a eim Grät teste.</li>\n</ul>\n\n<h2>Nutanix-Nodes (Dell XC, HPE DX, NX)</h2>\n<ul>\n<li>S Tool setzt nur BMC-Adrässe und BMC-Benutzer, nöd Hypervisor oder CVM.</li>\n<li>Nur <b>vor</b> em Erstelle vom Cluster so umstelle. Bi eme laufende Cluster s Nutanix-Verfahre\n    bruuche, will de Cluster d BMC-Date sälber bruucht.</li>\n</ul>\n\n<h2>Wenn öppis nöd klappt</h2>\n<ul>\n<li><b>DHCP startet nöd:</b> D Mäldig seit de Grund. Meischtens hät d Charte es Gateway oder ihri\n    Adrässe per DHCP. Under Linux z. B.:\n    <code>nmcli con add type ethernet ifname &lt;charte&gt; con-name staging ipv4.method manual\n    ipv4.addresses 192.168.0.1/24</code></li>\n<li><b>Kei Grät i de DHCP-Lischte:</b> Firewall (Linux mit ufw: UDP 67 freigäh) oder de BMC staht nöd uf DHCP.</li>\n<li><b>HTTP 401:</b> Benutzer oder Passwort falsch. Fäld „Benutzer“ lääre, Passwort vo de Ziile\n    prüefe.</li>\n<li><b>Hersteller nöd erchännt:</b> S Grät antwortet weder per Redfish no als iLO 3/4. iDRAC6, iLO 2 und\n    uralti Firmware wärded nöd understützt. Es iLO 3/4, wo d Grätuuskunft abgschaltet isch, laat sich\n    über di feschti Wahl „HPE iLO 3/4“ glich aaspräche.</li>\n<li><b>Benutzer abglehnt:</b> meischtens Passwortregle. D Mäldig vom BMC staht under de\n    Fählerziile.</li>\n<li><b>„neui IP isch scho vergäh“:</b> Under dere Adrässe antwortet scho es Grät. E anderi wähle.</li>\n<li><b>Lischte us em Excel oder eme andere Tool:</b> Ohni Spalte „fertig“ sind alli Ziile offe. Scho\n    umgstellti Grät sind under de alte Adrässe nöd erreichbar, die Ziile ewägnäh oder aapasse.</li>\n</ul>\n\n<h2>Date und Gränze</h2>\n<ul>\n<li>S Tool speicheret nüt vo sälber. „Lischte speichere“ fröget, öb Passwörter mit i d Datei sölled\n    (dänn im Klartext). E söttigi Datei nach em Rollout lösche.</li>\n<li>D Abläuf für HPE (au iLO 3/4), älteri iDRAC7/8, Supermicro, Lenovo und UEFI sind bis jetzt nur gäge simulierti Gägestelle prüeft. Vor eme grosse Luuf immer zerscht eis einzelns Grät\n    konfiguriere.</li>\n<li>S <b>Protokoll</b> unde isch für s ganze Programm s gliiche, au im Testlabor. Es füllt sich scho bim Start\n    und haltet jedi Aktion fescht. „Protokoll“ öffnet es als Textdatei im Standard-Editor; „Info“ oder „Debug“\n    wählt d Gnauigkeit. Passwörter stönd keini dine.</li>\n<li>D Sproch cha mer links une under em Menü umstelle, solang kein Luuf aktiv isch und de DHCP-Server gstoppt\n    isch.</li>\n</ul>\n\n"
}
