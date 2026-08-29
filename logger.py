# logger.py
# Shared rolling log buffer for both services. Every entry is tagged with
# the service that produced it so the combined /logs page stays readable.
# The buffer is also mirrored to a flash file so history survives a power
# loss or reset, not just the current run.

import athens_time

_MAX_LINES = 200
LOG_FILE = "system.log"

system_logs = "--- Log start ---\n"


def _load_from_flash():
    """Restores the log buffer from flash on import, so entries from before
    the last reboot/power loss are still visible on /logs."""
    global system_logs
    try:
        with open(LOG_FILE, "r") as f:
            saved = f.read()
        if saved:
            system_logs = saved
    except OSError:
        pass  # no log file yet (e.g. first boot) - keep the default


def _save_to_flash():
    """Mirrors the in-memory buffer to flash, already capped to _MAX_LINES.
    Failures are swallowed so a full or faulty filesystem never breaks
    logging or crashes whichever service called log()."""
    try:
        with open(LOG_FILE, "w") as f:
            f.write(system_logs)
    except OSError as e:
        print("Log persist failed:", e)


_load_from_flash()


def log(tag, text):
    """Outputs text to the USB console and the shared web log buffer, then
    persists the (line-capped) buffer to flash.
    tag should be a short label, e.g. 'net', 'watering', 'pump'."""
    global system_logs
    try:
        t = athens_time.localtime()
        stamp = "[{:04d}-{:02d}-{:02d} {:02d}:{:02d}:{:02d}] [{}] ".format(t[0], t[1], t[2], t[3], t[4], t[5], tag)
    except Exception:
        stamp = "[00:00:00] [{}] ".format(tag)

    line = stamp + text
    print(line)
    system_logs += line + "\n"

    lines = system_logs.split("\n")
    if len(lines) > _MAX_LINES:
        system_logs = "\n".join(lines[-_MAX_LINES:])

    _save_to_flash()


def get_logs():
    return system_logs
