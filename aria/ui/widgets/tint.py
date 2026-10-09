"""Smart Artwork: a calm background colour taken from the cover.

The cover's dominant hue is kept, but saturation is capped low and lightness
pinned to the theme, so text contrast and the token palette are never at risk.
"""

from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QObject, Qt, QVariantAnimation, Signal
from PySide6.QtGui import QColor, QImage, QPixmap

from aria.ui.theme import theme

_cache: dict[int, tuple[float, float] | None] = {}

LIGHT_L, DARK_L, CANVAS_L = 0.915, 0.155, 0.095
MAX_S_LIGHT, MAX_S_DARK, MAX_S_CANVAS = 0.22, 0.20, 0.24


def hue_of(pm: QPixmap) -> tuple[float, float] | None:
    """(hue 0..1, saturation 0..1) of the cover's dominant colour, or None for grey covers."""
    key = pm.cacheKey()
    if key in _cache:
        return _cache[key]
    img = pm.toImage().scaled(24, 24, Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation).convertToFormat(QImage.Format.Format_RGB32)
    r = g = b = wsum = 0.0
    for y in range(img.height()):
        for x in range(img.width()):
            c = QColor(img.pixel(x, y))
            s, v = c.hsvSaturationF(), c.valueF()
            w = s * v + 0.02          # vivid pixels decide the hue
            r += c.redF() * w
            g += c.greenF() * w
            b += c.blueF() * w
            wsum += w
    avg = QColor.fromRgbF(r / wsum, g / wsum, b / wsum)
    result = None if avg.hslSaturationF() < 0.06 or avg.hslHueF() < 0 else (avg.hslHueF(), avg.hslSaturationF())
    _cache[key] = result
    return result


def surface(hs: tuple[float, float] | None, kind: str = "page") -> QColor:
    """Tinted colour for ``kind``: page (follows light/dark) or canvas (always dark)."""
    if kind == "canvas":
        base = theme.color("canvas")
        if not hs:
            return base
        return QColor.fromHslF(hs[0], min(hs[1], MAX_S_CANVAS), CANVAS_L)
    base = theme.color("window")
    if not hs:
        return base
    if theme.dark:
        return QColor.fromHslF(hs[0], min(hs[1], MAX_S_DARK), DARK_L)
    return QColor.fromHslF(hs[0], min(hs[1], MAX_S_LIGHT), LIGHT_L)


def mix(a: QColor, b: QColor, t: float) -> QColor:
    return QColor.fromRgbF(a.redF() + (b.redF() - a.redF()) * t, a.greenF() + (b.greenF() - a.greenF()) * t,
                           a.blueF() + (b.blueF() - a.blueF()) * t, a.alphaF() + (b.alphaF() - a.alphaF()) * t)


class TintAnimator(QObject):
    """Holds the current tint and eases to new ones (250 ms)."""

    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hs: tuple[float, float] | None = None
        self.progress = 1.0
        self._from: QColor | None = None
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(250)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._step)
        self._kind = "page"

    def set(self, hs: tuple[float, float] | None, kind: str = "page") -> None:
        if hs == self.hs:
            return
        self._from = self.color(kind)
        self._kind = kind
        self.hs = hs
        self._anim.stop()
        self._anim.start()

    def _step(self, v) -> None:
        self.progress = float(v)
        self.changed.emit()

    def color(self, kind: str = "page") -> QColor:
        target = surface(self.hs, kind)
        if self._from is None or self.progress >= 1.0 or kind != self._kind:
            return target
        return mix(self._from, target, self.progress)
