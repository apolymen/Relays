# Relays

Home automation firmware for a Raspberry Pi Pico 2 W (MicroPython). Two independent
services share one device: a two-zone irrigation controller and a hot water pump
timer, both self-contained on the LAN with no cloud dependency.

```
main.py
├── Watering Service      (watering_service.py)
│   ├── 2 zones, 2 valves each
│   ├── Own schedules, own config file (watering_config.json)
│   └── Own dashboard, routes under /watering
├── Hot Water Pump Service (pump_service.py)
│   ├── 1 relay
│   ├── 16 programmable schedules (pump_schedules.json)
│   └── Own dashboard, routes under /pump
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

## Files

| File | Purpose |
|---|---|
| `main.py` | Boot sequence, shared web server, request routing |
| `config.py` | Wi-Fi, static IP, NTP, watchdog, and per-service pin settings |
| `athens_time.py` | DST-aware local time (Europe/Athens), RTC kept in UTC |
| `netmgr.py` | Wi-Fi connect, NTP sync with fallback, daily resync |
| `logger.py` | Shared rolling log buffer, tagged by service |
| `scheduler_core.py` | Shared scheduling primitives (see above) |
| `watering_service.py` | Zone/valve control, schedules, `/watering` routes |
| `pump_service.py` | Relay control, schedules, `/pump` routes |
| `landing.html` | Home page linking to both dashboards and logs |
| `watering.html` | Watering dashboard |
| `pump.html` | Pump dashboard |
| `logs.html` | Combined system log view |

## Setup

1. Edit `config.py`: Wi-Fi credentials, static IP settings, relay/valve GPIO
   pins for your wiring.
2. Copy all `.py` and `.html` files to the Pico's flash (e.g. via Thonny or
   `mpremote`).
3. Power on. The device connects to Wi-Fi, syncs time over NTP, and serves the
   web interface on port 80 at its static IP.

## Web routes

- `/` — landing page
- `/watering` — watering dashboard (zone schedules, manual start/stop)
- `/pump` — pump dashboard (16 schedules, live status)
- `/logs` — combined log of both services

## Hardware notes

- Relay boards are active-HIGH with a pull-down on the signal line, so relays
  stay de-energized during boot before MicroPython runs.
- Use optocoupler relay modules rated for 3V/3.3V logic; generic 5V boards can
  be unreliable on the Pico's GPIO.
