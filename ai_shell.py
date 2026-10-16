#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Inbrisk masaustu ajani: canli MCP araclari + Ollama/OpenAI.

Tasarim:
  * Kullanici cumlelerinden anahtar kelimeyle niyet/uygulama araci secilmez.
  * MCP'nin tools/list yaniti gercek arac adlarinin ve semalarinin tek kaynagidir.
  * Arac argumanlari sessizce donusturulmez; hatalar modele geri iletilir.
  * MCP'nin reddettigi islemler baska kanaldan zorla uygulanmaz.
  * Native tool calling ve tam yapisal JSON/<tool_call> ciktilari desteklenir.
  * /sh ve ! komutlari YALNIZCA kullanici tarafindan dogrudan calistirilir.

Windows uzerinde calistirin. Harici Python bagimliligi: jsonschema (onerilir).
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import pathlib
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any


ROOT = pathlib.Path(__file__).resolve().parent
CONFIG_FILE = ROOT / "config.json"
SESSION_DIR = ROOT / "sessions"
LOG_DIR = ROOT / "logs"
PROTOCOL_VERSION = "2025-06-18"
DEFAULT_EXE = pathlib.Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inbrisk" / "inbrisk.exe"


class AgentError(Exception):
    """Kullaniciya ve modele aciklanabilen uygulama hatasi."""


def load_config() -> dict[str, Any]:
    if not CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("config.json bir JSON nesnesi olmali")
        return data
    except (OSError, ValueError) as exc:
        raise AgentError(f"config.json okunamadi: {exc}") from exc


def json_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def safe_filename(name: str) -> str:
    """Oturum adini yol olarak yorumlamayi engeller; mesajlari filtrelemez."""
    if (not name or name in (".", "..") or pathlib.PurePath(name).name != name
            or "/" in name or "\\" in name or ":" in name or "\x00" in name):
        raise AgentError("Oturum adi sadece dosya adi olmali; klasor yolu olamaz.")
    return name.removesuffix(".json")


class Logger:
    def __init__(self, directory: pathlib.Path):
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f"session_{dt.datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}.jsonl"
        self._handle = self.path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def event(self, kind: str, **data: Any) -> None:
        record = {"ts": dt.datetime.now(dt.timezone.utc).isoformat(), "kind": kind, **data}
        with self._lock:
            self._handle.write(json_text(record) + "\n")
            self._handle.flush()

    def close(self) -> None:
        with self._lock:
            self._handle.close()


@dataclass
class ToolResult:
    text: str
    is_error: bool = False
    images: int = 0


def unwrap_mcp_text(text: str) -> str:
    """Inbrisk'in bilinen tum-kapsayici isaretlemesini gercekten varsa ac.

    Icerikte rastgele gecen benzer dizelere dokunmaz.
    """
    value = text.strip()
    if value.startswith("<untrusted_content") and value.endswith("</untrusted_content>"):
        close_tag_idx = value.find(">")
        if close_tag_idx != -1 and close_tag_idx < len(value) - len("</untrusted_content>"):
            return value[close_tag_idx + 1 : -len("</untrusted_content>")].strip()
    return value


class MCPClient:
    """Kalici, seri istekli, JSON-RPC stdio MCP istemcisi.

    Farkli istek ID'lerine ait yanitlari kaybetmez. Zaman asiminda bir araci
    OTOMATIK yeniden calistirmaz (bir yazma islemi tamamlanmis olabilir).
    """

    def __init__(self, executable: str, server_args: list[str] | None = None,
                 timeout: float = 45, logger: Logger | None = None):
        self.executable = executable
        self.server_args = list(server_args or [])
        self.timeout = timeout
        self.logger = logger
        self.proc: subprocess.Popen[str] | None = None
        self._responses: queue.Queue[dict[str, Any]] = queue.Queue()
        self._pending: dict[int, dict[str, Any]] = {}
        self._request_lock = threading.Lock()
        self._next_id = 0
        self.tool_defs: dict[str, dict[str, Any]] = {}
        self.server_info: dict[str, Any] = {}
        self.protocol_version = PROTOCOL_VERSION

    @property
    def connected(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def connect(self) -> None:
        self.close()
        exe = self.executable
        resolved = str(pathlib.Path(exe).expanduser()) if pathlib.Path(exe).is_file() else shutil.which(exe)
        if not resolved:
            raise AgentError(f"Inbrisk MCP bulunamadi: {exe}")
        self.proc = subprocess.Popen(
            [resolved, "mcp", *self.server_args],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            encoding="utf-8", errors="replace", text=True, bufsize=1,
        )
        self._responses = queue.Queue()
        self._pending.clear()
        self._next_id = 0
        threading.Thread(target=self._read_loop, args=(self.proc,), daemon=True).start()
        try:
            response = self._request("initialize", {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "inbrisk-ai-shell", "version": "3.0"},
            }, timeout=min(self.timeout, 20))
            result = response.get("result")
            if not isinstance(result, dict):
                raise AgentError("MCP initialize gecerli yanit vermedi")
            self.protocol_version = result.get("protocolVersion", PROTOCOL_VERSION)
            self.server_info = result.get("serverInfo") or {}
            self._notify("notifications/initialized", {})
            self.refresh_tools()
        except Exception:
            self.close()
            raise

    def _read_loop(self, proc: subprocess.Popen[str]) -> None:
        if proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                try:
                    item = json.loads(line)
                except json.JSONDecodeError:
                    if self.logger:
                        self.logger.event("mcp_invalid_stdout", sample=line[:300])
                    continue
                if isinstance(item, dict) and "id" in item:
                    self._responses.put(item)
        except (OSError, ValueError):
            pass

    def _send(self, payload: dict[str, Any]) -> None:
        if not self.connected or self.proc is None or self.proc.stdin is None:
            raise AgentError("MCP sunucu baglantisi kapali")
        try:
            self.proc.stdin.write(json_text(payload) + "\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise AgentError(f"MCP stdin hatasi: {exc}") from exc

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method: str, params: dict[str, Any],
                 timeout: float | None = None) -> dict[str, Any]:
        with self._request_lock:
            self._next_id += 1
            req_id = self._next_id
            self._send({"jsonrpc": "2.0", "id": req_id,
                        "method": method, "params": params})
            deadline = time.monotonic() + (timeout if timeout is not None else self.timeout)
            while time.monotonic() < deadline:
                if req_id in self._pending:
                    response = self._pending.pop(req_id)
                else:
                    try:
                        response = self._responses.get(timeout=min(0.2, max(0.001, deadline-time.monotonic())))
                    except queue.Empty:
                        if not self.connected:
                            raise AgentError("MCP sureci kapandi")
                        continue
                if response.get("id") != req_id:
                    other_id = response.get("id")
                    if isinstance(other_id, int):
                        self._pending[other_id] = response
                    continue
                if "error" in response:
                    raise AgentError(f"MCP {method}: {json_text(response['error'])}")
                return response
            raise AgentError(f"MCP {method}: yanit zaman asimi; eylem tamamlanmis olabilir, otomatik tekrar yok")

    def refresh_tools(self) -> dict[str, dict[str, Any]]:
        response = self._request("tools/list", {})
        definitions: dict[str, dict[str, Any]] = {}
        seen_cursors: set[str] = set()
        while True:
            result = response.get("result") or {}
            tools = result.get("tools") or []
            if not isinstance(tools, list):
                raise AgentError("MCP tools/list bir liste dondurmedi")
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                name = tool.get("name")
                if isinstance(name, str) and name:
                    definitions[name] = tool
            cursor = result.get("nextCursor")
            if not cursor:
                break
            if not isinstance(cursor, str) or cursor in seen_cursors:
                raise AgentError("MCP tools/list gecersiz veya tekrarlanan cursor")
            seen_cursors.add(cursor)
            response = self._request("tools/list", {"cursor": cursor})
        self.tool_defs = definitions
        return definitions

    def call(self, name: str, args: dict[str, Any]) -> ToolResult:
        if name not in self.tool_defs:
            raise AgentError(f"MCP araci yok: {name}. /tools ile gercek listeyi gor.")
        validate_arguments(self.tool_defs[name].get("inputSchema") or {}, args)
        reply = self._request("tools/call", {"name": name, "arguments": args})
        result = reply.get("result")
        if not isinstance(result, dict):
            raise AgentError(f"MCP {name}: hatali tools/call yaniti")
        blocks = result.get("content") or []
        parts = []
        image_count = 0
        for block in blocks:
            if not isinstance(block, dict):
                continue
            if block.get("type") == "text":
                # MCP sunucusunun untrusted_content sinirlarini koruyoruz.
                # Bu etiketler model icin veri/talimat ayrimini belirtir.
                parts.append(str(block.get("text", "")))
            elif block.get("type") == "image":
                image_count += 1
                parts.append("[MCP goruntu verisi uretildi; metin modeli bu goruntuyu inceleyemez]")
            elif block.get("type") == "resource":
                parts.append("[MCP kaynagi: " + json_text(block.get("resource", {}))[:1000] + "]")
        if "structuredContent" in result:
            parts.append(json_text(result["structuredContent"]))
        if not parts:
            parts.append(json_text(result))
        content = "\n".join(parts)
        error = bool(result.get("isError"))
        # Bazi sunucular MCP isError yerine kendi sonuc nesnelerini kullanir.
        for part in parts:
            try:
                parsed = json.loads(unwrap_mcp_text(part))
            except (json.JSONDecodeError, TypeError):
                continue
            if isinstance(parsed, dict) and (parsed.get("success") is False
                                             or parsed.get("isError") is True
                                             or parsed.get("error") not in (None, "")):
                error = True
        if self.logger:
            self.logger.event("mcp_result", tool=name, arguments=args,
                              error=error, text=content[:6000], images=image_count)
        return ToolResult(content, error, image_count)

    def close(self) -> None:
        process, self.proc = self.proc, None
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
        if process.stdin:
            process.stdin.close()
        if process.stdout:
            process.stdout.close()


def validate_arguments(schema: dict[str, Any], args: Any) -> None:
    """Sunucunun ilan ettigi JSON Schema kullanilir; isim/deger tahmini yok."""
    if not isinstance(args, dict):
        raise AgentError("Arac arguments alani JSON nesnesi olmali")
    if not isinstance(schema, dict):
        raise AgentError("MCP araci gecersiz inputSchema yayinladi")
    try:
        import jsonschema
    except ImportError:
        # Sunucu tools/call sirasinda da kendi semasini dogrular.
        # Bu durumda ek istemci dogrulamasi yoktur, ama sessiz donusum de yoktur.
        return
    try:
        validator_cls = jsonschema.validators.validator_for(schema)
        validator_cls.check_schema(schema)
        error = next(iter(validator_cls(schema).iter_errors(args)), None)
    except jsonschema.exceptions.SchemaError as exc:
        raise AgentError(f"MCP'nin yayinladigi JSON Schema hatali: {exc.message}") from exc
    if error:
        path = ".".join(str(x) for x in error.absolute_path) or "arguments"
        raise AgentError(f"Gecersiz MCP argumani ({path}): {error.message}")


IGNORED_TOOLS = {
    "computer_save_recipe", "computer_run_recipe", "computer_list_recipes",
    "computer_pause_run", "computer_resume_run", "ghost_control_open",
    "computer_ghost_control", "computer_run", "computer_capabilities",
    "computer_toolset", "computer_reset_input", "computer_do",
    "computer_batch", "computer_observe", "computer_inspect",
    "computer_find", "computer_wait_for", "computer_read"
}

CORE_TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "computer_launch": {
        "type": "object",
        "properties": {
            "app": {"type": "string", "description": "Uygulama adı (örn: Notepad, Calculator, Paint)"},
            "ghost": {"type": "boolean", "description": "Arka planda gizlice açmak için true, normal kullanıcı ekranı için false."}
        },
        "required": ["app"]
    },
    "computer_windows": {
        "type": "object",
        "properties": {}
    },
    "computer_close_window": {
        "type": "object",
        "properties": {
            "window": {"type": "string", "description": "Kapatılacak pencere başlığı veya işlem adı"}
        }
    },
    "computer_type": {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Yazılacak metin"}
        },
        "required": ["text"]
    },
    "computer_click": {
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "X piksel koordinatı"},
            "y": {"type": "integer", "description": "Y piksel koordinatı"}
        }
    },
    "computer_hotkey": {
        "type": "object",
        "properties": {
            "keys": {"type": "string", "description": "Tuş kombinasyonu (örn: 'ctrl+s', 'enter')"}
        },
        "required": ["keys"]
    },
    "computer_screenshot": {
        "type": "object",
        "properties": {}
    }
}

TOOL_DESCRIPTIONS_TR: dict[str, str] = {
    "computer_launch": "Bir Windows uygulamasını ekranda (ghost: false) veya izole arka planda (ghost: true) açar/başlatır.",
    "computer_click": "Ekranda belirtilen koordinata tıklar.",
    "computer_type": "Aktif pencereye metin yazar.",
    "computer_windows": "Şu anda açık olan pencereleri listeler.",
    "computer_screenshot": "Ekranın anlık görüntüsünü alır.",
    "computer_close_window": "Açık olan bir pencereyi kapatır.",
    "computer_hotkey": "Klavye kısayolu basar."
}


EN_PARAM_DESCRIPTIONS: dict[str, str] = {
    "app": "Application name (e.g. Notepad, Calculator, Paint).",
    "ghost": "true = run in the isolated background desktop; false = visible on the user's screen.",
    "text": "Text to type.",
    "window": "Window title or process name.",
    "keys": "Key combination (e.g. 'ctrl+s', 'enter').",
    "x": "X pixel coordinate.",
    "y": "Y pixel coordinate."
}


TOOL_DESCRIPTIONS_EN: dict[str, str] = {
    "computer_launch": "Launch a Windows app on the user's screen (ghost: false) or in the isolated background desktop (ghost: true).",
    "computer_click": "Click at the given pixel coordinates on the screen.",
    "computer_type": "Type text into the currently active window.",
    "computer_windows": "List the windows that are currently open.",
    "computer_screenshot": "Take a screenshot of the current screen.",
    "computer_close_window": "Close an open window.",
    "computer_hotkey": "Press a keyboard shortcut."
}


def published_tools(mcp: MCPClient | None) -> list[dict[str, Any]]:
    """Modelin gordugu arac tanimlari, dogrudan MCP tools/list'ten uretilir."""
    if mcp is None or not mcp.connected:
        return []
    result = []
    for name, tool in mcp.tool_defs.items():
        if name in IGNORED_TOOLS:
            continue
        schema = CORE_TOOL_SCHEMAS.get(name) or tool.get("inputSchema") or {"type": "object", "properties": {}}
        desc = TOOL_DESCRIPTIONS_TR.get(name) or tool.get("description", "") or f"MCP araci {name}"
        result.append({"type": "function", "function": {
            "name": name,
            "description": desc,
            "parameters": schema,
        }})
    return result


SYSTEM_PROMPT = ""

# Needle 3 yalnizca calistirici mod: konusma yasak, sadece tool cagrisi.
# Is bittiginde tek kelime onay ("DONE") donmesi beklenir.
NEEDLE_SYSTEM_PROMPT = (
    "Silent action executor. Execute the user's instruction using the "
    "provided tools. Never narrate, never explain, never answer in text: "
    "reply only with function calls that fulfill the instruction. When the "
    "tool results already show the requested action succeeded, reply with "
    "the single word DONE and nothing else. If the instruction maps to none "
    "of the tools, make no calls."
)


STANDARD_APP_LAUNCH_MAP: dict[str, str] = {
    "not defteri": "Notepad",
    "notepad": "Notepad",
    "hesap makinesi": "Calculator",
    "hesap": "Calculator",
    "calculator": "Calculator",
    "calc": "Calculator",
    "paint": "Paint",
    "boyama": "Paint",
    "mspaint": "Paint",
    "dosya gezgini": "explorer.exe",
    "gezgin": "explorer.exe",
    "explorer": "explorer.exe",
    "file explorer": "explorer.exe",
    "ayarlar": "ms-settings:",
    "settings": "ms-settings:",
    "komut istemi": "cmd.exe",
    "cmd": "cmd.exe",
    "terminal": "wt.exe",
}


def map_turkish_window(value: str) -> tuple[str, str | None]:
    """Map English or non-standard window titles to Turkish localized title & process."""
    val = value.strip()
    low = val.lower()

    # Calculator
    if low in ("calculator", "calc", "calc.exe", "calculatorapp", "hesap", "hesap makinesi") or "hesap" in low:
        return ("Hesap Makinesi", "ApplicationFrameHost.exe")

    # Notepad
    if (low in ("notepad", "notepad.exe", "untitled - notepad", "adsız - not defteri", "adsız, not defteri", "not defteri")
            or "notepad" in low or "not defteri" in low):
        if "adsız" in low:
            return ("Adsız, Not Defteri", "Notepad.exe")
        return ("Not Defteri", "Notepad.exe")

    # Paint
    if (low in ("paint", "mspaint", "mspaint.exe", "untitled - paint", "adsız - paint", "boyama")
            or "paint" in low or "boyama" in low):
        if "adsız" in low:
            return ("Adsız - Paint", "mspaint.exe")
        return ("Paint", "mspaint.exe")

    # File Explorer
    if (low in ("explorer", "file explorer", "explorer.exe", "dosya gezgini", "gezgin")
            or "gezgini" in low or "explorer" in low):
        return ("Dosya Gezgini", "explorer.exe")

    # Settings
    if low in ("settings", "systemsettings.exe", "ayarlar") or "ayarlar" in low:
        return ("Ayarlar", "SystemSettings.exe")

    return (val, None)


def optimize_computer_launch(arguments: dict[str, Any], user_text: str = "") -> dict[str, Any]:
    """Ensure waitFor option can be passed or defaults sensibly for standard apps."""
    args = dict(arguments)
    user_lower = user_text.lower()

    # Intent check for foreground vs background (ghost) execution
    bg_keywords = ("arka plan", "arkada", "ghost", "gizli", "sessiz", "rahatsız etmeden",
                   "behind the scenes", "down-low", "background", "hidden")
    fg_keywords = ("ekranımda", "ekranda", "ekran", "göster", "göreyim", "ön plan", "main monitor")
    if any(fg in user_lower for fg in fg_keywords):
        args["ghost"] = False
    elif any(bg in user_lower for bg in bg_keywords):
        args["ghost"] = True
    elif "ghost" not in args:
        args["ghost"] = False

    wait_for = args.get("waitFor")
    # If waitFor is not specified or empty, default sensibly for standard apps
    if wait_for is None or (isinstance(wait_for, str) and not wait_for.strip()):
        raw_app = str(args.get("app") or args.get("executable") or args.get("search") or "").lower().strip()
        base_app = raw_app.removesuffix(".exe").strip()
        standard_apps = {
            "notepad", "not defteri",
            "calculator", "hesap makinesi", "calc",
            "paint", "mspaint", "boyama",
            "explorer", "dosya gezgini",
            "settings", "ayarlar",
            "cmd", "komut istemi", "terminal", "wt"
        }
        if any(std in base_app or base_app in std for std in standard_apps) or args.get("app"):
            args["waitFor"] = "process"

    # Normalize app name if known Turkish/alias
    raw_app = str(args.get("app") or "").lower().strip()
    if raw_app in STANDARD_APP_LAUNCH_MAP:
        args["app"] = STANDARD_APP_LAUNCH_MAP[raw_app]

    # Inbrisk requires exactly one identifier: app|search|executable|path|aumid|uri
    identifiers = ["app", "executable", "path", "search", "aumid", "uri"]
    present = [k for k in identifiers if args.get(k)]
    if len(present) > 1:
        keep = "app" if "app" in present else present[0]
        for k in present:
            if k != keep:
                del args[k]

    return args


def optimize_tool_dispatch(name: str, arguments: dict[str, Any], user_text: str = "") -> tuple[str, dict[str, Any]]:
    """Optimize tool dispatch for Turkish localized window titles and app launch options."""
    args = dict(arguments)

    if name == "computer_launch":
        return name, optimize_computer_launch(args, user_text)

    target = args.get("target")
    if isinstance(target, dict):
        target = dict(target)
        win = target.get("window")
        if isinstance(win, str) and win:
            mapped_win, proc = map_turkish_window(win)
            target["window"] = mapped_win
            if proc and not target.get("process"):
                target["process"] = proc
        proc_val = target.get("process")
        if isinstance(proc_val, str) and proc_val:
            _, mapped_proc = map_turkish_window(proc_val)
            if mapped_proc:
                target["process"] = mapped_proc
        args["target"] = target
    elif isinstance(target, str) and target:
        mapped_win, proc = map_turkish_window(target)
        new_target: dict[str, Any] = {"window": mapped_win}
        if proc:
            new_target["process"] = proc
        args["target"] = new_target

    if "window" in args and isinstance(args["window"], str):
        mapped_win, proc = map_turkish_window(args["window"])
        args["window"] = mapped_win
        if proc and "process" in args and not args.get("process"):
            args["process"] = proc

    if "titleContains" in args and isinstance(args["titleContains"], str):
        mapped_win, proc = map_turkish_window(args["titleContains"])
        args["titleContains"] = mapped_win
        if proc and not args.get("process"):
            args["process"] = proc

    if "process" in args and isinstance(args["process"], str):
        _, proc = map_turkish_window(args["process"])
        if proc:
            args["process"] = proc

    return name, args



def build_prompt() -> str:
    if not SYSTEM_PROMPT:
        return ""
    return SYSTEM_PROMPT + f"\nYerel tarih-saat: {dt.datetime.now().astimezone().isoformat(timespec='seconds')}."


def decode_args(value: Any) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise AgentError(f"Arac arguments gecersiz JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise AgentError("Arac arguments JSON nesnesi olmali")
    return value


def normalize_calls(calls: Any) -> list[dict[str, Any]]:
    """Native fonksiyon cagrilari: adlari/argumanlari asla tahmin etmez."""
    if not isinstance(calls, list):
        raise AgentError("tool_calls liste olmali")
    normalized = []
    for i, call in enumerate(calls):
        if not isinstance(call, dict):
            raise AgentError("tool_calls elemanlari nesne olmali")
        func = call.get("function") or {}
        name = func.get("name")
        if not isinstance(name, str) or not name:
            raise AgentError("Tool cagrisi adi eksik")
        args = decode_args(func.get("arguments", {}))
        normalized.append({"id": str(call.get("id") or f"call_{uuid.uuid4().hex}"),
                           "type": "function", "function": {"name": name, "arguments": args}})
    return normalized


def parse_text_tool_calls(content: str) -> list[dict[str, Any]]:
    """Metinsel ciktilardan yapisal arac cagrisi ayristirir.

    1. <tool_call> ... </tool_call> bloklari
    2. JSON nesnesi veya listesi
    3. Python/DSL fonksiyon cagrilari: tool_name(arg=val, ...)
    """
    source = content.strip()
    if source.startswith("```") and source.endswith("```"):
        lines = source.splitlines()
        if len(lines) >= 2:
            source = "\n".join(lines[1:-1]).strip()

    # 1. XML <tool_call>...</tool_call>
    xml_matches = re.findall(r"<tool_call>(.*?)</tool_call>", source, re.DOTALL)
    if xml_matches:
        calls = []
        for raw in xml_matches:
            try:
                data = json.loads(raw.strip())
                if isinstance(data, dict) and "name" in data:
                    args = decode_args(data.get("arguments", {}))
                    calls.append({"id": f"text_call_{uuid.uuid4().hex}", "type": "function",
                                  "function": {"name": data["name"], "arguments": args}})
            except Exception:
                pass
        if calls:
            return calls

    # 2. JSON dogrudan
    try:
        data = json.loads(source)
        if isinstance(data, dict) and "name" in data:
            args = decode_args(data.get("arguments", {}))
            return [{"id": f"text_call_{uuid.uuid4().hex}", "type": "function",
                     "function": {"name": data["name"], "arguments": args}}]
        elif isinstance(data, list) and data and all(isinstance(x, dict) and "name" in x for x in data):
            calls = []
            for item in data:
                args = decode_args(item.get("arguments", {}))
                calls.append({"id": f"text_call_{uuid.uuid4().hex}", "type": "function",
                              "function": {"name": item["name"], "arguments": args}})
            return calls
    except Exception:
        pass

    # 3. Python/DSL stili: tool_name(param1=val1, param2=val2)
    match_fn = re.match(r"^([a-zA-Z_][a-zA-Z0-9_]*)\s*\((.*)\)$", source, re.DOTALL)
    if match_fn:
        fn_name = match_fn.group(1)
        args_str = match_fn.group(2).strip()
        args: dict[str, Any] = {}
        if args_str:
            tokens = re.findall(r'([a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*("[^"]*"|\'[^\']*\'|\[.*?\]|\{.*?\}|[^,]+)', args_str)
            for k, v in tokens:
                v = v.strip()
                if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
                    args[k] = v[1:-1]
                elif v.lower() == "true":
                    args[k] = True
                elif v.lower() == "false":
                    args[k] = False
                elif v.lower() in ("none", "null"):
                    args[k] = None
                else:
                    try:
                        args[k] = json.loads(v)
                    except Exception:
                        args[k] = v
        return [{"id": f"text_call_{uuid.uuid4().hex}", "type": "function",
                 "function": {"name": fn_name, "arguments": args}}]

    return []


def _http_json(url: str, payload: dict[str, Any] | None, timeout: float,
               api_key: str = "", retries: int = 2) -> dict[str, Any]:
    data = json_text(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                value = json.loads(resp.read().decode("utf-8"))
            if not isinstance(value, dict):
                raise AgentError("Model HTTP yaniti JSON nesnesi degil")
            return value
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == retries:
                details = exc.read(800).decode("utf-8", errors="replace")
                raise AgentError(f"HTTP {exc.code}: {details}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            if attempt == retries:
                raise AgentError(f"Model servisine baglanilamadi: {exc}") from exc
        time.sleep(min(2 ** attempt, 4))
    raise AgentError("HTTP istekleri basarisiz")


@dataclass
class ChatResponse:
    content: str
    calls: list[dict[str, Any]] = field(default_factory=list)


class Backend:
    def __init__(self, name: str, base_url: str, api_key: str = "",
                 temperature: float = 0, timeout: int = 180, think: bool = False,
                 num_ctx: int = 32768, needle_tokens: int = 288):
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.temperature = temperature
        self.timeout = timeout
        self.think = think
        self.num_ctx = num_ctx
        self.needle_tokens = needle_tokens
        # Needle 3 yarim-durumlu yerel motor icin ozel alanlar.
        self._needle_agent: Any | None = None
        self._needle_tools_sig: str | None = None
        self._needle_system: str | None = None

    def chat(self, model: str, messages: list[dict[str, Any]],
             tools: list[dict[str, Any]]) -> ChatResponse:
        if self.name == "needle":
            return self._needle_chat(model, messages, tools)
        if self.name == "ollama":
            body: dict[str, Any] = {
                "model": model, "messages": messages, "stream": False,
                "options": {"temperature": self.temperature, "num_ctx": self.num_ctx}, "think": self.think,
            }
            if tools:
                body["tools"] = tools
            data = _http_json(self.base_url + "/api/chat", body, self.timeout)
            msg = data.get("message") or {}
        else:
            body = {"model": model, "messages": messages,
                    "temperature": self.temperature, "stream": False}
            if tools:
                body["tools"] = tools
            data = _http_json(self.base_url + "/chat/completions", body,
                              self.timeout, self.api_key)
            choices = data.get("choices") or []
            if not choices:
                raise AgentError("OpenAI-uyumlu endpoint choices dondurmedi")
            msg = choices[0].get("message") or {}
        if not isinstance(msg, dict):
            raise AgentError("Model yaniti hatali")
        content = msg.get("content") or ""
        if not isinstance(content, str):
            # Metinsel olmayan icerigi sozde eyleme cevirmeyiz.
            content = json_text(content)
        native = msg.get("tool_calls") or []
        calls = normalize_calls(native) if native else parse_text_tool_calls(content)
        if not native and calls:
            content = ""
        return ChatResponse(content, calls)

    def _needle_schemas(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """OpenAI tarzi tanimlari Needle'in bekledigi duz semaya donusturur."""
        schemas = []
        for tool in tools or []:
            fn = tool.get("function") if isinstance(tool, dict) else None
            if not isinstance(fn, dict) or not fn.get("name"):
                continue
            name = str(fn["name"])
            parameters = fn.get("parameters") or {"type": "object", "properties": {}}
            if name in EN_PARAM_DESCRIPTIONS and isinstance(parameters.get("properties"), dict):
                # MCP'nin ilan ettigi semayi bozmadan sadece aciklamalari cevir.
                parameters = dict(parameters)
                parameters["properties"] = {
                    key: ({**prop, "description": EN_PARAM_DESCRIPTIONS[name]}
                          if isinstance(prop, dict) else prop)
                    for key, prop in parameters["properties"].items()
                }
            schemas.append({
                "name": name,
                "description": TOOL_DESCRIPTIONS_EN.get(name) or str(fn.get("description") or ""),
                "parameters": parameters,
            })
        return schemas

    def _needle_prompt(self, messages: list[dict[str, Any]]) -> tuple[str, bool, list[dict[str, Any]]]:
        """Son turu Needle'in tek-prompt sozlesmesine yansitir.

        Donus: (prompt, yeni_kullanici_turu_mu, arac_sonuclari).
        """
        tool_results: list[dict[str, Any]] = []
        prompt = ""
        for message in messages:
            role = message.get("role")
            if role == "user":
                prompt = str(message.get("content") or "")
            elif role == "tool":
                tool_results.append({
                    "tool": message.get("name") or message.get("tool_call_id"),
                    "content": message.get("content"),
                })
        fresh_user = bool(messages) and messages[-1].get("role") == "user"
        if tool_results:
            prompt = (prompt + "\n\n" if prompt else "") + json_text({"tool_results": tool_results})
        return prompt, fresh_user, tool_results

    def _needle_chat(self, model: str, messages: list[dict[str, Any]],
                     tools: list[dict[str, Any]]) -> ChatResponse:
        del model  # Needle 3 tek model ve tek agirlik kumesiyle gelir.
        try:
            import needle
        except ImportError as exc:
            raise AgentError("needle paketi kurulu degil: pip install cactus-needle") from exc
        # Needle her zaman sessiz-executor sistem promptu ile calisir; genel
        # konusma sistem prompt'unu (SYSTEM_PROMPT) hicbir zaman kullanmaz.
        system = NEEDLE_SYSTEM_PROMPT
        schemas = self._needle_schemas(tools)
        signature = json_text(schemas)
        # needle_init statik oneki (sistem + arac semalari) token eder; sema
        # veya sistem degisir ise motor yeniden yuklenmek zorundadir.
        if (self._needle_agent is None or signature != self._needle_tools_sig
                or system != self._needle_system):
            if self._needle_agent is not None:
                self._needle_agent.close()
                self._needle_agent = None
            try:
                self._needle_agent = needle.Needle(
                    system=system or None, tools=schemas, stateless=False, generation=3)
            except (RuntimeError, ValueError) as exc:
                raise AgentError(
                    "Needle 3 motoru baslatilamadi (arac semalari model "
                    "baglamina sigmiyor olabilir): " + str(exc)) from exc
            self._needle_tools_sig = signature
            self._needle_system = system
        prompt, fresh_user, tool_results = self._needle_prompt(messages)
        assert self._needle_agent is not None
        if fresh_user:
            # Bagimsiz sorgular arasinda konusma baglamini sifirla.
            self._needle_agent.reset()
        envelope = self._needle_agent.complete(prompt, max_new_tokens=self.needle_tokens)
        if not isinstance(envelope, dict):
            raise AgentError("Needle yaniti JSON nesnesi degil")
        raw_calls = envelope.get("function_calls") or []
        normalized = normalize_calls([
            {"id": f"needle_{uuid.uuid4().hex}", "type": "function",
             "function": {"name": str(call["name"]), "arguments": call.get("arguments") or {}}}
            for call in raw_calls
            if isinstance(call, dict) and call.get("name")
        ])
        content = str(envelope.get("reasoning") or envelope.get("reason") or "")
        if envelope.get("results") and not normalized:
            content = (content + "\n" if content else "") + json_text(envelope["results"])
        # complete() yalnizca arac sonucu olmadigindan burada "results" uretmez;
        # bos yanitli tur icin tool_results ozeti loglansin.
        if not normalized and not content:
            content = ""
        del tool_results
        return ChatResponse(content, normalized)

    def models(self) -> list[str]:
        if self.name == "needle":
            return ["needle3"]
        data = _http_json(url, None, min(self.timeout, 10), self.api_key, retries=0)
        raw = data.get("models", []) if self.name == "ollama" else data.get("data", [])
        return [m.get("name" if self.name == "ollama" else "id") for m in raw
                if isinstance(m, dict) and m.get("name" if self.name == "ollama" else "id")]


def direct_shell(command: str, timeout: int) -> str:
    """Yalnizca yerel kullanicinin /sh veya ! ile yazdigi komut.

    Model bu fonksiyona erisemez. Kabuk komutlarina kelime filtresi uygulanmaz.
    """
    if os.name != "nt":
        raise AgentError("PowerShell dogrudan komut modu Windows gerektirir")
    exe = shutil.which("pwsh") or shutil.which("powershell.exe")
    if not exe:
        raise AgentError("PowerShell kurulu degil")
    try:
        done = subprocess.run([exe, "-NoProfile", "-NonInteractive", "-Command", command],
                              capture_output=True, timeout=timeout,
                              encoding="utf-8", errors="replace", shell=False)
    except subprocess.TimeoutExpired as exc:
        raise AgentError(f"PowerShell zaman asimi ({timeout}s)") from exc
    output = (done.stdout + "\n" + done.stderr).strip()
    return f"ExitCode={done.returncode}\n{output}".strip()


def save_session(path: pathlib.Path, messages: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(messages, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def read_session(path: pathlib.Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(m, dict) and "role" in m for m in data):
        raise AgentError("Oturum gecersiz mesaj yapisina sahip")
    return data


def trim_history(messages: list[dict[str, Any]], max_user_turns: int) -> None:
    """Kullanici turu sinirlarini korur; assistant/tool cagrilarini koparmaz."""
    user_indices = [i for i, m in enumerate(messages) if m.get("role") == "user"]
    if max_user_turns < 1 or len(user_indices) <= max_user_turns:
        return
    start = user_indices[-max_user_turns]
    system = [m for m in messages[:start] if m.get("role") == "system"]
    messages[:] = system + messages[start:]


def _result_message(call: dict[str, Any], result: str, backend: str) -> dict[str, Any]:
    msg = {"role": "tool", "tool_call_id": call["id"], "content": result}
    if backend == "ollama":
        msg["name"] = call["function"]["name"]
    return msg


@dataclass
class Runtime:
    model: str
    backend: Backend
    mcp: MCPClient | None
    logger: Logger
    max_iterations: int
    history_turns: int
    shell_timeout: int
    messages: list[dict[str, Any]] = field(default_factory=list)
    last_call_failed: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.messages:
            p = build_prompt()
            self.messages = [{"role": "system", "content": p}] if p else []

    def tools(self) -> list[dict[str, Any]]:
        return published_tools(self.mcp)

    def reset_conversation(self) -> None:
        p = build_prompt()
        self.messages = [{"role": "system", "content": p}] if p else []
        self.last_call_failed.clear()

    def ask(self, user: str) -> str:
        # Saat bilgisi ilk calistirma tarihine takilip kalmasin.
        p = build_prompt()
        if p:
            if self.messages and self.messages[0].get("role") == "system":
                self.messages[0] = {"role": "system", "content": p}
            else:
                self.messages.insert(0, {"role": "system", "content": p})
        else:
            if self.messages and self.messages[0].get("role") == "system":
                self.messages.pop(0)
        self.messages.append({"role": "user", "content": user})
        self.logger.event("user", content=user)
        self.last_call_failed.clear()
        tools = self.tools()
        for iteration in range(self.max_iterations):
            self.logger.event("model_request", iteration=iteration+1, model=self.model)
            reply = self.backend.chat(self.model, self.messages, tools)
            if not reply.calls:
                answer = reply.content.strip()
                if not answer:
                    answer = "Model yanit olusturmadi; herhangi bir islem basarili sayilmadi."
                self.messages.append({"role": "assistant", "content": answer})
                self.logger.event("assistant", content=answer)
                trim_history(self.messages, self.history_turns)
                return answer
            # Provider tarafindan raporlanan native cagrilari sonraki turla
            # eslestirmek icin, her araca bir tool cevabi mutlaka eklenir.
            for call in reply.calls:
                fn = call["function"]
                fn["name"], fn["arguments"] = optimize_tool_dispatch(fn["name"], fn["arguments"], user)
            assistant_call = {"role": "assistant", "content": reply.content or "",
                              "tool_calls": reply.calls}
            self.messages.append(assistant_call)
            for call in reply.calls:
                fn = call["function"]
                name, arguments = fn["name"], fn["arguments"]
                signature = json.dumps({"name": name, "arguments": arguments},
                                       sort_keys=True, ensure_ascii=False,
                                       separators=(",", ":"))
                if self.last_call_failed.get(signature, 0) >= 2:
                    result = ToolResult(
                        "Bu arac ve ayni argumanlar iki defa basarisiz oldu. "
                        "Ayni istegi tekrar calistirmadim; yeni gozlem, farkli "
                        "gecerli argumanlar veya kullanici netlestirmesi gerekiyor.", True)
                else:
                    try:
                        if not self.mcp or not self.mcp.connected:
                            raise AgentError("Inbrisk MCP bagli degil, masaustu araci calistirilamadi")
                        result = self.mcp.call(name, arguments)
                    except (AgentError, ValueError, TypeError) as exc:
                        result = ToolResult(f"Arac calistirilamadi: {exc}", True)
                if result.is_error:
                    self.last_call_failed[signature] = self.last_call_failed.get(signature, 0) + 1
                else:
                    self.last_call_failed.pop(signature, None)
                self.logger.event("tool", name=name, args=arguments,
                                  result=result.text[:6000], error=result.is_error)
                print(f"\n[MCP] {name}({json_text(arguments)[:400]})")
                print(result.text[:1000] or "[Bos MCP cikti]")
                self.messages.append(_result_message(call, result.text, self.backend.name))
            # Birden cok basarisiz denemeden sonra modele geri donmeye devam
            # edebiliriz, ancak sunucu reddini kendiligimizden asmayiz.
        answer = ("Islem dongusu sinirina ulasildi. Son MCP sonuclarina bakmadan "
                  "gorevin tamamlandigini dogrulayamiyorum; daha hedefli bir "
                  "gorevle devam edilebilir.")
        self.messages.append({"role": "assistant", "content": answer})
        self.logger.event("turn_limit", limit=self.max_iterations)
        trim_history(self.messages, self.history_turns)
        return answer


HELP = """Komutlar:
  /help                 komutlari goster
  /tools                MCP araclarini ve aciklamalarini goster
  /mcp-reset            MCP'yi yeniden bagla ve arac semalarini yenile
  /model [ad]           modeli goster veya degistir
  /models               saglayicinin model listesini getir
  /backend [ad]         ollama veya openai saglayicisini sec
  /endpoint [url]       aktif saglayicinin API adresini degistir
  /timeout [saniye]     MCP/shell timeout ayari
  /clear                konusma gecmisini temizle
  /save [ad]            oturumu kaydet
  /resume [ad]          oturumu geri yukle
  /sessions             oturumlari listele
  /sh <komut>           PowerShell'i dogrudan KULLANICI olarak calistir
  !<komut>              /sh kisayolu; modele GITMEZ
  /log                  JSONL kaydinin yolunu goster
  /exit                 cikis
"""


def handle_command(line: str, runtime: Runtime, session_path: pathlib.Path,
                   backend_urls: dict[str, str], api_key: str) -> bool:
    """True -> programdan cik. Komutlar sadece kullanici girdisinden calisir."""
    cmd, _, arg = line.partition(" ")
    arg = arg.strip()
    if cmd in ("/exit", "/quit"):
        return True
    if cmd in ("/help", "/?"):
        print(HELP)
    elif cmd in ("/clear", "/reset"):
        runtime.reset_conversation()
        print("[Konusma sifirlandi]")
    elif cmd == "/tools":
        if not runtime.mcp or not runtime.mcp.connected:
            print("[MCP bagli degil]")
        else:
            for name, tool in runtime.mcp.tool_defs.items():
                print(f"{name}: {(tool.get('description') or '').strip()[:150]}")
    elif cmd == "/mcp-reset":
        if not runtime.mcp:
            print("[MCP tanimli degil]")
        else:
            try:
                runtime.mcp.connect()
                runtime.last_call_failed.clear()
                print(f"[MCP baglandi: {len(runtime.mcp.tool_defs)} arac]")
            except AgentError as exc:
                print(f"[MCP hata: {exc}]")
    elif cmd == "/model":
        if arg:
            runtime.model = arg
        print(f"[Model: {runtime.model}]")
    elif cmd == "/models":
        for model in runtime.backend.models():
            print(" ", model)
    elif cmd == "/backend":
        if arg:
            if arg not in ("ollama", "openai", "needle"):
                print("[Sadece ollama / openai / needle destekleniyor]")
            else:
                runtime.backend = Backend(arg, backend_urls[arg], api_key,
                                          runtime.backend.temperature, runtime.backend.timeout,
                                          runtime.backend.think, runtime.backend.num_ctx)
        print(f"[Backend: {runtime.backend.name}]")
    elif cmd == "/endpoint":
        if arg:
            backend_urls[runtime.backend.name] = arg.rstrip("/")
            runtime.backend.base_url = arg.rstrip("/")
        print(f"[Endpoint: {runtime.backend.base_url}]")
    elif cmd == "/timeout":
        if arg:
            value = int(arg)
            if value <= 0:
                raise AgentError("Timeout pozitif olmali")
            runtime.shell_timeout = value
            if runtime.mcp:
                runtime.mcp.timeout = value
        print(f"[Timeout: {runtime.shell_timeout}s]")
    elif cmd == "/save":
        filename = safe_filename(arg or dt.datetime.now().strftime("%Y%m%d_%H%M%S"))
        target = SESSION_DIR / f"{filename}.json"
        save_session(target, runtime.messages)
        print(f"[Kaydedildi: {target}]")
    elif cmd in ("/resume", "/load"):
        files = sorted((p for p in SESSION_DIR.glob("*.json") if p != session_path),
                       key=lambda p: p.stat().st_mtime)
        target = SESSION_DIR / f"{safe_filename(arg)}.json" if arg else (files[-1] if files else None)
        if not target or not target.is_file():
            raise AgentError("Yuklenecek oturum bulunamadi")
        runtime.messages = read_session(target)
        # Onceki surumden kalan sabit sistem prompt'unu tasimiyoruz.
        runtime.messages = [m for m in runtime.messages if m.get("role") != "system"]
        p = build_prompt()
        if p:
            runtime.messages.insert(0, {"role": "system", "content": p})
        runtime.last_call_failed.clear()
        print(f"[Yuklendi: {target}]")
    elif cmd == "/sessions":
        for path in sorted(SESSION_DIR.glob("*.json")):
            print(path.name)
    elif cmd == "/sh":
        if not arg:
            print("[/sh <PowerShell komutu>]")
        else:
            print(direct_shell(arg, runtime.shell_timeout))
    elif cmd == "/log":
        print(runtime.logger.path)
    else:
        print(f"[Bilinmeyen komut: {cmd}; /help]")
    return False


def parse_cli(config: dict[str, Any]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inbrisk MCP + LLM masaustu ajani")
    parser.add_argument("--backend", choices=("ollama", "openai", "needle", "inbrisk-agent"),
                        default=config.get("default_backend", "ollama"))
    parser.add_argument("--model", default=config.get("local_agent_model", "qwen3.5:2b"))
    parser.add_argument("--needle-tokens", type=int,
                        default=int(config.get("needle_tokens", 288)),
                        help="Needle 3 tur basina uretim token limiti")
    parser.add_argument("--ollama-url", default=config.get("ollama_url") or "http://localhost:11434")
    parser.add_argument("--base-url", default=config.get("openai_url") or "http://localhost:1234/v1")
    parser.add_argument("--api-key", default=os.environ.get("OPENAI_API_KEY") or config.get("openai_key", ""))
    parser.add_argument("--inbrisk-exe", default=config.get("inbrisk_exe", str(DEFAULT_EXE)))
    saved_args = config.get("mcp_args") or []
    if isinstance(saved_args, str):
        import shlex
        saved_args = shlex.split(saved_args)
    parser.add_argument("--mcp-arg", action="append", default=list(saved_args),
                        help="Inbrisk MCP'ye iletilecek arguman; or. --mcp-arg=--yes."
                             " Sunucu onay politikasi kullanicinin sorumlulugundadir.")
    parser.add_argument("--mcp-yes", action="store_true",
                        help="Acik istekle MCP sunucusuna --yes ekler; varsayilan kapali")
    parser.add_argument("--timeout", type=int, default=int(config.get("timeout", 45)))
    parser.add_argument("--model-timeout", type=int, default=int(config.get("model_timeout", 180)))
    parser.add_argument("--iterations", type=int, default=int(config.get("iterations", 12)))
    parser.add_argument("--history-turns", type=int, default=int(config.get("history_turns", 12)))
    parser.add_argument("--temperature", type=float, default=float(config.get("agent_temperature", 0.0)))
    parser.add_argument("--think", action="store_true", default=bool(config.get("ollama_think", False)))
    parser.add_argument("--num-ctx", type=int, default=int(config.get("num_ctx", 32768)))
    opts = parser.parse_args()
    if opts.backend == "inbrisk-agent":
        # Eski CLI/config adinin Ollama ile ayni backend olmasi.
        opts.backend = "ollama"
    if opts.mcp_yes and "--yes" not in opts.mcp_arg:
        opts.mcp_arg.append("--yes")
    if opts.needle_tokens <= 0:
        parser.error("needle-tokens pozitif olmali")
    if opts.timeout <= 0 or opts.model_timeout <= 0 or opts.iterations <= 0:
        parser.error("timeout ve iterations pozitif olmali")
    if opts.history_turns < 1:
        parser.error("history-turns en az 1 olmali")
    if not (0 <= opts.temperature <= 2):
        parser.error("temperature 0..2 araliginda olmali")
    return opts


def ensure_ollama_running(base_url: str) -> None:
    """Yalnizca varsayilan yerel Ollama endpoint'i icin otomatik baslatma."""
    from urllib.parse import urlsplit
    endpoint = urlsplit(base_url)
    if (endpoint.hostname not in ("localhost", "127.0.0.1", "::1")
            or endpoint.port not in (None, 11434)):
        return
    try:
        _http_json(base_url.rstrip("/") + "/api/tags", None, 2, retries=0)
        return
    except AgentError:
        pass
    executable = shutil.which("ollama")
    if not executable:
        print("[Ollama kurulu degil veya PATH'te bulunamadi]")
        return
    print("[Ollama servisi baslatiliyor]")
    subprocess.Popen([executable, "serve"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
                                    | getattr(subprocess, "DETACHED_PROCESS", 0)))
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            _http_json(base_url.rstrip("/") + "/api/tags", None, 2, retries=0)
            return
        except AgentError:
            time.sleep(0.5)
    print("[Ollama servisi henuz yanit vermiyor]")


def main() -> int:
    try:
        cfg = load_config()
        opts = parse_cli(cfg)
    except AgentError as exc:
        print(f"[Hata] {exc}", file=sys.stderr)
        return 2
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass
    logger = Logger(LOG_DIR)
    mcp = MCPClient(opts.inbrisk_exe, opts.mcp_arg, opts.timeout, logger)
    try:
        mcp.connect()
        print(f"[MCP] {mcp.server_info.get('name', 'Inbrisk')} - {len(mcp.tool_defs)} arac")
    except (AgentError, OSError) as exc:
        print(f"[MCP baglanti uyarisi] {exc}")
    backend_urls = {"ollama": opts.ollama_url, "openai": opts.base_url, "needle": ""}
    if opts.backend == "ollama":
        ensure_ollama_running(opts.ollama_url)
    backend = Backend(opts.backend, backend_urls[opts.backend], opts.api_key,
                      opts.temperature, opts.model_timeout, opts.think, opts.num_ctx,
                      opts.needle_tokens)
    runtime = Runtime(opts.model, backend, mcp, logger,
                      opts.iterations, opts.history_turns, opts.timeout)
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    auto_path = SESSION_DIR / f"auto_{dt.datetime.now():%Y%m%d_%H%M%S}_{os.getpid()}.json"
    print(f"[Hazir] Model: {opts.model} | Backend: {opts.backend} | /help | /exit")
    try:
        while True:
            try:
                raw = input("> ").strip()
            except EOFError:
                break
            if not raw:
                continue
            if raw.lower() in ("cikis", "exit", "quit"):
                break
            try:
                if raw.startswith("!"):
                    print(direct_shell(raw[1:], runtime.shell_timeout))
                elif raw.startswith("/"):
                    if handle_command(raw, runtime, auto_path, backend_urls, opts.api_key):
                        break
                else:
                    print("\n" + runtime.ask(raw) + "\n")
                save_session(auto_path, runtime.messages)
            except (AgentError, ValueError, OSError, urllib.error.URLError) as exc:
                logger.event("error", message=str(exc))
                print(f"[Hata] {exc}")
            except KeyboardInterrupt:
                print("\n[Islem kullanici tarafindan kesildi. MCP islemi otomatik tekrar edilmez.]")
    except KeyboardInterrupt:
        print("\n[Cikis]")
    finally:
        try:
            save_session(auto_path, runtime.messages)
        except OSError:
            pass
        mcp.close()
        logger.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
