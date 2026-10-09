"""Line icons on a 24×24 grid, stroke 2, round caps/joins (§11).

Shapes follow the Lucide icon set (ISC licence). Icons are drawn black in the
source and tinted at render time.
"""

from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

_SPEAKER = ('<path d="M11 4.702a.705.705 0 0 0-1.203-.498L6.413 7.587A1.4 1.4 0 0 1 5.416 8H3a1 1 0 0 0-1 1v6'
            'a1 1 0 0 0 1 1h2.416a1.4 1.4 0 0 1 .997.413l3.383 3.384A.705.705 0 0 0 11 19.298z"/>')
_REPEAT = ('<path d="m17 2 4 4-4 4"/><path d="M3 11v-1a4 4 0 0 1 4-4h14"/><path d="m7 22-4-4 4-4"/>'
           '<path d="M21 13v1a4 4 0 0 1-4 4H3"/>')

PATHS = {
    "play": '<path d="M6 4.5v15a1 1 0 0 0 1.5.86l12.5-7.5a1 1 0 0 0 0-1.72L7.5 3.64A1 1 0 0 0 6 4.5z"/>',
    "pause": '<rect x="6" y="4" width="4" height="16" rx="1"/><rect x="14" y="4" width="4" height="16" rx="1"/>',
    "next": '<path d="M5 5.5v13a1 1 0 0 0 1.55.83l9.5-6.5a1 1 0 0 0 0-1.66l-9.5-6.5A1 1 0 0 0 5 5.5z"/>'
            '<path d="M19 5v14"/>',
    "previous": '<path d="M19 18.5v-13a1 1 0 0 0-1.55-.83l-9.5 6.5a1 1 0 0 0 0 1.66l9.5 6.5A1 1 0 0 0 19 18.5z"/>'
                '<path d="M5 19V5"/>',
    "stop": '<rect x="5" y="5" width="14" height="14" rx="2"/>',
    "shuffle": '<path d="m18 14 4 4-4 4"/><path d="m18 2 4 4-4 4"/>'
               '<path d="M2 18h1.973a4 4 0 0 0 3.3-1.7l5.454-7.6a4 4 0 0 1 3.3-1.7H22"/>'
               '<path d="M2 6h1.972a4 4 0 0 1 3.6 2.2"/><path d="M22 18h-6.041a4 4 0 0 1-3.3-1.8l-.359-.45"/>',
    "repeat": _REPEAT,
    "repeat-one": _REPEAT + '<path d="M11 10h1v4"/>',
    "sparkles": '<path d="M9.937 15.5A2 2 0 0 0 8.5 14.063l-6.135-1.582a.5.5 0 0 1 0-.962L8.5 9.936A2 2 0 0 0 '
                '9.937 8.5l1.582-6.135a.5.5 0 0 1 .963 0L14.063 8.5A2 2 0 0 0 15.5 9.937l6.135 1.581a.5.5 0 0 1 '
                '0 .964L15.5 14.063a2 2 0 0 0-1.437 1.437l-1.582 6.135a.5.5 0 0 1-.963 0z"/>'
                '<path d="M20 3v4"/><path d="M22 5h-4"/><path d="M4 17v2"/><path d="M5 18H3"/>',
    "volume": _SPEAKER + '<path d="M16 9a5 5 0 0 1 0 6"/><path d="M19.364 18.364a9 9 0 0 0 0-12.728"/>',
    "volume-low": _SPEAKER + '<path d="M16 9a5 5 0 0 1 0 6"/>',
    "mute": _SPEAKER + '<line x1="22" x2="16" y1="9" y2="15"/><line x1="16" x2="22" y1="9" y2="15"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "heart": '<path d="M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2-4.5-2'
             'A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z"/>',
    "music": '<path d="M9 18V5l12-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="18" cy="16" r="3"/>',
    "library": '<path d="m16 6 4 14"/><path d="M12 6v14"/><path d="M8 8v12"/><path d="M4 4v16"/>',
    "queue": '<path d="M21 15V6"/><path d="M18.5 18a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5Z"/>'
             '<path d="M12 12H3"/><path d="M16 6H3"/><path d="M12 18H3"/>',
    "queue-add": '<path d="M11 12H3"/><path d="M16 6H3"/><path d="M16 18H3"/><path d="M18 9v6"/><path d="M21 12h-6"/>',
    "play-next": '<path d="M5 3h14"/><path d="m18 13-6-6-6 6"/><path d="M12 7v14"/>',
    "terminal": '<polyline points="4 17 10 11 4 5"/><line x1="12" x2="20" y1="19" y2="19"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "download": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/>'
                '<line x1="12" x2="12" y1="15" y2="3"/>',
    "export": '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="17 8 12 3 7 8"/>'
              '<line x1="12" x2="12" y1="3" y2="15"/>',
    "import": '<path d="M12 3v12"/><path d="m8 11 4 4 4-4"/>'
              '<path d="M8 5H6a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7a2 2 0 0 0-2-2h-2"/>',
    "folder": '<path d="m6 14 1.5-2.9A2 2 0 0 1 9.24 10H20a2 2 0 0 1 1.94 2.5l-1.54 6a2 2 0 0 1-1.95 1.5H4'
              'a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h3.9a2 2 0 0 1 1.69.9l.81 1.2a2 2 0 0 0 1.67.9H18a2 2 0 0 1 2 2v2"/>',
    "trash": '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/>'
             '<path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2v2"/><path d="M12 20v2"/><path d="m4.93 4.93 1.41 1.41"/>'
           '<path d="m17.66 17.66 1.41 1.41"/><path d="M2 12h2"/><path d="M20 12h2"/>'
           '<path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>',
    "moon": '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    "monitor": '<rect width="20" height="14" x="2" y="3" rx="2"/><line x1="8" x2="16" y1="21" y2="21"/>'
               '<line x1="12" x2="12" y1="17" y2="21"/>',
    "sidebar": '<rect width="18" height="18" x="3" y="3" rx="2"/><path d="M9 3v18"/>',
    "close": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "refresh": '<path d="M21 12a9 9 0 1 1-9-9c2.52 0 4.93 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>',
    "history": '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M12 7v5l4 2"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "check-circle": '<circle cx="12" cy="12" r="10"/><path d="m9 12 2 2 4-4"/>',
    "alert": '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3"/>'
             '<path d="M12 9v4"/><path d="M12 17h.01"/>',
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "link": '<path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71"/>'
            '<path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71"/>',
    "external": '<path d="M15 3h6v6"/><path d="M10 14 21 3"/>'
                '<path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
}

# Transport glyphs read better solid (like SF Symbols' *.fill variants).
FILLED = {"play", "pause", "next", "previous", "stop"}


def _svg(name: str, color: str, alpha: float) -> bytes:
    fill = color if name in FILLED else "none"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="{fill}" stroke="{color}" '
            f'stroke-opacity="{alpha:.3f}" fill-opacity="{alpha:.3f}" stroke-width="2" '
            f'stroke-linecap="round" stroke-linejoin="round">{PATHS[name]}</svg>').encode()


@lru_cache(maxsize=512)
def _pixmap(name: str, color: str, alpha: float, size: int, dpr: float) -> QPixmap:
    pm = QPixmap(round(size * dpr), round(size * dpr))
    pm.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(_svg(name, color, alpha)))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(p, QRectF(0, 0, pm.width(), pm.height()))
    p.end()
    pm.setDevicePixelRatio(dpr)
    return pm


def pixmap(name: str, color: QColor | str, size: int = 16, dpr: float = 2.0) -> QPixmap:
    c = QColor(color)
    return _pixmap(name, c.name(QColor.NameFormat.HexRgb), round(c.alphaF(), 3), size, dpr)


def icon(name: str, normal: QColor, size: int = 16, on: QColor | None = None,
         disabled: QColor | None = None, dpr: float = 2.0) -> QIcon:
    ic = QIcon()
    ic.addPixmap(pixmap(name, normal, size, dpr), QIcon.Mode.Normal, QIcon.State.Off)
    ic.addPixmap(pixmap(name, on or normal, size, dpr), QIcon.Mode.Normal, QIcon.State.On)
    if disabled is not None:
        ic.addPixmap(pixmap(name, disabled, size, dpr), QIcon.Mode.Disabled, QIcon.State.Off)
        ic.addPixmap(pixmap(name, disabled, size, dpr), QIcon.Mode.Disabled, QIcon.State.On)
    return ic


def size(px: int) -> QSize:
    return QSize(px, px)
