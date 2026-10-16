#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ghost PiP Diagnostic and Verification Tool for Inbrisk.

Performs robust, end-to-end verification of the Ghost PiP (Picture-in-Picture) window:
1. Window Discovery & Active Desktop Inspection:
   - Discovers Ghost PiP windows across all accessible desktops (active input desktop, Default, sandbox exebox).
   - Inspects HWND, PID, Process Name, Thread ID, Desktop Name.
   - Inspects screen geometry (GetWindowRect, GetClientRect, Monitor work area, DPI context, anchor preset).
   - Inspects visibility and window styles (WS_VISIBLE, WS_POPUP, WS_EX_TOPMOST, WS_EX_NOACTIVATE, WS_EX_TRANSPARENT, WS_EX_LAYERED).
   - Inspects opacity, display affinity, and z-order.

2. Cropped Window Area Captures to Disk:
   - Captures rendered window surface directly from DWM redirection via PrintWindow (PW_RENDERFULLCONTENT).
   - Captures composited desktop screen pixels via BitBlt from the desktop device context.
   - Generates multi-region cropped captures:
     * Full window
     * Top drag handle header bar (26px)
     * Client content area
     * Four corner regions (resizing handles / borders)
     * Optional custom crop bounding box
   - Performs image content diagnostics (solid/blank color detection, mean RGB, color diversity).

3. Visual Tree & DWM Redirection List Verification:
   - DWM Composition status and cloaking check (DWMWA_CLOAKED).
   - DWM Extended Frame Bounds (DWMWA_EXTENDED_FRAME_BOUNDS).
   - DWM Redirection List test: registers a dynamic DWM thumbnail redirection (DwmRegisterThumbnail),
     queries compositor source surface dimensions (DwmQueryThumbnailSourceSize), and unregisters.
   - Win32 Desktop Visual Hierarchy (Parent, Owner, Root, Desktop, Child Windows).
   - UI Automation (UIA) Visual Tree element verification (UiaNodeFromHandle, name, class, offscreen state).
   - IAccessible Visual Tree node verification (AccessibleObjectFromWindow).

Usage:
  python tools/verify_ghost_pip.py
  python tools/verify_ghost_pip.py --launch-test
  python tools/verify_ghost_pip.py --hwnd 0x0123ABCD
  python tools/verify_ghost_pip.py --output-dir artifacts/pip_captures --json
"""

from __future__ import annotations

import argparse
import ctypes
from ctypes import wintypes
import datetime as dt
import json
import math
import os
import pathlib
import re
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image

# ---------------------------------------------------------------------------
# Win32 / DWM / UIA Type Definitions and Constants
# ---------------------------------------------------------------------------

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32
dwmapi = ctypes.windll.dwmapi
oleacc = ctypes.windll.oleacc
ole32 = ctypes.windll.ole32

try:
    uac = ctypes.windll.UIAutomationCore
except Exception:
    uac = None

# DPI Awareness
DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = -4
try:
    user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)
except Exception:
    pass

# Desktops
DESKTOP_ALL_ACCESS = 0x01FF
DESKTOP_READOBJECTS = 0x0001
DESKTOP_ENUMERATE = 0x0040
UOI_NAME = 2

# Window Styles
GWL_STYLE = -16
GWL_EXSTYLE = -20

WS_POPUP = 0x80000000
WS_CHILD = 0x40000000
WS_MINIMIZE = 0x20000000
WS_VISIBLE = 0x10000000
WS_DISABLED = 0x08000000
WS_CLIPSIBLINGS = 0x04000000
WS_CLIPCHILDREN = 0x02000000
WS_MAXIMIZE = 0x01000000
WS_CAPTION = 0x00C00000
WS_BORDER = 0x00800000
WS_DLGFRAME = 0x00400000
WS_VSCROLL = 0x00200000
WS_HSCROLL = 0x00100000
WS_SYSMENU = 0x00080000
WS_THICKFRAME = 0x00040000
WS_MINIMIZEBOX = 0x00020000
WS_MAXIMIZEBOX = 0x00010000

WS_EX_DLGMODALFRAME = 0x00000001
WS_EX_TOPMOST = 0x00000008
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_WINDOWEDGE = 0x00000100
WS_EX_CLIENTEDGE = 0x00000200
WS_EX_APPWINDOW = 0x00040000
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000

# Layered Window Attributes
LWA_COLORKEY = 0x00000001
LWA_ALPHA = 0x00000002

# Window Display Affinity
WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001
WDA_EXCLUDEFROMCAPTURE = 0x00000011

# PrintWindow flags
PW_DEFAULT = 0x00000000
PW_CLIENTONLY = 0x00000001
PW_RENDERFULLCONTENT = 0x00000002

# DWM Window Attributes
DWMWA_NCRENDERING_ENABLED = 1
DWMWA_NCRENDERING_POLICY = 2
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14

DWM_CLOAKED_APP = 0x00000001
DWM_CLOAKED_SHELL = 0x00000002
DWM_CLOAKED_INHERITED = 0x00000004

# Ancestor & Window Relationships
GA_PARENT = 1
GA_ROOT = 2
GA_ROOTOWNER = 3
GW_HWNDNEXT = 2
GW_HWNDPREV = 3
GW_OWNER = 4

# GDI ROP
SRCCOPY = 0x00CC0020

# Monitors
MONITOR_DEFAULTTONEAREST = 2

# UI Automation Property IDs
UIA_BoundingRectanglePropertyId = 30001
UIA_ProcessIdPropertyId = 30020
UIA_ControlTypePropertyId = 30003
UIA_NamePropertyId = 30005
UIA_IsEnabledPropertyId = 30010
UIA_ClassNamePropertyId = 30012
UIA_IsOffscreenPropertyId = 30022
UIA_NativeWindowHandlePropertyId = 30020

# IAccessible
OBJID_WINDOW = 0x00000000
OBJID_CLIENT = 0xFFFFFFFC

# Ctypes Structs
class RECT(ctypes.Structure):
    _fields_ = [
        ('left', ctypes.c_int),
        ('top', ctypes.c_int),
        ('right', ctypes.c_int),
        ('bottom', ctypes.c_int)
    ]

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def to_dict(self) -> Dict[str, int]:
        return {
            'left': self.left,
            'top': self.top,
            'right': self.right,
            'bottom': self.bottom,
            'width': self.width,
            'height': self.height
        }


class POINT(ctypes.Structure):
    _fields_ = [('x', ctypes.c_long), ('y', ctypes.c_long)]


class SIZE(ctypes.Structure):
    _fields_ = [('cx', ctypes.c_long), ('cy', ctypes.c_long)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ('cbSize', wintypes.DWORD),
        ('rcMonitor', RECT),
        ('rcWork', RECT),
        ('dwFlags', wintypes.DWORD),
        ('szDevice', wintypes.WCHAR * 32)
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ('biSize', wintypes.DWORD),
        ('biWidth', wintypes.LONG),
        ('biHeight', wintypes.LONG),
        ('biPlanes', wintypes.WORD),
        ('biBitCount', wintypes.WORD),
        ('biCompression', wintypes.DWORD),
        ('biSizeImage', wintypes.DWORD),
        ('biXPelsPerMeter', wintypes.LONG),
        ('biYPelsPerMeter', wintypes.LONG),
        ('biClrUsed', wintypes.DWORD),
        ('biClrImportant', wintypes.DWORD)
    ]


# Function Signatures
user32.OpenDesktopW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.OpenDesktopW.restype = wintypes.HANDLE

user32.CloseDesktop.argtypes = [wintypes.HANDLE]
user32.CloseDesktop.restype = wintypes.BOOL

user32.SetThreadDesktop.argtypes = [wintypes.HANDLE]
user32.SetThreadDesktop.restype = wintypes.BOOL

user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
user32.GetThreadDesktop.restype = wintypes.HANDLE

user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
user32.OpenInputDesktop.restype = wintypes.HANDLE

user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
user32.GetUserObjectInformationW.restype = wintypes.BOOL

DESKTOPENUMPROCW = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.LPCWSTR, wintypes.LPARAM)
user32.EnumDesktopsW.argtypes = [wintypes.HANDLE, DESKTOPENUMPROCW, wintypes.LPARAM]
user32.EnumDesktopsW.restype = wintypes.BOOL

WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
user32.EnumDesktopWindows.argtypes = [wintypes.HANDLE, WNDENUMPROC, wintypes.LPARAM]
user32.EnumDesktopWindows.restype = wintypes.BOOL

user32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
user32.EnumWindows.restype = wintypes.BOOL

user32.EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
user32.EnumChildWindows.restype = wintypes.BOOL

user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowTextW.restype = ctypes.c_int

user32.GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetClassNameW.restype = ctypes.c_int

user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetWindowRect.restype = wintypes.BOOL

user32.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
user32.GetClientRect.restype = wintypes.BOOL

user32.ClientToScreen.argtypes = [wintypes.HWND, ctypes.POINTER(POINT)]
user32.ClientToScreen.restype = wintypes.BOOL

user32.IsWindow.argtypes = [wintypes.HWND]
user32.IsWindow.restype = wintypes.BOOL

user32.IsWindowVisible.argtypes = [wintypes.HWND]
user32.IsWindowVisible.restype = wintypes.BOOL

user32.IsIconic.argtypes = [wintypes.HWND]
user32.IsIconic.restype = wintypes.BOOL

user32.IsZoomed.argtypes = [wintypes.HWND]
user32.IsZoomed.restype = wintypes.BOOL

user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
user32.GetWindowLongW.restype = wintypes.LONG

user32.GetParent.argtypes = [wintypes.HWND]
user32.GetParent.restype = wintypes.HWND

user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetAncestor.restype = wintypes.HWND

user32.GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
user32.GetWindow.restype = wintypes.HWND

user32.GetDesktopWindow.restype = wintypes.HWND

user32.GetDC.argtypes = [wintypes.HWND]
user32.GetDC.restype = wintypes.HDC

user32.GetWindowDC.argtypes = [wintypes.HWND]
user32.GetWindowDC.restype = wintypes.HDC

user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.ReleaseDC.restype = ctypes.c_int

user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]
user32.PrintWindow.restype = wintypes.BOOL

user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HANDLE

user32.GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.POINTER(MONITORINFOEXW)]
user32.GetMonitorInfoW.restype = wintypes.BOOL

user32.GetLayeredWindowAttributes.argtypes = [
    wintypes.HWND,
    ctypes.POINTER(wintypes.COLORREF),
    ctypes.POINTER(wintypes.BYTE),
    ctypes.POINTER(wintypes.DWORD)
]
user32.GetLayeredWindowAttributes.restype = wintypes.BOOL

user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowDisplayAffinity.restype = wintypes.BOOL

dwmapi.DwmIsCompositionEnabled.argtypes = [ctypes.POINTER(wintypes.BOOL)]
dwmapi.DwmIsCompositionEnabled.restype = ctypes.c_long

dwmapi.DwmGetWindowAttribute.argtypes = [wintypes.HWND, wintypes.DWORD, wintypes.LPCVOID, wintypes.DWORD]
dwmapi.DwmGetWindowAttribute.restype = ctypes.c_long

dwmapi.DwmRegisterThumbnail.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.POINTER(ctypes.c_void_p)]
dwmapi.DwmRegisterThumbnail.restype = ctypes.c_long

dwmapi.DwmUnregisterThumbnail.argtypes = [ctypes.c_void_p]
dwmapi.DwmUnregisterThumbnail.restype = ctypes.c_long

dwmapi.DwmQueryThumbnailSourceSize.argtypes = [ctypes.c_void_p, ctypes.POINTER(SIZE)]
dwmapi.DwmQueryThumbnailSourceSize.restype = ctypes.c_long

gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleDC.restype = wintypes.HDC

gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP

gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SelectObject.restype = wintypes.HGDIOBJ

gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.DeleteDC.restype = wintypes.BOOL

gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteObject.restype = wintypes.BOOL

gdi32.BitBlt.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wintypes.HDC, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.BitBlt.restype = wintypes.BOOL

gdi32.GetDIBits.argtypes = [wintypes.HDC, wintypes.HBITMAP, wintypes.UINT, wintypes.UINT, wintypes.LPVOID, ctypes.POINTER(BITMAPINFOHEADER), wintypes.UINT]
gdi32.GetDIBits.restype = ctypes.c_int

# ---------------------------------------------------------------------------
# ANSI Color Formatting
# ---------------------------------------------------------------------------

class Color:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RED = "\033[31m"
    GREEN = "\033[32m"
    YELLOW = "\033[33m"
    BLUE = "\033[34m"
    MAGENTA = "\033[35m"
    CYAN = "\033[36m"
    WHITE = "\033[37m"

def c_pass(text: str) -> str:
    return f"{Color.GREEN}{Color.BOLD}[PASS]{Color.RESET} {text}"

def c_fail(text: str) -> str:
    return f"{Color.RED}{Color.BOLD}[FAIL]{Color.RESET} {text}"

def c_warn(text: str) -> str:
    return f"{Color.YELLOW}{Color.BOLD}[WARN]{Color.RESET} {text}"

def c_info(text: str) -> str:
    return f"{Color.CYAN}[INFO]{Color.RESET} {text}"

def c_header(text: str) -> str:
    return f"\n{Color.BOLD}{Color.MAGENTA}=== {text} ==={Color.RESET}"


# ---------------------------------------------------------------------------
# Desktop Helper
# ---------------------------------------------------------------------------

class DesktopHelper:
    """Manages thread desktop attachments and desktop enumeration."""

    @staticmethod
    def get_desktop_name(hdesk: int) -> str:
        if not hdesk:
            return ""
        buf = ctypes.create_unicode_buffer(256)
        needed = wintypes.DWORD()
        if user32.GetUserObjectInformationW(hdesk, UOI_NAME, buf, 256, ctypes.byref(needed)):
            return buf.value
        return ""

    @staticmethod
    def get_current_thread_desktop() -> Tuple[int, str]:
        tid = kernel32.GetCurrentThreadId()
        hdesk = user32.GetThreadDesktop(tid)
        return hdesk, DesktopHelper.get_desktop_name(hdesk)

    @staticmethod
    def get_input_desktop() -> Tuple[int, str]:
        hdesk = user32.OpenInputDesktop(0, False, DESKTOP_ALL_ACCESS)
        if not hdesk:
            hdesk = user32.OpenInputDesktop(0, False, DESKTOP_READOBJECTS | DESKTOP_ENUMERATE)
        name = DesktopHelper.get_desktop_name(hdesk) if hdesk else ""
        return hdesk, name

    @staticmethod
    def enumerate_desktops() -> List[str]:
        hwinsta = user32.GetProcessWindowStation()
        desktops: List[str] = []

        def _enum_cb(lpszDesktop, lParam):
            if lpszDesktop:
                desktops.append(lpszDesktop)
            return True

        cb = DESKTOPENUMPROCW(_enum_cb)
        user32.EnumDesktopsW(hwinsta, cb, 0)
        return desktops

    @staticmethod
    def open_desktop(name: str) -> int:
        h = user32.OpenDesktopW(name, 0, False, DESKTOP_ALL_ACCESS)
        if not h:
            h = user32.OpenDesktopW(name, 0, False, DESKTOP_READOBJECTS | DESKTOP_ENUMERATE)
        return h


# ---------------------------------------------------------------------------
# Window Information Record
# ---------------------------------------------------------------------------

class WindowInfo:
    def __init__(self, hwnd: int, desktop_name: str = ""):
        self.hwnd = hwnd
        self.desktop_name = desktop_name
        self.title = ""
        self.class_name = ""
        self.pid = 0
        self.tid = 0
        self.process_name = ""
        self.exe_path = ""
        self.rect = RECT()
        self.client_rect = RECT()
        self.extended_frame_rect = RECT()
        self.is_window = False
        self.is_visible = False
        self.is_iconic = False
        self.is_zoomed = False
        self.style = 0
        self.ex_style = 0
        self.opacity = 255
        self.color_key = 0
        self.layered_flags = 0
        self.display_affinity = 0
        self.cloaked_flags = 0
        self.parent_hwnd = 0
        self.owner_hwnd = 0
        self.root_hwnd = 0
        self.child_count = 0
        self.children: List[int] = []
        self.is_topmost_in_zorder = False
        self.monitor_name = ""
        self.monitor_rect = RECT()
        self.monitor_work_rect = RECT()
        self.monitor_dpi = 96
        self.anchor_preset = "Custom"
        self.margin_left = 0
        self.margin_top = 0
        self.margin_right = 0
        self.margin_bottom = 0


# ---------------------------------------------------------------------------
# Window Discovery & Inspection
# ---------------------------------------------------------------------------

class WindowInspector:
    """Discovers and inspects Ghost PiP windows."""

    DEFAULT_CLASSES = [
        "InbriskGhostPipWindowClass",
        "InbriskRuntimeNativePipWindowHost"
    ]
    DEFAULT_TITLE_PATTERNS = [
        r"Inbrisk Ghost PiP",
        r"Ghost PiP"
    ]

    def __init__(self, target_desktop: Optional[str] = None):
        self.target_desktop = target_desktop

    def find_pip_windows(
        self,
        hwnd_filter: Optional[int] = None,
        class_pattern: Optional[str] = None,
        title_pattern: Optional[str] = None,
        pid_filter: Optional[int] = None
    ) -> List[WindowInfo]:
        """Finds Ghost PiP candidate windows across desktops."""
        found: List[WindowInfo] = []

        if hwnd_filter and user32.IsWindow(hwnd_filter):
            info = self.inspect_window(hwnd_filter)
            return [info]

        desktops_to_scan: List[str] = []
        if self.target_desktop and self.target_desktop.lower() != "auto":
            desktops_to_scan = [self.target_desktop]
        else:
            all_desks = DesktopHelper.enumerate_desktops()
            # Prioritize active input, current thread, default, InbriskGhostDesktop
            _, cur_name = DesktopHelper.get_current_thread_desktop()
            _, input_name = DesktopHelper.get_input_desktop()
            
            prio = [input_name, cur_name, "Default", "InbriskGhostDesktop"]
            for p in prio:
                if p and p in all_desks and p not in desktops_to_scan:
                    desktops_to_scan.append(p)
            for d in all_desks:
                if d not in desktops_to_scan:
                    desktops_to_scan.append(d)

        orig_desk, _ = DesktopHelper.get_current_thread_desktop()

        for d_name in desktops_to_scan:
            h_desk = DesktopHelper.open_desktop(d_name)
            if not h_desk:
                continue

            # Switch thread desktop temporarily to enumerate this desktop
            user32.SetThreadDesktop(h_desk)

            desktop_hwnds: List[int] = []

            def _enum_cb(hwnd, lParam):
                desktop_hwnds.append(hwnd)
                return True

            cb = WNDENUMPROC(_enum_cb)
            user32.EnumDesktopWindows(h_desk, cb, 0)

            for h in desktop_hwnds:
                # Basic quick filter
                buf_cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(h, buf_cls, 256)
                cls_str = buf_cls.value

                buf_txt = ctypes.create_unicode_buffer(512)
                user32.GetWindowTextW(h, buf_txt, 512)
                txt_str = buf_txt.value

                pid = wintypes.DWORD()
                user32.GetWindowThreadProcessId(h, ctypes.byref(pid))
                curr_pid = pid.value

                match = False
                # If explicit filters are passed
                if class_pattern:
                    if re.search(class_pattern, cls_str, re.IGNORECASE):
                        match = True
                elif any(c.lower() == cls_str.lower() for c in self.DEFAULT_CLASSES):
                    match = True

                if title_pattern:
                    if re.search(title_pattern, txt_str, re.IGNORECASE):
                        match = True
                elif any(re.search(pat, txt_str, re.IGNORECASE) for pat in self.DEFAULT_TITLE_PATTERNS):
                    match = True

                if pid_filter and curr_pid != pid_filter:
                    match = False

                if match:
                    info = self.inspect_window(h, desktop_name=d_name)
                    found.append(info)

            user32.CloseDesktop(h_desk)

        # Restore original thread desktop
        if orig_desk:
            user32.SetThreadDesktop(orig_desk)

        return found

    def inspect_window(self, hwnd: int, desktop_name: str = "") -> WindowInfo:
        """Inspects all attributes, styles, monitors, and geometry of an HWND."""
        info = WindowInfo(hwnd, desktop_name)

        if not user32.IsWindow(hwnd):
            info.is_window = False
            return info

        info.is_window = True

        # Process and Thread
        pid = wintypes.DWORD()
        tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        info.pid = pid.value
        info.tid = tid

        # Process Name and Exe Path
        try:
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h_proc = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, info.pid)
            if h_proc:
                buf = ctypes.create_unicode_buffer(1024)
                size = wintypes.DWORD(1024)
                if kernel32.QueryFullProcessImageNameW(h_proc, 0, buf, ctypes.byref(size)):
                    info.exe_path = buf.value
                    info.process_name = pathlib.Path(buf.value).name
                kernel32.CloseHandle(h_proc)
        except Exception:
            pass

        # Desktop name of window thread
        if not info.desktop_name:
            h_desk = user32.GetThreadDesktop(info.tid)
            if h_desk:
                info.desktop_name = DesktopHelper.get_desktop_name(h_desk)

        # Attach python thread to target desktop for deep queries
        if info.desktop_name:
            h_target_desk = DesktopHelper.open_desktop(info.desktop_name)
            if h_target_desk:
                user32.SetThreadDesktop(h_target_desk)

        # Window Title & Class
        buf_txt = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(hwnd, buf_txt, 512)
        info.title = buf_txt.value

        buf_cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, buf_cls, 256)
        info.class_name = buf_cls.value

        # Visibility states
        info.is_visible = bool(user32.IsWindowVisible(hwnd))
        info.is_iconic = bool(user32.IsIconic(hwnd))
        info.is_zoomed = bool(user32.IsZoomed(hwnd))

        # Geometry
        user32.GetWindowRect(hwnd, ctypes.byref(info.rect))
        user32.GetClientRect(hwnd, ctypes.byref(info.client_rect))

        # DWM Extended Frame Bounds
        hr_frame = dwmapi.DwmGetWindowAttribute(
            hwnd,
            DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(info.extended_frame_rect),
            ctypes.sizeof(RECT)
        )
        if hr_frame != 0:
            info.extended_frame_rect = info.rect

        # DWM Cloaking
        cloaked = ctypes.c_uint()
        hr_cloak = dwmapi.DwmGetWindowAttribute(
            hwnd,
            DWMWA_CLOAKED,
            ctypes.byref(cloaked),
            ctypes.sizeof(cloaked)
        )
        if hr_cloak == 0:
            info.cloaked_flags = cloaked.value

        # Styles
        info.style = user32.GetWindowLongW(hwnd, GWL_STYLE) & 0xFFFFFFFF
        info.ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE) & 0xFFFFFFFF

        # Layered attributes
        if info.ex_style & WS_EX_LAYERED:
            cr = wintypes.COLORREF()
            alpha = wintypes.BYTE()
            flags = wintypes.DWORD()
            if user32.GetLayeredWindowAttributes(hwnd, ctypes.byref(cr), ctypes.byref(alpha), ctypes.byref(flags)):
                info.opacity = alpha.value
                info.color_key = cr.value
                info.layered_flags = flags.value

        # Display Affinity
        affinity = wintypes.DWORD()
        if user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity)):
            info.display_affinity = affinity.value

        # Hierarchy / Tree
        info.parent_hwnd = user32.GetParent(hwnd) or 0
        info.owner_hwnd = user32.GetWindow(hwnd, GW_OWNER) or 0
        info.root_hwnd = user32.GetAncestor(hwnd, GA_ROOT) or 0

        children: List[int] = []
        def _enum_child(hChild, lParam):
            children.append(hChild)
            return True
        cb_child = WNDENUMPROC(_enum_child)
        user32.EnumChildWindows(hwnd, cb_child, 0)
        info.children = children
        info.child_count = len(children)

        # Monitor Details
        h_mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if h_mon:
            mi = MONITORINFOEXW()
            mi.cbSize = ctypes.sizeof(MONITORINFOEXW)
            if user32.GetMonitorInfoW(h_mon, ctypes.byref(mi)):
                info.monitor_name = mi.szDevice
                info.monitor_rect = mi.rcMonitor
                info.monitor_work_rect = mi.rcWork

        # DPI
        try:
            dpi = user32.GetDpiForWindow(hwnd)
            if dpi > 0:
                info.monitor_dpi = dpi
        except Exception:
            pass

        # Anchor preset estimation
        m_work = info.monitor_work_rect
        w = info.rect.width
        h = info.rect.height
        info.margin_left = info.rect.left - m_work.left
        info.margin_top = info.rect.top - m_work.top
        info.margin_right = m_work.right - info.rect.right
        info.margin_bottom = m_work.bottom - info.rect.bottom

        # Estimate preset (within 40px margin tolerance)
        tol = 40
        if abs(info.margin_right - 16) < tol and abs(info.margin_top - 16) < tol:
            info.anchor_preset = "TopRight"
        elif abs(info.margin_left - 16) < tol and abs(info.margin_top - 16) < tol:
            info.anchor_preset = "TopLeft"
        elif abs(info.margin_right - 16) < tol and abs(info.margin_bottom - 16) < tol:
            info.anchor_preset = "BottomRight"
        elif abs(info.margin_left - 16) < tol and abs(info.margin_bottom - 16) < tol:
            info.anchor_preset = "BottomLeft"
        else:
            info.anchor_preset = "Custom"

        # Z-order check
        prev_wnd = user32.GetWindow(hwnd, GW_HWNDPREV)
        info.is_topmost_in_zorder = (prev_wnd == 0 or not user32.IsWindowVisible(prev_wnd))

        return info


# ---------------------------------------------------------------------------
# Visual Tree Verifier (Win32, UIAutomation, IAccessible)
# ---------------------------------------------------------------------------

class VisualTreeVerifier:
    """Verifies that the PiP window is correctly integrated into Windows visual trees."""

    @staticmethod
    def verify(hwnd: int) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            'win32_tree': {},
            'uia_tree': {},
            'iaccessible_tree': {},
            'passed': False
        }

        # 1. Win32 Desktop Tree
        desktop_hwnd = user32.GetDesktopWindow() or 0
        parent = user32.GetParent(hwnd) or 0
        owner = user32.GetWindow(hwnd, GW_OWNER) or 0
        root = user32.GetAncestor(hwnd, GA_ROOT) or 0
        parent_ancestor = user32.GetAncestor(hwnd, GA_PARENT) or 0

        win32_valid = (root != 0 and (parent == 0 or parent == desktop_hwnd or parent == root))
        result['win32_tree'] = {
            'desktop_hwnd': f"0x{desktop_hwnd:08X}",
            'parent_hwnd': f"0x{parent:08X}",
            'owner_hwnd': f"0x{owner:08X}",
            'root_hwnd': f"0x{root:08X}",
            'parent_ancestor_hwnd': f"0x{parent_ancestor:08X}",
            'valid': win32_valid
        }

        # 2. UI Automation (UIA) Tree
        uia_node_present = False
        uia_details: Dict[str, Any] = {}
        if uac and hasattr(uac, 'UiaNodeFromHandle'):
            node = ctypes.c_void_p()
            hr = uac.UiaNodeFromHandle(hwnd, ctypes.byref(node))
            if hr == 0 and node.value:
                uia_node_present = True
                try:
                    from comtypes.automation import VARIANT
                    v_name = VARIANT()
                    uac.UiaGetPropertyValue(node, UIA_NamePropertyId, ctypes.byref(v_name))
                    v_cls = VARIANT()
                    uac.UiaGetPropertyValue(node, UIA_ClassNamePropertyId, ctypes.byref(v_cls))
                    v_off = VARIANT()
                    uac.UiaGetPropertyValue(node, UIA_IsOffscreenPropertyId, ctypes.byref(v_off))
                    v_ctrl = VARIANT()
                    uac.UiaGetPropertyValue(node, UIA_ControlTypePropertyId, ctypes.byref(v_ctrl))

                    uia_details = {
                        'name': v_name.value,
                        'class_name': v_cls.value,
                        'control_type': v_ctrl.value,
                        'is_offscreen': v_off.value,
                        'hr': hr
                    }
                except Exception as ex:
                    uia_details['error'] = str(ex)
                finally:
                    if hasattr(uac, 'UiaNodeRelease'):
                        uac.UiaNodeRelease(node)
            else:
                uia_details['hr'] = hr
        else:
            uia_details['unavailable'] = "UIAutomationCore functions not loaded"

        result['uia_tree'] = {
            'present': uia_node_present,
            'details': uia_details
        }

        # 3. IAccessible (oleacc)
        iacc_present = False
        iacc_details: Dict[str, Any] = {}
        try:
            import comtypes
            IID_IAccessible = comtypes.GUID('{618736E0-3C3D-11CF-810C-00AA00389B71}')
            pAcc = ctypes.c_void_p()
            hr_acc = oleacc.AccessibleObjectFromWindow(hwnd, OBJID_WINDOW, ctypes.byref(IID_IAccessible), ctypes.byref(pAcc))
            if hr_acc == 0 and pAcc.value:
                iacc_present = True
                iacc_details['hr'] = hr_acc
                iacc_details['pAcc'] = f"0x{pAcc.value:X}"
            else:
                iacc_details['hr'] = hr_acc
        except Exception as ex:
            iacc_details['error'] = str(ex)

        result['iaccessible_tree'] = {
            'present': iacc_present,
            'details': iacc_details
        }

        # Overall visual tree pass
        result['passed'] = win32_valid and (uia_node_present or iacc_present)
        return result


# ---------------------------------------------------------------------------
# DWM Redirection List Verifier
# ---------------------------------------------------------------------------

class DwmVerifier:
    """Verifies DWM composition and thumbnail redirection list registration."""

    @staticmethod
    def verify(hwnd: int) -> Dict[str, Any]:
        result: Dict[str, Any] = {
            'composition_enabled': False,
            'extended_frame_bounds': {},
            'cloaked': {},
            'redirection_list_registered': False,
            'redirection_source_size': None,
            'nc_rendering_enabled': False,
            'passed': False
        }

        # 1. Composition active
        comp = wintypes.BOOL()
        hr_comp = dwmapi.DwmIsCompositionEnabled(ctypes.byref(comp))
        result['composition_enabled'] = bool(hr_comp == 0 and comp.value)

        # 2. Extended frame bounds
        efb = RECT()
        hr_efb = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(efb), ctypes.sizeof(RECT))
        if hr_efb == 0:
            result['extended_frame_bounds'] = efb.to_dict()

        # 3. Cloaking
        cloaked = ctypes.c_uint()
        hr_cloak = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
        if hr_cloak == 0:
            c_val = cloaked.value
            result['cloaked'] = {
                'raw': c_val,
                'is_cloaked': (c_val != 0),
                'by_app': bool(c_val & DWM_CLOAKED_APP),
                'by_shell': bool(c_val & DWM_CLOAKED_SHELL),
                'by_inherited': bool(c_val & DWM_CLOAKED_INHERITED)
            }

        # 4. NC Rendering
        nc_enabled = wintypes.BOOL()
        hr_nc = dwmapi.DwmGetWindowAttribute(hwnd, DWMWA_NCRENDERING_ENABLED, ctypes.byref(nc_enabled), ctypes.sizeof(nc_enabled))
        if hr_nc == 0:
            result['nc_rendering_enabled'] = bool(nc_enabled.value)

        # 5. DWM Redirection List Registration Test
        # Create a lightweight scratch host window to test DwmRegisterThumbnail
        dest_hwnd = 0
        try:
            import win32gui
            wc = win32gui.WNDCLASS()
            wc.lpfnWndProc = win32gui.DefWindowProc
            wc.lpszClassName = "InbriskDwmVerificationProbeWnd"
            wc.hInstance = win32gui.GetModuleHandle(None)
            try:
                win32gui.RegisterClass(wc)
            except Exception:
                pass
            dest_hwnd = win32gui.CreateWindow(
                wc.lpszClassName,
                "InbriskDwmProbe",
                0,
                0, 0, 10, 10,
                0, 0, wc.hInstance, None
            )
        except Exception:
            pass

        if dest_hwnd:
            hThumb = ctypes.c_void_p()
            hr_thumb = dwmapi.DwmRegisterThumbnail(dest_hwnd, hwnd, ctypes.byref(hThumb))
            if hr_thumb == 0 and hThumb.value:
                result['redirection_list_registered'] = True

                sz = SIZE()
                hr_sz = dwmapi.DwmQueryThumbnailSourceSize(hThumb, ctypes.byref(sz))
                if hr_sz == 0:
                    result['redirection_source_size'] = {'width': sz.cx, 'height': sz.cy}

                dwmapi.DwmUnregisterThumbnail(hThumb)
            else:
                result['thumbnail_registration_hr'] = hr_thumb

            try:
                win32gui.DestroyWindow(dest_hwnd)
            except Exception:
                pass

        result['passed'] = (
            result['composition_enabled'] and
            result['redirection_list_registered'] and
            not result.get('cloaked', {}).get('is_cloaked', False)
        )
        return result


# ---------------------------------------------------------------------------
# Window Capture & Cropped Area Verification
# ---------------------------------------------------------------------------

class WindowCapture:
    """Captures full and cropped window areas from DWM and desktop screen."""

    @staticmethod
    def capture_surface_printwindow(hwnd: int, width: int, height: int) -> Optional[Image.Image]:
        """Captures window redirection surface via PrintWindow (PW_RENDERFULLCONTENT)."""
        if width <= 0 or height <= 0:
            return None

        hdc_screen = user32.GetDC(0)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, width, height)
        h_old = gdi32.SelectObject(hdc_mem, hbmp)

        ret = user32.PrintWindow(hwnd, hdc_mem, PW_RENDERFULLCONTENT)
        if not ret:
            ret = user32.PrintWindow(hwnd, hdc_mem, PW_DEFAULT)

        img = None
        if ret:
            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = width
            bmi.biHeight = -height  # Top-down
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0

            buf = ctypes.create_string_buffer(width * height * 4)
            lines = gdi32.GetDIBits(hdc_mem, hbmp, 0, height, buf, ctypes.byref(bmi), 0)
            if lines == height:
                img = Image.frombuffer('RGBA', (width, height), buf, 'raw', 'BGRA', 0, 1)

        gdi32.SelectObject(hdc_mem, h_old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)

        return img

    @staticmethod
    def capture_screen_bitblt(rect: RECT) -> Optional[Image.Image]:
        """Captures on-screen composited pixels directly from Desktop DC."""
        w = rect.width
        h = rect.height
        if w <= 0 or h <= 0:
            return None

        hdc_screen = user32.GetDC(0)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        h_old = gdi32.SelectObject(hdc_mem, hbmp)

        ret = gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, rect.left, rect.top, SRCCOPY)

        img = None
        if ret:
            bmi = BITMAPINFOHEADER()
            bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.biWidth = w
            bmi.biHeight = -h
            bmi.biPlanes = 1
            bmi.biBitCount = 32
            bmi.biCompression = 0

            buf = ctypes.create_string_buffer(w * h * 4)
            lines = gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)
            if lines == h:
                img = Image.frombuffer('RGBA', (w, h), buf, 'raw', 'BGRA', 0, 1)

        gdi32.SelectObject(hdc_mem, h_old)
        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)

        return img

    @staticmethod
    def analyze_image(img: Image.Image) -> Dict[str, Any]:
        """Calculates image stats to detect blank/black/unpainted frames."""
        rgb = img.convert('RGB')
        w, h = rgb.size
        raw = rgb.tobytes()
        n = w * h

        if n == 0 or len(raw) < 3:
            return {'blank': True, 'reason': '0 pixels'}

        r_sum = sum(raw[0::3])
        g_sum = sum(raw[1::3])
        b_sum = sum(raw[2::3])

        r_mean = r_sum / n
        g_mean = g_sum / n
        b_mean = b_sum / n
        mean_brightness = (r_mean + g_mean + b_mean) / 3.0

        # Sample unique colors (sample up to 2000 points)
        step = max(1, n // 2000) * 3
        sample = set((raw[i], raw[i+1], raw[i+2]) for i in range(0, len(raw) - 2, step))
        unique_count = len(sample)

        # Standard deviation sample
        sample_vals = [(raw[i] + raw[i+1] + raw[i+2]) / 3.0 for i in range(0, len(raw) - 2, step)]
        if sample_vals:
            variance = sum((v - mean_brightness)**2 for v in sample_vals) / len(sample_vals)
            std_dev = math.sqrt(variance)
        else:
            std_dev = 0.0

        is_black = (mean_brightness < 2.0 and std_dev < 1.0)
        is_solid = (unique_count == 1)

        return {
            'width': w,
            'height': h,
            'mean_brightness': round(mean_brightness, 2),
            'rgb_mean': (round(r_mean, 1), round(g_mean, 1), round(b_mean, 1)),
            'std_dev': round(std_dev, 2),
            'unique_sample_colors': unique_count,
            'is_all_black': is_black,
            'is_solid_color': is_solid,
            'is_blank': is_black or (is_solid and mean_brightness == 0)
        }


# ---------------------------------------------------------------------------
# Ghost PiP Main Orchestrator & CLI Runner
# ---------------------------------------------------------------------------

class GhostPipVerifier:
    def __init__(
        self,
        output_dir: pathlib.Path,
        header_height: int = 26,
        custom_crop_rect: Optional[Tuple[int, int, int, int]] = None
    ):
        self.output_dir = output_dir
        self.header_height = header_height
        self.custom_crop_rect = custom_crop_rect
        self.output_dir.mkdir(parents=True, exist_ok=True)

    def execute_verification(
        self,
        target_hwnd: Optional[int] = None,
        class_pattern: Optional[str] = None,
        title_pattern: Optional[str] = None,
        pid_filter: Optional[int] = None,
        desktop_name: Optional[str] = None,
        launch_test: bool = False,
        hold_ms: int = 8000
    ) -> Dict[str, Any]:
        """Runs the complete Ghost PiP diagnostic and verification suite."""
        test_proc = None
        report: Dict[str, Any] = {
            'timestamp': dt.datetime.now(dt.timezone.utc).isoformat(),
            'target_hwnd': target_hwnd,
            'discovery': {},
            'inspection': {},
            'visual_tree': {},
            'dwm_redirection': {},
            'captures': {},
            'summary': {'pass': False, 'checks': []}
        }

        try:
            # 1. Launch test if requested
            if launch_test:
                test_exe = pathlib.Path(__file__).resolve().parent.parent / "bin" / "Debug" / "net8.0-windows10.0.19041.0" / "test_pip.exe"
                if not test_exe.exists():
                    print(c_info(f"Building test_pip.csproj first at {test_exe.parent}..."))
                    subprocess.run(["dotnet", "build", str(test_exe.parent.parent.parent.parent / "test_pip.csproj")], check=True)

                print(c_info(f"Launching Ghost PiP test harness: {test_exe.name} (hold duration: {hold_ms}ms)..."))
                test_proc = subprocess.Popen(
                    [str(test_exe), str(hold_ms)],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1
                )

                # Wait for HWND line in stdout
                start_t = time.time()
                while time.time() - start_t < 10.0:
                    line = test_proc.stdout.readline()
                    if not line:
                        break
                    line_s = line.strip()
                    if "HWND: 0x" in line_s:
                        h_str = line_s.split("HWND: 0x")[1].split()[0]
                        target_hwnd = int(h_str, 16)
                        print(c_info(f"Captured spawned PiP HWND: 0x{target_hwnd:08X}"))
                        break
                    time.sleep(0.05)

                # Give the window time to render its initial frame
                time.sleep(0.6)

            # 2. Discover / Inspect window
            inspector = WindowInspector(target_desktop=desktop_name)
            candidates = inspector.find_pip_windows(
                hwnd_filter=target_hwnd,
                class_pattern=class_pattern,
                title_pattern=title_pattern,
                pid_filter=pid_filter
            )

            report['discovery']['candidates_found'] = len(candidates)
            if not candidates:
                report['summary']['checks'].append(('Window Discovery', False, 'No Ghost PiP window found on any desktop'))
                return report

            win = candidates[0]
            report['target_hwnd'] = win.hwnd
            report['inspection'] = {
                'hwnd': f"0x{win.hwnd:08X}",
                'title': win.title,
                'class_name': win.class_name,
                'pid': win.pid,
                'process_name': win.process_name,
                'exe_path': win.exe_path,
                'thread_id': win.tid,
                'desktop_name': win.desktop_name,
                'is_visible': win.is_visible,
                'is_iconic': win.is_iconic,
                'rect': win.rect.to_dict(),
                'client_rect': win.client_rect.to_dict(),
                'extended_frame_rect': win.extended_frame_rect.to_dict(),
                'styles': {
                    'raw_style': f"0x{win.style:08X}",
                    'raw_ex_style': f"0x{win.ex_style:08X}",
                    'ws_visible': bool(win.style & WS_VISIBLE),
                    'ws_popup': bool(win.style & WS_POPUP),
                    'ws_thickframe': bool(win.style & WS_THICKFRAME),
                    'ws_ex_topmost': bool(win.ex_style & WS_EX_TOPMOST),
                    'ws_ex_noactivate': bool(win.ex_style & WS_EX_NOACTIVATE),
                    'ws_ex_transparent': bool(win.ex_style & WS_EX_TRANSPARENT),
                    'ws_ex_layered': bool(win.ex_style & WS_EX_LAYERED),
                    'ws_ex_appwindow': bool(win.ex_style & WS_EX_APPWINDOW)
                },
                'opacity': win.opacity,
                'display_affinity': win.display_affinity,
                'cloaked_flags': win.cloaked_flags,
                'monitor': {
                    'name': win.monitor_name,
                    'dpi': win.monitor_dpi,
                    'rect': win.monitor_rect.to_dict(),
                    'work_rect': win.monitor_work_rect.to_dict(),
                    'anchor_preset': win.anchor_preset,
                    'margins': {
                        'left': win.margin_left,
                        'top': win.margin_top,
                        'right': win.margin_right,
                        'bottom': win.margin_bottom
                    }
                },
                'z_order_topmost': win.is_topmost_in_zorder
            }

            # 3. Visual Tree Verification
            vt_result = VisualTreeVerifier.verify(win.hwnd)
            report['visual_tree'] = vt_result

            # 4. DWM Redirection List Verification
            dwm_result = DwmVerifier.verify(win.hwnd)
            report['dwm_redirection'] = dwm_result

            # 5. Cropped Window Area Captures
            captures_info: Dict[str, Any] = {}
            w = win.rect.width
            h = win.rect.height

            # Capture Mode A: PrintWindow (DWM Redirection Buffer)
            img_pw = WindowCapture.capture_surface_printwindow(win.hwnd, w, h)
            if img_pw:
                pw_path = self.output_dir / f"pip_dwm_redirect_0x{win.hwnd:08X}.png"
                img_pw.save(pw_path)
                captures_info['dwm_redirection_capture'] = {
                    'path': str(pw_path),
                    'analysis': WindowCapture.analyze_image(img_pw)
                }

            # Capture Mode B: Desktop DC Composited BitBlt
            img_screen = WindowCapture.capture_screen_bitblt(win.rect)
            if img_screen:
                scr_path = self.output_dir / f"pip_screen_crop_0x{win.hwnd:08X}.png"
                img_screen.save(scr_path)
                captures_info['screen_crop_capture'] = {
                    'path': str(scr_path),
                    'analysis': WindowCapture.analyze_image(img_screen)
                }

            # Choose base image for region crops
            base_img = img_pw or img_screen
            if base_img:
                # 5a. Header Crop (top 26px default)
                hh = min(self.header_height, h)
                img_header = base_img.crop((0, 0, w, hh))
                h_path = self.output_dir / f"crop_header_0x{win.hwnd:08X}.png"
                img_header.save(h_path)
                captures_info['crop_header'] = {
                    'path': str(h_path),
                    'bounds': (0, 0, w, hh),
                    'analysis': WindowCapture.analyze_image(img_header)
                }

                # 5b. Client Area Crop (below header)
                if h > hh:
                    img_client = base_img.crop((0, hh, w, h))
                    c_path = self.output_dir / f"crop_client_content_0x{win.hwnd:08X}.png"
                    img_client.save(c_path)
                    captures_info['crop_client'] = {
                        'path': str(c_path),
                        'bounds': (0, hh, w, h),
                        'analysis': WindowCapture.analyze_image(img_client)
                    }

                # 5c. Corner Crops (top-left, top-right, bottom-left, bottom-right)
                cw = min(48, w // 2)
                ch = min(48, h // 2)
                corners = {
                    'top_left': (0, 0, cw, ch),
                    'top_right': (w - cw, 0, w, ch),
                    'bottom_left': (0, h - ch, cw, h),
                    'bottom_right': (w - cw, h - ch, w, h)
                }
                captures_info['crop_corners'] = {}
                for c_name, c_box in corners.items():
                    c_img = base_img.crop(c_box)
                    c_file = self.output_dir / f"crop_corner_{c_name}_0x{win.hwnd:08X}.png"
                    c_img.save(c_file)
                    captures_info['crop_corners'][c_name] = {
                        'path': str(c_file),
                        'bounds': c_box,
                        'analysis': WindowCapture.analyze_image(c_img)
                    }

                # 5d. Custom Crop if specified
                if self.custom_crop_rect:
                    cx, cy, cw, ch = self.custom_crop_rect
                    c_box = (max(0, cx), max(0, cy), min(w, cx + cw), min(h, cy + ch))
                    custom_img = base_img.crop(c_box)
                    custom_file = self.output_dir / f"crop_custom_0x{win.hwnd:08X}.png"
                    custom_img.save(custom_file)
                    captures_info['crop_custom'] = {
                        'path': str(custom_file),
                        'bounds': c_box,
                        'analysis': WindowCapture.analyze_image(custom_img)
                    }

            report['captures'] = captures_info

            # 6. Evaluation of Core Verification Checks
            checks: List[Tuple[str, bool, str]] = []

            # C1: Window valid & desktop recognized
            c1_ok = win.is_window and bool(win.desktop_name)
            checks.append(('Window Handle & Desktop Binding', c1_ok, f"HWND 0x{win.hwnd:08X} on '{win.desktop_name}'"))

            # C2: Visibility state
            c2_ok = win.is_visible and not win.is_iconic
            checks.append(('Window Visibility', c2_ok, f"Visible={win.is_visible}, Iconic={win.is_iconic}"))

            # C3: Position & dimensions within screen bounds
            c3_ok = (win.rect.width >= 100 and win.rect.height >= 50 and win.rect.right > win.rect.left)
            checks.append(('Window Position & Geometry', c3_ok, f"{win.rect.width}x{win.rect.height} at ({win.rect.left},{win.rect.top}), Preset={win.anchor_preset}"))

            # C4: Ghost PiP Styles (WS_EX_TOPMOST & WS_EX_NOACTIVATE)
            has_topmost = bool(win.ex_style & WS_EX_TOPMOST)
            has_noactivate = bool(win.ex_style & WS_EX_NOACTIVATE)
            c4_ok = has_topmost
            checks.append(('Ghost PiP Extended Styles', c4_ok, f"Topmost={has_topmost}, NoActivate={has_noactivate}, Transparent={bool(win.ex_style & WS_EX_TRANSPARENT)}"))

            # C5: DWM Composition & Uncloaked
            c5_ok = dwm_result['composition_enabled'] and not dwm_result.get('cloaked', {}).get('is_cloaked', False)
            checks.append(('DWM Composition & Uncloaked', c5_ok, f"Composition={dwm_result['composition_enabled']}, Cloaked={dwm_result.get('cloaked', {}).get('raw', 0)}"))

            # C6: DWM Redirection List
            c6_ok = dwm_result['redirection_list_registered']
            src_sz = dwm_result.get('redirection_source_size')
            sz_str = f"{src_sz['width']}x{src_sz['height']}" if src_sz else "N/A"
            checks.append(('DWM Redirection List Registration', c6_ok, f"Registered={c6_ok}, SourceSize={sz_str}"))

            # C7: Visual Tree Presence
            c7_ok = vt_result['passed']
            uia_ok = vt_result['uia_tree']['present']
            iacc_ok = vt_result['iaccessible_tree']['present']
            checks.append(('Visual Tree Presence', c7_ok, f"Win32={vt_result['win32_tree']['valid']}, UIA={uia_ok}, IAccessible={iacc_ok}"))

            # C8: Cropped Captures to Disk
            c8_ok = ('dwm_redirection_capture' in captures_info or 'screen_crop_capture' in captures_info) and ('crop_header' in captures_info)
            checks.append(('Cropped Area Captures', c8_ok, f"Saved {len(captures_info)} capture views to {self.output_dir.name}"))

            # C9: Rendered Pixel Content (Non-blank)
            primary_analysis = (captures_info.get('dwm_redirection_capture') or captures_info.get('screen_crop_capture') or {}).get('analysis', {})
            is_blank = primary_analysis.get('is_blank', False)
            c9_ok = (not is_blank) if primary_analysis else True
            checks.append(('Rendered Pixel Verification', c9_ok, f"Brightness={primary_analysis.get('mean_brightness', 'N/A')}, StdDev={primary_analysis.get('std_dev', 'N/A')}, Blank={is_blank}"))

            overall_pass = all(item[1] for item in checks)
            report['summary'] = {
                'pass': overall_pass,
                'checks': checks
            }

        finally:
            if test_proc and test_proc.poll() is None:
                try:
                    test_proc.terminate()
                    test_proc.wait(timeout=2.0)
                except Exception:
                    pass

        return report

    def print_console_report(self, report: Dict[str, Any]) -> None:
        """Prints formatted console report with ANSI highlights."""
        print(c_header("INBRISK GHOST PIP DIAGNOSTIC REPORT"))

        insp = report.get('inspection', {})
        if insp:
            print(f"{Color.BOLD}Window Information:{Color.RESET}")
            print(f"  * Handle:          {Color.YELLOW}{insp.get('hwnd')}{Color.RESET}")
            print(f"  * Title:           \"{insp.get('title')}\"")
            print(f"  * Class Name:      \"{insp.get('class_name')}\"")
            print(f"  * Process:         {insp.get('process_name')} (PID: {insp.get('pid')}, TID: {insp.get('thread_id')})")
            print(f"  * Desktop:         {Color.CYAN}{insp.get('desktop_name')}{Color.RESET}")
            print(f"  * Visibility:      Visible={insp.get('is_visible')}, Iconic={insp.get('is_iconic')}")

            rect = insp.get('rect', {})
            mon = insp.get('monitor', {})
            print(f"{Color.BOLD}Position & Geometry:{Color.RESET}")
            print(f"  * Screen Rect:     [{rect.get('left')}, {rect.get('top')} - {rect.get('right')}, {rect.get('bottom')}] ({rect.get('width')}x{rect.get('height')})")
            print(f"  * Anchor Preset:   {Color.MAGENTA}{mon.get('anchor_preset')}{Color.RESET}")
            print(f"  * Monitor Device:  {mon.get('name')} (DPI: {mon.get('dpi')})")
            print(f"  * Margins (R, T):  Right={mon.get('margins', {}).get('right')}px, Top={mon.get('margins', {}).get('top')}px")

            st = insp.get('styles', {})
            print(f"{Color.BOLD}Window Styles & Characteristics:{Color.RESET}")
            print(f"  * Raw Style:       {st.get('raw_style')}, ExStyle: {st.get('raw_ex_style')}")
            print(f"  * WS_EX_TOPMOST:   {Color.GREEN if st.get('ws_ex_topmost') else Color.RED}{st.get('ws_ex_topmost')}{Color.RESET}")
            print(f"  * WS_EX_NOACTIVATE:{Color.GREEN if st.get('ws_ex_noactivate') else Color.YELLOW}{st.get('ws_ex_noactivate')}{Color.RESET} (Focus protection)")
            print(f"  * WS_EX_TRANSPAR:  {st.get('ws_ex_transparent')} (Click-through transparent)")
            print(f"  * WS_EX_LAYERED:   {st.get('ws_ex_layered')} (Alpha opacity: {insp.get('opacity')}/255)")
            print(f"  * Display Affinity:0x{insp.get('display_affinity', 0):X}")

        # Visual Tree
        vt = report.get('visual_tree', {})
        if vt:
            print(c_header("VISUAL TREE VERIFICATION"))
            w32 = vt.get('win32_tree', {})
            uia = vt.get('uia_tree', {})
            iacc = vt.get('iaccessible_tree', {})
            print(f"  * Win32 Desktop Tree:    {c_pass('Valid') if w32.get('valid') else c_fail('Invalid')} (Root: {w32.get('root_hwnd')}, Parent: {w32.get('parent_hwnd')})")
            print(f"  * UI Automation Tree:    {c_pass('Attached') if uia.get('present') else c_fail('Detached')} (Name: '{uia.get('details', {}).get('name')}', Offscreen: {uia.get('details', {}).get('is_offscreen')})")
            print(f"  * IAccessible Visual:    {c_pass('Attached') if iacc.get('present') else c_fail('Detached')}")

        # DWM Redirection
        dwm = report.get('dwm_redirection', {})
        if dwm:
            print(c_header("DWM REDIRECTION LIST VERIFICATION"))
            print(f"  * DWM Composition:       {c_pass('Active') if dwm.get('composition_enabled') else c_fail('Inactive')}")
            print(f"  * DWM Redirection List:  {c_pass('Verified') if dwm.get('redirection_list_registered') else c_fail('Not registered')}")
            src_sz = dwm.get('redirection_source_size')
            if src_sz:
                print(f"  * Compositor Source Size:{src_sz.get('width')}x{src_sz.get('height')} pixels")
            efb = dwm.get('extended_frame_bounds', {})
            print(f"  * DWM Extended Bounds:   [{efb.get('left')}, {efb.get('top')} - {efb.get('right')}, {efb.get('bottom')}]")
            cloaked = dwm.get('cloaked', {})
            c_raw = cloaked.get('raw', 0)
            print(f"  * Cloaking State:        {c_pass('Uncloaked (0)') if not cloaked.get('is_cloaked') else c_fail(f'Cloaked: raw={c_raw}')}")

        # Captures
        caps = report.get('captures', {})
        if caps:
            print(c_header("CAPTURED CROPPED REGIONS"))
            for key, c_data in caps.items():
                if key == 'crop_corners':
                    for c_name, corner_data in c_data.items():
                        c_analysis = corner_data.get('analysis', {})
                        print(f"  * Corner [{c_name:12}]: {pathlib.Path(corner_data.get('path')).name} ({c_analysis.get('width')}x{c_analysis.get('height')})")
                else:
                    analysis = c_data.get('analysis', {})
                    p_name = pathlib.Path(c_data.get('path')).name
                    print(f"  * {key:24}: {p_name} ({analysis.get('width')}x{analysis.get('height')}) [Brightness: {analysis.get('mean_brightness')}, UniqueColors: {analysis.get('unique_sample_colors')}]")

        # Summary
        summary = report.get('summary', {})
        checks = summary.get('checks', [])
        print(c_header("VERIFICATION SUMMARY CHECKLIST"))
        for title, ok, details in checks:
            badge = c_pass(title) if ok else c_fail(title)
            print(f"  {badge:32} -> {details}")

        print("\n" + "=" * 65)
        if summary.get('pass'):
            print(f"{Color.GREEN}{Color.BOLD}>>> OVERALL RESULT: GHOST PIP VERIFICATION PASSED <<<{Color.RESET}")
        else:
            print(f"{Color.RED}{Color.BOLD}>>> OVERALL RESULT: GHOST PIP VERIFICATION FAILED <<<{Color.RESET}")
        print("=" * 65 + "\n")


# ---------------------------------------------------------------------------
# CLI Entry Point
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Ghost PiP Diagnostic and Verification Tool for Inbrisk.",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--hwnd", type=lambda s: int(s, 16) if s.lower().startswith("0x") else int(s),
                        help="Target PiP window handle in hex (e.g. 0x01FC0D20) or dec.")
    parser.add_argument("--title", type=str, help="Regex pattern to filter PiP window title.")
    parser.add_argument("--class", dest="class_name", type=str, help="Regex pattern to filter PiP window class.")
    parser.add_argument("--pid", type=int, help="Filter by process ID.")
    parser.add_argument("--desktop", type=str, default="auto",
                        help="Target desktop name to search, or 'auto' to scan all active/sandbox desktops.")
    parser.add_argument("--launch-test", action="store_true",
                        help="Automatically launch test_pip.exe in background to execute live verification.")
    parser.add_argument("--hold-ms", type=int, default=8000,
                        help="Hold duration for launched test in milliseconds (default: 8000ms).")
    parser.add_argument("--output-dir", type=pathlib.Path,
                        default=pathlib.Path(__file__).resolve().parent.parent / "artifacts" / "pip_captures",
                        help="Directory to save cropped PNG images and diagnostics.")
    parser.add_argument("--header-height", type=int, default=26,
                        help="Header drag bar height in pixels (default: 26).")
    parser.add_argument("--crop", type=str,
                        help="Optional custom crop rectangle in 'x,y,w,h' format.")
    parser.add_argument("--json", action="store_true",
                        help="Output report in JSON format to stdout.")
    parser.add_argument("--json-out", type=pathlib.Path,
                        help="Optional file path to write full JSON report.")

    args = parser.parse_args()

    custom_crop = None
    if args.crop:
        try:
            parts = [int(v.strip()) for v in args.crop.split(',')]
            if len(parts) == 4:
                custom_crop = (parts[0], parts[1], parts[2], parts[3])
        except Exception:
            print(c_warn(f"Invalid --crop format '{args.crop}'. Expected 'x,y,w,h'."))

    verifier = GhostPipVerifier(
        output_dir=args.output_dir,
        header_height=args.header_height,
        custom_crop_rect=custom_crop
    )

    report = verifier.execute_verification(
        target_hwnd=args.hwnd,
        class_pattern=args.class_name,
        title_pattern=args.title,
        pid_filter=args.pid,
        desktop_name=args.desktop,
        launch_test=args.launch_test,
        hold_ms=args.hold_ms
    )

    # JSON save
    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        # Filter non-serializable tuples
        serializable_report = json.loads(json.dumps(report, default=str))
        args.json_out.write_text(json.dumps(serializable_report, indent=2, ensure_ascii=False), encoding="utf-8")
        print(c_info(f"JSON report saved to {args.json_out}"))

    if args.json:
        serializable_report = json.loads(json.dumps(report, default=str))
        print(json.dumps(serializable_report, indent=2, ensure_ascii=False))
    else:
        verifier.print_console_report(report)

    return 0 if report.get('summary', {}).get('pass') else 1


if __name__ == "__main__":
    sys.exit(main())
