# cc2-dash-lite

A lightweight, mobile-first Centauri Carbon 2 dashboard/portal shell inspired by OctoEverywhere's simple printer view.

This is meant to be a clean LAN dashboard layer, not a full replacement for the stock Elegoo portal. The big escape hatch is always present: **Go To Elegoo Web Portal**.

## What is included

- FastAPI backend
- Mobile-first dashboard UI
- First-run setup wizard
- UDP Centauri discovery using Elegoo method `7000`
- Fallback LAN port scanner with spinner/progress feedback
- PIN/access-code pairing during setup
- MQTT client using the printer serial + PIN/access code
- Normalized printer status from the CC2 MQTT status stream
- Stock Elegoo/OctoEverywhere-style portal bundle from the older cc2-dash source
- Local MQTT-over-WebSocket bridge for the stock portal
- Camera stream proxy/wake endpoint
- File Manager page for G-code files and timelapse/history video records
- G-code file list/detail/start/delete endpoints from the stock portal command set
- Timelapse/history load/export/download/delete controls where firmware allows it
- Configurable dashboard card visibility/order
- Configurable quick-action button visibility/order/confirmation
- JSON-based theme system
- Configurable font packs using local/system font stacks
- Filterable persisted Logs page for system, command, Portal AI, and vision events
- Portal AI v1 telemetry failure detection with explainable risk score
- Portal AI background watchdog monitoring, even when the browser is closed
- Optional Ollama vision monitoring using printer camera snapshots
- Local camera-frame heuristics for dark/low-contrast frames and stringing-ish fine-edge warnings
- Dashboard telemetry display for the current printer speed preset when reported by the printer
- Portal AI feedback buttons for Looks Good / Looks Bad / False Alarm tuning
- Header build badge showing version + Git/GitHub commit metadata when available
- LAN allowlist guard, defaulting to `192.168.1.0/24` plus localhost
- Install/uninstall scripts for Raspberry Pi/Linux
- Optional systemd service installation

## Install

```bash
unzip cc2-dash-lite-1.2.6.zip
cd cc2-dash-lite
./install.sh
./run.sh
```

Then open:

```text
http://<pi-ip>:8088/
```

## Install as a system service

```bash
./install.sh --service --port=8088
```

Useful commands:

```bash
sudo systemctl status cc2-dash-lite
sudo systemctl restart cc2-dash-lite
sudo journalctl -u cc2-dash-lite -f
```

## Uninstall

Remove the service but keep config/data:

```bash
./uninstall.sh
```

Remove service plus `.venv` and `data/`:

```bash
./uninstall.sh --purge
```

## First-run setup

The setup wizard now saves the pieces the CC2 actually needs:

```text
Printer IP
Printer serial / SN
Printer PIN / access code
MQTT port, default 1883
```

Discovery usually fills the serial automatically. If the fallback scanner finds only an IP, the setup screen asks you to type the serial manually.

If you already tested the previous cc2-dash-lite build and it saved a printer without serial/PIN, this build will route you back through setup instead of pretending everything is paired. That is intentional. No more “trust me bro” pairing.

## Stock Elegoo portal

The dashboard button now opens the bundled stock Elegoo portal through:

```text
/portal-fullscreen
```

The chrome/wrapper view is:

```text
/portal
```

The stock app itself is mounted under:

```text
/elegoo/octo_portal.html
```

The local bridge is:

```text
/ws/mqtt/<printer_id>
```

That bridge shuttles browser WebSocket MQTT frames to the printer's TCP MQTT port at `1883`.

## Portal AI v1

The dashboard now has a real **Portal AI 🤖** panel instead of a dummy label. This first pass is intentionally explainable and telemetry-first. It can optionally use Ollama vision models for camera-frame analysis, but it still does not auto-pause/cancel prints.

Current checks include:

```text
Printer reachable / connected / registered
Stale MQTT status age
Printer error/fail/emergency/stopped states
Paused state warning
Printer exception status
Progress stuck timer
Hotend/bed target sanity while a print appears active
Filament sensor says no filament while printing
Printer-reported camera availability hints
Optional Ollama camera-frame analysis
Local frame checks for dark camera images and high fine-edge/stringing-ish changes
```

The background watchdog starts with the FastAPI service and keeps evaluating configured printers on a timer, even if nobody has the dashboard open. The dashboard displays the latest cached watchdog result when available, so browser polling is no longer what keeps the AI alive.

The API returns the score under `portal_ai` in `/api/status` and exposes dedicated endpoints:

```text
GET  /api/ai/monitor
GET  /api/vision/models
GET  /api/printers/<printer_id>/ai/status
POST /api/printers/<printer_id>/ai/check-now
POST /api/printers/<printer_id>/ai/feedback
GET  /api/printers/<printer_id>/vision/status
GET  /api/vision/models
POST /api/vision/pull
POST /api/printers/<printer_id>/vision/check-now
GET  /api/printers/<printer_id>/vision/latest.jpg
```

Settings → Portal AI controls the rule toggles, thresholds, background monitor interval, watchdog logging level, Ollama host:port, vision model, local frame heuristics, vision interval, and prompt. Auto-pause settings are stored for the future, but this build remains advisory-only. No robot panic button yet.


## Ollama vision monitoring

Enable this in **Settings → Portal AI → Ollama vision monitoring**. The backend samples the printer camera, sends a single JPEG frame to your configured Ollama host, and merges the JSON result into the existing Portal AI risk score.

Defaults:

```text
Ollama host:port: http://192.168.1.24:11434
Vision model: llava
Vision interval: 120 seconds
Bad checks required: 2
```

Use **Load Models** to populate the themed model dropdown from `/api/tags`, **Test** to verify the selected model is installed, and **Pull** to request a model download through Ollama. The quick action **Analyze Camera Now** forces an immediate one-shot camera analysis. Local heuristics run before/alongside Ollama, so turning the printer light off should now produce a camera/view warning even if the model would otherwise shrug. High fine-edge density is logged as a possible stringing/spaghetti hint for visual review.

Vision is advisory-only in this build. It raises/lower Portal AI risk and stores the latest frame under `data/vision/<printer_id>/latest.jpg`, but it does not pause or cancel prints.

## Logs

The Logs page now reads from the in-memory console and `data/logs/system.jsonl`. Use the source/level/search filters to isolate:

```text
system/app/setup/settings
command
portal_ai
vision
scanner
```

Portal AI watchdog changes and vision state changes are logged automatically. Vision logs include heuristic flags such as `dark_frame`, `low_contrast_frame`, `high_fine_edge_density`, and `fine_edge_density_jump` when they trigger.

## Commands and safety

The CC2 command methods from the older cc2-dash source are included. Current quick actions map to:

```text
File Manager       -> methods 1044, 1046, 1047, 1051, 1020, 1038
Light Toggle       -> method 1029
Pause Print        -> method 1021
Resume Print       -> method 1023
Cancel Print       -> method 1022
Camera Wake/Enable -> methods 1042 / 1054
Set Speed Preset  -> method 1031 params {"mode": 0-3}; mode is chosen from the dashboard selector at click time
Analyze Camera Now -> server-side Ollama vision check
```

By default, non-dangerous commands are enabled for newly paired printers. Dangerous commands are still disabled by default, so `Cancel Print` may be blocked until you enable `allow_dangerous_commands` for that printer in the raw JSON settings. That is deliberate, because accidentally canceling a long print from a phone tap is how dashboards become haunted.

## Configuration

The app stores config here by default:

```text
./data/config.json
```

Override paths with environment variables:

```bash
export CC2_DATA_DIR=/path/to/data
export CC2_CONFIG=/path/to/config.json
export CC2_PORT=8088
```

## Theme system

Themes live in:

```text
cc2_dash_lite/themes.py
```

Included themes:

- Octo Dark Blue
- Amber Terminal
- Mainsail-ish Dark
- Carbon Glass
- High Contrast

Fonts are CSS font stacks only. No external font files are bundled.

## Frontend approach

The runtime UI currently uses plain CSS and vanilla JavaScript so it works on a Raspberry Pi without Node.

A starter Tailwind config is included under `frontend/` for future builds, but the app does not require Tailwind to run.

## Project layout

```text
cc2-dash-lite/
├── cc2_dash_lite/
│   ├── main.py
│   ├── config.py
│   ├── printer_client.py
│   ├── scanner.py
│   ├── themes.py
│   ├── logger.py
│   ├── cc2/
│   │   ├── client.py
│   │   ├── commands.py
│   │   ├── discovery.py
│   │   ├── runtime.py
│   │   └── state.py
│   └── elegoo_web/
│       ├── octo_portal.html
│       └── cc2dash-shim.js
├── static/
├── templates/
├── frontend/
├── install.sh
├── uninstall.sh
├── run.sh
└── requirements.txt
```

## Next obvious upgrades

- Better setup validation that waits for successful MQTT registration before finishing
- A dedicated printer edit screen for changing PIN/serial without raw JSON
- Surface file manager/timelapse controls directly in the lite UI
- Add drag/drop card ordering
- Add import/export config buttons
- Add a printer adapter plugin layer


## File Manager

The top navigation now includes:

```text
Files
```

That page has two separate panels:

```text
G-code Files        -> local / USB file list, info, print, delete
Timelapse Videos   -> timelapse/history records, download/export/delete
```

This uses the same CC2/Elegoo MQTT command family that the stock portal code uses. Some firmware builds return slightly different JSON shapes, so the frontend tries several known list keys before giving up.

Safety note: `Start Print`, `Delete File`, and `Delete History/Timelapse` are blocked by the backend unless that printer has dangerous commands enabled. That is intentional because phone thumbs are tiny chaos engines.


## v1.0.0 stable checkpoint

This release marks the current working cc2-dash-lite state as the stable **1.0.0** rollback point before further feature work.

Included in this checkpoint:

- First-run setup wizard with scan, PIN/access-code pairing, and saved printer configuration
- Bundled stock Elegoo portal and local MQTT-over-WebSocket bridge
- Mobile-first OctoEverywhere-style dashboard shell
- Theme/font system
- Configurable dashboard cards and quick action buttons
- Configurable top menu features, including the File Manager visibility toggle
- Files route and backend endpoints retained for later refinement, with the menu hidden when desired
- Raspberry Pi/Linux install, uninstall, venv, and optional systemd service scripts

Treat this as the known-good baseline before changing the file/timelapse system or adding heavier dashboard controls.


## v0.3.1 notes

- Files/timelapse read endpoints no longer convert printer `error_code` responses into HTTP 500s.
- Timelapse listing now loads through print history like the stock Elegoo portal; method 1051 is kept for export only.
- USB file-list errors now show a friendly message instead of crashing the Files page.


## 0.3.2 notes

- Timelapse tab now mirrors the stock Elegoo portal Video List behavior more closely.
- Backend filters Print History rows for timelapse records using TimeLapseVideoStatus 1/2 or video URL/size/duration markers.
- Added a fallback attempt for task-detail lookup using method 1037 when history rows do not include video metadata directly.
- UI now shows size, creation time, duration, generated/export-needed status, and download/export actions.


## v0.3.3 note

The top menu now has a configurable **File Manager menu option** toggle under **Settings → Menu / Features**. Turn it off to hide the Files link while the file/timelapse implementation is still being refined. The underlying `/files` route and backend endpoints are left in place for testing and later fixes.


## v1.1.1 notes

- Portal AI adds configurable multi-color / filament-swap progress-stall grace.
- Feedback labels are now also persisted to `data/ai_feedback.jsonl` for later tuning.



## v1.2.0 notes

- Portal AI now has a backend background watchdog task that starts with the service.
- Monitoring continues when the dashboard/browser is closed.
- `/api/status` now serves cached watchdog results when available instead of requiring the browser to drive AI evaluation.
- Added Settings → Portal AI controls for background monitor enable/disable, check interval, log-on-change behavior, and minimum watchdog log level.
- Added `/api/ai/monitor` for watchdog status/debug info.



## v1.2.1 notes

- Header now shows a tiny theme-font build badge near the app name, e.g. `v1.2.1 · commit abc1234`.
- Added runtime build metadata detection from Git checkout or environment variables such as `CC2_DASH_GIT_COMMIT`, `GITHUB_SHA`, `CC2_DASH_GIT_BRANCH`, and `GITHUB_REF_NAME`.
- Added `/api/version` and included build metadata in `/health` for quick diagnostics.
- ZIP/archive installs without `.git` will show `commit unknown` unless a commit env var is supplied by the service/deployment.


## v1.2.3 notes

- Settings → Portal AI now has a themed Ollama model dropdown. Use **Load Models** to fetch installed models from the configured Ollama host.
- Added **Pull** model support via Ollama `/api/pull` for grabbing a model by name from the settings screen.
- Dashboard hides the vision status block completely when Ollama vision monitoring is disabled.
- The Set Speed quick action now shows a dashboard selector for Silent / Balanced / Sport / Ludicrous and sends the selected mode at click time. The button settings keep show/hide, label, order, and confirmation only.


## v1.2.5 notes

- Vision prompts now include printer telemetry context before asking Ollama to classify the camera image. This prevents the model from calling an active print "idle" just because the still frame looks still.
- Added a telemetry/model mismatch guard. If telemetry says the printer is printing but Ollama says idle/ready, the dashboard marks the vision result as uncertain and logs `telemetry_model_mismatch`.
- Local frame heuristics are more sensitive to lights-off tests. They now detect absolute dark frames, low-contrast dim frames, and relative light drops from the learned baseline.
- Vision card now shows luma/contrast/edge metrics to make tuning less voodoo.
- Speed telemetry is shown in the Status block and the telemetry grid, with broader parsing for speed mode/percent fields.

## v1.2.6 notes

- Dashboard quick-action buttons for **Analyze Camera Now** and **Set Speed** now use the active theme color instead of the plain secondary/card style.
- Portal AI feedback buttons now use theme-matched colors while still visually separating good/bad/false-alarm labels.
- AI feedback now saves a richer labeled review record to `data/ai_feedback.jsonl`:
  - feedback label and note
  - current printer status snapshot without the huge raw MQTT payload
  - current Portal AI result
  - latest vision result
  - client/UI context
  - a stable copy of the latest vision frame when one exists
- Feedback frame copies are stored under `data/ai_feedback_frames/<printer_id>/`.
- Added feedback review endpoints:
  - `GET /api/ai/feedback/recent`
  - `GET /api/ai/feedback/stats`

Feedback is now a proper dataset builder, but it still does **not** auto-train or auto-tune live scoring. That is intentional for safety: the dashboard should collect labeled examples first, then later use that dataset for calibration or fine-tuning after review.
