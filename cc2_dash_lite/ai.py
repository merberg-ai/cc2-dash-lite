from __future__ import annotations

import time
from collections import deque
from typing import Any, Deque


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _text_contains(text: str, *needles: str) -> bool:
    hay = (text or "").lower()
    return any(n.lower() in hay for n in needles)


class PortalAIDetector:
    """Small explainable rule engine for cc2-dash-lite Portal AI.

    This intentionally starts boring and reliable: printer telemetry, MQTT freshness,
    temperature sanity, progress movement, and camera availability hints. Vision / OpenCV
    can be bolted on later without changing the UI contract.
    """

    def __init__(self) -> None:
        self._state: dict[str, dict[str, Any]] = {}
        self._feedback: Deque[dict[str, Any]] = deque(maxlen=200)

    def reset(self, printer_id: str | None = None) -> None:
        if printer_id:
            self._state.pop(printer_id, None)
        else:
            self._state.clear()

    def feedback(self, printer_id: str, label: str, note: str = "", snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        row = {
            "printer_id": printer_id,
            "label": str(label or "unknown"),
            "note": str(note or ""),
            "timestamp": time.time(),
            "snapshot": snapshot or {},
        }
        self._feedback.append(row)
        return row

    def recent_feedback(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(self._feedback)[-max(1, min(limit, 200)):]

    def evaluate(self, printer_id: str, status: dict[str, Any], snap: dict[str, Any] | None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
        cfg = cfg or {}
        ai_cfg = cfg.get("portal_ai", {}) or {}
        if not ai_cfg.get("enabled", True):
            return {
                "enabled": False,
                "state": "disabled",
                "level": "disabled",
                "risk": 0,
                "summary": "Disabled",
                "reasons": ["Portal AI is disabled in settings."],
                "last_check_epoch": time.time(),
                "last_check": time.strftime("%H:%M:%S"),
            }

        now = time.time()
        prev = self._state.setdefault(printer_id, {})
        reasons: list[str] = []
        positives: list[str] = []
        risk = 0

        snap = snap or {}
        normalized = snap.get("normalized") or {}
        state_text = str(status.get("status_text") or status.get("state") or normalized.get("sub_state") or normalized.get("state") or "unknown")
        state_lower = state_text.lower()
        reachable = bool(status.get("reachable"))
        connected = bool(status.get("connected"))
        registered = bool(status.get("registered"))
        message_age = _as_float(status.get("updated_at"), 999999.0)

        progress = _as_float(status.get("progress"), 0.0)
        file_name = str(status.get("file") or "-").strip()
        hotend_current = _as_float(status.get("hotend_current"), 0.0)
        hotend_target = _as_float(status.get("hotend_target"), 0.0)
        bed_current = _as_float(status.get("bed_current"), 0.0)
        bed_target = _as_float(status.get("bed_target"), 0.0)
        elapsed = (normalized.get("time") or {}).get("elapsed_sec")
        elapsed_sec = _as_float(elapsed, 0.0)
        exceptions = normalized.get("exceptions") or []
        camera_info = (normalized.get("external") or {}).get("camera")
        camera_attr = (normalized.get("attributes") or {}).get("camera_connected")
        filament = normalized.get("filament") or {}

        active_state = _text_contains(state_lower, "print", "paus", "resum", "stopp", "idle in print")
        has_print_markers = bool(file_name and file_name != "-" and progress < 99.9 and (hotend_target > 0 or bed_target > 0 or elapsed_sec > 0))
        active_print = bool(active_state or has_print_markers)

        if not reachable:
            risk += 45 if active_print else 30
            reasons.append("Printer is not reachable through the CC2 MQTT client.")
        elif not connected:
            risk += 30
            reasons.append("MQTT client is not fully connected yet.")
        elif not registered:
            risk += 25
            reasons.append("MQTT client is connected but registration is not confirmed yet.")
        else:
            positives.append("Printer telemetry is connected.")

        stale_after = _as_float(ai_cfg.get("stale_status_seconds"), 75.0)
        if message_age > stale_after:
            bump = 45 if active_print else 25
            risk += bump
            reasons.append(f"Last printer status is stale ({int(message_age)}s old).")
        elif message_age < 999999:
            positives.append(f"Telemetry is fresh ({int(message_age)}s old).")

        if _text_contains(state_lower, "error", "fail", "emergency", "exception"):
            risk += 80
            reasons.append(f"Printer state reports a problem: {state_text}.")
        if _text_contains(state_lower, "stopped") and 0 < progress < 99:
            risk += 65
            reasons.append("Print appears stopped before reaching 100% completion.")
        if _text_contains(state_lower, "paused"):
            risk += 12
            reasons.append("Print is paused. This may be intentional, but it needs attention.")
        if exceptions:
            risk += 45
            reasons.append(f"Printer reported exception status: {exceptions}.")

        if active_print:
            positives.append("A print appears to be active or recently active.")
            if hotend_target <= 0 and progress < 99:
                risk += 35
                reasons.append("Hotend target is off while the print still appears active.")
            elif hotend_target >= 150:
                diff = hotend_target - hotend_current
                if diff > 35:
                    risk += 45
                    reasons.append(f"Hotend is far below target ({hotend_current:.1f}/{hotend_target:.1f}°C).")
                elif diff > 20:
                    risk += 22
                    reasons.append(f"Hotend is below target ({hotend_current:.1f}/{hotend_target:.1f}°C).")
                else:
                    positives.append("Hotend temperature is near target.")

            if bed_target >= 35:
                diff = bed_target - bed_current
                if diff > 18:
                    risk += 25
                    reasons.append(f"Bed is well below target ({bed_current:.1f}/{bed_target:.1f}°C).")
                elif diff <= 10:
                    positives.append("Bed temperature is near target.")

            if filament.get("sensor_enabled") and filament.get("detected") is False:
                risk += 80
                reasons.append("Filament sensor reports no filament while printing.")

            stuck_minutes = _as_float(ai_cfg.get("progress_stuck_minutes"), 8.0)
            last_progress = prev.get("progress")
            changed_at = prev.get("progress_changed_at") or now
            if last_progress is None or abs(progress - _as_float(last_progress)) >= 0.15:
                prev["progress"] = progress
                prev["progress_changed_at"] = now
                prev["file"] = file_name
                positives.append("Progress has moved recently.")
            else:
                stuck_for = (now - changed_at) / 60.0
                if progress > 0.1 and stuck_for >= stuck_minutes:
                    risk += 45
                    reasons.append(f"Progress has not changed for about {stuck_for:.1f} minutes.")
                elif progress > 0.1 and stuck_for >= max(2.0, stuck_minutes / 2.0):
                    risk += 18
                    reasons.append(f"Progress has been unchanged for about {stuck_for:.1f} minutes.")
        else:
            prev["progress"] = progress
            prev["progress_changed_at"] = now
            if reachable:
                positives.append("Printer is not reporting an active print.")

        if ai_cfg.get("camera_rules_enabled", True):
            # This is deliberately a lightweight camera health hint. The browser still
            # displays the stream; future versions can add frame sampling/OpenCV here.
            camera_known = camera_info is not None or camera_attr is not None
            camera_bad = False
            if isinstance(camera_info, dict):
                camera_bad = camera_info.get("status") in (False, 0, "0", "off", "offline") or camera_info.get("connected") is False
            elif camera_info in (False, 0, "0", "off", "offline"):
                camera_bad = True
            if camera_attr in (False, 0, "0", "false", "False"):
                camera_bad = True
            if camera_bad:
                risk += 18 if active_print else 8
                reasons.append("Printer reports the camera may be unavailable.")
            elif camera_known:
                positives.append("Camera status hint looks okay.")

        risk = max(0, min(100, int(round(risk))))
        if risk >= 75:
            level = "high"
            state = "failure_likely"
            summary = "Failure Likely"
        elif risk >= 50:
            level = "medium"
            state = "suspicious"
            summary = "Suspicious"
        elif risk >= 25:
            level = "watch"
            state = "watch"
            summary = "Watching Closely"
        else:
            level = "low"
            state = "watching" if active_print else "standing_by"
            summary = "Watching" if active_print else "Standing By"

        if not reasons:
            reasons = positives[:3] or ["No warning rules are currently triggered."]
        else:
            # Add one positive hint for context when something is mildly wrong.
            if positives and risk < 75:
                reasons.append(positives[0])

        result = {
            "enabled": True,
            "state": state,
            "level": level,
            "risk": risk,
            "summary": summary,
            "reasons": reasons[:5],
            "positives": positives[:5],
            "active_print": active_print,
            "last_check_epoch": now,
            "last_check": time.strftime("%H:%M:%S"),
            "rules": {
                "telemetry": bool(ai_cfg.get("telemetry_rules_enabled", True)),
                "camera": bool(ai_cfg.get("camera_rules_enabled", True)),
                "vision": bool(ai_cfg.get("vision_ai_enabled", False)),
            },
        }
        prev["last_result"] = result
        return result


portal_ai = PortalAIDetector()
