# notify.py
# Best-effort push notification via ntfy.sh (https://ntfy.sh), sent once per
# genuine boot. Skipped after a deploy_service.py-triggered reboot, since
# machine.reset_cause() is known to misreport ordinary resets as WDT_RESET
# on this port and can't be trusted to tell reboot causes apart - instead,
# deploy_service.py sets a flag file right before its own machine.reset()
# call, and this module checks for (and clears) that flag on the next boot.
#
# NOTE: ssl.wrap_socket() doesn't validate the server's TLS certificate by
# default on this port (no bundled CA store). Acceptable here since a
# boot-time "device is up" message isn't sensitive, but worth knowing.

import socket
import ssl
import os

import config
from logger import log

DEPLOY_REBOOT_FLAG = "deploy_reboot.flag"


def mark_deploy_reboot():
    """Called by deploy_service.py right before it reboots into new code,
    so the next boot's notification (if any) gets skipped."""
    try:
        with open(DEPLOY_REBOOT_FLAG, "w") as f:
            f.write("1")
    except OSError as e:
        log("notify", "Could not write deploy-reboot flag: " + str(e))


def _consume_deploy_flag():
    """True if this boot follows a deploy commit. Clears the flag either
    way, so it never lingers into some unrelated future reboot."""
    try:
        os.remove(DEPLOY_REBOOT_FLAG)
        return True
    except OSError:
        return False


async def send_boot_notification(time_str):
    """Posts a plaintext 'Device booted at HH:MM:SS' message to the
    configured ntfy.sh topic. Best-effort: any failure is logged, not
    raised, so a slow or unreachable ntfy.sh can't hold up boot or trip
    the watchdog. Intended to be scheduled as its own task (not awaited
    inline) so the rest of boot doesn't wait on it either."""
    if _consume_deploy_flag():
        log("notify", "Skipping boot notification (deploy-triggered reboot).")
        return

    if not config.NTFY_TOPIC:
        return

    sock = None
    try:
        message = "Device booted at " + time_str
        body = message.encode("utf-8")

        addr = socket.getaddrinfo("ntfy.sh", 443)[0][-1]
        sock = socket.socket()
        sock.settimeout(5)
        sock.connect(addr)
        config.wdt.feed()
        sock = ssl.wrap_socket(sock, server_hostname="ntfy.sh")

        request = (
            "POST /{} HTTP/1.1\r\n"
            "Host: ntfy.sh\r\n"
            "Content-Type: text/plain\r\n"
            "Content-Length: {}\r\n"
            "Connection: close\r\n\r\n"
        ).format(config.NTFY_TOPIC, len(body)).encode("utf-8") + body

        sock.write(request)
        sock.read(1)  # touch the response so the request is fully flushed
        log("notify", "Boot notification sent.")
    except Exception as e:
        log("notify", "Boot notification failed: " + str(e))
    finally:
        if sock is not None:
            try:
                sock.close()
            except Exception:
                pass
