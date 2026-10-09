"""Bottom transport bar: what's playing · controls + seek · auto-recommend + volume."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QFontMetrics, QPainter, QPainterPath
from PySide6.QtWidgets import QFrame, QGridLayout, QLabel, QVBoxLayout, QWidget

from aria.core.models import Track, format_duration
from aria.core.player import LOADING, PLAYING
from aria.ui import icons
from aria.ui.theme import font, theme
from aria.ui.widgets import thumbs as thumbs_mod
from aria.ui.widgets.controls import IconButton, hbox, label
from aria.ui.widgets.slider import Slider

REPEAT_ICON = {"off": "repeat", "all": "repeat", "one": "repeat-one"}
REPEAT_TIP = {"off": "重複：關", "all": "重複：全部", "one": "重複：單曲"}


class Cover(QWidget):
    """44×44 thumbnail."""

    def __init__(self):
        super().__init__()
        self.setFixedSize(44, 44)
        self.track: Track | None = None
        thumbs_mod.instance().ready.connect(lambda _k: self.update())
        theme.changed.connect(self.update)

    def set_track(self, t: Track | None) -> None:
        self.track = t
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(self.rect())
        path = QPainterPath()
        path.addRoundedRect(r, 6, 6)
        p.setClipPath(path)
        pm = thumbs_mod.instance().get(self.track.thumbnail) if self.track and self.track.thumbnail else None
        if pm and not pm.isNull():
            side = min(pm.width(), pm.height())
            p.drawPixmap(r, pm, QRectF((pm.width() - side) / 2, (pm.height() - side) / 2, side, side))
        else:
            p.fillRect(r, theme.color("fill"))
            p.drawPixmap(QRectF(12, 12, 20, 20).toRect(), icons.pixmap("music", theme.color("tertiary"), 20))
        p.end()


class ElidedLabel(QLabel):
    def __init__(self, role: str = ""):
        super().__init__()
        if role:
            self.setObjectName(role)
        self._full = ""
        self.setMinimumWidth(40)

    def set_full_text(self, text: str) -> None:
        self._full = text
        self.setToolTip(text)
        self._elide()

    def resizeEvent(self, e):
        super().resizeEvent(e)
        self._elide()

    def _elide(self):
        self.setText(QFontMetrics(self.font()).elidedText(self._full, Qt.TextElideMode.ElideRight, self.width()))


class PlayerBar(QFrame):
    def __init__(self, playback, library, parent=None):
        super().__init__(parent)
        self.setObjectName("PlayerBar")
        self.pb = playback
        self.library = library
        self._length = 0.0

        grid = QGridLayout(self)
        grid.setContentsMargins(16, 10, 16, 10)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(4)

        # left: now playing
        self.cover = Cover()
        self.title = ElidedLabel("Headline")
        self.artist = ElidedLabel("Caption")
        texts = QVBoxLayout()
        texts.setSpacing(0)
        texts.addStretch(1)
        texts.addWidget(self.title)
        texts.addWidget(self.artist)
        texts.addStretch(1)
        self.save_btn = IconButton("heart", "收藏（Ctrl+D）", checkable=True, tone="secondary")
        left = QWidget()
        left.setFixedWidth(280)
        left.setLayout(hbox(self.cover, 4, texts, self.save_btn, spacing=8))
        left.layout().setStretch(2, 1)
        grid.addWidget(left, 0, 0, 2, 1)

        # centre: transport + seek
        self.shuffle_btn = IconButton("shuffle", "打亂佇列", tone="secondary")
        self.prev_btn = IconButton("previous", "上一首（Ctrl+←）", icon_size=15)
        self.play_btn = IconButton("play", "播放（空白鍵）", icon_size=16, tone="on_accent")
        self.play_btn.setObjectName("PlayButton")
        self.next_btn = IconButton("next", "下一首（Ctrl+→）", icon_size=15)
        self.repeat_btn = IconButton("repeat", REPEAT_TIP["off"], checkable=True, tone="secondary")
        grid.addLayout(hbox(None, self.shuffle_btn, self.prev_btn, self.play_btn, self.next_btn,
                            self.repeat_btn, None, spacing=8), 0, 1)

        self.time = label("0:00", mono=True)
        self.time.setObjectName("Mono")
        self.time.setFont(font("caption", mono=True))
        self.time.setFixedWidth(46)
        self.time.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.length = label("0:00", mono=True)
        self.length.setObjectName("Mono")
        self.length.setFont(font("caption", mono=True))
        self.length.setFixedWidth(46)
        self.seek = Slider(0, 0, step=5, name="播放進度")
        self.seek.setToolTip("點擊或拖曳跳轉；方向鍵 ±5 秒")
        grid.addLayout(hbox(self.time, self.seek, self.length, spacing=8), 1, 1)

        # right: auto-recommend + volume
        self.auto_btn = IconButton("sparkles", "自動推薦：佇列播完時接著播放相關歌曲", checkable=True)
        self.mute_btn = IconButton("volume", "靜音", tone="secondary")
        self.volume = Slider(100, 70, step=5, name="音量")
        self.volume.setFixedWidth(110)
        self.volume.setToolTip("音量（雙擊回到 70）")
        right = QWidget()
        right.setFixedWidth(280)
        right.setLayout(hbox(None, self.auto_btn, 8, self.mute_btn, self.volume, spacing=4))
        grid.addWidget(right, 0, 2, 2, 1)
        grid.setColumnStretch(1, 1)

        self._wire()
        self.set_track(None)

    # ---- wiring --------------------------------------------------------------

    def _wire(self) -> None:
        pb = self.pb
        self.play_btn.clicked.connect(pb.toggle)
        self.prev_btn.clicked.connect(pb.previous)
        self.next_btn.clicked.connect(pb.next)
        self.shuffle_btn.clicked.connect(pb.shuffle)
        self.repeat_btn.clicked.connect(lambda: self._show_repeat(pb.cycle_repeat()))
        self.auto_btn.toggled.connect(pb.set_autoplay)
        self.auto_btn.setChecked(pb.autoplay)
        self._show_repeat(pb.repeat)

        self.seek.moved.connect(lambda v: self.time.setText(format_duration(v) if v else "0:00"))
        self.seek.committed.connect(pb.seek)
        self.volume.set_value(pb.player.volume)
        self.volume.moved.connect(lambda v: self._set_volume(v))
        self.mute_btn.clicked.connect(self._toggle_mute)
        self._unmuted = pb.player.volume or 70
        self._update_volume_icon(pb.player.volume)

        self.save_btn.clicked.connect(self._toggle_saved)

        pb.current_changed.connect(self.set_track)
        pb.state_changed.connect(self._on_state)
        pb.position_changed.connect(self._on_position)
        self.library.changed.connect(self._sync_saved)

    def _show_repeat(self, mode: str) -> None:
        self.repeat_btn.set_icon(REPEAT_ICON[mode])
        self.repeat_btn.setChecked(mode != "off")
        self.repeat_btn.setToolTip(REPEAT_TIP[mode])
        self.repeat_btn.setAccessibleName(REPEAT_TIP[mode])

    def _set_volume(self, v: float) -> None:
        self.pb.set_volume(int(v))
        if v:
            self._unmuted = int(v)
        self._update_volume_icon(v)

    def _toggle_mute(self) -> None:
        target = 0 if self.pb.player.volume else (self._unmuted or 70)
        self.volume.set_value(target)
        self._set_volume(target)

    def _update_volume_icon(self, v: float) -> None:
        self.mute_btn.set_icon("mute" if v == 0 else "volume-low" if v < 50 else "volume")
        self.mute_btn.setToolTip("取消靜音" if v == 0 else "靜音")

    def _toggle_saved(self) -> None:
        if self.pb.current:
            self.library.toggle(self.pb.current)

    def _sync_saved(self) -> None:
        t = self.pb.current
        self.save_btn.setChecked(bool(t and t.key in self.library))

    # ---- updates -------------------------------------------------------------

    def set_track(self, t: Track | None) -> None:
        self.cover.set_track(t)
        self.title.set_full_text(t.title if t else "Aria")
        self.artist.set_full_text((f"{t.artist} · {t.source_label}" if t.artist else t.source_label) if t else "")
        self.save_btn.setEnabled(t is not None)
        self._sync_saved()
        self._on_position(0, t.duration if t else 0)

    def _on_state(self, state: str) -> None:
        playing = state in (PLAYING, LOADING)
        self.play_btn.set_icon("pause" if playing else "play")
        tip = "暫停（空白鍵）" if playing else "播放（空白鍵）"
        self.play_btn.setToolTip(tip)
        self.play_btn.setAccessibleName(tip.split("（")[0])

    def _on_position(self, pos: float, length: float) -> None:
        if length and length != self._length:
            self._length = length
            self.seek.set_maximum(length)
            self.length.setText(format_duration(length))
        elif not length:
            self._length = 0
            self.seek.set_maximum(0)
            self.length.setText("0:00")
        if not self.seek.dragging():
            self.seek.set_value(pos)
            self.time.setText(format_duration(pos) if pos else "0:00")
