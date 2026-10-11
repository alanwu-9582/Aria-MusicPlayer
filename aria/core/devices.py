"""Audio Device Profiles: each output remembers its own volume and balance.

Plug in headphones and Aria comes back at the 35 % you used on them; switch to
the speakers and it returns to their 65 %. The output can be chosen in
Settings ("System Default" follows Windows). If the chosen output disappears,
Aria can fall back to the default one and returns once it's back.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QTimer, Signal

from aria.core.balance import default_output_guid

log = logging.getLogger(__name__)

POLL_MS = 2000


def short_name(name: str, others: list[str] | None = None) -> str:
    """"Headphones (G522 LIGHTSPEED - Wireless Mode)" → "Headphones", unless that would be ambiguous."""
    short = name.split(" (")[0].strip() or name
    if others and sum(1 for o in others if (o.split(" (")[0].strip() or o) == short) > 1:
        return name
    return short


class AudioDevices(QObject):
    switched = Signal(str)          # a message for the user ("Headphones · 35 %")
    devices_changed = Signal()      # the list of outputs changed

    def __init__(self, settings, playback, balance):
        super().__init__()
        self.settings = settings
        self.pb = playback
        self.balance = balance
        self._outputs: list[tuple[str, str]] = []
        self._applied: str | None = None      # device id handed to VLC
        self._key: str | None = None          # whose profile is active
        self._restoring = False
        self.refresh()
        self._apply_output(initial=True)
        playback.volume_changed.connect(lambda _v: self._remember())
        balance.changed.connect(lambda _v: self._remember())
        self._timer = QTimer(self)
        self._timer.setInterval(POLL_MS)
        self._timer.timeout.connect(self._poll)
        self._timer.start()
        self._poll()                           # the current output's profile is in place from the start

    # ---- queries -------------------------------------------------------------

    def outputs(self) -> list[tuple[str, str]]:
        return list(self._outputs)

    def name_of(self, device: str) -> str:
        """Short display name of an output."""
        full = next((n for d, n in self._outputs if d == device), "") or \
            (self.settings["device_names"] or {}).get(device, "")
        return short_name(full, [n for _d, n in self._outputs]) if full else "Output"

    def current(self) -> str:
        """The output actually playing: the chosen one if present, else Windows' default."""
        chosen = self.settings["output_device"]
        if chosen and any(d == chosen for d, _n in self._outputs):
            return chosen
        return default_output_guid() or "default"

    def profiles(self) -> dict:
        return dict(self.settings["device_profiles"] or {})

    # ---- changes -------------------------------------------------------------

    def refresh(self) -> None:
        try:
            outs = self.pb.player.output_devices()
        except Exception as exc:
            log.warning("Couldn’t list audio outputs: %s", exc)
            return
        if outs != self._outputs:
            self._outputs = outs
            self.devices_changed.emit()

    def choose(self, device: str) -> None:
        """"" = follow the system default."""
        self.settings["output_device"] = device
        self._apply_output()
        self._poll()

    def _apply_output(self, initial: bool = False) -> None:
        chosen = self.settings["output_device"]
        present = bool(chosen) and any(d == chosen for d, _n in self._outputs)
        target = chosen if present else ""
        if target == self._applied:
            return
        if chosen and not present and not initial:
            if not self.settings["device_fallback"]:
                return                         # stay on the missing device (silence) as asked
            self.switched.emit(f"{self.name_of(chosen)} disconnected · using the default output")
        elif chosen and present and self._applied == "" and not initial:
            self.switched.emit(f"Back on {self.name_of(chosen)}")
        self._applied = target
        self.pb.player.set_output_device(target)

    def _poll(self) -> None:
        self.refresh()
        self._apply_output()
        key = self.current()
        if key == self._key:
            return
        first = self._key is None
        self._key = key
        names = dict(self.settings["device_names"] or {})
        if key in dict(self._outputs):
            names[key] = dict(self._outputs)[key]
            self.settings["device_names"] = names
        profile = self.profiles().get(key)
        if profile and self.settings["device_profiles_on"]:
            self._restoring = True
            try:
                self.pb.set_volume(int(profile.get("volume", self.pb.player.volume)))
                if self.balance is not None and "balance" in profile:
                    self.balance.set_value(float(profile["balance"]))
            finally:
                self._restoring = False
            if not first:
                self.switched.emit(f"{self.name_of(key)} · {self.pb.player.volume}%")
        else:
            self._remember()                   # first time on this output: start its profile

    def _remember(self) -> None:
        if self._restoring or not self._key or not self.settings["device_profiles_on"]:
            return
        profiles = self.profiles()
        profiles[self._key] = {"volume": self.pb.player.volume,
                               "balance": round(self.balance.value, 3) if self.balance is not None else 0.0}
        self.settings["device_profiles"] = profiles
