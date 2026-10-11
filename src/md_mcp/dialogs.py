"""Which modal dialogs Marvelous Designer is showing, seen from outside MD.

A modal dialog holds MD's main thread, which is the thread serving the bridge, so while
one is up nothing gets an answer -- and some appear after a script has already reported
success (the cape session's "pattern boundary is intersected while meshing"). So this
looks from md-mcp's own process, with plain Win32 calls, and needs nothing from MD.

Measured on MD 2026.0.315 (2026-10-11), with the "abnormal seamlines" dialog that sewing
an edge to itself raises:

* the dialog is a visible top-level window of class ``Qt6103QWindow``, **owned by MD's
  main window** (``Qt6103QWindowIcon``) and titled the same as it;
* MD's main window is **disabled** while it is up, and enabled again once it closes --
  which is what makes it modal and what this keys on;
* its text is not readable: UI Automation exposes only a "Information" label and an OK
  button, not the message. So this names the dialog; it cannot quote it.

Pressing that OK button through UI Automation from another process closed the dialog
and let the blocked request finish -- not done here: dismissing a dialog is a decision.
"""

from __future__ import annotations

import ctypes
import sys

#: MD's main window title starts with this (Enterprise Offline: "Marvelous Designer
#: Enterprise Offline Annual"); its dialogs carry the same title.
MD_TITLE = "Marvelous Designer"

_GW_OWNER = 4


def showing() -> list[dict]:
    """Every modal dialog an MD window is showing: title, class and owner. [] if none.

    Never raises: a check that cannot be made is no dialog, which leaves md-mcp as it
    was before this existed. Off Windows there is nothing to look at.
    """
    if sys.platform != "win32":
        return []
    try:
        return _showing()
    except (OSError, AttributeError, ValueError):
        return []


def _showing() -> list[dict]:
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    enum_proc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user32.EnumWindows.argtypes = [enum_proc, wintypes.LPARAM]
    user32.IsWindowVisible.argtypes = [wintypes.HWND]
    user32.IsWindowEnabled.argtypes = [wintypes.HWND]
    user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
    user32.GetWindow.restype = wintypes.HWND
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]

    def text(hwnd) -> str:
        buffer = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buffer, 512)
        return buffer.value

    def class_name(hwnd) -> str:
        buffer = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buffer, 256)
        return buffer.value

    windows = []

    def collect(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            windows.append(hwnd)
        return True

    user32.EnumWindows(enum_proc(collect), 0)

    found = []
    for hwnd in windows:
        owner = user32.GetWindow(hwnd, _GW_OWNER)
        if not owner or user32.IsWindowEnabled(owner):
            continue
        owner_title = text(owner)
        if not owner_title.startswith(MD_TITLE):
            continue
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        found.append(
            {
                "title": text(hwnd),
                "class": class_name(hwnd),
                "owner": owner_title,
                "pid": pid.value,
            }
        )
    return found


def describe(found: list[dict]) -> str:
    """One sentence for a reply: what MD is showing, and what to do about it."""
    names = ", ".join(sorted({item["class"] for item in found})) or "a dialog"
    return (
        f"Marvelous Designer is showing a modal dialog ({names}). Its text cannot be "
        "read from outside MD, so look at MD: the dialog holds MD's main thread, nothing "
        "reaches MD until somebody dismisses it, and whatever raised it may not have "
        "done what its reply said."
    )
