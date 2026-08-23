# deploy_service.py
# Remote code-update service, reachable only via Tailscale (network-level
# authentication) plus a shared-secret token (application-level, defense in
# depth). Files are staged in staging/, never touching the live copy, until
# an explicit POST /deploy/commit swaps them in (keeping one backup
# generation per file in backup/) and reboots into the new code.
#
# Deliberately minimal for now: no automatic rollback on a bad deploy, no
# multi-generation backup history. Recovering from a broken deploy means
# either pushing backup/ files back over the network (if the device still
# boots far enough to serve HTTP), or physical access if it doesn't.

import os
import json
import machine
import uasyncio as asyncio

import config
import watering_service
import pump_service
from logger import log

STAGING_DIR = "staging"
BACKUP_DIR = "backup"


def _ensure_dirs():
    for d in (STAGING_DIR, BACKUP_DIR):
        try:
            os.mkdir(d)
        except OSError:
            pass  # already exists


_ensure_dirs()


def _parse_query(path):
    """Splits '/deploy/stage?filename=main.py' into
    ('/deploy/stage', {'filename': 'main.py'})."""
    if "?" not in path:
        return path, {}
    base, qs = path.split("?", 1)
    params = {}
    for pair in qs.split("&"):
        if "=" in pair:
            k, v = pair.split("=", 1)
            params[k] = v
    return base, params


def _safe_filename(name):
    """Rejects anything that could escape the intended flat, root-level
    file layout - no subdirectories, no path traversal."""
    if not name:
        return False
    if "/" in name or "\\" in name or ".." in name:
        return False
    return True


def _anything_running():
    """True if either service currently has something active. A commit
    (and the reboot that follows it) is refused while this is true."""
    for zone_id in watering_service.ZONES:
        if watering_service.zone_busy(zone_id):
            return True
    return pump_service.relay_state


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


async def handle(method, path, token, body_text, writer):
    """Returns True if this module handled the request (and wrote a
    response). token is the X-Deploy-Token header value, extracted by
    main.py alongside Content-Length (the only other header any service
    here needs to read)."""
    base_path, params = _parse_query(path)

    if not base_path.startswith("/deploy"):
        return False

    if token != config.DEPLOY_TOKEN:
        log("deploy", "Rejected request to " + base_path + ": bad or missing token.")
        response = json_response({"ok": False, "error": "unauthorized"}, status=401)
        writer.write(response)
        await writer.drain()
        return True

    if method == "GET" and base_path == "/deploy/status":
        response = json_response({"ok": True, "staged": os.listdir(STAGING_DIR)})
        writer.write(response)
        await writer.drain()
        return True

    if method == "POST" and base_path == "/deploy/stage":
        filename = params.get("filename", "")
        if not _safe_filename(filename):
            response = json_response({"ok": False, "error": "invalid filename"}, status=400)
        else:
            try:
                with open(STAGING_DIR + "/" + filename, "w") as f:
                    f.write(body_text)
                log("deploy", "Staged " + filename + " (" + str(len(body_text)) + " bytes).")
                response = json_response({"ok": True})
            except Exception as e:
                response = json_response({"ok": False, "error": str(e)}, status=500)
        writer.write(response)
        await writer.drain()
        return True

    if method == "POST" and base_path == "/deploy/commit":
        if _anything_running():
            log("deploy", "Commit refused: a zone or the pump is currently active.")
            response = json_response({"ok": False, "error": "device busy, try again once idle"}, status=409)
            writer.write(response)
            await writer.drain()
            return True

        staged = os.listdir(STAGING_DIR)
        if not staged:
            response = json_response({"ok": False, "error": "nothing staged"}, status=400)
            writer.write(response)
            await writer.drain()
            return True

        try:
            for filename in staged:
                try:
                    os.rename(filename, BACKUP_DIR + "/" + filename)
                except OSError:
                    pass  # no existing live copy to back up (new file)
                os.rename(STAGING_DIR + "/" + filename, filename)

            log("deploy", "Committed " + str(len(staged)) + " file(s): " + ", ".join(staged) + ". Rebooting...")
            response = json_response({"ok": True, "committed": staged, "rebooting": True})
            writer.write(response)
            await writer.drain()
            await asyncio.sleep(3)  # give the response time to actually leave before reset
            machine.reset()
        except Exception as e:
            # A failure partway through leaves a mix of new/old files on
            # flash - the *running* code is unaffected (already in RAM),
            # but this mix would be what boots next time. Worth checking
            # /deploy/status and the affected files by hand before the
            # next reboot, whenever that happens.
            log("deploy", "Commit failed partway through: " + str(e))
            response = json_response({"ok": False, "error": str(e)}, status=500)
            writer.write(response)
            await writer.drain()
        return True

    return False
