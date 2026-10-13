"""One background process of its own, at idle priority — for work too heavy for a thread.

Resolving streams (yt-dlp), decoding audio and running a speech model are Python- and
CPU-heavy; in a thread they would compete with the interface for the interpreter lock and make
it stutter. Each :class:`IdleWorker` starts one spawned process on first use (only spare CPU —
and the helpers it starts inherit that), keeps it for the next job, and can be shut down (e.g.
to release a model's files). No Qt here: the worker process imports this module too.
"""

from __future__ import annotations

import os


def idle_priority() -> None:
    """Runs first in the worker process."""
    if os.name == "nt":
        import ctypes
        k32 = ctypes.windll.kernel32
        k32.SetPriorityClass(k32.GetCurrentProcess(), 0x00000040)       # IDLE_PRIORITY_CLASS


class IdleWorker:
    def __init__(self):
        self._executor = None

    def pool(self):
        """The process pool (one process), started on first use."""
        if self._executor is None:
            import multiprocessing
            from concurrent.futures import ProcessPoolExecutor
            self._executor = ProcessPoolExecutor(1, mp_context=multiprocessing.get_context("spawn"),
                                                 initializer=idle_priority)
        return self._executor

    def shutdown(self) -> None:
        """Stop the process now (whatever it holds open is released); the next job starts a new one."""
        if self._executor is None:
            return
        procs = list(getattr(self._executor, "_processes", {}).values())
        self._executor.shutdown(wait=False, cancel_futures=True)
        for proc in procs:
            try:
                proc.terminate()
            except Exception:
                pass
        self._executor = None
