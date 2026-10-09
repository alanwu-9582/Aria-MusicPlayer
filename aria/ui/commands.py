"""Console commands."""

from __future__ import annotations

import logging
import subprocess
import sys

from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices

from aria import __version__, paths, providers
from aria.core import tasks
from aria.core.models import format_duration

log = logging.getLogger("console")

HELP = """\
play [關鍵字|連結]   播放／暫停；帶參數時直接播放
add <關鍵字|連結>    加入佇列（連結可為歌單）
search <關鍵字>      到搜尋頁搜尋
next · prev · stop  切歌／停止
vol [0-100]         查看或設定音量
seek <秒|分:秒>      跳到指定時間
queue · history     列出佇列／播放紀錄
shuffle · clear     打亂／清空佇列
auto [on|off]       自動推薦
repeat [off|all|one] 重複模式
rec                 重新推薦並列出
theme [system|light|dark]
data                開啟資料夾
update              更新 yt-dlp（YouTube 改版而無法播放時）
version · cls       版本／清除畫面"""


class Commands:
    def __init__(self, window):
        self.w = window
        self.pb = window.playback

    def __call__(self, line: str) -> str | None:
        name, _, arg = line.strip().partition(" ")
        fn = getattr(self, f"cmd_{name.lower()}", None)
        if fn is None:
            return f"未知的指令「{name}」，輸入 help 查看全部"
        return fn(arg.strip())

    # ---- playback ------------------------------------------------------------

    def _resolve(self, arg: str, then) -> str:
        def work():
            if providers.is_link(arg):
                return providers.parse(arg)
            return providers.search("youtube", arg, 1)

        def done(tracks):
            if not tracks:
                log.warning("找不到：%s", arg)
                return
            then(tracks)

        tasks.run(work, done, lambda e: log.error("找不到 %s: %s", arg, e))
        return "處理中…"

    def cmd_play(self, arg):
        if not arg:
            self.pb.toggle()
            return None
        return self._resolve(arg, lambda ts: self.w.actions.play(ts))

    def cmd_add(self, arg):
        if not arg:
            return "用法：add <關鍵字|連結>"
        return self._resolve(arg, lambda ts: self.w.actions.enqueue(ts))

    def cmd_search(self, arg):
        self.w.go(1)
        self.w.search_page.run(arg)

    def cmd_pause(self, _):
        self.pb.toggle()

    def cmd_next(self, _):
        self.pb.next()

    def cmd_prev(self, _):
        self.pb.previous()

    def cmd_stop(self, _):
        self.pb.stop()

    def cmd_vol(self, arg):
        if arg:
            v = max(0, min(100, int(arg)))
            self.pb.set_volume(v)
            self.w.player_bar.volume.set_value(v)
            self.w.player_bar._update_volume_icon(v)
        return f"音量 {self.pb.player.volume}"

    def cmd_seek(self, arg):
        parts = [float(x) for x in arg.split(":")]
        secs = parts[0] * 60 + parts[1] if len(parts) == 2 else parts[0]
        self.pb.seek(secs)
        return f"跳到 {format_duration(secs)}"

    def cmd_queue(self, _):
        lines = [f"{i + 1:>3}. {t.title}" for i, t in enumerate(self.pb.queue[:50])]
        cur = f"▶ {self.pb.current.title}" if self.pb.current else "（未播放）"
        return "\n".join([cur] + lines) if lines else cur + "\n佇列是空的"

    def cmd_history(self, _):
        return "\n".join(f"{t.title}" for t in self.pb.history[::-1][:30]) or "沒有紀錄"

    def cmd_shuffle(self, _):
        self.pb.shuffle()

    def cmd_clear(self, _):
        self.pb.clear()

    def cmd_auto(self, arg):
        on = {"on": True, "off": False}.get(arg.lower(), not self.pb.autoplay) if arg else not self.pb.autoplay
        self.w.player_bar.auto_btn.setChecked(on)
        return f"自動推薦 {'開' if on else '關'}"

    def cmd_repeat(self, arg):
        if arg in ("off", "all", "one"):
            while self.pb.repeat != arg:
                self.pb.cycle_repeat()
        else:
            self.pb.cycle_repeat()
        self.w.player_bar._show_repeat(self.pb.repeat)
        return f"重複：{self.pb.repeat}"

    def cmd_rec(self, _):
        self.pb.refresh_recommendations(force=True)
        if self.pb.recommendations:
            return "\n".join(t.title for t in self.pb.recommendations)
        return "推薦中…"

    # ---- app -----------------------------------------------------------------

    def cmd_theme(self, arg):
        if arg in ("system", "light", "dark"):
            self.w.set_appearance(arg)
        return f"外觀：{self.w.settings['appearance']}"

    def cmd_data(self, _):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(paths.DATA_DIR)))

    def cmd_update(self, _):
        def work():
            cmd = [sys.executable, "-m", "pip", "install", "-U", "yt-dlp", "yt-dlp-ejs"]
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            r = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags, timeout=300)
            if r.returncode:
                raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1])
            import importlib.metadata as md
            return md.version("yt-dlp")

        tasks.run(work, lambda v: log.info("yt-dlp 已更新到 %s，重新啟動 Aria 後生效", v),
                  lambda e: log.error("更新失敗: %s", e))
        return "更新中…"

    def cmd_version(self, _):
        return f"Aria {__version__}"

    def cmd_help(self, _):
        return HELP

    def cmd_cls(self, _):
        return "\f"
