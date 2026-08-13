# watering_service.py
# Watering Service: 2 zones, 2 valves each, own schedules, own config file,
# own html page and routes (all under /watering).

import machine
import uasyncio as asyncio
import json

import config
import athens_time
import scheduler_core
from logger import log

CONFIG_FILE = "watering_config.json"

# --- HARDWARE SETUP ---
valves_a = []
valves_b = []

# Precautionary: pull pins low instantly to prevent high-level trigger on boot
for pin_num in config.ZONE_A_PINS:
    valves_a.append(machine.Pin(pin_num, machine.Pin.OUT, value=0))
for pin_num in config.ZONE_B_PINS:
    valves_b.append(machine.Pin(pin_num, machine.Pin.OUT, value=0))

# --- LIVE PARAMETERS (MODIFIABLE VIA WEB INTERFACE) ---
ZONES = {
    "zone_a": {
        "name": "Zone A", "valves": valves_a, "duration_min": 10, "day_interval": 1,
        "sched_1_hr": 6, "sched_1_min": 0, "sched_1_en": 1,
        "sched_2_hr": 18, "sched_2_min": 0, "sched_2_en": 0, "last_watered_day": 0
    },
    "zone_b": {
        "name": "Zone B", "valves": valves_b, "duration_min": 10, "day_interval": 1,
        "sched_1_hr": 23, "sched_1_min": 0, "sched_1_en": 1,
        "sched_2_hr": 19, "sched_2_min": 30, "sched_2_en": 0, "last_watered_day": 0
    }
}

jobs = {
    "zone_a": scheduler_core.Job(ZONES["zone_a"]["name"], tag="watering"),
    "zone_b": scheduler_core.Job(ZONES["zone_b"]["name"], tag="watering"),
}


def get_epoch_days():
    return int(athens_time.epoch_time() // 86400)


def load_config():
    try:
        f = open(CONFIG_FILE, "r")
        data = json.loads(f.read())
        f.close()
        for k in ["zone_a", "zone_b"]:
            if k in data:
                ZONES[k].update(data[k])
        log("watering", "Schedules loaded from flash.")
    except Exception:
        log("watering", "No saved schedules found, using defaults.")


def save_config():
    try:
        data = {}
        for k in ["zone_a", "zone_b"]:
            z = ZONES[k]
            data[k] = {
                "duration_min": z["duration_min"],
                "day_interval": z["day_interval"],
                "sched_1_hr": z["sched_1_hr"],
                "sched_1_min": z["sched_1_min"],
                "sched_1_en": z["sched_1_en"],
                "sched_2_hr": z["sched_2_hr"],
                "sched_2_min": z["sched_2_min"],
                "sched_2_en": z["sched_2_en"],
                "last_watered_day": z["last_watered_day"]
            }
        f = open(CONFIG_FILE, "w")
        f.write(json.dumps(data))
        f.close()
        log("watering", "Schedules saved to flash.")
    except Exception as e:
        log("watering", "Schedules save failed: " + str(e))


# --- EXECUTION / SCHEDULING ---

async def execute_watering(zone_id):
    """Asynchronously drives valves, feeding the watchdog during runtime.
    Busy-tracking and cancellation are handled by the wrapping Job; this
    function only owns the hardware action and its cleanup guarantee."""
    z = ZONES[zone_id]
    log("watering", "--- Cycle starting for " + z["name"] + " ---")
    try:
        for i, valve_pin in enumerate(z["valves"]):
            log("watering", "Opening Valve " + str(i + 1) + " of " + z["name"])
            valve_pin.value(1)

            rem = z["duration_min"] * 60
            while rem > 0:
                await asyncio.sleep(1)
                config.wdt.feed()
                rem -= 1

            valve_pin.value(0)
            log("watering", "Safely Closed Valve " + str(i + 1))

            await asyncio.sleep(1); config.wdt.feed()
            await asyncio.sleep(1); config.wdt.feed()

        log("watering", "--- Cycle finished for " + z["name"] + " ---")
    finally:
        for valve_pin in z["valves"]:
            valve_pin.value(0)


async def scheduler_task():
    """Background task monitoring the current clock time against target thresholds."""
    log("watering", "Scheduler monitoring loop initialised.")
    while True:
        config.wdt.feed()
        t = athens_time.localtime()
        hr, mn, epoch_day = t[3], t[4], get_epoch_days()

        for zone_id in ["zone_a", "zone_b"]:
            z = ZONES[zone_id]

            if not scheduler_core.interval_elapsed(epoch_day, z["last_watered_day"], z["day_interval"]):
                continue

            run_triggered = False
            if z["sched_1_en"] and scheduler_core.exact_minute_due(hr, mn, z["sched_1_hr"], z["sched_1_min"]):
                run_triggered = True
            elif z["sched_2_en"] and scheduler_core.exact_minute_due(hr, mn, z["sched_2_hr"], z["sched_2_min"]):
                run_triggered = True

            if run_triggered:
                z["last_watered_day"] = epoch_day
                task = jobs[zone_id].start(execute_watering(zone_id))
                if task is not None:
                    await task  # serialized: only one zone waters at a time
                    for _ in range(60):
                        await asyncio.sleep(1)
                        config.wdt.feed()

        await asyncio.sleep(5)


# --- WEB INTERFACE ---

def generate_html_page():
    """Reads watering.html and populates it with system tokens."""
    try:
        f = open("watering.html", "r")
        html = f.read()
        f.close()
    except Exception as e:
        return "<html><body><h1>Internal Storage Read Error: " + str(e) + "</h1></body></html>"

    t = athens_time.localtime()
    time_str = "{:02d}:{:02d}".format(t[3], t[4])

    html = html.replace("{{TIME}}", time_str)
    from logger import get_logs
    html = html.replace("{{LOGS}}", get_logs())

    for k in ["zone_a", "zone_b"]:
        sfx = "_A" if k == "zone_a" else "_B"
        z = ZONES[k]
        html = html.replace("{{NAME" + sfx + "}}", z["name"])
        html = html.replace("{{DUR" + sfx + "}}", str(z["duration_min"]))
        html = html.replace("{{INT" + sfx + "}}", str(z["day_interval"]))
        html = html.replace("{{S1H" + sfx + "}}", str(z["sched_1_hr"]))
        html = html.replace("{{S1M" + sfx + "}}", str(z["sched_1_min"]))
        html = html.replace("{{S2H" + sfx + "}}", str(z["sched_2_hr"]))
        html = html.replace("{{S2M" + sfx + "}}", str(z["sched_2_min"]))
        html = html.replace("{{S1E" + sfx + "}}", "checked" if z["sched_1_en"] else "")
        html = html.replace("{{S2E" + sfx + "}}", "checked" if z["sched_2_en"] else "")

    return html


def _parse_form_params(text):
    params = {}
    if not text:
        return params
    try:
        for pair in text.split("&"):
            if "=" in pair:
                parts = pair.split("=")
                params[parts[0]] = parts[1]
    except Exception:
        pass
    return params


async def handle(method, path, body_text, writer):
    """Returns True if this module handled the request (and wrote a response)."""

    if path == "/watering" or path == "/watering/":
        response = generate_html_page()
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n")
        writer.write(response.encode("utf-8"))
        await writer.drain()
        return True

    if method == "POST" and path == "/watering/update":
        p = _parse_form_params(body_text)
        zk = p.get("zone")
        if zk in ZONES:
            ZONES[zk]["duration_min"] = int(p.get("duration", 10))
            ZONES[zk]["day_interval"] = int(p.get("interval", 1))
            ZONES[zk]["sched_1_hr"] = int(p.get("s1_hr", 6))
            ZONES[zk]["sched_1_min"] = int(p.get("s1_mn", 0))
            ZONES[zk]["sched_1_en"] = 1 if "s1_en" in p else 0
            ZONES[zk]["sched_2_hr"] = int(p.get("s2_hr", 18))
            ZONES[zk]["sched_2_min"] = int(p.get("s2_mn", 0))
            ZONES[zk]["sched_2_en"] = 1 if "s2_en" in p else 0
            log("watering", "Updated settings for " + ZONES[zk]["name"])
            save_config()
        writer.write(b"HTTP/1.1 303 See Other\r\nLocation: /watering\r\n\r\n")
        await writer.drain()
        return True

    if method == "POST" and path == "/watering/manual":
        p = _parse_form_params(body_text)
        zk = p.get("zone")
        if zk in ZONES:
            if jobs[zk].busy:
                log("watering", "Manual run rejected, " + ZONES[zk]["name"] + " is already running.")
            else:
                log("watering", "Manual override triggered for " + ZONES[zk]["name"])
                jobs[zk].start(execute_watering(zk))
        writer.write(b"HTTP/1.1 303 See Other\r\nLocation: /watering\r\n\r\n")
        await writer.drain()
        return True

    if method == "POST" and path == "/watering/stop":
        p = _parse_form_params(body_text)
        zk = p.get("zone")
        if zk in ZONES:
            jobs[zk].stop()
        writer.write(b"HTTP/1.1 303 See Other\r\nLocation: /watering\r\n\r\n")
        await writer.drain()
        return True

    return False


def force_all_off():
    for valve_pin in valves_a:
        valve_pin.value(0)
    for valve_pin in valves_b:
        valve_pin.value(0)
