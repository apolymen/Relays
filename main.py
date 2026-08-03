# main.py
#
# main.py
# |
# +-- Watering Service    (watering_service.py) - 2 zones, 2 valves each
# +-- Hot Water Pump Service (pump_service.py)   - 1 relay, 16 schedules
# +-- Wi-Fi Manager       (netmgr.py)
# +-- Time/NTP Manager    (netmgr.py, daily_resync_loop)
# +-- Web Server          (this file: single listener, dispatches by path)

import uasyncio as asyncio

import config
import netmgr
import watering_service
import pump_service
from log import log, get_logs


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


def _landing_page():
    return """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Home Control</title>
<style>
body{font-family:Arial,sans-serif;background:#f0f2f5;color:#333;margin:40px;}
.container{max-width:500px;margin:auto;text-align:center;}
a.btn{display:block;background:#007bff;color:white;padding:16px;margin:12px 0;
border-radius:8px;text-decoration:none;font-size:16px;}
a.logs{background:#444;}
</style></head><body>
<div class="container">
<h1>Home Control</h1>
<a class="btn" href="/watering">Watering Service</a>
<a class="btn" href="/pump">Hot Water Pump Service</a>
<a class="btn logs" href="/logs">System Logs</a>
</div></body></html>"""


def _logs_page():
    return """<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>System Logs</title>
<style>
body{font-family:Arial,sans-serif;background:#f0f2f5;color:#333;margin:20px;}
.container{max-width:700px;margin:auto;}
a{color:#007bff;}
.logs-card{background:#222;color:#00ff00;padding:15px;border-radius:8px;
font-family:monospace;white-space:pre-wrap;}
</style></head><body>
<div class="container">
<p><a href="/">&larr; Home</a></p>
<h1>Combined System Logs</h1>
<div class="logs-card">""" + get_logs() + """</div>
</div></body></html>"""


async def handle_client(reader, writer):
    """Single shared HTTP server. Reads the request once, then dispatches
    to whichever service owns the path (or handles / and /logs directly)."""
    config.wdt.feed()
    try:
        request_line = await reader.readline()
        request = request_line.decode("utf-8")

        content_length = 0
        while True:
            line = await reader.readline()
            if line == b"\r\n" or line == b"":
                break
            if line.lower().startswith(b"content-length:"):
                content_length = int(line.split(b":", 1)[1].strip())

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

        writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\nNot found")
        await writer.drain()

    except Exception as e:
        print("Web internal routing error:", e)
    finally:
        await writer.close()
        await writer.wait_closed()


async def main():
    log("boot", "Booting merged system, initial setup...")

    watering_service.load_config()

    await netmgr.connect_and_sync()

    asyncio.create_task(netmgr.daily_resync_loop())
    asyncio.create_task(watering_service.scheduler_task())
    asyncio.create_task(pump_service.scheduler_loop())
    asyncio.create_task(pump_service.heartbeat_loop())

    log("boot", "Starting shared web server on port " + str(config.WEB_PORT) + "...")
    await asyncio.start_server(handle_client, "0.0.0.0", config.WEB_PORT)

    while True:
        config.wdt.feed()
        await asyncio.sleep(1)


try:
    asyncio.run(main())
except KeyboardInterrupt:
    print("Forced termination. Clearing execution blocks.")
finally:
    watering_service.force_all_off()
    pump_service.force_all_off()
