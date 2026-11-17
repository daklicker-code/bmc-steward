"""Tests von BMC-Steward.

Arbeitsdaten der Tests (Zertifikats-Fingerabdrücke, Schlüsselbund-Zustand, Sicherungen ...) dürfen nie im
Repo-Ordner landen. Die Umgebungsvariable muss gesetzt sein, bevor irgendein Test bmc_steward.i18n lädt;
deshalb steht sie hier und nicht erst in tests/_support.py."""
import os as _os
import tempfile as _tempfile

_os.environ.setdefault("BMC_STEWARD_DATA", _tempfile.mkdtemp(prefix="bmc-steward-test-"))
