"""BMC-Steward: Markenzeichen (Logo) als Fenster-/App-Icon.

Das Logo ist eine Kugel mit Leiterbahnen und zentralem Chip samt Häkchen –
der „Steward", der jeden Server sicher einrichtet. Im Monitor und auf der
Hilfe-Seite baut es sich animiert auf und ab (monitor.AnimatedLogo). Es steckt als SVG direkt in
dieser Datei, damit die Windows-.exe kein zusätzliches Asset bündeln muss.
QtSvg gehört zu PyQt6; fehlt es doch einmal, läuft das Tool ohne Logo weiter.
"""


MARK_SVG = r'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">
<defs>
<radialGradient id="shade" cx="44" cy="45" r="65" gradientUnits="userSpaceOnUse">
<stop offset="0" stop-color="#1e6052"/><stop offset="0.55" stop-color="#0d342d"/><stop offset="1" stop-color="#051613"/>
</radialGradient>
<radialGradient id="shine" cx="40" cy="39" r="14" gradientUnits="userSpaceOnUse">
<stop offset="0" stop-color="#ffffff" stop-opacity="0.24"/><stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
</radialGradient>
</defs>
<circle cx="60" cy="63" r="45" fill="url(#shade)"/>
<circle cx="40" cy="39" r="14" fill="url(#shine)"/>
<circle cx="60" cy="63" r="45" fill="none" stroke="#34d399" stroke-width="5"/>
<line x1="32" y1="44" x2="44" y2="44" stroke="#2dd4bf" stroke-width="3.5" stroke-linecap="round"/>
<line x1="76" y1="44" x2="88" y2="44" stroke="#2dd4bf" stroke-width="3.5" stroke-linecap="round"/>
<line x1="32" y1="82" x2="44" y2="82" stroke="#2dd4bf" stroke-width="3.5" stroke-linecap="round"/>
<line x1="76" y1="82" x2="88" y2="82" stroke="#2dd4bf" stroke-width="3.5" stroke-linecap="round"/>
<circle cx="32" cy="44" r="4" fill="#34d399"/>
<circle cx="88" cy="44" r="4" fill="#34d399"/>
<circle cx="32" cy="82" r="4" fill="#34d399"/>
<circle cx="88" cy="82" r="4" fill="#34d399"/>
<rect x="46" y="50" width="28" height="28" rx="5" fill="none" stroke="#34d399" stroke-width="4"/>
<line x1="51" y1="45" x2="51" y2="50" stroke="#34d399" stroke-width="3" stroke-linecap="round"/>
<line x1="60" y1="45" x2="60" y2="50" stroke="#34d399" stroke-width="3" stroke-linecap="round"/>
<line x1="69" y1="45" x2="69" y2="50" stroke="#34d399" stroke-width="3" stroke-linecap="round"/>
<line x1="51" y1="78" x2="51" y2="83" stroke="#34d399" stroke-width="3" stroke-linecap="round"/>
<line x1="60" y1="78" x2="60" y2="83" stroke="#34d399" stroke-width="3" stroke-linecap="round"/>
<line x1="69" y1="78" x2="69" y2="83" stroke="#34d399" stroke-width="3" stroke-linecap="round"/>
<path d="M53 64 L58 69 L68 58" fill="none" stroke="#2dd4bf" stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>
</svg>'''


def _renderer():
    """QSvgRenderer für das Markenzeichen oder None, falls QtSvg fehlt."""
    try:
        from PyQt6.QtSvg import QSvgRenderer
        from PyQt6.QtCore import QByteArray
    except Exception:
        return None
    return QSvgRenderer(QByteArray(MARK_SVG.encode("utf-8")))


def app_icon():
    """QIcon aus dem SVG-Markenzeichen (mehrere Größen). Leeres Icon als Fallback."""
    from PyQt6.QtGui import QIcon, QPixmap, QPainter
    from PyQt6.QtCore import Qt
    renderer = _renderer()
    if renderer is None:
        return QIcon()
    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pm = QPixmap(size, size)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        renderer.render(p)
        p.end()
        icon.addPixmap(pm)
    return icon

