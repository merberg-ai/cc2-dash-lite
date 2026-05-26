from __future__ import annotations

import copy
import json
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

APP_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("CC2_DATA_DIR", APP_ROOT / "data"))
CONFIG_PATH = Path(os.environ.get("CC2_CONFIG", DATA_DIR / "config.json"))


@dataclass
class PrinterConfig:
    id: str
    name: str
    host: str
    serial: str
    access_code: str = "123456"
    port: int = 1883
    enabled: bool = True
    allow_commands: bool = True
    allow_dangerous_commands: bool = False


def safe_printer_id(name_or_serial: str) -> str:
    out = []
    for ch in str(name_or_serial or "").lower():
        if ch.isalnum():
            out.append(ch)
        elif ch in ("-", "_", " ", "."):
            out.append("-")
    value = "".join(out).strip("-")
    while "--" in value:
        value = value.replace("--", "-")
    return value or "cc2-printer"


def printer_dict_to_config(printer_id: str, data: dict[str, Any]) -> PrinterConfig:
    host = data.get("host") or data.get("ip") or ""
    serial = data.get("serial") or data.get("sn") or data.get("printer_id") or printer_id or host
    return PrinterConfig(
        id=str(printer_id or safe_printer_id(serial or host)),
        name=str(data.get("name") or data.get("host_name") or "Centauri Carbon 2"),
        host=str(host),
        serial=str(serial),
        access_code=str(data.get("access_code") or data.get("pin") or "123456"),
        port=int(data.get("port") or 1883),
        enabled=bool(data.get("enabled", True)),
        allow_commands=bool(data.get("allow_commands", True)),
        allow_dangerous_commands=bool(data.get("allow_dangerous_commands", False)),
    )


def public_printer_dict(cfg: PrinterConfig, include_secret: bool = False) -> dict[str, Any]:
    data = asdict(cfg)
    data["portal_url"] = f"/portal-fullscreen?printer={cfg.id}"
    data["portal_chrome_url"] = f"/portal?printer={cfg.id}"
    data["direct_portal_url"] = f"http://{cfg.host}/"
    data["camera_url"] = f"/api/printers/{cfg.id}/camera/stream"
    data["direct_camera_url"] = f"http://{cfg.host}:8080/"
    if not include_secret:
        data.pop("access_code", None)
    data["access_code_set"] = bool(cfg.access_code)
    return data

DEFAULT_CONFIG: dict[str, Any] = {
    "config_version": 1,
    "app": {
        "name": "cc2-dash-lite",
        "bind_host": "0.0.0.0",
        "port": 8088,
        "default_printer": None,
        "theme": "octo_dark_blue",
        "setup_complete": False,
    },
    "network": {
        "allow_mode": "subnet",
        "allowed_subnets": ["192.168.1.0/24"],
        "allowed_hosts": [],
        "always_allow_localhost": True,
        "scan_ports": [80, 8080, 3030, 1883, 8899],
    },
    "appearance": {
        "font_pack": "Terminal Modern",
        "font_scale": "normal",
        "letter_spacing": "normal",
        "uppercase_buttons": False,
        "fonts": {
            "base": "Terminal Modern",
            "heading": "Terminal Modern",
            "number": "Terminal Modern",
            "button": "Terminal Modern",
        },
    },
    "printers": {},
    "dashboard": {
        "refresh_interval_seconds": 3,
        "camera_autoload": True,
        "show_footer": True,
        "compact_mode": False,
        "cards": [
            {"id": "camera_status", "label": "Camera + Status", "enabled": True, "order": 10},
            {"id": "quick_actions", "label": "Quick Actions", "enabled": True, "order": 20},
            {"id": "connection_info", "label": "Connection", "enabled": True, "order": 30},
        ],
    },
    "actions": {
        "light_toggle": {
            "label": "Light Toggle",
            "enabled": True,
            "visible": True,
            "order": 10,
            "style": "primary",
            "requires_confirm": False,
            "confirm_text": "Toggle the printer light?",
            "spinner_text": "Sending light command...",
        },
        "pause_resume": {
            "label": "Pause / Resume Print",
            "enabled": True,
            "visible": True,
            "order": 20,
            "style": "primary",
            "requires_confirm": False,
            "confirm_text": "Pause or resume the current print?",
            "spinner_text": "Sending pause/resume...",
        },
        "cancel_print": {
            "label": "Cancel Print",
            "enabled": True,
            "visible": True,
            "order": 30,
            "style": "danger",
            "requires_confirm": True,
            "confirm_text": "Cancel the current print? This cannot be undone.",
            "spinner_text": "Canceling print...",
        },
        "restart_camera": {
            "label": "Restart Camera Stream",
            "enabled": True,
            "visible": True,
            "order": 40,
            "style": "secondary",
            "requires_confirm": False,
            "confirm_text": "Restart the camera stream?",
            "spinner_text": "Restarting camera...",
        },
    },
    "effects": {
        "fade_in_cards": True,
        "button_spinners": True,
        "loading_skeletons": True,
        "toast_notifications": True,
        "status_dot_pulse": True,
    },
    "advanced": {
        "adapter": "generic_elegoo_lite",
        "request_timeout_seconds": 2.5,
        "portal_proxy_enabled": False,
        "command_endpoints": {},
        "status_paths": ["/api/status", "/status", "/printer/status"],
    },
}


def deep_merge(defaults: Any, loaded: Any) -> Any:
    if isinstance(defaults, dict) and isinstance(loaded, dict):
        out = copy.deepcopy(defaults)
        for key, value in loaded.items():
            out[key] = deep_merge(out.get(key), value) if key in out else value
        return out
    return copy.deepcopy(loaded if loaded is not None else defaults)


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    ensure_data_dir()
    if not CONFIG_PATH.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with CONFIG_PATH.open("r", encoding="utf-8") as fh:
            loaded = json.load(fh)
    except Exception:
        backup = CONFIG_PATH.with_suffix(".broken.json")
        try:
            CONFIG_PATH.replace(backup)
        except Exception:
            pass
        return copy.deepcopy(DEFAULT_CONFIG)
    return deep_merge(DEFAULT_CONFIG, loaded)


def save_config(cfg: dict[str, Any]) -> dict[str, Any]:
    ensure_data_dir()
    merged = deep_merge(DEFAULT_CONFIG, cfg)
    tmp = CONFIG_PATH.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(merged, fh, indent=2, sort_keys=True)
    tmp.replace(CONFIG_PATH)
    return merged


def needs_setup(cfg: dict[str, Any] | None = None) -> bool:
    cfg = cfg or load_config()
    printers = cfg.get("printers") or {}
    if not cfg.get("app", {}).get("setup_complete") or not printers:
        return True
    # Older cc2-dash-lite builds could save only host/URL. The CC2 MQTT bridge
    # needs both serial number and PIN/access code, so route back through setup
    # until at least one configured printer has pairing details.
    for p in printers.values():
        if p.get("host") and p.get("serial") and p.get("access_code"):
            return False
    return True


def sorted_cards(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    return sorted(cfg.get("dashboard", {}).get("cards", []), key=lambda x: int(x.get("order", 999)))


def sorted_actions(cfg: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    actions = cfg.get("actions", {})
    return sorted(actions.items(), key=lambda kv: int(kv[1].get("order", 999)))


def default_printer(cfg: dict[str, Any]) -> tuple[str | None, dict[str, Any] | None]:
    printers = cfg.get("printers", {}) or {}
    wanted = cfg.get("app", {}).get("default_printer")
    if wanted and wanted in printers:
        return wanted, printers[wanted]
    if printers:
        pid = next(iter(printers.keys()))
        return pid, printers[pid]
    return None, None
