from __future__ import annotations

import asyncio
import ipaddress
from pathlib import Path
from typing import Any, Optional

import httpx
import requests
from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import __version__
from .config import (
    APP_ROOT,
    default_printer,
    load_config,
    needs_setup,
    printer_dict_to_config,
    public_printer_dict,
    safe_printer_id,
    save_config,
    sorted_actions,
    sorted_cards,
)
from .logger import get_logs, log
from .printer_client import PrinterClient
from .scanner import default_subnet_guess, scan_network
from .themes import FONT_STACKS, THEMES, get_theme, theme_css_vars
from .cc2.commands import (
    DELETE_FILE,
    ENABLE_WEBCAM,
    GET_CANVAS_STATUS,
    GET_DISK_INFO,
    GET_FILE_DETAIL,
    GET_FILE_LIST,
    GET_FILE_THUMBNAIL,
    GET_HISTORY_TASK,
    GET_TIME_LAPSE_VIDEO_LIST,
    PAUSE_PRINT,
    RESUME_PRINT,
    START_PRINT,
    HISTORY_DELETE,
    SET_LIGHT,
    START_VIDEO_STREAM,
    STOP_PRINT,
    delete_file_params,
    history_delete_params,
    file_detail_params,
    file_list_params,
    file_thumbnail_params,
    start_print_params,
    timelapse_export_params,
    light_params,
    method_allowed,
    webcam_params,
)
# Import CommandError from the client module; the weird import above is avoided by this explicit import.
from .cc2.client import CommandError
from .cc2.discovery import discover
from .cc2.runtime import LitePrinterRuntime
from .cc2.state import seconds_to_hms

app = FastAPI(title="cc2-dash-lite", version=__version__)
app.mount("/static", StaticFiles(directory=str(APP_ROOT / "static")), name="static")
ELEGEEGO_WEB_DIR = Path(__file__).resolve().parent / "elegoo_web"
if ELEGEEGO_WEB_DIR.exists():
    app.mount("/elegoo", StaticFiles(directory=str(ELEGEEGO_WEB_DIR), html=True), name="elegoo")
templates = Jinja2Templates(directory=str(APP_ROOT / "templates"))
runtime = LitePrinterRuntime()


class ScanRequest(BaseModel):
    subnet: str | None = None
    ports: list[int] | None = None


class AddPrinterRequest(BaseModel):
    id: str | None = None
    name: str = "Centauri Carbon 2"
    host: str
    serial: str | None = None
    access_code: str = "123456"
    port: int = 1883
    enabled: bool = True
    allow_commands: bool = True
    allow_dangerous_commands: bool = False
    portal_url: str | None = None
    camera_url: str | None = None
    set_default: bool = True


class PrinterSettingsRequest(BaseModel):
    name: Optional[str] = None
    host: Optional[str] = None
    serial: Optional[str] = None
    access_code: Optional[str] = None
    port: Optional[int] = None
    enabled: Optional[bool] = None
    allow_commands: Optional[bool] = None
    allow_dangerous_commands: Optional[bool] = None


class ActionRequest(BaseModel):
    printer_id: str | None = None


class CommandRequest(BaseModel):
    method: int
    params: dict[str, Any] = Field(default_factory=dict)
    wait: bool = True
    timeout: float = 10.0


class LightRequest(BaseModel):
    on: bool


class DeleteFileRequest(BaseModel):
    file_path: str
    storage_media: str = "local"


class StartPrintRequest(BaseModel):
    filename: str
    storage_media: str = "local"
    start_layer: int = 0
    calibration: bool = False
    platform_type: int = 0
    timelapse: bool = False


class TimelapseExportRequest(BaseModel):
    url: str


class HistoryDeleteRequest(BaseModel):
    task_ids: list[str | int]


class SaveConfigRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def _allowed_request(request: Request, cfg: dict) -> bool:
    ip = _client_ip(request)
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return ip in {"testclient"}
    net_cfg = cfg.get("network", {})
    if net_cfg.get("always_allow_localhost", True) and addr.is_loopback:
        return True
    for host in net_cfg.get("allowed_hosts", []) or []:
        try:
            if addr == ipaddress.ip_address(host):
                return True
        except ValueError:
            continue
    for subnet in net_cfg.get("allowed_subnets", []) or []:
        try:
            if addr in ipaddress.ip_network(subnet, strict=False):
                return True
        except ValueError:
            continue
    return False


@app.middleware("http")
async def lan_guard(request: Request, call_next):
    cfg = load_config()
    if not _allowed_request(request, cfg):
        return JSONResponse({"ok": False, "error": "Client IP is not allowed by cc2-dash-lite network settings."}, status_code=403)
    return await call_next(request)


@app.on_event("startup")
async def startup_event() -> None:
    runtime.start_all()


@app.on_event("shutdown")
async def shutdown_event() -> None:
    runtime.stop_all()


def _configured_printers() -> dict[str, dict[str, Any]]:
    return load_config().get("printers", {}) or {}


def _portal_target(printer: Optional[str] = None):
    cfg = load_config()
    printers = cfg.get("printers") or {}
    if printer:
        if printer in printers:
            return printer_dict_to_config(printer, printers[printer])
        lowered = printer.lower()
        for pid, pdata in printers.items():
            pcfg = printer_dict_to_config(pid, pdata)
            if pcfg.host == printer or pcfg.name.lower() == lowered or pcfg.serial.lower() == lowered:
                return pcfg
    pid, pdata = default_printer(cfg)
    if pid and pdata:
        return printer_dict_to_config(pid, pdata)
    return None


def view_context(request: Request) -> dict[str, Any]:
    cfg = load_config()
    theme = get_theme(cfg.get("app", {}).get("theme"))
    pid, printer = default_printer(cfg)
    public_printer = None
    if pid and printer:
        public_printer = public_printer_dict(printer_dict_to_config(pid, printer), include_secret=False)
    return {
        "request": request,
        "version": __version__,
        "cfg": cfg,
        "needs_setup": needs_setup(cfg),
        "cards": sorted_cards(cfg),
        "actions": sorted_actions(cfg),
        "themes": THEMES,
        "font_stacks": FONT_STACKS,
        "theme": theme,
        "theme_vars": theme_css_vars(cfg.get("app", {}).get("theme"), cfg.get("appearance", {})),
        "printer_id": pid,
        "printer": public_printer,
        "default_subnet": default_subnet_guess(),
    }


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    cfg = load_config()
    if needs_setup(cfg):
        return RedirectResponse("/setup")
    return templates.TemplateResponse("index.html", view_context(request))


@app.get("/setup", response_class=HTMLResponse)
async def setup(request: Request):
    return templates.TemplateResponse("setup.html", view_context(request))


@app.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    return templates.TemplateResponse("settings.html", view_context(request))


@app.get("/logs", response_class=HTMLResponse)
async def logs_page(request: Request):
    return templates.TemplateResponse("logs.html", view_context(request))


@app.get("/files", response_class=HTMLResponse)
async def files_page(request: Request):
    cfg = load_config()
    if needs_setup(cfg):
        return RedirectResponse("/setup")
    return templates.TemplateResponse("files.html", view_context(request))


@app.get("/portal", response_class=HTMLResponse)
async def portal(request: Request, printer: Optional[str] = None):
    pcfg = _portal_target(printer)
    if not pcfg:
        return RedirectResponse("/setup")
    root_url = f"http://{pcfg.host}/"
    octo_url = f"/portal-octo?printer={pcfg.id}"
    fullscreen_url = f"/portal-fullscreen?printer={pcfg.id}"
    diag_url = f"/api/portal-probe?printer={pcfg.id}"
    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>Elegoo Portal - cc2-dash-lite</title>
  <style>
    html,body{{margin:0;height:100%;background:#111827;color:#e5e7eb;font-family:system-ui,sans-serif;}}
    .bar{{min-height:46px;display:flex;gap:12px;align-items:center;padding:0 14px;background:rgba(17,24,39,.94);border-bottom:1px solid rgba(148,163,184,.18);backdrop-filter:blur(12px);flex-wrap:wrap}}
    .bar strong{{font-size:14px;white-space:nowrap}} .bar span{{color:#94a3b8;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
    .bar a{{color:#93c5fd;text-decoration:none;font-size:13px;white-space:nowrap}}
    iframe{{display:block;width:100%;height:calc(100vh - 47px);border:0;background:#202124;}}
  </style>
</head>
<body>
  <div class="bar"><strong>Elegoo portal</strong><span>{pcfg.name} · {pcfg.host}</span><a href="/">Back</a><a href="{fullscreen_url}" target="_blank">Fullscreen</a><a href="{root_url}" target="_blank">Printer root</a><a href="{diag_url}" target="_blank">Probe</a></div>
  <iframe src="{octo_url}" title="Elegoo live portal"></iframe>
</body>
</html>"""
    return HTMLResponse(html)


@app.get("/portal-octo", response_class=HTMLResponse)
async def portal_octo(printer: Optional[str] = None):
    pcfg = _portal_target(printer)
    if not pcfg:
        return HTMLResponse("""<!doctype html><html><body style="background:#111827;color:#e5e7eb;font-family:system-ui;padding:32px"><h1>No printer configured</h1><p>Add/scan your printer first.</p><p><a style="color:#93c5fd" href="/setup">Back to setup</a></p></body></html>""")
    app_url = (
        f"/elegoo/octo_portal.html"
        f"?id={pcfg.id}&ip={pcfg.host}&print_ip={pcfg.host}&sn={pcfg.serial}"
        f"&access_code={pcfg.access_code}&username=elegoo&lang=en-US#/index"
    )
    html = f"""<!doctype html>
<html lang="en">
<head><meta charset="utf-8" /><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
<title>Elegoo Live Portal - cc2-dash-lite</title>
<style>html,body{{margin:0;height:100%;background:#111827;color:#e5e7eb;font-family:system-ui,sans-serif;}}.bar{{min-height:46px;display:flex;gap:12px;align-items:center;padding:0 14px;background:rgba(17,24,39,.92);border-bottom:1px solid rgba(148,163,184,.18);backdrop-filter:blur(12px);flex-wrap:wrap}}.bar strong{{font-size:14px;white-space:nowrap}}.bar span{{color:#94a3b8;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}.bar a{{color:#93c5fd;text-decoration:none;font-size:13px;white-space:nowrap}}iframe{{display:block;width:100%;height:calc(100vh - 47px);border:0;background:#202124;}}</style>
</head><body><div class="bar"><strong>Elegoo live portal</strong><span>{pcfg.name} · MQTT WS bridge · {pcfg.host}:{pcfg.port}</span><a href="/">Back</a><a href="{app_url}" target="_blank">Open raw app</a><a href="/api/portal-probe?printer={pcfg.id}" target="_blank">Probe</a></div><iframe src="{app_url}" title="Elegoo live portal"></iframe></body></html>"""
    return HTMLResponse(html)


@app.get("/portal-fullscreen", response_class=HTMLResponse)
async def portal_fullscreen(printer: Optional[str] = None):
    pcfg = _portal_target(printer)
    if not pcfg:
        return HTMLResponse("""<!doctype html><html><head><meta name="viewport" content="width=device-width,initial-scale=1"><title>CC2 setup</title></head><body style="margin:0;background:#05070b;color:#e5e7eb;font-family:system-ui;display:grid;place-items:center;min-height:100vh;padding:20px;text-align:center"><div><h1>No printer configured</h1><p>Run setup first.</p><p><a style="color:#7dd3fc" href="/setup">Open setup</a></p></div></body></html>""")
    app_url = (
        f"/elegoo/octo_portal.html"
        f"?id={pcfg.id}&ip={pcfg.host}&print_ip={pcfg.host}&sn={pcfg.serial}"
        f"&access_code={pcfg.access_code}&username=elegoo&lang=en-US#/index"
    )
    return HTMLResponse(f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8" />
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover" />
<meta name="theme-color" content="#202124" />
<title>Elegoo Portal</title>
<style>html,body{{margin:0;width:100%;height:100%;overflow:hidden;background:#202124}}iframe{{display:block;width:100vw;height:100dvh;border:0;background:#202124}}</style>
</head><body><iframe src="{app_url}" title="Elegoo Portal"></iframe></body></html>""")


@app.get("/oe-relay-static/elegoo-os-relay.js")
async def oe_relay_js():
    return HTMLResponse("console.log('[cc2-dash-lite] local oe relay stub loaded');", media_type="application/javascript")


@app.get("/oe-relay-static/elegoo-os-relay.css")
async def oe_relay_css():
    return HTMLResponse("/* cc2-dash-lite local oe relay css stub */", media_type="text/css")


@app.websocket("/ws/mqtt/{printer_id}")
async def mqtt_websocket_bridge(websocket: WebSocket, printer_id: str) -> None:
    pcfg = _portal_target(printer_id)
    if not pcfg:
        await websocket.close(code=1008)
        return
    await websocket.accept(subprotocol=websocket.headers.get("sec-websocket-protocol"))
    reader = writer = None
    try:
        reader, writer = await asyncio.open_connection(pcfg.host, pcfg.port)

        async def ws_to_tcp() -> None:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data is None:
                    text = msg.get("text")
                    if text is None:
                        continue
                    data = text.encode("utf-8")
                writer.write(data)
                await writer.drain()

        async def tcp_to_ws() -> None:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                await websocket.send_bytes(data)

        _, pending = await asyncio.wait(
            {asyncio.create_task(ws_to_tcp()), asyncio.create_task(tcp_to_ws())},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log("warn", f"MQTT WS bridge failed for {printer_id}: {exc}", "portal")
        try:
            await websocket.close(code=1011)
        except Exception:
            pass
    finally:
        if writer:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


@app.get("/health")
async def health():
    cfg = load_config()
    return {"ok": True, "version": __version__, "setup_required": needs_setup(cfg), "printers": len(cfg.get("printers") or {})}


@app.get("/api/health")
async def api_health():
    return await health()


@app.get("/api/config")
async def api_get_config():
    return {"ok": True, "config": load_config(), "themes": THEMES, "font_stacks": list(FONT_STACKS.keys())}


@app.post("/api/config")
async def api_save_config(req: SaveConfigRequest):
    cfg = save_config(req.config)
    runtime.reload()
    log("info", "Configuration saved", "settings")
    return {"ok": True, "config": cfg}


def _discovery_targets(subnet_or_host: str) -> list[str]:
    subnet_or_host = (subnet_or_host or default_subnet_guess()).strip()
    targets: list[str] = []
    try:
        if "/" in subnet_or_host:
            net = ipaddress.ip_network(subnet_or_host, strict=False)
            targets.append(str(net.broadcast_address))
        elif subnet_or_host.endswith(".x"):
            targets.append(subnet_or_host[:-2] + ".255")
        else:
            ipaddress.ip_address(subnet_or_host)
            targets.append(subnet_or_host)
    except Exception:
        pass
    targets.append("255.255.255.255")
    out = []
    for t in targets:
        if t not in out:
            out.append(t)
    return out


async def _discover_cc2(subnet_or_host: str, timeout: float = 3.5) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for target in _discovery_targets(subnet_or_host):
        try:
            rows = await asyncio.to_thread(discover, timeout, target)
            for p in rows:
                d = p.to_dict()
                host = d.get("ip")
                if not host:
                    continue
                found[host] = {
                    "host": host,
                    "open_ports": [1883, 80, 8080],
                    "http_title": d.get("machine_model") or d.get("host_name") or "Centauri Carbon 2",
                    "likely_printer": True,
                    "notes": ["UDP discovery method 7000"],
                    "portal_url": f"http://{host}/",
                    "camera_url": f"http://{host}:8080/",
                    "serial": d.get("serial") or "",
                    "host_name": d.get("host_name") or "Centauri Carbon 2",
                    "machine_model": d.get("machine_model") or "Centauri Carbon 2",
                    "token_status": d.get("token_status"),
                    "lan_status": d.get("lan_status"),
                    "raw": d.get("raw"),
                }
        except Exception as exc:
            log("warn", f"UDP discovery failed for target {target}: {exc}", "scanner")
    return list(found.values())


@app.get("/api/discover")
async def api_discover(timeout: float = Query(4.0, ge=0.5, le=15.0), target: str = Query("255.255.255.255")):
    printers = await asyncio.to_thread(discover, timeout, target)
    return {"count": len(printers), "printers": [p.to_dict() for p in printers]}


@app.post("/api/scan")
async def api_scan(req: ScanRequest):
    cfg = load_config()
    subnet = req.subnet or (cfg.get("network", {}).get("allowed_subnets") or [default_subnet_guess()])[0]
    ports = req.ports or cfg.get("network", {}).get("scan_ports") or [80, 8080, 3030, 1883, 8899]
    try:
        udp_found = await _discover_cc2(subnet)
        generic_found = await scan_network(subnet, ports)
    except Exception as exc:
        log("error", f"Scan failed: {exc}", "scanner")
        raise HTTPException(status_code=400, detail=str(exc))
    merged: dict[str, dict[str, Any]] = {c["host"]: c for c in generic_found if c.get("host")}
    for c in udp_found:
        host = c.get("host")
        if not host:
            continue
        if host in merged:
            merged[host].update({k: v for k, v in c.items() if v not in (None, "", [])})
            merged[host]["likely_printer"] = True
        else:
            merged[host] = c
    candidates = sorted(merged.values(), key=lambda c: (not c.get("likely_printer", False), c.get("host", "")))
    return {"ok": True, "subnet": subnet, "ports": ports, "candidates": candidates}


@app.get("/api/printers")
async def api_list_printers():
    cfg = load_config()
    configured = []
    for printer_id, data in (cfg.get("printers") or {}).items():
        configured.append(public_printer_dict(printer_dict_to_config(printer_id, data), include_secret=False))
    return {"configured": configured, "status": runtime.snapshots()}


@app.post("/api/printers")
async def api_add_printer(req: AddPrinterRequest):
    cfg = load_config()
    serial = (req.serial or "").strip() or req.host.strip()
    safe_id = req.id or safe_printer_id(serial or req.name or req.host)
    base_id = safe_id
    n = 2
    while safe_id in cfg.get("printers", {}) and not req.id:
        safe_id = f"{base_id}-{n}"
        n += 1
    cfg.setdefault("printers", {})[safe_id] = {
        "name": req.name,
        "host": req.host.strip(),
        "serial": serial,
        "access_code": req.access_code.strip() or "123456",
        "port": int(req.port or 1883),
        "model": "centauri_carbon_2",
        "enabled": bool(req.enabled),
        "paired": True,
        "allow_commands": bool(req.allow_commands),
        "allow_dangerous_commands": bool(req.allow_dangerous_commands),
        "portal_enabled": True,
        "camera_enabled": True,
        "portal_url": f"/portal-fullscreen?printer={safe_id}",
        "direct_portal_url": req.portal_url or f"http://{req.host}/",
        "camera_url": f"/api/printers/{safe_id}/camera/stream",
        "direct_camera_url": req.camera_url or f"http://{req.host}:8080/",
    }
    if req.set_default or not cfg.get("app", {}).get("default_printer"):
        cfg.setdefault("app", {})["default_printer"] = safe_id
    cfg.setdefault("app", {})["setup_complete"] = True
    cfg = save_config(cfg)
    runtime.restart(safe_id, printer_dict_to_config(safe_id, cfg["printers"][safe_id]))
    log("info", f"Printer paired/saved: {req.name} at {req.host} serial={serial}", "setup")
    return {"ok": True, "printer_id": safe_id, "config": cfg, "printer": public_printer_dict(printer_dict_to_config(safe_id, cfg["printers"][safe_id]))}


@app.patch("/api/printers/{printer_id}")
async def api_update_printer(printer_id: str, patch: PrinterSettingsRequest):
    cfg = load_config()
    if printer_id not in (cfg.get("printers") or {}):
        raise HTTPException(404, "Printer not configured")
    data = cfg["printers"][printer_id]
    for key, value in patch.model_dump(exclude_unset=True).items():
        if value is None:
            continue
        if key == "access_code" and value == "":
            continue
        data[key] = value
    cfg = save_config(cfg)
    runtime.restart(printer_id, printer_dict_to_config(printer_id, cfg["printers"][printer_id]))
    return {"ok": True, "printer": public_printer_dict(printer_dict_to_config(printer_id, cfg["printers"][printer_id]))}


@app.delete("/api/printers/{printer_id}")
async def api_delete_printer(printer_id: str):
    cfg = load_config()
    if printer_id not in (cfg.get("printers") or {}):
        raise HTTPException(404, "Printer not configured")
    runtime.stop(printer_id)
    cfg["printers"].pop(printer_id, None)
    if cfg.get("app", {}).get("default_printer") == printer_id:
        cfg["app"]["default_printer"] = next(iter(cfg.get("printers", {}).keys()), None)
    if not cfg.get("printers"):
        cfg.setdefault("app", {})["setup_complete"] = False
    save_config(cfg)
    return {"ok": True}


def _status_from_snapshot(printer_id: str, printer: dict[str, Any], snap: Optional[dict[str, Any]]) -> dict[str, Any]:
    pcfg = printer_dict_to_config(printer_id, printer)
    if not snap:
        return PrinterClient(printer_id, printer, load_config())._empty_status("CC2 client is not running", reachable=False)
    n = snap.get("normalized") or {}
    temps = n.get("temps") or {}
    nozzle = temps.get("nozzle") or {}
    bed = temps.get("bed") or {}
    progress = n.get("progress") or 0
    try:
        progress = float(progress)
        if progress <= 1:
            progress *= 100.0
        progress = max(0, min(100, progress))
    except Exception:
        progress = 0.0
    state = n.get("sub_state") or n.get("state") or ("registered" if snap.get("registered") else "offline")
    reachable = bool(snap.get("connected") or snap.get("registered"))
    return {
        "printer_id": printer_id,
        "name": pcfg.name,
        "host": pcfg.host,
        "serial": pcfg.serial,
        "reachable": reachable,
        "connected": bool(snap.get("connected")),
        "registered": bool(snap.get("registered")),
        "state": str(state).lower(),
        "status_text": str(state).replace("_", " ").title(),
        "message": snap.get("last_error") or ("Registered with printer" if snap.get("registered") else "Waiting for MQTT registration"),
        "progress": round(progress, 1),
        "print_time": seconds_to_hms((n.get("time") or {}).get("elapsed_sec")) or "-",
        "time_left": (n.get("time") or {}).get("remaining_human") or seconds_to_hms((n.get("time") or {}).get("remaining_sec")) or "-",
        "completion": f"{round(progress, 1)}%",
        "filament_used": "-",
        "hotend_current": nozzle.get("actual"),
        "hotend_target": nozzle.get("target"),
        "bed_current": bed.get("actual"),
        "bed_target": bed.get("target"),
        "file": n.get("file") or "-",
        "updated_at": snap.get("last_message_age_sec"),
        "camera_url": f"/api/printers/{printer_id}/camera/stream",
        "direct_camera_url": f"http://{pcfg.host}:8080/",
        "portal_url": f"/portal-fullscreen?printer={printer_id}",
        "portal_chrome_url": f"/portal?printer={printer_id}",
        "direct_portal_url": f"http://{pcfg.host}/",
        "raw": snap,
    }


@app.get("/api/status")
async def api_status():
    cfg = load_config()
    pid, printer = default_printer(cfg)
    if not pid or not printer:
        raise HTTPException(status_code=404, detail="No printer configured")
    if not runtime.get_client(pid):
        runtime.start(pid, printer_dict_to_config(pid, printer))
    snap = runtime.snapshot(pid)
    return _status_from_snapshot(pid, printer, snap)


@app.get("/api/status/{printer_id}")
async def api_status_printer(printer_id: str):
    cfg = load_config()
    printer = cfg.get("printers", {}).get(printer_id)
    if not printer:
        raise HTTPException(status_code=404, detail="Printer not configured")
    if not runtime.get_client(printer_id):
        runtime.start(printer_id, printer_dict_to_config(printer_id, printer))
    snap = runtime.snapshot(printer_id)
    return _status_from_snapshot(printer_id, printer, snap)


@app.get("/api/printers/{printer_id}/status")
async def api_legacy_status(printer_id: str):
    cfg = load_config()
    if printer_id not in (cfg.get("printers") or {}):
        raise HTTPException(404, "Printer not configured")
    if not runtime.get_client(printer_id):
        runtime.start(printer_id, printer_dict_to_config(printer_id, cfg["printers"][printer_id]))
    return runtime.snapshot(printer_id)


def _send_command(printer_id: str, method: int, params: dict[str, Any] | None = None, wait: bool = True, timeout: float = 10.0) -> dict[str, Any]:
    cfg = load_config()
    pdata = (cfg.get("printers") or {}).get(printer_id)
    if not pdata:
        raise HTTPException(404, "Printer not configured")
    pcfg = printer_dict_to_config(printer_id, pdata)
    if not method_allowed(method, pcfg.allow_commands, pcfg.allow_dangerous_commands):
        raise HTTPException(403, "Command blocked by safety settings. Enable allow_commands / allow_dangerous_commands for this printer if you really mean it.")
    client = runtime.get_client(printer_id)
    if not client:
        runtime.start(printer_id, pcfg)
        client = runtime.get_client(printer_id)
    if not client:
        raise HTTPException(409, "Printer client is not running; check host, serial, and PIN/access code.")
    try:
        result = client.send_request(method, params or {}, wait=wait, timeout=timeout)
        return {"ok": True, "result": result}
    except CommandError as exc:
        raise HTTPException(500, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc


@app.post("/api/printers/{printer_id}/command")
async def api_command(printer_id: str, body: CommandRequest):
    return await asyncio.to_thread(_send_command, printer_id, body.method, body.params, body.wait, body.timeout)


@app.post("/api/action/{action_id}")
async def api_action(action_id: str, req: ActionRequest | None = None):
    cfg = load_config()
    pid = req.printer_id if req and req.printer_id else cfg.get("app", {}).get("default_printer")
    if not pid:
        pid, _ = default_printer(cfg)
    if not pid or pid not in (cfg.get("printers") or {}):
        raise HTTPException(status_code=404, detail="Printer not configured")
    actions = cfg.get("actions", {})
    action_cfg = actions.get(action_id)
    if not action_cfg or not action_cfg.get("enabled", False):
        raise HTTPException(status_code=403, detail="Action disabled")

    method = None
    params: dict[str, Any] = {}
    wait = True
    timeout = 20.0
    if action_id == "light_toggle":
        snap = runtime.snapshot(pid) or {}
        led = ((snap.get("normalized") or {}).get("led") or {}).get("status")
        turn_on = not bool(led)
        method, params = SET_LIGHT, light_params(turn_on)
    elif action_id == "pause_resume":
        snap = runtime.snapshot(pid) or {}
        state = str(((snap.get("normalized") or {}).get("sub_state") or (snap.get("normalized") or {}).get("state") or "")).lower()
        method, params, timeout = (RESUME_PRINT if "pause" in state else PAUSE_PRINT), {}, 60.0
    elif action_id == "cancel_print":
        method, params, timeout = STOP_PRINT, {}, 60.0
    elif action_id == "restart_camera":
        # Wake/enable the webcam. The MJPEG stream is served directly/proxied on :8080.
        try:
            await asyncio.to_thread(_send_command, pid, ENABLE_WEBCAM, webcam_params(True), False, 5.0)
        except Exception:
            pass
        method, params, wait = START_VIDEO_STREAM, {}, False
    else:
        raise HTTPException(404, f"Unknown action: {action_id}")

    result = await asyncio.to_thread(_send_command, pid, method, params, wait, timeout)
    log("info", f"Action {action_id} sent", "command", printer=pid)
    return {"ok": True, "message": f"{action_cfg.get('label', action_id)} sent", "result": result.get("result")}


@app.get("/api/printers/{printer_id}/files")
async def api_files(printer_id: str, path: str = "/", storage_media: str = "local", page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), offset: Optional[int] = None, limit: Optional[int] = None):
    return await asyncio.to_thread(_send_command, printer_id, GET_FILE_LIST, file_list_params(path, storage_media, page, page_size, offset, limit), True, 15.0)


@app.get("/api/printers/{printer_id}/files/detail")
async def api_file_detail(printer_id: str, filename: str, storage_media: str = "local", directory: Optional[str] = None):
    return await asyncio.to_thread(_send_command, printer_id, GET_FILE_DETAIL, file_detail_params(filename, storage_media, directory), True, 15.0)


@app.get("/api/printers/{printer_id}/files/thumbnail")
async def api_file_thumbnail(printer_id: str, filename: str, storage_media: str = "local"):
    return await asyncio.to_thread(_send_command, printer_id, GET_FILE_THUMBNAIL, file_thumbnail_params(filename, storage_media), True, 15.0)


@app.post("/api/printers/{printer_id}/files/delete")
async def api_file_delete(printer_id: str, body: DeleteFileRequest):
    return await asyncio.to_thread(_send_command, printer_id, DELETE_FILE, delete_file_params(body.file_path, body.storage_media), True, 15.0)


@app.post("/api/printers/{printer_id}/files/start")
async def api_file_start(printer_id: str, body: StartPrintRequest):
    return await asyncio.to_thread(
        _send_command,
        printer_id,
        START_PRINT,
        start_print_params(body.filename, body.storage_media, body.start_layer, body.calibration, body.platform_type, body.timelapse),
        True,
        20.0,
    )


@app.get("/api/printers/{printer_id}/disk")
async def api_disk(printer_id: str, storage_media: str = "local"):
    return await asyncio.to_thread(_send_command, printer_id, GET_DISK_INFO, {"storage_media": storage_media}, True, 10.0)


@app.get("/api/printers/{printer_id}/canvas")
async def api_canvas(printer_id: str):
    return await asyncio.to_thread(_send_command, printer_id, GET_CANVAS_STATUS, {}, True, 10.0)


@app.get("/api/printers/{printer_id}/history")
async def api_history(printer_id: str):
    return await asyncio.to_thread(_send_command, printer_id, GET_HISTORY_TASK, {}, True, 20.0)


@app.get("/api/printers/{printer_id}/timelapse")
async def api_timelapse(printer_id: str):
    return await asyncio.to_thread(_send_command, printer_id, GET_TIME_LAPSE_VIDEO_LIST, {}, True, 20.0)


@app.post("/api/printers/{printer_id}/timelapse/export")
async def api_timelapse_export(printer_id: str, body: TimelapseExportRequest):
    return await asyncio.to_thread(_send_command, printer_id, GET_TIME_LAPSE_VIDEO_LIST, timelapse_export_params(body.url), True, 180.0)


@app.post("/api/printers/{printer_id}/history/delete")
async def api_history_delete(printer_id: str, body: HistoryDeleteRequest):
    return await asyncio.to_thread(_send_command, printer_id, HISTORY_DELETE, history_delete_params(body.task_ids), True, 20.0)


@app.post("/api/printers/{printer_id}/light")
async def api_light(printer_id: str, body: LightRequest):
    return await asyncio.to_thread(_send_command, printer_id, SET_LIGHT, light_params(body.on), True, 10.0)


@app.post("/api/printers/{printer_id}/camera/enable")
async def api_camera_enable(printer_id: str):
    return await asyncio.to_thread(_send_command, printer_id, ENABLE_WEBCAM, webcam_params(True), False, 5.0)


@app.get("/api/printers/{printer_id}/camera/url")
async def api_camera_url(printer_id: str):
    pcfg = _portal_target(printer_id)
    if not pcfg:
        raise HTTPException(404, "Printer not configured")
    return {"url": f"/api/printers/{printer_id}/camera/stream", "direct_url": f"http://{pcfg.host}:8080/", "alt_direct_url": f"http://{pcfg.host}:8080/?action=stream"}


@app.get("/api/printers/{printer_id}/camera/stream")
async def api_camera_stream(printer_id: str):
    pcfg = _portal_target(printer_id)
    if not pcfg:
        raise HTTPException(404, "Printer not configured")
    client = runtime.get_client(printer_id)
    if client:
        try:
            client.send_request(ENABLE_WEBCAM, webcam_params(True), wait=False)
        except Exception:
            pass

    urls = [f"http://{pcfg.host}:8080/", f"http://{pcfg.host}:8080/?action=stream"]
    headers = {"User-Agent": "cc2-dash-lite/" + __version__, "Accept": "multipart/x-mixed-replace,*/*", "Cache-Control": "no-cache"}
    upstream = None
    last_error = None
    for url in urls:
        try:
            resp = requests.get(url, stream=True, timeout=(5, None), headers=headers)
            if resp.status_code >= 400:
                last_error = f"HTTP {resp.status_code} from {url}"
                resp.close()
                continue
            upstream = resp
            break
        except Exception as exc:
            last_error = str(exc)
    if upstream is None:
        raise HTTPException(502, f"Camera stream unavailable: {last_error or 'no upstream response'}")

    content_type = upstream.headers.get("content-type") or "multipart/x-mixed-replace"

    def body_iter():
        try:
            for chunk in upstream.iter_content(chunk_size=16384):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    return StreamingResponse(body_iter(), media_type=content_type, headers={"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0"})


@app.get("/api/portal-url")
async def api_portal_url(printer: Optional[str] = None):
    pcfg = _portal_target(printer)
    if not pcfg:
        raise HTTPException(404, "No printer configured")
    return {"printer": public_printer_dict(pcfg), "url": f"http://{pcfg.host}/", "index_url": f"http://{pcfg.host}/index", "proxy_url": f"/portal-proxy/{pcfg.id}/", "stock_url": f"/portal-fullscreen?printer={pcfg.id}"}


@app.get("/api/portal-probe")
async def api_portal_probe(printer: Optional[str] = None):
    pcfg = _portal_target(printer)
    if not pcfg:
        raise HTTPException(404, "No printer configured")
    candidates = ["/", "/index", "/index.html", "/home", "/home.html", "/web", "/ui", "/dashboard", "/api", "/camera", "/stream", "/webcam", ":8080/", ":8080/?action=stream"]
    out = []
    async with httpx.AsyncClient(timeout=2.5, follow_redirects=False) as client:
        for path in candidates:
            url = f"http://{pcfg.host}{path}" if path.startswith(":") else f"http://{pcfg.host}{path}"
            try:
                r = await client.get(url)
                ctype = r.headers.get("content-type", "")
                text = r.text[:160].replace("\n", " ").replace("\r", " ") if "text" in ctype or "html" in ctype or "json" in ctype else ""
                out.append({"url": url, "status": r.status_code, "content_type": ctype, "server": r.headers.get("server", ""), "location": r.headers.get("location", ""), "sample": text})
            except Exception as exc:
                out.append({"url": url, "error": str(exc)})
    return {"printer": public_printer_dict(pcfg), "results": out}


@app.api_route("/portal-proxy/{printer_id}/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
async def portal_proxy(printer_id: str, path: str, request: Request):
    pcfg = _portal_target(printer_id)
    if not pcfg:
        raise HTTPException(404, "Printer not found")
    target = f"http://{pcfg.host}/{path}"
    if request.url.query:
        target += f"?{request.url.query}"
    headers = {k: v for k, v in request.headers.items() if k.lower() not in {"host", "content-length", "connection", "accept-encoding"}}
    body = await request.body()
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        try:
            r = await client.request(request.method, target, headers=headers, content=body)
        except Exception as exc:
            raise HTTPException(502, f"Printer proxy failed: {exc}")
    excluded = {"content-encoding", "transfer-encoding", "connection", "content-length"}
    resp_headers = {k: v for k, v in r.headers.items() if k.lower() not in excluded}
    content = r.content
    ctype = r.headers.get("content-type", "")
    if "text/html" in ctype:
        try:
            html = content.decode(r.encoding or "utf-8", errors="replace")
            base = f"/portal-proxy/{pcfg.id}/"
            html = html.replace("<head>", f'<head><base href="{base}">', 1)
            content = html.encode("utf-8")
            resp_headers["content-type"] = "text/html; charset=utf-8"
        except Exception:
            pass
    return StreamingResponse(iter([content]), status_code=r.status_code, headers=resp_headers, media_type=resp_headers.get("content-type"))


@app.get("/api/logs")
async def api_logs(limit: int = 120):
    return {"ok": True, "logs": get_logs(limit)}


@app.post("/api/setup/finish")
async def api_setup_finish():
    cfg = load_config()
    cfg.setdefault("app", {})["setup_complete"] = True
    save_config(cfg)
    runtime.reload()
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn

    cfg = load_config()
    uvicorn.run(
        "cc2_dash_lite.main:app",
        host=cfg.get("app", {}).get("bind_host", "0.0.0.0"),
        port=int(cfg.get("app", {}).get("port", 8088)),
        reload=False,
    )
