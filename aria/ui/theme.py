"""Design tokens (介面設計規範 §1–3), fonts, and the global stylesheet.

Widgets never hard-code colours: they ask ``theme.color("token")`` and repaint
on ``theme.changed``. Light/dark follows the OS unless the user picks one.
"""

from __future__ import annotations

import re

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPalette

# Project accent — drawn once per §1.3 (H157 S20 L39 / dark S25 L66), never re-rolled.
ACCENT = {
    "light": {
        "accent": "#507768",
        "accent_hover": "#436558",
        "accent_soft": "rgba(80,119,104,0.14)",
        "on_accent": "#ffffff",
        "slider_thumb": "#60907d",
        "slider_thumb_hover": "#436558",
    },
    "dark": {
        "accent": "#93bead",
        "accent_hover": "#a6c9bc",
        "accent_soft": "rgba(147,190,173,0.22)",
        "on_accent": "#1c1c1e",
        "slider_thumb": "#bfd9cf",
        "slider_thumb_hover": "#d9e8e2",
    },
}

TOKENS = {
    "light": {
        "window": "#f5f5f7", "content": "#ffffff", "sidebar": "#ebebef", "elevated": "#ffffff",
        "control": "#ffffff", "control_hover": "#f2f2f5",
        "fill": "rgba(120,120,128,0.12)", "fill_strong": "rgba(120,120,128,0.20)",
        "label": "#1d1d1f", "secondary": "#6e6e73", "tertiary": "#a1a1a6",
        "separator": "rgba(0,0,0,0.10)", "separator_hex": "#e0e0e5",
        "canvas": "#1c1c1e", "segment_on": "#ffffff",
        "red": "#ff3b30", "orange": "#ff9500", "yellow": "#ffcc00", "green": "#34c759",
        "teal": "#30b0c7", "blue": "#007aff", "purple": "#af52de", "gray": "#8e8e93",
        **ACCENT["light"],
    },
    "dark": {
        "window": "#1c1c1e", "content": "#232325", "sidebar": "#28282b", "elevated": "#2c2c2e",
        "control": "#3a3a3c", "control_hover": "#444447",
        "fill": "rgba(120,120,128,0.24)", "fill_strong": "rgba(120,120,128,0.36)",
        "label": "#f5f5f7", "secondary": "#a1a1a6", "tertiary": "#6e6e73",
        "separator": "rgba(255,255,255,0.10)", "separator_hex": "#3a3a3d",
        "canvas": "#111113", "segment_on": "#636366",
        "red": "#ff453a", "orange": "#ff9f0a", "yellow": "#ffd60a", "green": "#30d158",
        "teal": "#40c8e0", "blue": "#0a84ff", "purple": "#bf5af2", "gray": "#98989d",
        **ACCENT["dark"],
    },
}

# Overlay surfaces on the (always dark) canvas, independent of theme (§1.2, §7.2).
OVERLAY = "rgba(28,28,30,0.84)"
TOAST = "rgba(30,30,32,0.92)"

# Type scale (§2.2): name → (px, weight)
TYPE = {
    "large": (26, 700), "title2": (18, 600), "title3": (16, 600), "headline": (14, 600),
    "body": (14, 400), "callout": (13, 400), "caption": (12, 400),
}
UI_FAMILIES = ["Segoe UI", "Microsoft JhengHei UI", "Microsoft JhengHei"]
MONO_FAMILIES = ["Cascadia Mono", "Consolas", "Microsoft JhengHei UI"]

ROW = 30          # §0.1 control height


def radius(r: float, w: float, h: float) -> float:
    """§0.2: a corner radius is always below a third of the shortest side."""
    return max(0.0, min(r, min(w, h) / 3 - 0.01))


def parse_color(value: str) -> QColor:
    m = re.fullmatch(r"rgba\((\d+),(\d+),(\d+),([\d.]+)\)", value.replace(" ", ""))
    if m:
        r, g, b, a = m.groups()
        return QColor(int(r), int(g), int(b), round(float(a) * 255))
    return QColor(value)


def font(style: str = "body", weight: int | None = None, mono: bool = False) -> QFont:
    px, w = TYPE[style]
    f = QFont()
    f.setFamilies(MONO_FAMILIES if mono else UI_FAMILIES)
    f.setPixelSize(px)
    f.setWeight(QFont.Weight(weight or w))
    f.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    f.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    return f


class Theme(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.appearance = "system"      # system | light | dark
        self.mode = "light"
        self._colors: dict[str, QColor] = {}
        self._app = None

    # ---- lookups -------------------------------------------------------------

    def color(self, token: str) -> QColor:
        c = self._colors.get(token)
        if c is None:
            c = self._colors[token] = parse_color(TOKENS[self.mode][token])
        return QColor(c)

    def hex(self, token: str) -> str:
        return TOKENS[self.mode][token]

    @property
    def dark(self) -> bool:
        return self.mode == "dark"

    # ---- switching -----------------------------------------------------------

    def attach(self, app, appearance: str) -> None:
        self._app = app
        self.appearance = appearance
        app.setFont(font("body"))
        hints = QGuiApplication.styleHints()
        if hasattr(hints, "colorSchemeChanged"):
            hints.colorSchemeChanged.connect(lambda _s: self._refresh())
        self._refresh(force=True)

    def set_appearance(self, appearance: str) -> None:
        self.appearance = appearance
        self._refresh()

    def _system_mode(self) -> str:
        hints = QGuiApplication.styleHints()
        scheme = hints.colorScheme() if hasattr(hints, "colorScheme") else Qt.ColorScheme.Light
        return "dark" if scheme == Qt.ColorScheme.Dark else "light"

    def _refresh(self, force: bool = False) -> None:
        mode = self._system_mode() if self.appearance == "system" else self.appearance
        if mode == self.mode and not force:
            return
        self.mode = mode
        self._colors.clear()
        if self._app:
            self._app.setPalette(self._palette())
            self._app.setStyleSheet(stylesheet(TOKENS[mode]))
        self.changed.emit()

    def _palette(self) -> QPalette:
        p = QPalette()
        role = QPalette.ColorRole
        pairs = {
            role.Window: "window", role.Base: "control", role.AlternateBase: "content",
            role.Text: "label", role.WindowText: "label", role.ButtonText: "label",
            role.Button: "content", role.ToolTipBase: "elevated", role.ToolTipText: "label",
            role.Highlight: "accent", role.HighlightedText: "on_accent",
            role.PlaceholderText: "tertiary", role.Link: "accent", role.BrightText: "on_accent",
        }
        for r, token in pairs.items():
            p.setColor(r, self.color(token))
        p.setColor(QPalette.ColorGroup.Disabled, role.Text, self.color("tertiary"))
        p.setColor(QPalette.ColorGroup.Disabled, role.WindowText, self.color("tertiary"))
        p.setColor(QPalette.ColorGroup.Disabled, role.ButtonText, self.color("tertiary"))
        return p


theme = Theme()


def stylesheet(t: dict[str, str]) -> str:
    """Global QSS. Sizes follow §3; heights are 30 (5 + 20 + 5)."""
    return f"""
QWidget {{ color: {t['label']}; }}
QMainWindow, QDialog, #Page {{ background: {t['window']}; }}
QToolTip {{
    background: {t['elevated']}; color: {t['label']}; border: 1px solid {t['separator_hex']};
    border-radius: 6px; padding: 4px 8px; font-size: 12px; font-weight: 500;
}}

QPushButton {{
    background: {t['fill']}; color: {t['label']}; border: none; border-radius: 7px;
    padding: 5px 12px; min-height: 20px; max-height: 20px;
}}
QPushButton:hover {{ background: {t['fill_strong']}; }}
QPushButton:pressed {{ background: {t['fill_strong']}; }}
QPushButton:disabled {{ color: {t['tertiary']}; background: {t['fill']}; }}
QPushButton[kind="primary"] {{ background: {t['accent']}; color: {t['on_accent']}; font-weight: 600; }}
QPushButton[kind="primary"]:hover {{ background: {t['accent_hover']}; }}
QPushButton[kind="primary"]:disabled {{ background: {t['fill']}; color: {t['tertiary']}; }}
QPushButton[kind="borderless"] {{ background: transparent; color: {t['accent']}; }}
QPushButton[kind="borderless"]:hover {{ background: {t['fill']}; }}
QPushButton[kind="danger"] {{ background: transparent; color: {t['red']}; }}
QPushButton[kind="danger"]:hover {{ background: {t['fill']}; }}
QPushButton[kind="borderless"]:disabled, QPushButton[kind="danger"]:disabled {{
    background: transparent; color: {t['tertiary']};
}}
QPushButton[kind="destructive"] {{ background: {t['red']}; color: #ffffff; font-weight: 600; }}
QPushButton:focus {{ outline: none; }}

QToolButton {{ background: transparent; border: none; border-radius: 7px; padding: 0; }}
QToolButton:hover {{ background: {t['fill']}; }}
QToolButton:pressed {{ background: {t['fill_strong']}; }}
QToolButton:checked {{ background: {t['accent_soft']}; }}
QToolButton:focus {{ outline: none; }}
QToolButton#PlayButton {{ background: {t['accent']}; border-radius: 9px; }}
QToolButton#PlayButton:hover {{ background: {t['accent_hover']}; }}

QLineEdit {{
    background: {t['control']}; border: 1px solid {t['separator_hex']}; border-radius: 7px;
    padding: 4px 8px; min-height: 20px; max-height: 20px; selection-background-color: {t['accent']};
    selection-color: {t['on_accent']};
}}
QLineEdit:focus {{ border: 2px solid {t['accent']}; padding: 3px 7px; }}
QLineEdit[error="true"] {{ border: 2px solid {t['red']}; padding: 3px 7px; }}
QLineEdit:disabled {{ background: {t['fill']}; border-color: transparent; color: {t['secondary']}; }}

QListView, QTextEdit, QPlainTextEdit {{ background: transparent; border: none; outline: none; }}
QPlainTextEdit {{ selection-background-color: {t['accent_soft']}; selection-color: {t['label']}; }}

QScrollBar:vertical {{ background: transparent; width: 11px; margin: 0; }}
QScrollBar::handle:vertical {{
    background: {t['fill_strong']}; min-height: 36px; border-radius: 3px; margin: 2px 2px 2px 2px;
}}
QScrollBar::handle:vertical:hover, QScrollBar::handle:vertical:pressed {{
    background: {t['tertiary']}; border-radius: 5px; margin: 0;
}}
QScrollBar[empty="true"]::handle:vertical {{ background: transparent; }}
QScrollBar:horizontal {{ height: 0; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QMenu {{
    background: {t['elevated']}; border: 1px solid {t['separator_hex']}; border-radius: 9px; padding: 5px;
}}
QMenu::item {{ padding: 5px 22px 5px 10px; border-radius: 4px; }}
QMenu::item:selected {{ background: {t['accent']}; color: {t['on_accent']}; }}
QMenu::item:disabled {{ color: {t['tertiary']}; }}
QMenu::separator {{ height: 1px; background: {t['separator_hex']}; margin: 4px 8px; }}
QMenu::icon {{ padding-left: 6px; }}

QProgressBar {{ background: {t['fill_strong']}; border: none; border-radius: 1px; max-height: 5px; min-height: 5px; }}
QProgressBar::chunk {{ background: {t['accent']}; border-radius: 1px; }}

QSplitter::handle {{ background: transparent; }}

#Sidebar {{ background: {t['sidebar']}; border-right: 1px solid {t['separator']}; }}
#StatusBar {{ border-top: 1px solid {t['separator']}; }}
#PlayerBar {{ background: {t['content']}; border-top: 1px solid {t['separator']}; }}
#Card {{ background: {t['content']}; border: 1px solid {t['separator_hex']}; border-radius: 12px; }}
#Secondary {{ color: {t['secondary']}; }}
#Caption {{ color: {t['secondary']}; font-size: 12px; }}
#Mono {{ color: {t['secondary']}; font-size: 12px; }}
#Large {{ font-size: 26px; font-weight: 700; }}
#Title2 {{ font-size: 18px; font-weight: 600; }}
#Title3 {{ font-size: 16px; font-weight: 600; }}
#Headline {{ font-size: 14px; font-weight: 600; }}
#SectionTitle {{ color: {t['secondary']}; font-size: 12px; font-weight: 600; }}
#VLine {{ background: {t['separator_hex']}; }}
"""
