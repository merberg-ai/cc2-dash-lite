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
- Console/log page
- LAN allowlist guard, defaulting to `192.168.1.0/24` plus localhost
- Install/uninstall scripts for Raspberry Pi/Linux
- Optional systemd service installation

## Install

```bash
unzip cc2-dash-lite.zip
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

## Commands and safety

The CC2 command methods from the older cc2-dash source are included. Current quick actions map to:

```text
File Manager       -> methods 1044, 1046, 1047, 1051, 1020, 1038
Light Toggle       -> method 1029
Pause Print        -> method 1021
Resume Print       -> method 1023
Cancel Print       -> method 1022
Camera Wake/Enable -> methods 1042 / 1054
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


## v0.3.1 notes

- Files/timelapse read endpoints no longer convert printer `error_code` responses into HTTP 500s.
- Timelapse listing now loads through print history like the stock Elegoo portal; method 1051 is kept for export only.
- USB file-list errors now show a friendly message instead of crashing the Files page.


## 0.3.2 notes

- Timelapse tab now mirrors the stock Elegoo portal Video List behavior more closely.
- Backend filters Print History rows for timelapse records using TimeLapseVideoStatus 1/2 or video URL/size/duration markers.
- Added a fallback attempt for task-detail lookup using method 1037 when history rows do not include video metadata directly.
- UI now shows size, creation time, duration, generated/export-needed status, and download/export actions.
