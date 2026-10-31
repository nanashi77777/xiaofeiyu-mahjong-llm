"""Fresh-window, physical Windows clicks. No game APIs or packet writes."""
from __future__ import annotations

import ctypes
import sys
import threading
import time
from ctypes import wintypes
from dataclasses import dataclass


class InputDeferred(RuntimeError):
    pass


class TargetExpired(InputDeferred):
    """No press was sent; the same semantic target may be observed again."""


def windows_command_args(command):
    """Parse native quoting without executing the inspected command line."""
    shell, kernel = ctypes.windll.shell32, ctypes.windll.kernel32
    shell.CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    shell.CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [ctypes.c_void_p], ctypes.c_void_p
    count = ctypes.c_int()
    argv = shell.CommandLineToArgvW(command, ctypes.byref(count))
    if not argv:
        raise InputDeferred('browser process identity unavailable')
    try:
        return [argv[i] for i in range(count.value)]
    finally:
        kernel.LocalFree(argv)


@dataclass(frozen=True)
class Window:
    hwnd: int
    pid: int
    title: str


class WindowsMouse:
    def __init__(self):
        if sys.platform != "win32":
            raise RuntimeError("physical input requires Windows")
        self.user = ctypes.windll.user32
        for name, args, result in (
            ("GetForegroundWindow", [], wintypes.HWND),
            ("GetAncestor", [wintypes.HWND,wintypes.UINT], wintypes.HWND),
            ("GetWindowThreadProcessId", [wintypes.HWND,ctypes.POINTER(wintypes.DWORD)], wintypes.DWORD),
            ("IsWindow", [wintypes.HWND], wintypes.BOOL),
            ("IsWindowVisible", [wintypes.HWND], wintypes.BOOL),
            ("IsIconic", [wintypes.HWND], wintypes.BOOL),
            ("GetWindowTextW", [wintypes.HWND,wintypes.LPWSTR,ctypes.c_int], ctypes.c_int),
            ("GetClientRect", [wintypes.HWND,ctypes.POINTER(wintypes.RECT)], wintypes.BOOL),
            ("ClientToScreen", [wintypes.HWND,ctypes.POINTER(wintypes.POINT)], wintypes.BOOL),
            ("SendInput", [wintypes.UINT,ctypes.c_void_p,ctypes.c_int], wintypes.UINT),
        ):
            function = getattr(self.user,name)
            function.argtypes, function.restype = args,result
        self.user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
        self.stop = threading.Event()

    def windows(self):
        result = []
        callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def visit(hwnd, _):
            if self.user.IsWindowVisible(hwnd):
                buffer = ctypes.create_unicode_buffer(1024)
                self.user.GetWindowTextW(hwnd, buffer, len(buffer))
                if "雀魂" in buffer.value or "Mahjong Soul" in buffer.value:
                    pid = wintypes.DWORD()
                    self.user.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                    result.append(Window(hwnd, pid.value, buffer.value))
            return True

        self.user.EnumWindows(callback_type(visit), 0)
        return result

    def cursor(self):
        point = wintypes.POINT()
        self.user.GetCursorPos(ctypes.byref(point))
        return point.x, point.y

    def rect(self, window):
        pid = wintypes.DWORD()
        self.user.GetWindowThreadProcessId(window.hwnd, ctypes.byref(pid))
        if pid.value != window.pid or not self.user.IsWindow(window.hwnd) or self.user.IsIconic(window.hwnd):
            raise InputDeferred("bound window unavailable")
        rect = wintypes.RECT()
        self.user.GetClientRect(window.hwnd, ctypes.byref(rect))
        point = wintypes.POINT(0, 0)
        self.user.ClientToScreen(window.hwnd, ctypes.byref(point))
        return point.x, point.y, rect.right, rect.bottom

    def check(self, window, rect, point, observed_mono, deadline):
        if self.stop.is_set() or self.user.GetAsyncKeyState(0x1B) & 0x8000:
            self.stop.set()
            raise InputDeferred("input stopped")
        if self.rect(window) != tuple(rect) or self.user.GetForegroundWindow() != window.hwnd:
            raise InputDeferred("window moved or is not foreground")
        now = time.monotonic()
        if now >= deadline or now - observed_mono > 1.2:
            raise InputDeferred("layout expired")
        x, y = point
        left, top, width, height = rect
        if not (left < x < left + width and top < y < top + height):
            raise InputDeferred("target outside bound client")
        self.user.WindowFromPoint.argtypes = [wintypes.POINT]
        self.user.WindowFromPoint.restype = wintypes.HWND
        self.user.GetAncestor.restype = wintypes.HWND
        under = self.user.WindowFromPoint(wintypes.POINT(x, y))
        if self.user.GetAncestor(under, 2) != window.hwnd:
            raise InputDeferred("target is covered")

    def click(self, window, rect, point, observed_mono, deadline):
        self.check(window, rect, point, observed_mono, deadline)
        original = self.cursor()
        destination = tuple(map(int, point))

        class MouseInput(ctypes.Structure):
            _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG), ("mouseData", wintypes.DWORD),
                        ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class KeyboardInput(ctypes.Structure):
            _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                        ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

        class Union(ctypes.Union):
            _fields_ = [("mi", MouseInput), ("ki", KeyboardInput), ("padding", ctypes.c_byte * 32)]

        class Input(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("u", Union)]

        try:
            self.user.SetCursorPos(*destination)
            self.check(window, rect, destination, observed_mono, deadline)
            # One SendInput call preserves move-then-immediate-click semantics.
            down = Input(0, Union(mi=MouseInput(0, 0, 0, 0x0002, 0, 0)))
            up = Input(0, Union(mi=MouseInput(0, 0, 0, 0x0004, 0, 0)))
            if self.user.SendInput(1, ctypes.byref(down), ctypes.sizeof(Input)) != 1:
                raise InputDeferred("Windows rejected physical input")
            # Press immediately after movement. Unity polls the held state, so
            # down/up must cross a render frame rather than vanish in one batch.
            try:
                time.sleep(0.025)
            finally:
                if self.user.SendInput(1, ctypes.byref(up), ctypes.sizeof(Input)) != 1:
                    raise InputDeferred("Windows rejected mouse release")
            # WebGL clients sample pointer position on their render frame. Let
            # the already-sent click reach one frame before restoring the cursor.
            time.sleep(0.035)
            return time.monotonic()
        finally:
            if self.cursor() == destination:
                self.user.SetCursorPos(*original)
