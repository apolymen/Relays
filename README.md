# Relays

Home automation firmware for a Raspberry Pi Pico 2 W (MicroPython). Two independent
services share one device: a two-zone irrigation controller and a hot water pump
timer, both self-contained on the LAN with no cloud dependency.

```
main.py
├── Watering Service      (watering_service.py)
│   ├── 2 zones, 2 valves each - manual start/stop per valve
│   ├── Master OFF switch (indefinite, or auto-resume after N days)
│   ├── Own schedules, own config file (watering_schedules.json)
│   └── JSON API + dashboard, routes under /watering
├── Hot Water Pump Service (pump_service.py)
│   ├── 1 relay
│   ├── 16 programmable schedules (pump_schedules.json)
│   └── JSON API + dashboard, routes under /pump
├── Remote Deploy Service (deploy_service.py)
│   └── Stage files, then commit (atomic swap + reboot), routes under /deploy
├── Wi-Fi Manager          (netmgr.py)
├── Time/NTP Manager       (netmgr.py, daily resync)
└── Web Server             (main.py, single listener, routes by path)
```

`scheduler_core.py` holds scheduling logic shared by both services (day-of-week
window checks, exact-time triggers, day-interval cooldowns, and a `Job` helper for
tracking/cancelling a running task). Each service still owns its own hardware
action and its own definition of "due", since the two run on different models:
the pump holds a relay on for as long as a time window is active, while watering
fires a one-shot multi-valve sequence at an exact minute and deliberately runs
one zone at a time to avoid splitting water pressure.

Both `watering.html` and `pump.html` are static dashboards (dark theme) that poll
a JSON API every 5 seconds for live status (Wi-Fi/NTP dots, clock, valve/relay
state) - there's no server-side HTML templating left in either service.

## Files

| File | Purpose |
|---|---|
| `main.py` | Boot-safety countdown, boot sequence, shared web server, request routing |
| `config.py` | Wi-Fi, NTP, watchdog (with a dev-mode disable flag), deploy token, per-service pin settings |
| `athens_time.py` | DST-aware local time (Europe/Athens), RTC kept in UTC |
| `netmgr.py` | Wi-Fi connect (DHCP), NTP sync with fallback, daily resync |
| `logger.py` | Shared rolling log buffer, tagged by service, mirrored to `system.log` on flash so history survives a reboot |
| `scheduler_core.py` | Shared scheduling primitives (see above) |
| `watering_service.py` | Zone/valve control, master switch, schedules, `/watering` routes |
| `pump_service.py` | Relay control, schedules, `/pump` routes |
| `deploy_service.py` | Remote code updates: stage files, then atomically commit + reboot, `/deploy` routes |
| `landing.html` | Home page linking to both dashboards and logs |
| `watering.html` | Watering dashboard (live valve status, per-valve start/stop, master switch) |
| `pump.html` | Pump dashboard (live relay status, 16 schedules) |
| `logs.html` | Combined system log view |
| `deploy.ps1` | Windows/PowerShell client for pushing updates via `deploy_service.py` |

Two directories are created on flash automatically the first time they're needed:
`staging/` (files uploaded but not yet committed) and `backup/` (previous version
of each file, one generation back, kept after a commit).

## Setup

1. Edit `config.py`: Wi-Fi credentials, relay/valve GPIO pins for your wiring,
   and `DEPLOY_TOKEN` (change it from the placeholder before using remote
   deploy). The device uses DHCP by default; `STATIC_IP_SETTINGS` is kept
   in `config.py` unused, for a possible future switch back to a static IP.
   A DHCP reservation on your router is the current way to keep its address
   stable.
2. Copy all `.py` and `.html` files to the Pico's flash (e.g. via Thonny).
3. Power on. `main.py` runs an 8-second countdown before importing `config`
   (and creating the watchdog, if `WATCHDOG_ENABLED`), giving you a
   guaranteed Ctrl-C window in Thonny on every boot, useful if you ever need
   to break in and flip that flag off for development. After the countdown,
   the device connects to Wi-Fi, syncs time over NTP, and serves the web
   interface on port 80.

## Web routes

- `/` - landing page
- `/watering` - watering dashboard; JSON API under `/watering/api/config`,
  `/watering/api/status`, `/watering/api/valve/start`, `/watering/api/valve/stop`,
  `/watering/api/master` (the OFF switch)
- `/pump` - pump dashboard; JSON API under `/pump/api/schedules`, `/pump/api/status`
- `/logs` - combined log of all services
- `/deploy/status`, `/deploy/stage`, `/deploy/commit` - remote code updates,
  each requiring an `X-Deploy-Token` header matching `config.DEPLOY_TOKEN`

## Remote deploy

`deploy_service.py` lets you push code updates over the network instead of
plugging in over USB, intended to be reached via Tailscale subnet routing
(the Pico itself doesn't run a Tailscale client; some other always-on device
on the LAN advertises the subnet). From a Windows machine:

```powershell
$env:PICO_DEPLOY_TOKEN = "your-secret-here"
.\deploy.ps1                                            # deploy everything
.\deploy.ps1 -Files "watering_service.py","watering.html" # deploy just these
```

Files are staged first and only swapped into place (with the previous version
backed up to `backup/`) once every staged file has arrived and `/deploy/commit`
is called, which then reboots the device. A commit is refused while any zone,
valve, or the pump is actively running. There's no automatic rollback yet if a
deploy turns out to be broken, recovery means either pushing the `backup/`
files back over the network (if the device still boots far enough to serve
HTTP) or physical access if it doesn't.

## Hardware notes

- Relay boards are active-HIGH with a pull-down on the signal line, so relays
  stay de-energized during boot before MicroPython runs.
- Use optocoupler relay modules rated for 3V/3.3V logic; generic 5V boards can
  be unreliable on the Pico's GPIO.
- RP2's `machine.WDT` has a hardware ceiling of 8388ms; `config.py` uses 8000ms.
