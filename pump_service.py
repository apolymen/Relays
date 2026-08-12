# pump_service.py
# Hot Water Pump Service: single relay, 16 programmable schedules, own
# config file, own html page and routes (all under /pump).

import machine
import uasyncio as asyncio
import json

import config
import athens_time
import scheduler_core
from logger import log

SCHEDULES_FILE = "pump_schedules.json"

relay_pin = machine.Pin(config.PUMP_RELAY_PIN, machine.Pin.OUT)

# Ensure the relay is OFF at boot
relay_pin.value(1 if config.PUMP_RELAY_ACTIVE_LOW else 0)

relay_state = False

try:
    status_led = machine.Pin("LED", machine.Pin.OUT)
except (ValueError, TypeError):
    status_led = None


def relay_set(on):
    global relay_state
    if config.PUMP_RELAY_ACTIVE_LOW:
        relay_pin.value(0 if on else 1)
    else:
        relay_pin.value(1 if on else 0)
    relay_state = on


# --- SCHEDULE STORAGE ---

def default_schedules():
    return [
        {
            "id": i,
            "enabled": False,
            "start_hour": 6,
            "start_minute": 0,
            "duration_min": 15,
            "days": [0, 1, 2, 3, 4, 5, 6],
            "label": "",
        }
        for i in range(config.PUMP_NUM_SCHEDULES)
    ]


def load_schedules():
    try:
        with open(SCHEDULES_FILE) as f:
            data = json.load(f)
        if isinstance(data, list) and len(data) == config.PUMP_NUM_SCHEDULES:
            log("pump", "Schedules loaded from flash.")
            return data
    except (OSError, ValueError):
        pass
    log("pump", "No saved schedules found, using defaults.")
    fresh = default_schedules()
    save_schedules(fresh)
    return fresh


def save_schedules(schedules_data):
    with open(SCHEDULES_FILE, "w") as f:
        json.dump(schedules_data, f)


schedules = load_schedules()


def validate_schedules(data):
    if not isinstance(data, list) or len(data) != config.PUMP_NUM_SCHEDULES:
        raise ValueError("expected a list of %d schedules" % config.PUMP_NUM_SCHEDULES)
    cleaned = []
    for i, s in enumerate(data):
        try:
            hour = int(s.get("start_hour", 0))
            minute = int(s.get("start_minute", 0))
            duration = int(s.get("duration_min", 0))
            days = [int(d) for d in s.get("days", [])]
        except (TypeError, ValueError, AttributeError):
            raise ValueError("slot %d has non-numeric fields" % i)
        if not (0 <= hour <= 23):
            raise ValueError("slot %d: start_hour must be 0-23" % i)
        if not (0 <= minute <= 59):
            raise ValueError("slot %d: start_minute must be 0-59" % i)
        if not (0 <= duration <= 1439):
            raise ValueError("slot %d: duration_min must be 0-1439" % i)
        if any(d < 0 or d > 6 for d in days):
            raise ValueError("slot %d: days must be 0-6" % i)
        cleaned.append({
            "id": i,
            "enabled": bool(s.get("enabled", False)),
            "start_hour": hour,
            "start_minute": minute,
            "duration_min": duration,
            "days": days,
            "label": str(s.get("label", ""))[:40],
        })
    return cleaned


# --- SCHEDULER ---
# NOTE: local time now comes from the shared, DST-aware athens_time module
# (same one the watering service uses) instead of the old fixed
# UTC_OFFSET_HOURS, so you no longer have to hand-edit config twice a year.

def local_time():
    t = athens_time.localtime()
    return t[3], t[4], t[5], t[6]


def local_time_string():
    h, m, s, _ = local_time()
    return "{:02d}:{:02d}:{:02d}".format(h, m, s)


def schedule_is_active(sched, now_minutes, weekday):
    if not sched["enabled"]:
        return False
    return scheduler_core.weekday_window_active(
        now_minutes, weekday,
        sched["start_hour"], sched["start_minute"], sched["duration_min"],
        sched["days"]
    )


def any_schedule_active():
    hour, minute, _, weekday = local_time()
    now_minutes = hour * 60 + minute
    active_ids = [
        s["id"] for s in schedules
        if schedule_is_active(s, now_minutes, weekday)
    ]
    return len(active_ids) > 0, active_ids


async def scheduler_loop():
    log("pump", "Scheduler monitoring loop initialised.")
    while True:
        config.wdt.feed()
        should_be_on, _ = any_schedule_active()
        if should_be_on != relay_state:
            relay_set(should_be_on)
            log("pump", "Relay -> " + ("ON" if should_be_on else "OFF") + " at " + local_time_string())
        await asyncio.sleep(config.PUMP_SCHEDULER_INTERVAL_SECONDS)


async def heartbeat_loop():
    if status_led is None:
        return
    while True:
        status_led.toggle()
        await asyncio.sleep(2)


# --- WEB INTERFACE ---

def json_response(obj, status=200):
    body = json.dumps(obj)
    headers = (
        "HTTP/1.1 {} OK\r\n"
        "Content-Type: application/json\r\n"
        "Content-Length: {}\r\n"
        "Connection: close\r\n\r\n"
    ).format(status, len(body))
    return headers + body


def file_response(path, content_type):
    try:
        with open(path) as f:
            body = f.read()
    except OSError:
        return text_response("Not found", 404)
    headers = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: {}\r\n"
        "Content-Length: {}\r\n"
        "Connection: close\r\n\r\n"
    ).format(content_type, len(body))
    return headers + body


def text_response(msg, status=200):
    headers = (
        "HTTP/1.1 {} \r\n"
        "Content-Type: text/plain\r\n"
        "Content-Length: {}\r\n"
        "Connection: close\r\n\r\n"
    ).format(status, len(msg))
    return headers + msg


async def handle(method, path, body_text, writer):
    """Returns True if this module handled the request (and wrote a response)."""
    global schedules

    if method == "GET" and (path == "/pump" or path == "/pump/"):
        response = file_response("pump.html", "text/html")
        writer.write(response.encode() if isinstance(response, str) else response)
        await writer.drain()
        return True

    if method == "GET" and path == "/pump/api/schedules":
        response = json_response(schedules)
        writer.write(response.encode())
        await writer.drain()
        return True

    if method == "POST" and path == "/pump/api/schedules":
        try:
            data = json.loads(body_text)
            cleaned = validate_schedules(data)
            schedules = cleaned
            save_schedules(schedules)
            log("pump", "Schedules updated via web UI.")
            response = json_response({"ok": True})
        except ValueError as e:
            response = json_response({"ok": False, "error": str(e)}, status=400)
        except Exception:
            response = json_response({"ok": False, "error": "invalid request"}, status=400)
        writer.write(response.encode())
        await writer.drain()
        return True

    if method == "GET" and path == "/pump/api/status":
        import netmgr
        active, active_ids = any_schedule_active()
        response = json_response({
            "time": local_time_string(),
            "relay_on": relay_state,
            "active_schedules": active_ids,
            "wifi_connected": netmgr.wlan.isconnected(),
            "ntp_synced": netmgr.last_sync_ok,
        })
        writer.write(response.encode())
        await writer.drain()
        return True

    return False


def force_all_off():
    relay_set(False)
