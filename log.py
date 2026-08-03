# log.py
# Shared rolling log buffer for both services. Every entry is tagged with
# the service that produced it so the combined /logs page stays readable.

import athens_time

_MAX_LINES = 40
system_logs = "--- System Boot Init ---\n"


def log(tag, text):
    """Outputs text to the USB console and the shared web log buffer.
    tag should be a short label, e.g. 'net', 'watering', 'pump'."""
    global system_logs
    try:
        t = athens_time.localtime()
        stamp = "[{:02d}:{:02d}:{:02d}] [{}] ".format(t[3], t[4], t[5], tag)
    except Exception:
        stamp = "[00:00:00] [{}] ".format(tag)

    line = stamp + text
    print(line)
    system_logs += line + "\n"

    lines = system_logs.split("\n")
    if len(lines) > _MAX_LINES:
        system_logs = "\n".join(lines[-_MAX_LINES:])


def get_logs():
    return system_logs
