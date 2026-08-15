"""Tests for tagteam.notify (Phase 32): per-platform dispatch, fallbacks,
never-raise, TAGTEAM_NO_NOTIFY."""
from unittest.mock import patch, MagicMock

import pytest

from tagteam import notify as n


def _ok(*a, **k):
    m = MagicMock(); m.returncode = 0; return m


def _fail(*a, **k):
    m = MagicMock(); m.returncode = 1; return m


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("TAGTEAM_NO_NOTIFY", raising=False)


def test_darwin_uses_osascript(monkeypatch):
    monkeypatch.setattr(n.sys, "platform", "darwin")
    with patch("tagteam.notify.subprocess.run", side_effect=_ok) as run:
        assert n.notify("Tagteam", 'hi "there"') is True
    argv = run.call_args[0][0]
    assert argv[0] == "osascript" and 'with title "Tagteam"' in argv[2]
    assert '\\"there\\"' in argv[2]


def test_win32_toast_then_msg_fallback(monkeypatch):
    monkeypatch.setattr(n.sys, "platform", "win32")
    monkeypatch.setattr(n.shutil, "which",
                        lambda name: {"powershell": "C:/ps.exe", "msg": "C:/msg.exe"}.get(name))
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        return _fail() if argv[0] == "C:/ps.exe" else _ok()
    with patch("tagteam.notify.subprocess.run", side_effect=run):
        assert n.notify("T", "M") is True
    assert calls[0][0] == "C:/ps.exe" and "-NonInteractive" in calls[0]
    assert calls[1][0] == "C:/msg.exe" and calls[1][2] == "T: M"


def test_win32_toast_success_skips_msg(monkeypatch):
    monkeypatch.setattr(n.sys, "platform", "win32")
    monkeypatch.setattr(n.shutil, "which", lambda name: "C:/ps.exe" if name == "powershell" else None)
    with patch("tagteam.notify.subprocess.run", side_effect=_ok) as run:
        assert n.notify("T", "M") is True
    assert run.call_count == 1
    assert run.call_args[1]["env"]["TAGTEAM_TOAST_TITLE"] == "T"


def test_win32_no_backends(monkeypatch):
    monkeypatch.setattr(n.sys, "platform", "win32")
    monkeypatch.setattr(n.shutil, "which", lambda name: None)
    with patch("tagteam.notify.subprocess.run", side_effect=_ok) as run:
        assert n.notify("T", "M") is False
    run.assert_not_called()


def test_linux_notify_send(monkeypatch):
    monkeypatch.setattr(n.sys, "platform", "linux")
    monkeypatch.setattr(n.shutil, "which", lambda name: "/usr/bin/notify-send" if name == "notify-send" else None)
    with patch("tagteam.notify.subprocess.run", side_effect=_ok) as run:
        assert n.notify("T", "M") is True
    assert run.call_args[0][0] == ["/usr/bin/notify-send", "T", "M"]


def test_never_raises(monkeypatch):
    monkeypatch.setattr(n.sys, "platform", "darwin")
    with patch("tagteam.notify.subprocess.run", side_effect=OSError("boom")):
        assert n.notify("T", "M") is False


def test_env_short_circuit(monkeypatch):
    monkeypatch.setenv("TAGTEAM_NO_NOTIFY", "1")
    with patch("tagteam.notify.subprocess.run") as run:
        assert n.notify("T", "M") is False
    run.assert_not_called()


def test_watcher_alias_dispatches(monkeypatch):
    from tagteam import watcher
    with patch("tagteam.notify.notify") as nn:
        watcher.notify_macos("A", "B")
    nn.assert_called_once_with("A", "B")


@pytest.mark.skipif(not __import__("os").environ.get("CI"),
                    reason="pops a real desktop notification; run on CI only")
def test_real_call_does_not_raise_and_returns_quickly(monkeypatch):
    """Runs the real backend on whatever platform CI is (headless runners
    may not render anything) — asserts only 'no exception, bounded time'."""
    import time
    t0 = time.monotonic()
    n.notify("Tagteam test", "notification backend smoke")
    assert time.monotonic() - t0 < n.TIMEOUT_S + 3
