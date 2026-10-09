"""Track browser activity and stop the finance hub once it becomes idle.

``POST /api/heartbeat`` calls :meth:`IdleMonitor.beat` to reset the clock.
``start_idle_watcher`` polls the monitor from a background thread and stops
the server after heartbeats have been absent for too long.
"""
from __future__ import annotations

import threading
import time
from http.server import ThreadingHTTPServer
from typing import Callable


class IdleMonitor:
    def __init__(
        self, timeout_seconds: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._timeout_seconds = timeout_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._last_beat = clock()

    def beat(self) -> None:
        with self._lock:
            self._last_beat = self._clock()

    def is_stale(self) -> bool:
        with self._lock:
            return (self._clock() - self._last_beat) > self._timeout_seconds


def start_idle_watcher(
    server: ThreadingHTTPServer,
    idle_monitor: IdleMonitor,
    *,
    check_interval_seconds: float,
) -> threading.Event:
    """Stop *server* once *idle_monitor* is stale.

    Return an event that callers can set to stop the daemon thread early.
    """
    stop = threading.Event()

    def _watch() -> None:
        while not stop.is_set():
            if idle_monitor.is_stale():
                server.shutdown()
                return
            stop.wait(check_interval_seconds)

    threading.Thread(target=_watch, daemon=True).start()
    return stop
