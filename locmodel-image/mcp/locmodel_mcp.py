#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""LocModel image MCP server.

Bridges ZCode to the local LocModel image engine, which runs
stable-diffusion.cpp's sd-server.exe on http://127.0.0.1:<port> with an
A1111-compatible API (/sdapi/v1/txt2img, /sdapi/v1/img2img, /sdapi/v1/loras)
plus LocModel's own /sdcpp/v1/* endpoints.

Zero third-party dependencies: MCP is JSON-RPC 2.0 over stdio
(newline-delimited JSON). Everything is stdlib.
"""

import base64
import json
import os
import re
import subprocess
import sys
import time
import traceback
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

SERVER_NAME = "locmodel-image"
SERVER_VERSION = "0.1.1"
GEN_TIMEOUT = 600          # seconds for a generation POST
PROBE_TIMEOUT = 4          # seconds for discovery GETs
PROBE_PORTS = [7860, 7861, 8080, 8000, 8001, 8188]  # 8188 = ComfyUI, just in case
ENGINE_PROCESS = "sd-server.exe"

# ---------------------------------------------------------------------------
# stdout/stderr hygiene: stdout carries the JSON-RPC stream only.
# ---------------------------------------------------------------------------
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass


def log(*args):
    print("[locmodel-image]", *args, file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------
class EngineError(Exception):
    pass


def _http(base, path, method="GET", payload=None, timeout=PROBE_TIMEOUT):
    url = base.rstrip("/") + path
    data = None
    headers = {"Accept": "application/json"}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
    ctype = resp.headers.get("Content-Type", "")
    if "json" in ctype:
        return json.loads(body.decode("utf-8", errors="replace"))
    return {"_raw": body.decode("utf-8", errors="replace")[:2000]}


def _probe_port(port):
    """Return base URL if something answering the sd API listens on port."""
    base = f"http://127.0.0.1:{port}"
    for path in ("/sdcpp/v1/capabilities", "/sdapi/v1/loras"):
        try:
            _http(base, path)
            return base
        except urllib.error.HTTPError as e:
            # An HTTP answer at all means a server is there; 4xx on one path
            # may just mean the endpoint is absent on that build.
            if e.code in (404, 405):
                continue
            return base
        except (urllib.error.URLError, TimeoutError, OSError):
            continue
    return None


# ---------------------------------------------------------------------------
# Port discovery
# ---------------------------------------------------------------------------
_port_cache = {"base": None, "at": 0.0}


def _state_file():
    """Last-known-good engine port, persisted so a fresh host process can
    reuse it even when the process scan is unavailable in its environment."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or str(Path.home())
    return Path(base) / "locmodel-image" / "last_port.json"


def _load_last_port():
    try:
        port = int(json.loads(_state_file().read_text("utf-8")).get("port", 0))
        return port if 0 < port < 65536 else None
    except Exception:
        return None


def _save_last_port(port):
    try:
        f = _state_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps({"port": port,
                                 "at": datetime.now().isoformat(timespec="seconds")}),
                     "utf-8")
    except Exception as e:
        log("save last_port failed:", e)


def _pids_of(image_name):
    try:
        out = subprocess.run(
            ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/FO", "CSV", "/NH"],
            capture_output=True, text=True, timeout=15,
        ).stdout
    except Exception as e:
        log("tasklist failed:", e)
        return []
    if not out:
        return []
    pids = []
    for line in out.splitlines():
        parts = [p.strip().strip('"') for p in line.split('","')]
        if len(parts) >= 2 and parts[0].lower() == image_name.lower():
            try:
                pids.append(int(parts[1]))
            except ValueError:
                pass
    return pids


def _listening_ports(pids):
    if not pids:
        return []
    try:
        out = subprocess.run(["netstat", "-ano"], capture_output=True,
                             text=True, timeout=20).stdout
    except Exception as e:
        log("netstat failed:", e)
        return []
    if not out:
        return []
    ports = []
    for line in out.splitlines():
        f = line.split()
        if len(f) >= 5 and f[0].upper() == "TCP" and f[3].upper() == "LISTENING":
            try:
                pid = int(f[4])
                port = int(f[1].rsplit(":", 1)[1])
            except (ValueError, IndexError):
                continue
            if pid in pids and port not in ports:
                ports.append(port)
    return ports


def discover_base(force=False):
    """Find the sd-server base URL. Cached for 30s; force=True re-scans."""
    now = time.time()
    if not force and _port_cache["base"] and now - _port_cache["at"] < 30:
        return _port_cache["base"]

    candidates = []
    env_port = os.environ.get("LOCMODEL_PORT")
    if env_port and env_port.isdigit():
        candidates.append(int(env_port))

    last_port = _load_last_port()
    if last_port and last_port not in candidates:
        candidates.append(last_port)

    for pid_port in _listening_ports(_pids_of(ENGINE_PROCESS)):
        if pid_port not in candidates:
            candidates.append(pid_port)

    for p in PROBE_PORTS:
        if p not in candidates:
            candidates.append(p)

    for port in candidates:
        base = _probe_port(port)
        if base:
            _port_cache.update(base=base, at=now)
            _save_last_port(port)
            log("engine found at", base)
            return base

    _port_cache.update(base=None, at=now)
    return None


def _call_engine(path, method="GET", payload=None, timeout=PROBE_TIMEOUT):
    base = discover_base()
    if not base:
        raise EngineError(
            "没有找到 LocModel 画图引擎（sd-server.exe）。\n"
            "请先打开 LocModel 应用并启动画图模式（选好画图模型即可），然后重试。\n"
            "如果引擎跑在非默认端口，也可以设置环境变量 LOCMODEL_PORT 后重试。"
        )
    try:
        return _http(base, path, method, payload, timeout)
    except EngineError:
        raise
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        # Stale cache (engine restarted on another port) -> retry once.
        base2 = discover_base(force=True)
        if base2 and base2 != base:
            return _http(base2, path, method, payload, timeout)
        raise EngineError(f"画图引擎没有响应（{base}{path}）：{e}")


# ---------------------------------------------------------------------------
# Image saving
# ---------------------------------------------------------------------------
def _out_dir():
    d = Path(os.environ.get("LOCMODEL_OUT_DIR") or
             Path.cwd() / "generated-images" / "locmodel")
    d.mkdir(parents=True, exist_ok=True)
    return d


_DATA_URI = re.compile(r"^data:image/[a-zA-Z0-9.+-]+;base64,")


def _decode_images(images, tag):
    saved = []
    for i, item in enumerate(images or []):
        if not isinstance(item, str):
            continue
        b64 = _DATA_URI.sub("", item)
        try:
            raw = base64.b64decode(b64)
        except Exception as e:
            log(f"image {i} decode failed: {e}")
            continue
        suffix = ".png" if raw[:8] == b"\x89PNG\r\n\x1a\n" else (
            ".jpg" if raw[:3] == b"\xff\xd8\xff" else ".png")
        path = _out_dir() / f"{tag}-{datetime.now():%Y%m%d-%H%M%S}-{i}{suffix}"
        path.write_bytes(raw)
        saved.append(str(path))
    return saved


def _fmt_result(saved, info):
    lines = [f"已保存 {len(saved)} 张图片："]
    lines += [f"- {p}" for p in saved]
    if info:
        lines.append(f"\n引擎返回参数：{json.dumps(info, ensure_ascii=False)[:800]}")
    return "\n".join(lines)


def _pick_info(resp):
    info = resp.get("info")
    if isinstance(info, str):
        try:
            return json.loads(info)
        except Exception:
            return info
    return info or resp.get("parameters")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------
def tool_status(args):
    base = discover_base(force=True)
    if not base:
        return ("LocModel 画图引擎当前没有在运行。\n"
                "启动方法：打开 LocModel → 进入画图模式（选择任一画图模型加载）。\n"
                "引擎起来后本工具即可自动发现端口。")
    data = {"running": True, "base": base}
    try:
        data["capabilities"] = _call_engine("/sdcpp/v1/capabilities")
    except EngineError:
        pass
    try:
        loras = _call_engine("/sdapi/v1/loras")
        if isinstance(loras, list):
            data["lora_count"] = len(loras)
    except EngineError:
        pass
    return json.dumps(data, ensure_ascii=False, indent=2)


def tool_txt2img(args):
    payload = {"prompt": args["prompt"]}
    for key in ("negative_prompt", "width", "height", "steps", "cfg_scale",
                "seed", "batch_size", "sampler_name"):
        if args.get(key) is not None:
            payload[key] = args[key]
    resp = _call_engine("/sdapi/v1/txt2img", "POST", payload, GEN_TIMEOUT)
    saved = _decode_images(resp.get("images"), "txt2img")
    if not saved:
        raise EngineError(f"引擎返回了响应但没有解析出图片数据：{json.dumps(resp, ensure_ascii=False)[:500]}")
    return _fmt_result(saved, _pick_info(resp))


def tool_img2img(args):
    img_path = Path(args["image_path"]).expanduser()
    if not img_path.is_file():
        raise EngineError(f"找不到输入图片：{img_path}")
    raw = img_path.read_bytes()
    payload = {
        "prompt": args["prompt"],
        "init_images": [base64.b64encode(raw).decode("ascii")],
        "denoising_strength": args.get("denoising_strength", 0.6),
    }
    for key in ("negative_prompt", "width", "height", "steps", "cfg_scale",
                "seed", "sampler_name", "resize_mode"):
        if args.get(key) is not None:
            payload[key] = args[key]
    resp = _call_engine("/sdapi/v1/img2img", "POST", payload, GEN_TIMEOUT)
    saved = _decode_images(resp.get("images"), "img2img")
    if not saved:
        raise EngineError(f"引擎返回了响应但没有解析出图片数据：{json.dumps(resp, ensure_ascii=False)[:500]}")
    return _fmt_result(saved, _pick_info(resp))


def tool_loras(args):
    resp = _call_engine("/sdapi/v1/loras")
    if isinstance(resp, list):
        names = [item.get("name", str(item)) for item in resp if isinstance(item, dict)]
        return "可用 LoRA：\n- " + "\n- ".join(names) if names else "当前没有已导入的 LoRA。"
    return json.dumps(resp, ensure_ascii=False)


TOOLS = [
    {
        "name": "locmodel_status",
        "description": (
            "检查 LocModel 本地画图引擎（stable-diffusion.cpp sd-server）是否在运行，"
            "返回端口、能力和 LoRA 数量。画图前不确定引擎状态时先调用它。"
        ),
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "locmodel_txt2img",
        "description": (
            "用 LocModel 本地引擎文生图（当前默认模型多为 Z-Image Turbo：步数少、"
            "支持中文提示词）。图片保存到工作区 generated-images/locmodel/ 并返回路径。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "正向提示词，支持中文"},
                "negative_prompt": {"type": "string", "description": "反向提示词"},
                "width": {"type": "integer", "description": "宽度像素，默认用引擎当前设置"},
                "height": {"type": "integer", "description": "高度像素"},
                "steps": {"type": "integer", "description": "采样步数；Z-Image Turbo 用 8，普通 SD/FLUX 模型用 20-30"},
                "cfg_scale": {"type": "number", "description": "提示词引导强度"},
                "seed": {"type": "integer", "description": "随机种子；不传则随机"},
                "batch_size": {"type": "integer", "description": "一次生成几张"},
                "sampler_name": {"type": "string", "description": "采样器，如 euler"},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "locmodel_img2img",
        "description": (
            "用 LocModel 本地引擎做图生图/重绘：以本地图片为底图加提示词重绘。"
            "denoising_strength 越高改动越大（0.3 轻微，0.8 大改）。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "正向提示词"},
                "image_path": {"type": "string", "description": "底图本地路径"},
                "denoising_strength": {"type": "number", "description": "重绘幅度 0-1，默认 0.6"},
                "negative_prompt": {"type": "string"},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
                "steps": {"type": "integer"},
                "cfg_scale": {"type": "number"},
                "seed": {"type": "integer"},
                "resize_mode": {"type": "integer"},
            },
            "required": ["prompt", "image_path"],
        },
    },
    {
        "name": "locmodel_loras",
        "description": "列出 LocModel 引擎当前可用的 LoRA 模型名称。",
        "inputSchema": {"type": "object", "properties": {}, "required": []},
    },
]

TOOL_FUNCS = {
    "locmodel_status": tool_status,
    "locmodel_txt2img": tool_txt2img,
    "locmodel_img2img": tool_img2img,
    "locmodel_loras": tool_loras,
}


# ---------------------------------------------------------------------------
# JSON-RPC / MCP plumbing
# ---------------------------------------------------------------------------
def _send(msg):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _reply(msg_id, result=None, error=None):
    resp = {"jsonrpc": "2.0", "id": msg_id}
    if error is not None:
        resp["error"] = error
    else:
        resp["result"] = result
    _send(resp)


def _handle(msg):
    method = msg.get("method")
    msg_id = msg.get("id")
    is_notification = msg_id is None

    if method == "initialize":
        params = msg.get("params") or {}
        _reply(msg_id, {
            "protocolVersion": params.get("protocolVersion", "2024-11-05"),
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        })
    elif method == "notifications/initialized":
        pass
    elif method == "ping":
        if not is_notification:
            _reply(msg_id, {})
    elif method == "tools/list":
        if not is_notification:
            _reply(msg_id, {"tools": TOOLS})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        fn = TOOL_FUNCS.get(name)
        if fn is None:
            _reply(msg_id, error={"code": -32602, "message": f"unknown tool: {name}"})
            return
        try:
            text = fn(args)
            _reply(msg_id, {"content": [{"type": "text", "text": text}],
                            "isError": False})
        except EngineError as e:
            _reply(msg_id, {"content": [{"type": "text", "text": str(e)}],
                            "isError": True})
        except Exception as e:  # noqa: BLE001 - surface everything to the caller
            tb = traceback.format_exc()
            log("tool", name, "failed:", repr(e))
            log(tb)
            _reply(msg_id, {"content": [{"type": "text",
                            "text": f"工具执行出错：{type(e).__name__}: {e}\n{tb[-1500:]}"}],
                            "isError": True})
    elif is_notification:
        pass
    else:
        _reply(msg_id, error={"code": -32601, "message": f"method not found: {method}"})


def main():
    log("starting; cwd =", os.getcwd())
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError as e:
            log("bad json line:", e)
            continue
        try:
            _handle(msg)
        except Exception as e:  # noqa: BLE001
            log("handler crash:", repr(e))
            log(traceback.format_exc())
            msg_id = msg.get("id")
            if msg_id is not None:
                _reply(msg_id, error={"code": -32603, "message": f"internal error: {e}"})


if __name__ == "__main__":
    main()
