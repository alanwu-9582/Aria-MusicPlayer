"""Is a downloaded file really the song? Checked before it's ever offered as offline audio.

Several checks, none of them alone: the file is there and not tiny; its first bytes are an audio
container (not an HTML error page or JSON that came back with a 200); the size fits the song's
length (a few seconds of audio for a four-minute song is a cut-off download); and VLC — the
player Aria plays it with — can open it and finds about the expected length (edits and silence
allowed for). An MP4 / M4A or WebM whose own structure says it's longer than what arrived was cut
off (their headers carry the full length, so the player alone wouldn't notice). Blocking: run
it in a background task.
"""

from __future__ import annotations

import os
import threading
import time

MIN_BYTES = 32 * 1024
MIN_RATE = 4_000                 # bytes per second (32 kbit/s): below that it can't be the whole song
_HEADS = ((b"ftyp", 4, "mp4"), (b"\x1aE\xdf\xa3", 0, "webm"), (b"OggS", 0, "ogg"), (b"fLaC", 0, "flac"),
          (b"RIFF", 0, "wav"), (b"ID3", 0, "mp3"), (b"#!AMR", 0, "amr"), (b"\x30\x26\xb2\x75", 0, "wma"))
_TEXT = (b"<", b"{", b"[", b"\xef\xbb\xbf<")


def sniff(head: bytes) -> str:
    """The container ``head`` starts like ("mp4", "webm", "mp3"…), "text" for a page or JSON, "" unknown."""
    for magic, at, kind in _HEADS:
        if head[at:at + len(magic)] == magic:
            return kind
    if len(head) >= 2 and head[0] == 0xFF and head[1] & 0xE0 == 0xE0:
        return "mp3"                                         # an MPEG / ADTS frame
    if head.lstrip()[:1] in (b"<", b"{", b"[") or head.startswith(_TEXT):
        return "text"
    return ""


def _cut_short(path: str, kind: str, size: int) -> bool:
    """The container's own sizes run past the end of the file (an interrupted download)."""
    try:
        with open(path, "rb") as f:
            if kind == "mp4":                                # top-level boxes: 4-byte size, 4-byte type
                pos = 0
                while pos < size:
                    f.seek(pos)
                    head = f.read(16)
                    if len(head) < 8:
                        return True
                    n = int.from_bytes(head[:4], "big")
                    if n == 1:
                        n = int.from_bytes(head[8:16], "big")
                    elif n == 0:
                        return False                         # "to the end of the file"
                    if n < 8:
                        return True
                    pos += n
                return pos > size
            if kind == "webm":                               # EBML header, then the Segment's size
                data = f.read(4096)
                i = data.find(bytes.fromhex("18538067"))           # the Segment element
                if i < 0 or i + 5 > len(data):
                    return False
                first = data[i + 4]
                length = next((k + 1 for k in range(8) if first & (0x80 >> k)), 0)
                if not length:
                    return False
                value = first & ((0x80 >> (length - 1)) - 1)
                for b in data[i + 5: i + 4 + length]:
                    value = (value << 8) | b
                if value == (1 << (7 * length)) - 1:
                    return False                             # size left open (live writing): can't tell
                return i + 4 + length + value > size
    except OSError:
        return True
    return False


_vlc = None
_vlc_lock = threading.Lock()


def probe_seconds(path: str, timeout: float = 6.0) -> float | None:
    """The length VLC reads from the file (None: it couldn't open it; 0: it didn't say)."""
    global _vlc
    try:
        import importlib
        importlib.import_module("aria.core.player")         # points python-vlc at the bundled VLC
        import vlc
    except Exception:
        return 0.0                                         # no VLC here (tests): say nothing either way
    with _vlc_lock:
        if _vlc is None:
            _vlc = vlc.Instance(["--quiet", "--no-video", "--intf=dummy"])
        media = _vlc.media_new_path(os.path.normpath(path))
        if media is None:
            return None
        media.parse_with_options(vlc.MediaParseFlag.local, int(timeout * 1000))
        end = time.monotonic() + timeout + 0.5
        status = media.get_parsed_status().value
        while status not in (1, 2, 3, 4) and time.monotonic() < end:     # skipped / failed / timeout / done
            time.sleep(0.05)
            status = media.get_parsed_status().value
        ms = media.get_duration()
        media.release()
    if status == 2:
        return None
    return max(0.0, ms / 1000) if ms and ms > 0 else 0.0


def verify(path: str, duration: float = 0.0, probe: bool = True) -> tuple[bool, str]:
    """(ok, why not). ``duration``: the song's length as listed (0 = unknown)."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            head = f.read(64)
    except OSError:
        return False, "the file isn’t there"
    if size < MIN_BYTES:
        return False, f"only {size} bytes"
    kind = sniff(head)
    if kind == "text":
        return False, "a web page or error message, not audio"
    if duration > 20 and size / duration < MIN_RATE:
        return False, f"too small for {duration:.0f} s of music"
    if _cut_short(path, kind, size):
        return False, "cut off before the end"
    if probe:
        seconds = probe_seconds(path)
        if seconds is None or (seconds == 0 and not kind):
            return False, "the player can’t open it"
        if seconds and duration > 20 and seconds < duration - max(20.0, duration * 0.25):
            return False, f"{seconds:.0f} s long, the song is {duration:.0f} s"
    elif not kind:
        return False, "not an audio file Aria knows"
    return True, ""
