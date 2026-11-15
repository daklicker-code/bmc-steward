"""BMC-Steward: Markenzeichen (Logo) als Fenster-/App-Icon.

Das Logo ist eine Kugel mit einem Netzwerk aus Knoten und Verbindungen um einen zentralen Stern –
der „Steward", der jeden Server sicher einrichtet. Im Monitor und auf der
Hilfe-Seite baut es sich animiert auf und ab (monitor.AnimatedLogo). Es steckt als SVG direkt in
dieser Datei, damit die Windows-.exe kein zusätzliches Asset bündeln muss.
QtSvg gehört zu PyQt6; fehlt es doch einmal, läuft das Tool ohne Logo weiter.
"""


MARK_SVG = r'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120">
<defs>
<radialGradient id="shade" cx="44" cy="45" r="65" gradientUnits="userSpaceOnUse">
<stop offset="0" stop-color="#2f9a82"/><stop offset="0.55" stop-color="#16604f"/><stop offset="1" stop-color="#08322a"/>
</radialGradient>
<radialGradient id="shine" cx="40" cy="39" r="16" gradientUnits="userSpaceOnUse">
<stop offset="0" stop-color="#ffffff" stop-opacity="0.34"/><stop offset="1" stop-color="#ffffff" stop-opacity="0"/>
</radialGradient>
</defs>
<g><circle cx="60" cy="63" r="45" fill="url(#shade)"/><circle cx="40" cy="39" r="16" fill="url(#shine)"/></g>
<circle cx="60" cy="63" r="45" stroke="#34d399" stroke-width="5" fill="none"/>
<g stroke="#5eead4" stroke-opacity="0.55" stroke-width="2.5" stroke-linecap="round" fill="none"><line x1="66.5" y1="33.8" x2="82.1" y2="42.8"/><line x1="88.6" y1="54.0" x2="88.6" y2="72.0"/><line x1="82.1" y1="83.2" x2="66.5" y2="92.2"/><line x1="53.5" y1="92.2" x2="37.9" y2="83.2"/><line x1="31.4" y1="72.0" x2="31.4" y2="54.0"/><line x1="37.9" y1="42.8" x2="53.5" y2="33.8"/></g>
<g stroke="#5eead4" stroke-width="3.5" stroke-linecap="round" fill="none"><line x1="60.0" y1="48.0" x2="60.0" y2="35.5"/><line x1="73.0" y1="55.5" x2="83.8" y2="49.2"/><line x1="73.0" y1="70.5" x2="83.8" y2="76.8"/><line x1="60.0" y1="78.0" x2="60.0" y2="90.5"/><line x1="47.0" y1="70.5" x2="36.2" y2="76.8"/><line x1="47.0" y1="55.5" x2="36.2" y2="49.2"/></g>
<g fill="#34d399"><circle cx="60.0" cy="30.0" r="5"/><circle cx="88.6" cy="46.5" r="5"/><circle cx="88.6" cy="79.5" r="5"/><circle cx="60.0" cy="96.0" r="5"/><circle cx="31.4" cy="79.5" r="5"/><circle cx="31.4" cy="46.5" r="5"/></g>
<circle cx="60" cy="63" r="12" stroke="#34d399" stroke-width="4" fill="none"/>
<path d="M60.0 55.6 L62.2 61.0 L68.0 61.4 L63.5 65.1 L64.9 70.8 L60.0 67.7 L55.1 70.8 L56.5 65.1 L52.0 61.4 L57.8 61.0 Z" fill="#d1fae5" stroke="none"/>
<path d="M60.0 55.6 L62.2 61.0 L68.0 61.4 L63.5 65.1 L64.9 70.8 L60.0 67.7 L55.1 70.8 L56.5 65.1 L52.0 61.4 L57.8 61.0 Z" stroke="#5eead4" stroke-width="2.5" stroke-linejoin="round" stroke-linecap="round" fill="none"/>
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

