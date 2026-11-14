# BMC-Steward

*Deutsche Fassung: [README.de.md](README.de.md)*

BMC-Steward is a small desktop tool for the first-time setup of server management controllers. It serves
one server after another: it creates a new administrator account, sets the hostname and switches the BMC
from its factory/DHCP address to a static one.

Supported: **Dell iDRAC**, **HPE iLO**, **Supermicro** and **Lenovo XCC** via Redfish. Older HPE servers
with **iLO 3 and iLO 4** are handled through the RIBCL XML interface, older Dell servers with
**iDRAC7/8** through Redfish (firmware 2.50.50.50 or newer).

![BMC-Steward screenshot](docs/screenshot.png)

<details>
<summary>Screenshots of all tabs</summary>

| | |
|---|---|
| **1 · DHCP server** | **2 · Configure servers** (above) |
| ![DHCP server](docs/screenshot-1-dhcp.png) | |
| **2b · Existing servers** (connection test) | **3 · UEFI settings** |
| ![Existing servers](docs/screenshot-2b-existing.png) | ![UEFI settings](docs/screenshot-3-uefi.png) |
| **4 · Services** | **5 · UEFI console** |
| ![Services](docs/screenshot-4-services.png) | ![UEFI console](docs/screenshot-5-console.png) |
| **6 · Help** | |
| ![Help](docs/screenshot-6-help.png) | |

</details>

## Features

- Modular layout (package `bmc_steward/`), runs on Linux and Windows
- Built-in DHCP server for a separate staging switch (dnsmasq on Linux, built-in on Windows)
- Server list with validation, CSV import/export, profiles for recurring settings
- Dry run that shows every call without changing anything
- Servers can be configured one by one; finished rows turn green and are skipped
- Optional UEFI/BIOS settings, read from a real reference device
- Optional management services across the list: NTP, syslog, LDAP/AD with group-to-role mapping, HTTPS certificate import
- Existing servers (tab 2b): enter address, port, user and password per server and run a read-only connection test (port, encryption, vendor, login, model, firmware, admin rights); successful servers are handed over to tabs 2–4
- UEFI console (tab 5): shows a server's UEFI/BIOS through the BMC, as embedded HTML5 console, text console (SOL/SSH) or in the default browser; untested on real hardware
- Interface in German, English, French and Swiss German

## Built to forgive mistakes

- Dry run is the default; a live run shows what will happen and "Cancel" is preselected
- Missing entries are highlighted yellow, wrong ones red, finished ones green
- Duplicate or already used IP addresses are rejected before anything is changed
- An occupied Dell user slot is never overwritten
- The DHCP server refuses to start on anything that looks like a production network
  (default route, DHCP-assigned address, public address, another DHCP server answering)
- UEFI password, TPM and erase attributes are locked
- Passwords stay in memory unless you explicitly save them

## Requirements

- Python 3.9 or newer
- PyQt6
- Linux only: `dnsmasq` and polkit (`pkexec`) for the DHCP server
- Optional, for the UEFI console: PyQt6-WebEngine (embedded console), `pyte` + `ipmitool` + `sshpass` (text console, Linux only)

## Install and run

**Arch Linux / CachyOS**

```bash
sudo pacman -S python-pyqt6 dnsmasq
python bmc-steward.py
```

**Debian / Ubuntu**

```bash
sudo apt install python3-pyqt6 dnsmasq
python3 bmc-steward.py
```

**Windows**

```powershell
pip install PyQt6
python bmc-steward.py
```

## How to use it

1. Connect your computer and the new servers' BMC ports to a **separate staging switch**. Give your
   network card a static address without a gateway, e.g. `192.168.0.1/24`.
2. Tab 1: start the DHCP server. The BMCs get an address and appear in the list. Add them to the server
   list.
3. Tab 2: enter the factory login, the new administrator and, per server, the new IP, subnet mask,
   gateway and hostname.
4. Start a dry run and read the log.
5. Untick "Dry run", start the live run and confirm.

The built-in help (tab 6) explains every step and the differences between the vendors.

## CSV format

```
# current_ip,password,new_ip,subnet_mask,gateway,hostname,done
192.168.0.101,,10.20.0.11,255.255.255.0,10.20.0.1,srv-idrac-01,
```

The password column may be left out. Semicolons work too, so an Excel export can be loaded directly.

## Test status

| Vendor | Designed for | Status |
|---|---|---|
| Dell | iDRAC9 (14th to 16th generation) | first target platform (R6625) |
| Dell | iDRAC7/8 (12th/13th generation), firmware 2.50.50.50+ | tested against simulated BMCs only |
| HPE | iLO 5 and 6 | tested against simulated BMCs only |
| HPE | iLO 3 (G7) and iLO 4 (Gen8/Gen9), via RIBCL | tested against simulated BMCs only |
| Supermicro | X12/X13 | tested against simulated BMCs only |
| Lenovo | XCC2/XCC3 | tested against simulated BMCs only |

The UEFI settings, the management services (tab 4) and the Windows DHCP server have also only been
tested against simulations. Service coverage: NTP on all four vendors; syslog on Dell and HPE only
(skipped on Supermicro/Lenovo); LDAP with group mapping on all four; HTTPS certificate import is
experimental and vendor-specific (on HPE the key must come from a CSR generated on the iLO). Services
are not available for iLO 3/4.

Not supported: iDRAC6, iLO 2 and older, and iDRAC7/8 firmware before 2.50.50.50 (the tool detects it,
says so and changes nothing). iLO 3 only speaks TLS 1.0/1.1; the tool falls back to the old encryption
for such devices automatically and notes it in the log.
**Always try a single device first before a large rollout.**

## Security notes

- Use the tool only in an isolated staging network.
- BMCs ship with self-signed certificates, so TLS certificates are not verified.
- A saved list contains passwords only if you choose so, and then in plain text. Delete such a file after
  the rollout.

## License

MIT, see [LICENSE](LICENSE). The software is provided without warranty; you use it at your own risk.

## Author

Björn Franzke
