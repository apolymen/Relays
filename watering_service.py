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

CONFIG_FILE = "watering_schedules.json"

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

# One Job per physical valve, for manual single-valve control.
valve_jobs = {
    zone_id: [
        scheduler_core.Job(ZONES[zone_id]["name"] + " Valve " + str(i + 1), tag="watering")
        for i in range(len(ZONES[zone_id]["valves"]))
    ]
    for zone_id in ZONES
}

# Which valve index a zone's full cycle (jobs[zone_id]) currently has open,
# or None. Lets the status API reflect scheduled runs the same way it
# already reflects manual single-valve runs (which are tracked by valve_jobs
# busy state instead).
active_valve = {zone_id: None for zone_id in ZONES}

# Master switch: when disabled, no schedule can trigger and no manual valve
# start is allowed (server-side, not just hidden in the UI). off_days == 0
# means "off indefinitely, only a manual ON resumes it"; off_days > 0 means
# "auto-resume this many days after off_start_day (inclusive of that day)".
master = {"enabled": True, "off_days": 0, "off_start_day": 0}

# Set by load_config() if the master switch was OFF at boot. The actual
# day-count restart happens later, in apply_master_boot_offset(), once NTP
# has made the clock trustworthy - load_config() runs before Wi-Fi/NTP sync,
# so computing "today" here would use a meaningless default RTC value.
_master_boot_recalc = False


def zone_busy(zone_id):
    """True if the zone's full cycle, or any single valve within it, is
    currently running. Keeps a zone's valves mutually exclusive of each
    other no matter which control path (schedule, full-zone manual start,
    or single-valve manual start) started the job - same one-thing-at-a-
    time-per-zone principle the full cycle already enforces between zones."""
    if jobs[zone_id].busy:
        return True
    return any(j.busy for j in valve_jobs[zone_id])


def get_epoch_days():
    return int(athens_time.epoch_time() // 86400)


def load_config():
    global _master_boot_recalc
    try:
        f = open(CONFIG_FILE, "r")
        data = json.loads(f.read())
        f.close()
        for k in ["zone_a", "zone_b"]:
            if k in data:
                ZONES[k].update(data[k])
        if "master" in data:
            master.update(data["master"])
        log("watering", "Schedules loaded from flash.")
    except Exception:
        log("watering", "No saved schedules found, using defaults.")
    if not master["enabled"]:
        _master_boot_recalc = True


def apply_master_boot_offset():
    """If the master switch was OFF at boot, restart its day-count from
    today rather than trying to preserve elapsed time across a power loss
    (today's date isn't known until NTP has synced, hence this runs as a
    separate step after connect_and_sync(), not inside load_config())."""
    global _master_boot_recalc
    if _master_boot_recalc:
        master["off_start_day"] = get_epoch_days()
        save_config()
        log("watering", "Master switch was OFF at boot; day count restarted from today.")
    _master_boot_recalc = False


def _check_master_resume():
    """Auto-resumes the master switch once its off_days window has elapsed.
    A off_days of 0 means indefinite - never auto-resumes."""
    if master["enabled"] or master["off_days"] <= 0:
        return
    elapsed = get_epoch_days() - master["off_start_day"]
    if elapsed >= master["off_days"]:
        master["enabled"] = True
        save_config()
        log("watering", "Master switch auto-resumed after " + str(master["off_days"]) + " day(s).")


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
        data["master"] = {
            "enabled": master["enabled"],
            "off_days": master["off_days"],
            "off_start_day": master["off_start_day"],
        }
        f = open(CONFIG_FILE, "w")
        f.write(json.dumps(data))
        f.close()
        # log("watering", "Schedules saved to flash.")
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
            active_valve[zone_id] = i
            valve_pin.value(1)

            rem = z["duration_min"] * 60
            while rem > 0:
                await asyncio.sleep(1)
                config.wdt.feed()
                rem -= 1

            valve_pin.value(0)
            active_valve[zone_id] = None
            log("watering", "Safely Closed Valve " + str(i + 1))

            await asyncio.sleep(1); config.wdt.feed()
            await asyncio.sleep(1); config.wdt.feed()

        log("watering", "--- Cycle finished for " + z["name"] + " ---")
    finally:
        for valve_pin in z["valves"]:
            valve_pin.value(0)
        active_valve[zone_id] = None


async def execute_valve(zone_id, valve_index):
    """Manually drives a single valve for the zone's configured duration.
    Same auto-off safety and cleanup guarantee as execute_watering, scoped
    to one valve instead of the full zone sequence."""
    z = ZONES[zone_id]
    valve_pin = z["valves"][valve_index]
    label = z["name"] + " Valve " + str(valve_index + 1)
    log("watering", "Manual valve start: " + label)
    try:
        valve_pin.value(1)
        rem = z["duration_min"] * 60
        while rem > 0:
            await asyncio.sleep(1)
            config.wdt.feed()
            rem -= 1
        log("watering", label + " finished (auto-off after " + str(z["duration_min"]) + " min).")
    finally:
        valve_pin.value(0)


async def scheduler_task():
    """Background task monitoring the current clock time against target thresholds."""
    log("watering", "Scheduler monitoring loop initialised.")
    while True:
        config.wdt.feed()
        _check_master_resume()

        if not master["enabled"]:
            await asyncio.sleep(5)
            continue

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
                if zone_busy(zone_id):
                    log("watering", "Scheduled run skipped, " + z["name"] + " already busy.")
                else:
                    z["last_watered_day"] = epoch_day
                    task = jobs[zone_id].start(execute_watering(zone_id))
                    if task is not None:
                        await task  # serialized: only one zone waters at a time
                        for _ in range(60):
                            await asyncio.sleep(1)
                            config.wdt.feed()

        await asyncio.sleep(5)


# --- WEB INTERFACE ---

def json_response(obj, status=200):
    body = json.dumps(obj).encode("utf-8")
    headers = (
        "HTTP/1.1 {} OK\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: {}\r\n"
        "Connection: close\r\n\r\n"
    ).format(status, len(body))
    return headers.encode("utf-8") + body


def file_response(path, content_type):
    try:
        with open(path, "rb") as f:
            body = f.read()
    except OSError:
        return text_response("Not found", 404)
    headers = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: {}\r\n"
        "Content-Length: {}\r\n"
        "Connection: close\r\n\r\n"
    ).format(content_type, len(body))
    return headers.encode("utf-8") + body


def text_response(msg, status=200):
    body = msg.encode("utf-8")
    headers = (
        "HTTP/1.1 {} \r\n"
        "Content-Type: text/plain\r\n"
        "Content-Length: {}\r\n"
        "Connection: close\r\n\r\n"
    ).format(status, len(body))
    return headers.encode("utf-8") + body


def _zone_config_out(zk):
    z = ZONES[zk]
    return {
        "name": z["name"],
        "duration_min": z["duration_min"],
        "day_interval": z["day_interval"],
        "sched_1_hr": z["sched_1_hr"],
        "sched_1_min": z["sched_1_min"],
        "sched_1_en": bool(z["sched_1_en"]),
        "sched_2_hr": z["sched_2_hr"],
        "sched_2_min": z["sched_2_min"],
        "sched_2_en": bool(z["sched_2_en"]),
        "valve_count": len(z["valves"]),
    }


def _validate_zone_update(data):
    """Raises ValueError with a readable message if data isn't a usable
    zone update. Returns a cleaned dict of the fields ZONES[...] expects."""
    try:
        duration = int(data.get("duration_min", 10))
        interval = int(data.get("day_interval", 1))
        s1_hr = int(data.get("sched_1_hr", 6))
        s1_min = int(data.get("sched_1_min", 0))
        s2_hr = int(data.get("sched_2_hr", 18))
        s2_min = int(data.get("sched_2_min", 0))
    except (TypeError, ValueError):
        raise ValueError("all fields must be numeric")
    if not (1 <= duration <= 30):
        raise ValueError("duration_min must be 1-30")
    if not (1 <= interval <= 7):
        raise ValueError("day_interval must be 1-7")
    if not (0 <= s1_hr <= 23) or not (0 <= s2_hr <= 23):
        raise ValueError("hours must be 0-23")
    if not (0 <= s1_min <= 59) or not (0 <= s2_min <= 59):
        raise ValueError("minutes must be 0-59")
    return {
        "duration_min": duration,
        "day_interval": interval,
        "sched_1_hr": s1_hr,
        "sched_1_min": s1_min,
        "sched_1_en": 1 if data.get("sched_1_en") else 0,
        "sched_2_hr": s2_hr,
        "sched_2_min": s2_min,
        "sched_2_en": 1 if data.get("sched_2_en") else 0,
    }


async def handle(method, path, body_text, writer):
    """Returns True if this module handled the request (and wrote a response)."""

    if method == "GET" and (path == "/watering" or path == "/watering/"):
        response = file_response("watering.html", "text/html; charset=utf-8")
        writer.write(response)
        await writer.drain()
        return True

    if method == "GET" and path == "/watering/api/config":
        data = {zk: _zone_config_out(zk) for zk in ZONES}
        writer.write(json_response(data))
        await writer.drain()
        return True

    if method == "POST" and path == "/watering/api/config":
        try:
            payload = json.loads(body_text)
            zk = payload.get("zone")
            if zk not in ZONES:
                raise ValueError("unknown zone")
            cleaned = _validate_zone_update(payload)
            ZONES[zk].update(cleaned)
            save_config()
            log("watering", "Updated schedules for " + ZONES[zk]["name"])
            response = json_response({"ok": True})
        except ValueError as e:
            response = json_response({"ok": False, "error": str(e)}, status=400)
        except Exception:
            response = json_response({"ok": False, "error": "invalid request"}, status=400)
        writer.write(response)
        await writer.drain()
        return True

    if method == "GET" and path == "/watering/api/status":
        import netmgr
        t = athens_time.localtime()
        zones_status = {}
        for zk in ZONES:
            busy = [j.busy for j in valve_jobs[zk]]
            if jobs[zk].busy and active_valve[zk] is not None:
                busy[active_valve[zk]] = True
            zones_status[zk] = {"valve_busy": busy}
        master_status = {"enabled": master["enabled"], "off_days": master["off_days"]}
        if not master["enabled"] and master["off_days"] > 0:
            elapsed = get_epoch_days() - master["off_start_day"]
            master_status["days_remaining"] = max(0, master["off_days"] - elapsed)
        data = {
            "time": "{:02d}:{:02d}:{:02d}".format(t[3], t[4], t[5]),
            "date": "{:04d}-{:02d}-{:02d}".format(t[0], t[1], t[2]),
            "wifi_connected": netmgr.wlan.isconnected(),
            "ntp_synced": netmgr.last_sync_ok,
            "master": master_status,
            "zones": zones_status,
        }
        writer.write(json_response(data))
        await writer.drain()
        return True

    if method == "POST" and path == "/watering/api/master":
        try:
            payload = json.loads(body_text)
            enabled = bool(payload.get("enabled", True))
            if enabled:
                master["enabled"] = True
                log("watering", "Master switch turned ON.")
            else:
                days = int(payload.get("days", 0))
                if days < 0:
                    raise ValueError("days must be 0 or greater")
                master["enabled"] = False
                master["off_days"] = days
                master["off_start_day"] = get_epoch_days()
                if days > 0:
                    log("watering", "Master switch turned OFF for " + str(days) + " day(s).")
                else:
                    log("watering", "Master switch turned OFF indefinitely.")
            save_config()
            response = json_response({"ok": True})
        except (ValueError, TypeError):
            response = json_response({"ok": False, "error": "invalid request"}, status=400)
        except Exception:
            response = json_response({"ok": False, "error": "invalid request"}, status=400)
        writer.write(response)
        await writer.drain()
        return True

    if method == "POST" and path == "/watering/api/valve/start":
        try:
            payload = json.loads(body_text)
            zk = payload.get("zone")
            idx = int(payload.get("valve", 0)) - 1  # UI sends 1-based valve numbers
        except Exception:
            zk, idx = None, -1
        if zk in ZONES and 0 <= idx < len(ZONES[zk]["valves"]):
            if not master["enabled"]:
                response = json_response({"ok": False, "error": "Watering is currently OFF"}, status=409)
            elif zone_busy(zk):
                log("watering", "Manual valve start rejected, " + ZONES[zk]["name"] + " is already busy.")
                response = json_response({"ok": False, "error": ZONES[zk]["name"] + " is already busy"}, status=409)
            else:
                valve_jobs[zk][idx].start(execute_valve(zk, idx))
                response = json_response({"ok": True})
        else:
            response = json_response({"ok": False, "error": "invalid zone/valve"}, status=400)
        writer.write(response)
        await writer.drain()
        return True

    if method == "POST" and path == "/watering/api/valve/stop":
        try:
            payload = json.loads(body_text)
            zk = payload.get("zone")
            idx = int(payload.get("valve", 0)) - 1
        except Exception:
            zk, idx = None, -1
        if zk in ZONES and 0 <= idx < len(ZONES[zk]["valves"]):
            if jobs[zk].busy:
                # A scheduled/full-zone cycle is running - cancel the whole
                # thing regardless of which valve happens to be open right
                # now, rather than just the valve the Stop button belongs to.
                jobs[zk].stop()
            else:
                valve_jobs[zk][idx].stop()
            response = json_response({"ok": True})
        else:
            response = json_response({"ok": False, "error": "invalid zone/valve"}, status=400)
        writer.write(response)
        await writer.drain()
        return True

    return False


def force_all_off():
    for valve_pin in valves_a:
        valve_pin.value(0)
    for valve_pin in valves_b:
        valve_pin.value(0)
