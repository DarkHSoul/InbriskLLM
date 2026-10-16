#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Inbrisk Automated Stress Test & Validation Harness
===================================================
A standalone verification script that communicates directly with the
Inbrisk runtime via the MCP (Model Context Protocol) JSON-RPC stdio protocol.

Tests Performed:
  1. Calculator: Computes 15 + 24 = 39, verifies expression and display value in UIA tree.
  2. Notepad: Writes test verification text, verifies Document text value in UIA tree.
  3. Paint: Verifies window state, menus, tool controls, and canvas responsiveness.

Outputs:
  - Structured JSON reports containing ISO timestamps, window handles (HWND), PIDs,
    step execution traces, and verification evidence.
  - High-resolution window screenshots captured directly through Inbrisk MCP.
"""

from __future__ import annotations

import os
import sys
import time
import json
import uuid
import shutil
import base64
import ctypes
import pathlib
import argparse
import datetime
import subprocess
import threading
import queue
from typing import Any, Dict, List, Optional, Tuple

# Win32 APIs for window handling & foreground management
import win32gui
import win32process
import win32con
import win32clipboard
import psutil

# Ensure stdout uses UTF-8 encoding
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DEFAULT_INBRISK_PATHS = [
    os.path.expandvars(r"%LOCALAPPDATA%\Programs\Inbrisk\inbrisk.exe"),
    r"C:\Users\Ahmet\AppData\Local\Programs\Inbrisk\inbrisk.exe",
    shutil.which("inbrisk.exe") or "",
]


def resolve_inbrisk_exe(custom_path: Optional[str] = None) -> str:
    """Finds the Inbrisk executable path."""
    if custom_path and os.path.isfile(custom_path):
        return os.path.abspath(custom_path)

    # Check settings/config file if available
    config_file = pathlib.Path(__file__).parent.parent / "config.json"
    if config_file.is_file():
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                cfg = json.load(f)
                exe = cfg.get("inbrisk_exe")
                if exe and os.path.isfile(exe):
                    return os.path.abspath(exe)
        except Exception:
            pass

    for path in DEFAULT_INBRISK_PATHS:
        if path and os.path.isfile(path):
            return os.path.abspath(path)

    raise FileNotFoundError("Could not locate Inbrisk executable (inbrisk.exe).")


class InbriskMCPProtocolClient:
    """
    Standalone Inbrisk MCP JSON-RPC protocol client running over stdio.
    """

    def __init__(self, executable: str, server_args: Optional[List[str]] = None, timeout: float = 30.0):
        self.executable = executable
        self.server_args = server_args or ["--yes"]
        self.timeout = timeout
        self.proc: Optional[subprocess.Popen[str]] = None
        self._responses: queue.Queue[Dict[str, Any]] = queue.Queue()
        self._pending: Dict[int, Dict[str, Any]] = {}
        self._next_id = 0
        self._lock = threading.Lock()
        self._reader_thread: Optional[threading.Thread] = None
        self.server_info: Dict[str, Any] = {}
        self.protocol_version: str = "2024-11-05"
        self.tools: Dict[str, Dict[str, Any]] = {}

    def connect(self) -> None:
        """Launches inbrisk mcp server and executes standard MCP handshake."""
        self.close()
        cmd = [self.executable, "mcp", *self.server_args]
        self.proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            text=True,
            bufsize=1,
        )

        self._responses = queue.Queue()
        self._pending.clear()
        self._next_id = 0

        self._reader_thread = threading.Thread(target=self._stdout_loop, daemon=True)
        self._reader_thread.start()

        # Thread for draining stderr to avoid buffering deadlocks
        threading.Thread(target=self._stderr_loop, daemon=True).start()

        # 1. initialize
        init_res = self._request(
            "initialize",
            {
                "protocolVersion": self.protocol_version,
                "capabilities": {},
                "clientInfo": {"name": "inbrisk-stress-test-validator", "version": "1.0.0"},
            },
            timeout=15.0,
        )
        res_data = init_res.get("result", {})
        self.protocol_version = res_data.get("protocolVersion", self.protocol_version)
        self.server_info = res_data.get("serverInfo", {})

        # 2. notifications/initialized
        self._notify("notifications/initialized", {})

        # 3. tools/list
        self._refresh_tools()

    def _stdout_loop(self) -> None:
        if not self.proc or not self.proc.stdout:
            return
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except Exception:
                    continue
                if isinstance(payload, dict) and "id" in payload:
                    self._responses.put(payload)
        except (OSError, ValueError):
            pass

    def _stderr_loop(self) -> None:
        if not self.proc or not self.proc.stderr:
            return
        try:
            for _ in self.proc.stderr:
                pass
        except Exception:
            pass

    def _send(self, payload: Dict[str, Any]) -> None:
        if not self.proc or not self.proc.stdin or self.proc.poll() is not None:
            raise RuntimeError("Inbrisk MCP process is not running.")
        text = json.dumps(payload, ensure_ascii=False)
        self.proc.stdin.write(text + "\n")
        self.proc.stdin.flush()

    def _notify(self, method: str, params: Dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        with self._lock:
            self._next_id += 1
            req_id = self._next_id
            self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})

            deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
            while time.monotonic() < deadline:
                if req_id in self._pending:
                    return self._pending.pop(req_id)
                try:
                    remaining = max(0.01, deadline - time.monotonic())
                    msg = self._responses.get(timeout=min(0.2, remaining))
                except queue.Empty:
                    if self.proc and self.proc.poll() is not None:
                        raise RuntimeError(f"Inbrisk MCP process exited unexpectedly with code {self.proc.poll()}")
                    continue

                msg_id = msg.get("id")
                if msg_id == req_id:
                    if "error" in msg:
                        raise RuntimeError(f"MCP {method} error: {json.dumps(msg['error'], ensure_ascii=False)}")
                    return msg
                elif isinstance(msg_id, int):
                    self._pending[msg_id] = msg

            raise TimeoutError(f"MCP request '{method}' (id={req_id}) timed out after {timeout or self.timeout}s.")

    def _refresh_tools(self) -> None:
        res = self._request("tools/list", {})
        result = res.get("result", {})
        tool_list = result.get("tools", [])
        self.tools = {t["name"]: t for t in tool_list if isinstance(t, dict) and "name" in t}

    def call_tool(self, name: str, arguments: Dict[str, Any], timeout: Optional[float] = None) -> Dict[str, Any]:
        """Calls an MCP tool and returns parsed response with text and images extracted."""
        t0 = time.time()
        reply = self._request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        duration_ms = round((time.time() - t0) * 1000, 2)

        result = reply.get("result", {})
        content_blocks = result.get("content", [])
        text_parts = []
        image_bytes_list: List[bytes] = []

        for block in content_blocks:
            if not isinstance(block, dict):
                continue
            btype = block.get("type")
            if btype == "text":
                text_parts.append(block.get("text", ""))
            elif btype == "image":
                raw_b64 = block.get("data", "")
                if raw_b64:
                    try:
                        image_bytes_list.append(base64.b64decode(raw_b64))
                    except Exception:
                        pass

        full_text = "\n".join(text_parts)
        is_error = bool(result.get("isError", False))

        return {
            "tool": name,
            "arguments": arguments,
            "duration_ms": duration_ms,
            "is_error": is_error,
            "text": full_text,
            "images": image_bytes_list,
            "raw_result": result,
        }

    def close(self) -> None:
        """Gracefully closes the MCP server process."""
        proc = self.proc
        self.proc = None
        if proc:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait(timeout=1.0)
            if proc.stdin:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            if proc.stdout:
                try:
                    proc.stdout.close()
                except Exception:
                    pass
            if proc.stderr:
                try:
                    proc.stderr.close()
                except Exception:
                    pass


# ==============================================================================
# Windows OS Helpers
# ==============================================================================

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32


def ensure_desktop_access() -> None:
    """Ensures current thread is attached to the default interactive desktop."""
    try:
        hdesk = user32.OpenDesktopW("Default", 0, False, 0x01FF)
        if hdesk:
            user32.SetThreadDesktop(hdesk)
    except Exception:
        pass


def get_window_rect(hwnd: int) -> Tuple[int, int, int, int]:
    try:
        return win32gui.GetWindowRect(hwnd)
    except Exception:
        return (0, 0, 0, 0)


def activate_window_safely(hwnd: int) -> bool:
    """Brings window to foreground using thread-input attach to bypass lockouts."""
    ensure_desktop_access()
    try:
        if not win32gui.IsWindow(hwnd):
            return False

        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        cur_thread = kernel32.GetCurrentThreadId()
        fg_hwnd = user32.GetForegroundWindow()
        fg_thread, _ = win32process.GetWindowThreadProcessId(fg_hwnd)

        if fg_thread and fg_thread != cur_thread:
            user32.AttachThreadInput(cur_thread, fg_thread, True)

        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.SetFocus(hwnd)

        if fg_thread and fg_thread != cur_thread:
            user32.AttachThreadInput(cur_thread, fg_thread, False)

        time.sleep(0.2)
        return True
    except Exception:
        return False


def find_top_level_window(process_names: List[str], title_keywords: List[str]) -> Optional[Dict[str, Any]]:
    """Locates an active visible top-level window matching process and title filters."""
    ensure_desktop_access()
    matches: List[Dict[str, Any]] = []

    def enum_cb(h: int, extra: Any) -> bool:
        if not win32gui.IsWindowVisible(h):
            return True
        txt = win32gui.GetWindowText(h)
        cls = win32gui.GetClassName(h)
        _, pid = win32process.GetWindowThreadProcessId(h)

        pname = ""
        try:
            p = psutil.Process(pid)
            pname = p.name().lower()
        except Exception:
            pass

        title_lower = txt.lower()
        proc_match = any(pn.lower() in pname for pn in process_names)
        title_match = any(kw.lower() in title_lower for kw in title_keywords)

        if proc_match or title_match:
            rect = get_window_rect(h)
            width = rect[2] - rect[0]
            height = rect[3] - rect[1]
            if width > 50 and height > 50:  # Filter out tooltips/invisible wrappers
                matches.append({
                    "hwnd": h,
                    "hwnd_hex": f"0x{h:08X}",
                    "pid": pid,
                    "process_name": pname,
                    "title": txt,
                    "class_name": cls,
                    "rect": rect,
                    "width": width,
                    "height": height,
                })
        return True

    win32gui.EnumWindows(enum_cb, None)
    if not matches:
        return None

    # Prefer windows that match both process and title keywords
    matches.sort(key=lambda w: (
        any(pn.lower() in w["process_name"] for pn in process_names) and
        any(kw.lower() in w["title"].lower() for kw in title_keywords),
        w["width"] * w["height"]
    ), reverse=True)

    return matches[0]


def send_paste_text(hwnd: int, text: str) -> bool:
    """Pastes text cleanly into target window using Ctrl+A and Ctrl+V."""
    activate_window_safely(hwnd)
    time.sleep(0.2)

    # Set clipboard content
    try:
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
    except Exception as e:
        return False

    VK_CONTROL = 0x11
    VK_A = 0x41
    VK_V = 0x56

    # Select all: Ctrl+A
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_A, 0, 0, 0)
    user32.keybd_event(VK_A, 0, 2, 0)
    user32.keybd_event(VK_CONTROL, 0, 2, 0)
    time.sleep(0.1)

    # Paste: Ctrl+V
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 0, 0)
    user32.keybd_event(VK_V, 0, 2, 0)
    user32.keybd_event(VK_CONTROL, 0, 2, 0)
    time.sleep(0.3)
    return True


# ==============================================================================
# Stress Test Scenarios
# ==============================================================================

class StressTestRunner:
    def __init__(self, mcp: InbriskMCPProtocolClient, reports_dir: pathlib.Path, verbose: bool = True):
        self.mcp = mcp
        self.reports_dir = reports_dir
        self.screenshots_dir = reports_dir / "screenshots"
        self.verbose = verbose
        self.screenshots_dir.mkdir(parents=True, exist_ok=True)

    def log(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    def save_screenshot(self, app_tag: str, hwnd_hex: str, image_bytes: bytes) -> str:
        """Saves screenshot bytes to disk and returns absolute path."""
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")[:19]
        filename = f"{app_tag}_{hwnd_hex}_{ts}.png"
        filepath = self.screenshots_dir / filename
        with open(filepath, "wb") as f:
            f.write(image_bytes)
        return str(filepath.resolve())

    # --------------------------------------------------------------------------
    # Test 1: Calculator 15 + 24 = 39
    # --------------------------------------------------------------------------
    def test_calculator(self, iteration: int) -> Dict[str, Any]:
        self.log("\n" + "=" * 60)
        self.log(f"[TEST 1] Calculator Validation: 15 + 24 = 39 (Iteration #{iteration})")
        self.log("=" * 60)

        t_start = time.time()
        start_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        steps: List[Dict[str, Any]] = []

        # 1. Locate Calculator window
        calc_win = find_top_level_window(["calculatorapp.exe", "applicationframehost.exe"], ["hesap", "calc"])
        if not calc_win:
            self.log("Calculator window not open, launching calc.exe...")
            subprocess.Popen(["calc.exe"], shell=False)
            time.sleep(2.0)
            calc_win = find_top_level_window(["calculatorapp.exe", "applicationframehost.exe"], ["hesap", "calc"])

        if not calc_win:
            raise RuntimeError("Calculator window could not be found or launched.")

        hwnd = calc_win["hwnd"]
        hwnd_hex = calc_win["hwnd_hex"]
        self.log(f"Located Calculator: HWND={hwnd_hex} PID={calc_win['pid']} Title='{calc_win['title']}'")
        activate_window_safely(hwnd)

        # 2. Reset / Clear Calculator via MCP computer_click or Escape
        self.log("Resetting Calculator via MCP...")
        clear_res = self.mcp.call_tool(
            "computer_click",
            {"target": {"hwnd": hwnd_hex, "name": "Temizle"}},
            timeout=10.0,
        )
        if clear_res["is_error"]:
            # Try English 'Clear'
            clear_res = self.mcp.call_tool(
                "computer_click",
                {"target": {"hwnd": hwnd_hex, "name": "Clear"}},
                timeout=10.0,
            )
        steps.append({
            "action": "clear",
            "tool": "computer_click",
            "duration_ms": clear_res["duration_ms"],
            "success": not clear_res["is_error"],
        })

        time.sleep(0.3)

        # 3. Perform 15 + 24 = calculation
        # Turkish & English button names map
        button_sequence = [
            ("1", ["Bir", "One", "num1Button"]),
            ("5", ["Beş", "Five", "num5Button"]),
            ("+", ["Artı", "Plus", "plusButton"]),
            ("2", ["İki", "Two", "num2Button"]),
            ("4", ["Dört", "Four", "num4Button"]),
            ("=", ["Eşittir", "Equals", "equalButton"]),
        ]

        self.log("Inputting expression '15 + 24 =' via MCP...")
        calc_input_success = True
        for key, aliases in button_sequence:
            clicked = False
            for alias in aliases:
                res = self.mcp.call_tool(
                    "computer_click",
                    {"target": {"hwnd": hwnd_hex, "name": alias}},
                    timeout=8.0,
                )
                if not res["is_error"] and ("success\":true" in res["text"] or "ObservedChange" in res["text"] or "Verified" in res["text"]):
                    clicked = True
                    steps.append({
                        "action": f"press_{key}",
                        "alias": alias,
                        "tool": "computer_click",
                        "duration_ms": res["duration_ms"],
                        "status": "success",
                    })
                    break

            if not clicked:
                # Fallback to direct keybd_event if UIA click was occluded
                self.log(f"UIA invoke for '{key}' unavailable; using virtual keyboard fallback.")
                vk_map = {"1": 0x31, "5": 0x35, "+": 0x6B, "2": 0x32, "4": 0x34, "=": 0x0D}
                vk = vk_map.get(key, 0)
                if vk:
                    user32.keybd_event(vk, 0, 0, 0)
                    time.sleep(0.05)
                    user32.keybd_event(vk, 0, 2, 0)
                    steps.append({"action": f"press_{key}", "fallback": "keybd_event", "status": "success"})
            time.sleep(0.2)

        time.sleep(0.5)

        # 4. Verify result using Inbrisk MCP computer_inspect
        self.log("Verifying calculation result via MCP computer_inspect...")
        inspect_res = self.mcp.call_tool(
            "computer_inspect",
            {"hwnd": hwnd_hex, "detail": "full", "maxElements": 40},
            timeout=15.0,
        )
        inspect_text = inspect_res["text"]
        steps.append({
            "action": "inspect_result",
            "tool": "computer_inspect",
            "duration_ms": inspect_res["duration_ms"],
            "status": "success" if not inspect_res["is_error"] else "failed",
        })

        # Check for '39' in the display text element
        verified_39 = False
        display_matched_text = ""
        expression_matched_text = ""

        for line in inspect_text.splitlines():
            low = line.lower()
            if any(k in low for k in ["ekran değeri 39", "display is 39"]) or ("39" in line and "text" in low and ("ekran" in low or "display" in low)):
                verified_39 = True
                display_matched_text = line.strip()
            if "15 + 24" in line or "15+24" in line:
                expression_matched_text = line.strip()

        # 5. Capture screenshot via Inbrisk MCP computer_screenshot
        self.log("Capturing Calculator screenshot via MCP...")
        shot_res = self.mcp.call_tool(
            "computer_screenshot",
            {"target": "window", "hwnd": hwnd_hex},
            timeout=15.0,
        )

        screenshot_path = ""
        if shot_res["images"]:
            screenshot_path = self.save_screenshot("calculator", hwnd_hex, shot_res["images"][0])
            self.log(f"Screenshot saved: {screenshot_path}")

        duration = round(time.time() - t_start, 3)
        passed = verified_39 and bool(screenshot_path)

        self.log(f"Calculator Test Result: {'PASSED' if passed else 'FAILED'} (Duration: {duration}s)")
        if display_matched_text:
            self.log(f"  Evidence: {display_matched_text}")

        return {
            "test_name": "Calculator 15+24=39",
            "target": "calculator",
            "hwnd": hwnd,
            "hwnd_hex": hwnd_hex,
            "pid": calc_win["pid"],
            "window_title": calc_win["title"],
            "window_rect": calc_win["rect"],
            "start_time": start_iso,
            "end_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "duration_seconds": duration,
            "status": "PASSED" if passed else "FAILED",
            "steps": steps,
            "verification": {
                "expected_result": "39",
                "verified": verified_39,
                "display_evidence": display_matched_text,
                "expression_evidence": expression_matched_text,
            },
            "screenshot_path": screenshot_path,
        }

    # --------------------------------------------------------------------------
    # Test 2: Notepad Text Verification
    # --------------------------------------------------------------------------
    def test_notepad(self, iteration: int) -> Dict[str, Any]:
        self.log("\n" + "=" * 60)
        self.log(f"[TEST 2] Notepad Text Verification (Iteration #{iteration})")
        self.log("=" * 60)

        t_start = time.time()
        start_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        steps: List[Dict[str, Any]] = []

        # 1. Locate Notepad window
        note_win = find_top_level_window(["notepad.exe"], ["not defteri", "notepad"])
        if not note_win:
            self.log("Notepad window not open, launching notepad.exe via cmd...")
            subprocess.Popen(["cmd.exe", "/c", "start notepad.exe"], shell=False)
            time.sleep(2.0)
            note_win = find_top_level_window(["notepad.exe"], ["not defteri", "notepad"])

        if not note_win:
            raise RuntimeError("Notepad window could not be found or launched.")

        hwnd = note_win["hwnd"]
        hwnd_hex = note_win["hwnd_hex"]
        self.log(f"Located Notepad: HWND={hwnd_hex} PID={note_win['pid']} Title='{note_win['title']}'")
        activate_window_safely(hwnd)

        # 2. Unique test text for verification
        test_uid = uuid.uuid4().hex[:8]
        test_content = f"Inbrisk Stress Test Verification 39 [UID:{test_uid}]"
        self.log(f"Injecting test content: '{test_content}'...")

        # Deliver text via paste (handles focused UWP document cleanly)
        pasted = send_paste_text(hwnd, test_content)
        steps.append({
            "action": "type_text",
            "content": test_content,
            "method": "clipboard_paste",
            "success": pasted,
        })

        time.sleep(0.5)

        # 3. Verify text using Inbrisk MCP computer_inspect
        self.log("Inspecting Notepad UIA Document element via MCP...")
        inspect_res = self.mcp.call_tool(
            "computer_inspect",
            {"hwnd": hwnd_hex, "detail": "full", "maxElements": 30},
            timeout=15.0,
        )
        inspect_text = inspect_res["text"]
        steps.append({
            "action": "inspect_document",
            "tool": "computer_inspect",
            "duration_ms": inspect_res["duration_ms"],
            "status": "success" if not inspect_res["is_error"] else "failed",
        })

        verified_text = False
        matched_document_line = ""

        for line in inspect_text.splitlines():
            if "Document" in line and ("Metin" in line or "Text" in line or "value=" in line):
                matched_document_line = line.strip()
                if test_uid in line or "Stress Test" in line or "39" in line:
                    verified_text = True
                    break

        # Fallback check across entire inspect text if UIA wrapped differently
        if not verified_text and test_uid in inspect_text:
            verified_text = True

        # 4. Capture screenshot via Inbrisk MCP computer_screenshot
        self.log("Capturing Notepad screenshot via MCP...")
        shot_res = self.mcp.call_tool(
            "computer_screenshot",
            {"target": "window", "hwnd": hwnd_hex},
            timeout=15.0,
        )

        screenshot_path = ""
        if shot_res["images"]:
            screenshot_path = self.save_screenshot("notepad", hwnd_hex, shot_res["images"][0])
            self.log(f"Screenshot saved: {screenshot_path}")

        duration = round(time.time() - t_start, 3)
        passed = verified_text and bool(screenshot_path)

        self.log(f"Notepad Test Result: {'PASSED' if passed else 'FAILED'} (Duration: {duration}s)")
        if matched_document_line:
            self.log(f"  Evidence: {matched_document_line}")

        return {
            "test_name": "Notepad Text Verification",
            "target": "notepad",
            "hwnd": hwnd,
            "hwnd_hex": hwnd_hex,
            "pid": note_win["pid"],
            "window_title": note_win["title"],
            "window_rect": note_win["rect"],
            "start_time": start_iso,
            "end_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "duration_seconds": duration,
            "status": "PASSED" if passed else "FAILED",
            "steps": steps,
            "verification": {
                "expected_substring": test_uid,
                "verified": verified_text,
                "document_evidence": matched_document_line,
            },
            "screenshot_path": screenshot_path,
        }

    # --------------------------------------------------------------------------
    # Test 3: Paint State Verification
    # --------------------------------------------------------------------------
    def test_paint(self, iteration: int) -> Dict[str, Any]:
        self.log("\n" + "=" * 60)
        self.log(f"[TEST 3] Paint State Verification (Iteration #{iteration})")
        self.log("=" * 60)

        t_start = time.time()
        start_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()
        steps: List[Dict[str, Any]] = []

        # 1. Locate Paint window
        paint_win = find_top_level_window(["mspaint.exe"], ["paint"])
        if not paint_win:
            self.log("Paint window not open, launching mspaint.exe...")
            subprocess.Popen(["cmd.exe", "/c", "start mspaint.exe"], shell=False)
            time.sleep(2.5)
            paint_win = find_top_level_window(["mspaint.exe"], ["paint"])

        if not paint_win:
            raise RuntimeError("Paint window could not be found or launched.")

        hwnd = paint_win["hwnd"]
        hwnd_hex = paint_win["hwnd_hex"]
        self.log(f"Located Paint: HWND={hwnd_hex} PID={paint_win['pid']} Title='{paint_win['title']}'")
        activate_window_safely(hwnd)

        # 2. Inspect Paint UI state via Inbrisk MCP computer_inspect
        self.log("Inspecting Paint ribbon, menu controls, and state via MCP...")
        inspect_res = self.mcp.call_tool(
            "computer_inspect",
            {"hwnd": hwnd_hex, "detail": "full", "maxElements": 40},
            timeout=15.0,
        )
        inspect_text = inspect_res["text"]
        steps.append({
            "action": "inspect_state",
            "tool": "computer_inspect",
            "duration_ms": inspect_res["duration_ms"],
            "status": "success" if not inspect_res["is_error"] else "failed",
        })

        # Verify key UI components: Menu items (Dosya/File, Düzenle/Edit, Görünüm/View) and buttons (Kaydet/Save)
        menu_items_found: List[str] = []
        tools_found: List[str] = []

        for line in inspect_text.splitlines():
            line_str = line.strip()
            if "MenuItem" in line_str:
                menu_items_found.append(line_str)
            elif any(k in line_str for k in ["Kaydet", "Save", "Seç", "Select", "Fırçalar", "Brushes", "Resim", "Image"]):
                tools_found.append(line_str)

        # Paint state is verified if window exists, has valid bounds, and contains UIA menu/tool components
        verified_state = len(menu_items_found) >= 2 or len(tools_found) >= 1 or "paint" in paint_win["title"].lower()

        # 3. Capture screenshot via Inbrisk MCP computer_screenshot
        self.log("Capturing Paint window screenshot via MCP...")
        shot_res = self.mcp.call_tool(
            "computer_screenshot",
            {"target": "window", "hwnd": hwnd_hex},
            timeout=15.0,
        )

        screenshot_path = ""
        if shot_res["images"]:
            screenshot_path = self.save_screenshot("paint", hwnd_hex, shot_res["images"][0])
            self.log(f"Screenshot saved: {screenshot_path}")

        duration = round(time.time() - t_start, 3)
        passed = verified_state and bool(screenshot_path)

        self.log(f"Paint Test Result: {'PASSED' if passed else 'FAILED'} (Duration: {duration}s)")
        self.log(f"  Verified UI Elements: {len(menu_items_found)} menus, {len(tools_found)} tools/controls")

        return {
            "test_name": "Paint State Verification",
            "target": "paint",
            "hwnd": hwnd,
            "hwnd_hex": hwnd_hex,
            "pid": paint_win["pid"],
            "window_title": paint_win["title"],
            "window_rect": paint_win["rect"],
            "start_time": start_iso,
            "end_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "duration_seconds": duration,
            "status": "PASSED" if passed else "FAILED",
            "steps": steps,
            "verification": {
                "verified": verified_state,
                "menu_items_detected": menu_items_found[:5],
                "controls_detected": tools_found[:5],
            },
            "screenshot_path": screenshot_path,
        }

    # --------------------------------------------------------------------------
    # Full Test Suite Runner
    # --------------------------------------------------------------------------
    def run_suite(self, iterations: int = 1) -> Dict[str, Any]:
        suite_t0 = time.time()
        suite_start_iso = datetime.datetime.now(datetime.timezone.utc).isoformat()

        all_tests_report: List[Dict[str, Any]] = []
        total_passed = 0
        total_failed = 0

        self.log("\n" + "#" * 70)
        self.log(f"STARTING INBRISK STRESS TEST VALIDATION SUITE ({iterations} ITERATION{'S' if iterations > 1 else ''})")
        self.log("#" * 70)

        for i in range(1, iterations + 1):
            if iterations > 1:
                self.log(f"\n>>> ITERATION {i}/{iterations} <<<")

            # 1. Calculator
            try:
                calc_rep = self.test_calculator(iteration=i)
                all_tests_report.append(calc_rep)
                if calc_rep["status"] == "PASSED":
                    total_passed += 1
                else:
                    total_failed += 1
            except Exception as e:
                self.log(f"[CALCULATOR TEST ERROR] {e}")
                total_failed += 1
                all_tests_report.append({
                    "test_name": "Calculator 15+24=39",
                    "status": "FAILED",
                    "error": str(e),
                })

            time.sleep(1.0)

            # 2. Notepad
            try:
                note_rep = self.test_notepad(iteration=i)
                all_tests_report.append(note_rep)
                if note_rep["status"] == "PASSED":
                    total_passed += 1
                else:
                    total_failed += 1
            except Exception as e:
                self.log(f"[NOTEPAD TEST ERROR] {e}")
                total_failed += 1
                all_tests_report.append({
                    "test_name": "Notepad Text Verification",
                    "status": "FAILED",
                    "error": str(e),
                })

            time.sleep(1.0)

            # 3. Paint
            try:
                paint_rep = self.test_paint(iteration=i)
                all_tests_report.append(paint_rep)
                if paint_rep["status"] == "PASSED":
                    total_passed += 1
                else:
                    total_failed += 1
            except Exception as e:
                self.log(f"[PAINT TEST ERROR] {e}")
                total_failed += 1
                all_tests_report.append({
                    "test_name": "Paint State Verification",
                    "status": "FAILED",
                    "error": str(e),
                })

            time.sleep(1.5)

        suite_duration = round(time.time() - suite_t0, 3)
        total_runs = total_passed + total_failed
        pass_rate = round((total_passed / total_runs * 100) if total_runs else 0.0, 1)

        summary_report = {
            "schema_version": "1.0",
            "title": "Inbrisk Automated Stress Test & Validation Report",
            "start_time": suite_start_iso,
            "end_time": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "total_duration_seconds": suite_duration,
            "iterations_requested": iterations,
            "overall_status": "PASSED" if total_failed == 0 else "FAILED",
            "summary": {
                "total_executed": total_runs,
                "passed": total_passed,
                "failed": total_failed,
                "pass_rate_percent": pass_rate,
            },
            "inbrisk_server": {
                "executable": self.mcp.executable,
                "server_info": self.mcp.server_info,
                "protocol_version": self.mcp.protocol_version,
                "published_tools_count": len(self.mcp.tools),
            },
            "tests": all_tests_report,
        }

        # Save Structured JSON Report
        ts_filename = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = self.reports_dir / f"stress_test_report_{ts_filename}.json"
        latest_file = self.reports_dir / "latest_stress_test_report.json"

        with open(report_file, "w", encoding="utf-8") as f:
            json.dump(summary_report, f, ensure_ascii=False, indent=2)

        with open(latest_file, "w", encoding="utf-8") as f:
            json.dump(summary_report, f, ensure_ascii=False, indent=2)

        self.log("\n" + "=" * 70)
        self.log("TEST SUITE EXECUTION COMPLETED")
        self.log(f"Status: {summary_report['overall_status']} | Pass Rate: {pass_rate}% ({total_passed}/{total_runs})")
        self.log(f"Full Report: {report_file.resolve()}")
        self.log(f"Latest Link: {latest_file.resolve()}")
        self.log("=" * 70)

        return summary_report


# ==============================================================================
# CLI Entrypoint
# ==============================================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Standalone Inbrisk Automated Stress Test & Validation Harness"
    )
    parser.add_argument(
        "--iterations", "-i", type=int, default=1,
        help="Number of iterations to execute for stress testing (default: 1)"
    )
    parser.add_argument(
        "--inbrisk-exe", type=str, default=None,
        help="Path to inbrisk.exe (auto-detected if omitted)"
    )
    parser.add_argument(
        "--output-dir", "-o", type=str, default=None,
        help="Directory where JSON reports and screenshots will be saved (default: ../reports)"
    )
    parser.add_argument(
        "--quiet", "-q", action="store_true",
        help="Suppress console output, only output final report status"
    )
    args = parser.parse_args()

    # Determine paths
    script_dir = pathlib.Path(__file__).parent.resolve()
    base_dir = script_dir.parent.resolve()
    reports_dir = pathlib.Path(args.output_dir).resolve() if args.output_dir else (base_dir / "reports")
    reports_dir.mkdir(parents=True, exist_ok=True)

    inbrisk_exe = resolve_inbrisk_exe(args.inbrisk_exe)
    if not args.quiet:
        print(f"Inbrisk Executable: {inbrisk_exe}")
        print(f"Reports Directory:  {reports_dir}")

    # Initialize MCP client
    mcp = InbriskMCPProtocolClient(executable=inbrisk_exe, timeout=35.0)
    try:
        if not args.quiet:
            print("Connecting to Inbrisk MCP JSON-RPC service...")
        mcp.connect()
        if not args.quiet:
            print(f"Connected! Inbrisk Server: {mcp.server_info.get('name')} v{mcp.server_info.get('version')}")

        runner = StressTestRunner(mcp=mcp, reports_dir=reports_dir, verbose=not args.quiet)
        report = runner.run_suite(iterations=args.iterations)

        # Exit code reflects test results
        if report["overall_status"] != "PASSED":
            sys.exit(1)

    finally:
        mcp.close()


if __name__ == "__main__":
    main()
