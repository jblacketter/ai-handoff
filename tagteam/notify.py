"""
Cross-platform desktop notifications (Phase 32).

`notify(title, message)` is best-effort and never raises:

- macOS: `osascript -e 'display notification ...'` (unchanged from the
  pre-3.0 `watcher.notify_macos`).
- Windows: a PowerShell WinRT toast (`Windows.UI.Notifications`, no
  dependency); if PowerShell is unavailable or the toast script fails,
  fall back to `msg <user> "<title>: <message>"`.
- Linux / other: `notify-send` when it is on PATH.

`TAGTEAM_NO_NOTIFY=1` disables everything (CI, tests, quiet servers).
Every backend runs with a 5-second timeout.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

TIMEOUT_S = 5

_TOAST_PS = r'''
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$texts = $template.GetElementsByTagName('text')
$texts.Item(0).AppendChild($template.CreateTextNode($env:TAGTEAM_TOAST_TITLE)) | Out-Null
$texts.Item(1).AppendChild($template.CreateTextNode($env:TAGTEAM_TOAST_MESSAGE)) | Out-Null
$toast = [Windows.UI.Notifications.ToastNotification]::new($template)
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('Tagteam').Show($toast)
'''


def _run(argv: list[str], **kw) -> bool:
    try:
        r = subprocess.run(argv, capture_output=True, timeout=TIMEOUT_S, **kw)
        return r.returncode == 0
    except Exception:
        return False


def _osascript_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace('"', '\\"')


def notify_darwin(title: str, message: str) -> bool:
    return _run(["osascript", "-e",
                 f'display notification "{_osascript_escape(message)}" '
                 f'with title "{_osascript_escape(title)}"'])


def notify_win32(title: str, message: str) -> bool:
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if ps:
        env = dict(os.environ, TAGTEAM_TOAST_TITLE=title,
                   TAGTEAM_TOAST_MESSAGE=message)
        if _run([ps, "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS],
                env=env):
            return True
    msg = shutil.which("msg")
    if msg:
        user = os.environ.get("USERNAME") or "*"
        return _run([msg, user, f"{title}: {message}"])
    return False


def notify_linux(title: str, message: str) -> bool:
    ns = shutil.which("notify-send")
    if not ns:
        return False
    return _run([ns, title, message])


def notify(title: str, message: str) -> bool:
    """Send a desktop notification. Returns True if a backend reported
    success; False otherwise. Never raises."""
    if os.environ.get("TAGTEAM_NO_NOTIFY"):
        return False
    try:
        plat = sys.platform
        if plat == "darwin":
            return notify_darwin(title, message)
        if plat == "win32":
            return notify_win32(title, message)
        return notify_linux(title, message)
    except Exception:
        return False
