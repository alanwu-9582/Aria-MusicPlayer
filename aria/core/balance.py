"""Volume Balance: Aria's music against everything else that is playing.

One value from -1 to 1:
  < 0  favour other apps — Aria's own music gets quieter;
  > 0  favour Aria — other apps get quieter (Windows per-app volume);
  = 0  everything at its own level.
Only ever the *other* side is turned down, so nothing suddenly gets louder.
Apps can be excluded (calls in Discord, Teams, Zoom… stay untouched), or the
balance can manage only a chosen few.

Other apps' original levels are remembered and restored when the balance goes
back to the middle or Aria quits — and, should Aria ever crash, on next start.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from ctypes import wintypes

from PySide6.QtCore import QObject, QTimer

from aria import paths
from aria.core.storage import read_json, write_json

log = logging.getLogger(__name__)

MAX_DB = 24.0                  # at the far ends the other side is 24 dB quieter
# Loudness is heard in decibels: a straight volume cut barely registers over most of
# the slider, so the slider position maps to dB instead.
DEFAULT_EXCLUDED = ["discord.exe", "teams.exe", "ms-teams.exe", "zoom.exe", "skype.exe", "slack.exe",
                    "webexmta.exe", "ciscocollabhost.exe", "line.exe", "telegram.exe", "whatsapp.exe",
                    "signal.exe", "msedgewebview2.exe"]

try:                                       # Windows Core Audio through comtypes
    if sys.platform != "win32":
        raise ImportError("Windows only")
    import comtypes
    from comtypes import COMMETHOD, GUID, HRESULT, IUnknown

    class ISimpleAudioVolume(IUnknown):
        _iid_ = GUID("{87CE5498-68D6-44E5-9215-6DA47EF883D8}")
        _methods_ = [
            COMMETHOD([], HRESULT, "SetMasterVolume", (["in"], ctypes.c_float, "fLevel"),
                      (["in"], ctypes.POINTER(GUID), "EventContext")),
            COMMETHOD([], HRESULT, "GetMasterVolume", (["out"], ctypes.POINTER(ctypes.c_float), "pfLevel")),
            COMMETHOD([], HRESULT, "SetMute", (["in"], wintypes.BOOL, "bMute"),
                      (["in"], ctypes.POINTER(GUID), "EventContext")),
            COMMETHOD([], HRESULT, "GetMute", (["out"], ctypes.POINTER(wintypes.BOOL), "pbMute")),
        ]

    class IAudioSessionControl(IUnknown):
        _iid_ = GUID("{F4B1A599-7266-4319-A8CA-E70ACB11E8CD}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetState", (["out"], ctypes.POINTER(ctypes.c_int), "pRetVal")),
            COMMETHOD([], HRESULT, "GetDisplayName", (["out"], ctypes.POINTER(ctypes.c_wchar_p), "pRetVal")),
            COMMETHOD([], HRESULT, "SetDisplayName", (["in"], ctypes.c_wchar_p, "Value"),
                      (["in"], ctypes.POINTER(GUID), "EventContext")),
            COMMETHOD([], HRESULT, "GetIconPath", (["out"], ctypes.POINTER(ctypes.c_wchar_p), "pRetVal")),
            COMMETHOD([], HRESULT, "SetIconPath", (["in"], ctypes.c_wchar_p, "Value"),
                      (["in"], ctypes.POINTER(GUID), "EventContext")),
            COMMETHOD([], HRESULT, "GetGroupingParam", (["out"], ctypes.POINTER(GUID), "pRetVal")),
            COMMETHOD([], HRESULT, "SetGroupingParam", (["in"], ctypes.POINTER(GUID), "Override"),
                      (["in"], ctypes.POINTER(GUID), "EventContext")),
            COMMETHOD([], HRESULT, "RegisterAudioSessionNotification", (["in"], ctypes.c_void_p, "NewNotifications")),
            COMMETHOD([], HRESULT, "UnregisterAudioSessionNotification", (["in"], ctypes.c_void_p, "NewNotifications")),
        ]

    class IAudioSessionControl2(IAudioSessionControl):
        _iid_ = GUID("{BFB7FF88-7239-4FC9-8FA2-07C950BE9C6D}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetSessionIdentifier", (["out"], ctypes.POINTER(ctypes.c_wchar_p), "pRetVal")),
            COMMETHOD([], HRESULT, "GetSessionInstanceIdentifier",
                      (["out"], ctypes.POINTER(ctypes.c_wchar_p), "pRetVal")),
            COMMETHOD([], HRESULT, "GetProcessId", (["out"], ctypes.POINTER(wintypes.DWORD), "pRetVal")),
            COMMETHOD([], HRESULT, "IsSystemSoundsSession"),
            COMMETHOD([], HRESULT, "SetDuckingPreference", (["in"], wintypes.BOOL, "optOut")),
        ]

    class IAudioSessionEnumerator(IUnknown):
        _iid_ = GUID("{E2F5BB11-0570-40CA-ACDD-3AA01277DEE8}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetCount", (["out"], ctypes.POINTER(ctypes.c_int), "SessionCount")),
            COMMETHOD([], HRESULT, "GetSession", (["in"], ctypes.c_int, "SessionCount"),
                      (["out"], ctypes.POINTER(ctypes.POINTER(IAudioSessionControl)), "Session")),
        ]

    class IAudioSessionManager2(IUnknown):
        _iid_ = GUID("{77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetAudioSessionControl", (["in"], ctypes.POINTER(GUID), "AudioSessionGuid"),
                      (["in"], wintypes.DWORD, "StreamFlags"),
                      (["out"], ctypes.POINTER(ctypes.POINTER(IAudioSessionControl)), "SessionControl")),
            COMMETHOD([], HRESULT, "GetSimpleAudioVolume", (["in"], ctypes.POINTER(GUID), "AudioSessionGuid"),
                      (["in"], wintypes.DWORD, "StreamFlags"),
                      (["out"], ctypes.POINTER(ctypes.POINTER(ISimpleAudioVolume)), "AudioVolume")),
            COMMETHOD([], HRESULT, "GetSessionEnumerator",
                      (["out"], ctypes.POINTER(ctypes.POINTER(IAudioSessionEnumerator)), "SessionEnum")),
            COMMETHOD([], HRESULT, "RegisterSessionNotification", (["in"], ctypes.c_void_p, "SessionNotification")),
            COMMETHOD([], HRESULT, "UnregisterSessionNotification", (["in"], ctypes.c_void_p, "SessionNotification")),
            COMMETHOD([], HRESULT, "RegisterDuckNotification", (["in"], ctypes.c_wchar_p, "sessionID"),
                      (["in"], ctypes.c_void_p, "duckNotification")),
            COMMETHOD([], HRESULT, "UnregisterDuckNotification", (["in"], ctypes.c_void_p, "duckNotification")),
        ]

    class IMMDevice(IUnknown):
        _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
        _methods_ = [
            COMMETHOD([], HRESULT, "Activate", (["in"], ctypes.POINTER(GUID), "iid"),
                      (["in"], wintypes.DWORD, "dwClsCtx"), (["in"], ctypes.c_void_p, "pActivationParams"),
                      (["out"], ctypes.POINTER(ctypes.POINTER(IUnknown)), "ppInterface")),
        ]

    class IMMDeviceCollection(IUnknown):
        _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
        _methods_ = [
            COMMETHOD([], HRESULT, "GetCount", (["out"], ctypes.POINTER(ctypes.c_uint), "pcDevices")),
            COMMETHOD([], HRESULT, "Item", (["in"], ctypes.c_uint, "nDevice"),
                      (["out"], ctypes.POINTER(ctypes.POINTER(IMMDevice)), "ppDevice")),
        ]

    class IMMDeviceEnumerator(IUnknown):
        _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
        _methods_ = [
            COMMETHOD([], HRESULT, "EnumAudioEndpoints", (["in"], ctypes.c_int, "dataFlow"),
                      (["in"], wintypes.DWORD, "dwStateMask"),
                      (["out"], ctypes.POINTER(ctypes.POINTER(IMMDeviceCollection)), "ppDevices")),
            COMMETHOD([], HRESULT, "GetDefaultAudioEndpoint", (["in"], ctypes.c_int, "dataFlow"),
                      (["in"], ctypes.c_int, "role"),
                      (["out"], ctypes.POINTER(ctypes.POINTER(IMMDevice)), "ppDevice")),
        ]

    CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
    AVAILABLE = True
except Exception as _exc:                  # pragma: no cover - other platforms / missing comtypes
    AVAILABLE = False
    log.info("Volume Balance unavailable: %s", _exc)


def _process_name(pid: int) -> str:
    """Executable name of a process (lower case), "" when it can't be read."""
    if not pid or sys.platform != "win32":
        return ""
    k32 = ctypes.windll.kernel32
    h = k32.OpenProcess(0x1000, False, pid)          # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value).lower()
        return ""
    finally:
        k32.CloseHandle(h)


def gain(v: float) -> float:
    """Volume multiplier for the side being turned down at balance strength ``v`` (0 … 1)."""
    v = max(0.0, min(1.0, v))
    return 1.0 if v <= 0 else 10 ** (-MAX_DB * v / 20)


def _devices() -> list:
    """Every active output (speakers, headset, monitor…): apps may play on any of them."""
    enum = comtypes.CoCreateInstance(CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, comtypes.CLSCTX_ALL)
    try:
        coll = enum.EnumAudioEndpoints(0, 1)                # eRender, DEVICE_STATE_ACTIVE
        return [coll.Item(i) for i in range(coll.GetCount())]
    except Exception:
        return [enum.GetDefaultAudioEndpoint(0, 1)]


def _sessions() -> list[tuple[int, str, object, str]]:
    """(pid, exe name, ISimpleAudioVolume, session id) for every app session on every output.
    The session id tells apart several sessions of one app (and two apps with the same exe name)."""
    out = []
    for device in _devices():
        try:
            mgr = device.Activate(ctypes.byref(IAudioSessionManager2._iid_), comtypes.CLSCTX_ALL, None)
            sessions = mgr.QueryInterface(IAudioSessionManager2).GetSessionEnumerator()
        except Exception:
            continue
        out += _device_sessions(sessions)
    return out


def _device_sessions(sessions) -> list[tuple[int, str, object, str]]:
    out = []
    for i in range(sessions.GetCount()):
        ctl = sessions.GetSession(i)
        try:
            ctl2 = ctl.QueryInterface(IAudioSessionControl2)
            pid = ctl2.GetProcessId()
            if not pid:
                continue                                    # system sounds
            try:
                sid = f"{pid}|{ctl2.GetSessionInstanceIdentifier()}"
            except Exception:
                sid = f"{pid}"
            out.append((pid, _process_name(pid), ctl.QueryInterface(ISimpleAudioVolume), sid))
        except Exception:
            continue
    return out


class VolumeBalance(QObject):
    """Applies the balance; other apps are re-checked every few seconds while it leans to Aria."""

    def __init__(self, settings, player):
        super().__init__()
        self.settings = settings
        self.player = player
        self.available = AVAILABLE
        self._original: dict[str, float] = {}       # session id → level before Aria touched it
        self._applied: dict[str, float] = {}        # session id → level Aria set
        self._timer = QTimer(self)
        self._timer.setInterval(2500)
        self._timer.timeout.connect(self._apply_others)
        self._restore_after_crash()
        self.set_value(float(settings["balance"]))

    # ---- api -----------------------------------------------------------------

    @property
    def value(self) -> float:
        return float(self.settings["balance"])

    def set_value(self, v: float) -> None:
        """-1 … 1 (see module doc)."""
        v = max(-1.0, min(1.0, v))
        if abs(v) < 0.03:
            v = 0.0                                 # the middle snaps
        self.settings["balance"] = round(v, 3)
        self.player.set_balance_gain(gain(-v))
        if v > 0 and self.available:
            self._apply_others()
            self._timer.start()
        else:
            self._timer.stop()
            self.restore()

    def apps(self) -> list[str]:
        """Apps with sound right now (exe names), Aria itself left out."""
        if not self.available:
            return []
        try:
            me = os.getpid()
            return sorted({name for pid, name, _v, _s in _sessions() if name and pid != me})
        except Exception as exc:
            log.warning("Couldn’t list audio apps: %s", exc)
            return []

    def describe(self, v: float | None = None) -> str:
        """"Aria 100% · Other apps 25%" — what the balance does right now."""
        v = self.value if v is None else v
        aria, others = gain(-v), gain(v)
        return f"Aria {round(aria * 100)}% · Other apps {round(others * 100)}%"

    def managed(self, exe: str) -> bool:
        exe = exe.lower()
        if self.settings["balance_mode"] == "only":
            return exe in {a.lower() for a in self.settings["balance_only"]}
        return exe not in {a.lower() for a in self.excluded()}

    def excluded(self) -> list[str]:
        saved = self.settings["balance_excluded"]
        return list(DEFAULT_EXCLUDED if saved is None else saved)

    def restore(self) -> None:
        """Put every app Aria turned down back where it was."""
        if not self._original or not self.available:
            return
        try:
            for _pid, _name, vol, sid in _sessions():
                if sid in self._original:
                    vol.SetMasterVolume(self._original[sid], None)
        except Exception as exc:
            log.warning("Couldn’t restore app volumes: %s", exc)
        self._original.clear()
        self._applied.clear()
        self._persist()

    def shutdown(self) -> None:
        self._timer.stop()
        self.restore()

    # ---- internals -----------------------------------------------------------

    def _apply_others(self) -> None:
        v = self.value
        if v <= 0 or not self.available:
            return
        factor = gain(v)
        me = os.getpid()
        try:
            sessions = _sessions()
        except Exception as exc:
            log.warning("Volume Balance: %s", exc)
            return
        for pid, name, vol, sid in sessions:
            if pid == me or not name:
                continue
            try:
                level = vol.GetMasterVolume()
                if not self.managed(name):
                    if sid in self._original:                     # newly excluded: give it back
                        vol.SetMasterVolume(self._original.pop(sid), None)
                        self._applied.pop(sid, None)
                    continue
                if sid in self._applied and abs(level - self._applied[sid]) > 0.02:
                    self._original[sid] = min(1.0, level / factor)    # the user moved it themselves
                orig = self._original.setdefault(sid, level)
                target = orig * factor
                if abs(level - target) > 0.005:
                    vol.SetMasterVolume(target, None)
                self._applied[sid] = target
            except Exception:
                continue
        self._persist()

    def _persist(self) -> None:
        try:
            if self._original:
                write_json(paths.BALANCE_FILE, {"original": self._original})
            elif paths.BALANCE_FILE.exists():
                paths.BALANCE_FILE.unlink()
        except OSError:
            pass

    def _restore_after_crash(self) -> None:
        saved = read_json(paths.BALANCE_FILE, {}).get("original", {}) if paths.BALANCE_FILE.exists() else {}
        if saved and self.available:
            self._original = {k: float(v) for k, v in saved.items()}
            self.restore()
