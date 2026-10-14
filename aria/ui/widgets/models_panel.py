"""Settings › Lyrics › Speech Models: download, choose and delete the models Forced Alignment uses.

One dropdown picks a model; beside it the one action that fits it — Download (Cancel while it
downloads), Use, or "In Use" — and a trash button once it's on disk. Models go to ``data/models``. Downloads run in the window's ModelManager, so they carry on
when Settings is closed; the panel only shows them. Without the speech engine
(faster-whisper) installed, the panel offers to install it first.
"""

from __future__ import annotations

import subprocess
import sys

from PySide6.QtCore import QPoint, Qt
from PySide6.QtWidgets import QHBoxLayout, QMenu, QVBoxLayout, QWidget

from aria import paths
from aria.core import aligner, cache, tasks
from aria.ui import icons
from aria.ui.theme import theme
from aria.ui.widgets.controls import Button, IconButton, MenuButton, label


ACTIONS = ("Download", "Cancel", "Use", "In Use")


class ModelPicker(MenuButton):
    """The model dropdown: shows the chosen model's name; the list also says which are on disk."""

    def __init__(self):
        super().__init__([m.name for m in aligner.MODELS])
        self.states: list[str] = [""] * len(aligner.MODELS)

    def _pop(self) -> None:
        m = QMenu(self)
        for i, (name, state) in enumerate(zip(self.items, self.states)):
            act = m.addAction(f"{name}  ·  {state}" if state else name, lambda i=i: self.set_index(i, emit=True))
            if i == self._index:
                act.setIcon(icons.icon("check", theme.color("accent"), 16))
        m.setMinimumWidth(self.width())
        m.exec(self.mapToGlobal(QPoint(0, self.height() + 2)))


class ModelsPanel(QWidget):
    def __init__(self, settings, manager: aligner.ModelManager, confirm, toast, on_change):
        super().__init__()
        self.settings, self.manager, self.confirm, self.toast, self.on_change = settings, manager, confirm, toast, on_change
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        # the engine itself (a Python package), if it isn't there yet
        self.engine_row = QWidget()
        er = QHBoxLayout(self.engine_row)
        er.setContentsMargins(8, 2, 2, 6)
        self.engine_note = label("Forced Alignment needs the speech engine (faster-whisper).", "Caption")
        self.engine_note.setWordWrap(True)
        er.addWidget(self.engine_note, 1)
        self.engine_btn = Button("Install Engine", "borderless", icon="download")
        self.engine_btn.clicked.connect(self._install_engine)
        er.addWidget(self.engine_btn)
        lay.addWidget(self.engine_row)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.picker = ModelPicker()
        self.picker.setAccessibleName("Speech model")
        self.picker.set_index(self._first())
        self.picker.changed.connect(lambda _i: self.refresh())
        row.addWidget(self.picker, 1)
        self.action = Button("Download", "secondary")
        self.action.clicked.connect(self._act)
        # one width for every state, so the dropdown never changes size
        self.action.setFixedWidth(max(Button(t, "secondary").sizeHint().width() for t in ACTIONS))
        row.addWidget(self.action)
        self.delete = IconButton("trash", "Delete Model", tone="secondary")
        keep = self.delete.sizePolicy()
        keep.setRetainSizeWhenHidden(True)
        self.delete.setSizePolicy(keep)
        self.delete.clicked.connect(lambda: self.delete_model(self.model().id))
        row.addWidget(self.delete)
        lay.addLayout(row)
        self.detail = label("", "Caption")
        self.detail.setContentsMargins(8, 4, 0, 0)
        lay.addWidget(self.detail)
        self.where = label("", "Caption")
        self.where.setContentsMargins(8, 0, 0, 0)
        self.where.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.where.setWordWrap(True)
        lay.addWidget(self.where)
        manager.progress.connect(self._on_progress)
        manager.finished.connect(self._on_finished)
        self.refresh()

    # ---- actions -----------------------------------------------------------------

    def _first(self) -> int:
        """Open on the model in use, else the one downloading, else the first on disk, else Small."""
        ids = [m.id for m in aligner.MODELS]
        for mid in (self.settings["align_model"] if aligner.installed(self.settings["align_model"]) else None,
                    self.manager.active,
                    next((m for m in ids if aligner.installed(m)), None), "small"):
            if mid in ids:
                return ids.index(mid)
        return 0

    def model(self) -> aligner.SpeechModel:
        return aligner.MODELS[self.picker.index()]

    def _act(self) -> None:
        mgr, mid = self.manager, self.model().id
        if mgr.active == mid:
            mgr.cancel()
        elif aligner.installed(mid):
            self.use(mid)
        else:
            mgr.start(mid)
        self.refresh()

    def use(self, model_id: str) -> None:
        self.settings["align_model"] = model_id
        self.on_change()
        self.refresh()

    def delete_model(self, model_id: str) -> None:
        m = aligner.BY_ID[model_id]
        if not self.confirm(self, f"Delete {m.name}?", f"Frees {cache.human(aligner.disk_size(model_id))}. "
                            "It can be downloaded again any time.", "Delete", danger=True):
            return
        aligner.shutdown_worker()                    # the model may be loaded (and its files open) there
        aligner.delete(model_id)
        if self.settings["align_model"] == model_id:
            self.settings["align_model"] = next((x.id for x in aligner.MODELS if aligner.installed(x.id)), "")
            self.on_change()
        self.toast("success" if not aligner.installed(model_id) else "warning",
                   f"{m.name} deleted" if not aligner.installed(model_id) else f"Couldn’t delete {m.name} yet")
        self.refresh()

    def _install_engine(self) -> None:
        self.engine_btn.setEnabled(False)
        self.engine_note.setText("Installing the speech engine…")

        def work():
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            r = subprocess.run([sys.executable, "-m", "pip", "install", "faster-whisper"], capture_output=True,
                               text=True, creationflags=flags, timeout=900)
            if r.returncode:
                raise RuntimeError((r.stderr or r.stdout).strip().splitlines()[-1])

        def done(_r):
            import importlib
            importlib.invalidate_caches()
            self.toast("success", "Speech engine installed")
            self.refresh()

        def failed(exc):
            self.engine_btn.setEnabled(True)
            self.engine_note.setText(f"Couldn’t install it: {exc}")

        tasks.run(work, done, failed)

    # ---- showing -----------------------------------------------------------------

    def _on_progress(self, model_id: str, done: int, total: int) -> None:
        if self.model().id == model_id:
            self._show_model((done, total))

    def _on_finished(self, model_id: str, error: str) -> None:
        m = aligner.BY_ID[model_id]
        if not error:
            if not aligner.installed(self.settings["align_model"]):
                self.settings["align_model"] = model_id          # the first model is the one in use
                self.on_change()
            self.toast("success", f"{m.name} downloaded")
        elif error != "cancelled":
            self.toast("danger", f"Couldn’t download {m.name}")
        self.refresh()

    def refresh(self) -> None:
        engine = aligner.engine_available()
        self.engine_row.setVisible(not engine)
        if not engine and paths.FROZEN:                # no pip in Aria.exe: the engine comes with the build
            self.engine_btn.hide()
            self.engine_note.setText("This build of Aria doesn’t include the speech engine (faster-whisper).")
        elif not engine and self.engine_btn.isEnabled():
            self.engine_note.setText("Forced Alignment needs the speech engine (faster-whisper).")
        self.picker.states = [("In Use" if m.id == self.settings["align_model"] else "Downloaded")
                              if aligner.installed(m.id) else ("Downloading" if self.manager.active == m.id else "")
                              for m in aligner.MODELS]
        self._show_model()
        used = sum(aligner.disk_size(m.id) for m in aligner.MODELS)
        self.where.setText(f"{cache.human(used)} in {aligner.models_dir()}" if used else f"Saved in {aligner.models_dir()}")

    def _show_model(self, progress: tuple[int, int] | None = None) -> None:
        """The action area for the model in the dropdown."""
        m, mgr = self.model(), self.manager
        have = aligner.installed(m.id)
        in_use = have and self.settings["align_model"] == m.id
        size = aligner.disk_size(m.id) if have else m.size_mb * 1_000_000
        detail = f"{m.note} · {cache.human(size)}"
        self.delete.setVisible(have and mgr.active != m.id)
        self.delete.setAccessibleName(f"Delete {m.name}")
        self.action.setEnabled(True)
        if mgr.active == m.id:
            done, total = progress or (0, m.size_mb * 1_000_000 >> 20)
            detail += f" · Downloading {int(100 * done / max(1, total))} % ({done} / {total} MB)"
            self.action.setText("Cancel")
        elif in_use:
            self.action.setText("In Use")
            self.action.setEnabled(False)
        elif have:
            self.action.setText("Use")
        else:
            self.action.setText("Download")
            self.action.setEnabled(mgr.active is None and aligner.engine_available())
        self.detail.setText(detail)
        self.picker.update()
