# main.py
# |
# +-- Watering Service    (watering_service.py) - 2 zones, 2 valves each
# +-- Hot Water Pump Service (pump_service.py)   - 1 relay, 16 schedules
# +-- Wi-Fi Manager       (netmgr.py)
# +-- Time/NTP Manager    (netmgr.py, daily_resync_loop)
# +-- Web Server          (this file: single listener, dispatches by path)

# Safety window: machine.WDT() can't be stopped or reconfigured once created,
# so this delay runs before config (and everything else) gets imported,
# guaranteeing a few interrupt-able seconds on every boot, even if the
# watchdog is currently enabled. Press Ctrl-C in Thonny during the countdown
# to break in before it's created, then edit config.WATCHDOG_ENABLED.
import time
for remaining in range(8, 0, -1):
    print("Starting in", remaining, "- Ctrl-C now for safe mode")
    time.sleep(1)

import machine
import uasyncio as asyncio

import config
import netmgr
import watering_service
import pump_service
import deploy_service
from logger import log, get_logs


try:
    status_led = machine.Pin("LED", machine.Pin.OUT)
except (ValueError, TypeError):
    status_led = None


async def heartbeat_loop():
    if status_led is None:
        return
    while True:
        status_led.on()
        await asyncio.sleep(0.2)
        status_led.off()
        await asyncio.sleep(3)


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


def _read_file(path):
    try:
        with open(path, "r") as f:
            return f.read()
    except Exception as e:
        return "<html><body><h1>Internal Storage Read Error: " + str(e) + "</h1></body></html>"


def _landing_page():
    return _read_file("landing.html")


def _logs_page():
    html = _read_file("logs.html")
    return html.replace("{{LOGS}}", get_logs())


async def handle_client(reader, writer):
    """Single shared HTTP server. Reads the request once, then dispatches
    to whichever service owns the path (or handles / and /logs directly)."""
    config.wdt.feed()
    try:
        request_line = await reader.readline()
        request = request_line.decode("utf-8")

        content_length = 0
        deploy_token = None
        while True:
            line = await reader.readline()
            if line == b"\r\n" or line == b"":
                break
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":", 1)[1].strip())
            elif line.lower().startswith(b"x-deploy-token:"):
                deploy_token = line.split(b":", 1)[1].strip().decode("utf-8")

        body = b""
        if content_length:
            body = await reader.readexactly(content_length)
        body_text = body.decode("utf-8")

        parts = request.split(" ")
        if len(parts) < 2:
            return
        method = parts[0]
        path = parts[1]

        if path == "/" or path == "":
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n")
            writer.write(_landing_page().encode("utf-8"))
            await writer.drain()
            return

        if path == "/logs":
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/html; charset=utf-8\r\nConnection: close\r\n\r\n")
            writer.write(_logs_page().encode("utf-8"))
            await writer.drain()
            return

        if path.startswith("/watering"):
            handled = await watering_service.handle(method, path, body_text, writer)
            if handled:
                return

        if path.startswith("/pump"):
            handled = await pump_service.handle(method, path, body_text, writer)
            if handled:
                return

        if path.startswith("/deploy"):
            handled = await deploy_service.handle(method, path, deploy_token, body_text, writer)
            if handled:
                return

        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nNot found")
        await writer.drain()

    except Exception as e:
        print("Web internal routing error:", e)
    finally:
        writer.close()
        await writer.wait_closed()


async def main():
    log("boot", "--- System Boot Init ---")

    watering_service.load_config()
    pump_service.init_schedules()

    await netmgr.connect_and_sync()
    watering_service.apply_master_boot_offset()

    asyncio.create_task(netmgr.daily_resync_loop())
    asyncio.create_task(watering_service.scheduler_task())
    asyncio.create_task(pump_service.scheduler_loop())
    asyncio.create_task(heartbeat_loop())

    log("boot", "Starting web server on port " + str(config.WEB_PORT) + "...")
    await asyncio.start_server(handle_client, "0.0.0.0", config.WEB_PORT)

    while True:
        config.wdt.feed()
        await asyncio.sleep(1)


try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("Stopped by user")
finally:
    watering_service.force_all_off()
    pump_service.force_all_off()
    if status_led:
        status_led.off()
    print("All relays turned off")
