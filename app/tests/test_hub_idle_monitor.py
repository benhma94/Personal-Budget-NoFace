"""Unit tests for IdleMonitor and start_idle_watcher: a fake clock for the
pure staleness logic, and a fake server double plus short real sleeps for
the watcher-thread wiring. No real sockets."""
from __future__ import annotations

import time

from finance_hub.idle_monitor import IdleMonitor, start_idle_watcher


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class _FakeServer:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def test_not_stale_before_timeout():
    clock = _FakeClock()
    monitor = IdleMonitor(timeout_seconds=60, clock=clock)
    clock.now += 30
    assert monitor.is_stale() is False


def test_stale_after_timeout():
    clock = _FakeClock()
    monitor = IdleMonitor(timeout_seconds=60, clock=clock)
    clock.now += 61
    assert monitor.is_stale() is True


def test_not_yet_stale_at_exactly_the_timeout():
    clock = _FakeClock()
    monitor = IdleMonitor(timeout_seconds=60, clock=clock)
    clock.now += 60
    assert monitor.is_stale() is False


def test_beat_resets_staleness():
    clock = _FakeClock()
    monitor = IdleMonitor(timeout_seconds=60, clock=clock)
    clock.now += 50
    monitor.beat()
    clock.now += 50
    assert monitor.is_stale() is False


def test_watcher_calls_server_shutdown_once_idle_monitor_goes_stale():
    server = _FakeServer()
    idle_monitor = IdleMonitor(timeout_seconds=0.05)
    stop = start_idle_watcher(server, idle_monitor, check_interval_seconds=0.01)
    try:
        time.sleep(0.2)
        assert server.shutdown_calls == 1
    finally:
        stop.set()


def test_watcher_does_not_shut_down_while_beats_keep_arriving():
    server = _FakeServer()
    idle_monitor = IdleMonitor(timeout_seconds=0.05)
    stop = start_idle_watcher(server, idle_monitor, check_interval_seconds=0.01)
    try:
        for _ in range(4):
            time.sleep(0.03)
            idle_monitor.beat()
        assert server.shutdown_calls == 0
    finally:
        stop.set()
