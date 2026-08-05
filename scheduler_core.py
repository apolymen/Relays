# scheduler_core.py
#
# Shared scheduling primitives used by watering_service.py and
# pump_service.py (and any future service). Each service still owns its
# own "is this due" semantics and its own hardware action; this module only
# provides the genuinely reusable pieces:
#
#   1. Calendar/eligibility math (day-of-week windows, exact-time triggers,
#      day-interval cooldowns)
#   2. Job-run bookkeeping (busy tracking, cancellable tasks, cleanup)
#
# What stays per-service on purpose: the pump holds a continuous on/off
# level for as long as a window is active, while watering fires a one-shot
# multi-step job at an exact minute. Collapsing those two into one shared
# "scheduler loop" would force one service to adopt the other's semantics,
# so this module gives both the building blocks instead of a single loop.

import uasyncio as asyncio
from log import log


# ---------------------------------------------------------------------------
# Calendar / eligibility helpers
# ---------------------------------------------------------------------------

def weekday_window_active(now_minutes, weekday, start_hour, start_minute, duration_min, days):
    """Level-based check: is 'now' inside a start/duration window, filtered
    by day-of-week? Handles windows that cross midnight. This is the same
    logic the pump service used internally as schedule_is_active()."""
    if duration_min <= 0:
        return False
    start = start_hour * 60 + start_minute
    end = start + duration_min
    if end <= 1440:
        return weekday in days and start <= now_minutes < end
    end_wrapped = end - 1440
    prev_weekday = (weekday - 1) % 7
    started_today = weekday in days and now_minutes >= start
    continuing_from_yesterday = prev_weekday in days and now_minutes < end_wrapped
    return started_today or continuing_from_yesterday


def exact_minute_due(hour, minute, target_hour, target_minute):
    """Edge-based check: does 'now' exactly match a trigger time? This is
    the same check the watering service used inline in scheduler_task()."""
    return hour == target_hour and minute == target_minute


def interval_elapsed(current_epoch_day, last_run_day, interval_days):
    """Cooldown check: has enough time passed since the last run?
    last_run_day of 0 means 'never run yet', which is always eligible."""
    if last_run_day == 0:
        return True
    return (current_epoch_day - last_run_day) >= interval_days


# ---------------------------------------------------------------------------
# Job runner: busy-tracking + cancellable task scaffolding
# ---------------------------------------------------------------------------

class Job:
    """Tracks a single named, cancellable, exclusive-running job so a
    service doesn't need to hand-roll its own busy-flag dict and task
    dict (as watering_service.py's zone_busy/zone_tasks used to).

    A service supplies its own coroutine, the actual hardware action; this
    class only tracks whether it's currently running and lets it be
    cancelled from a /stop-style route. The service's own coroutine is
    still responsible for its own try/finally hardware cleanup (e.g.
    forcing a valve closed) - Job only guarantees the busy flag clears.
    """

    def __init__(self, name, tag="job"):
        self.name = name
        self.tag = tag
        self._task = None

    @property
    def busy(self):
        return self._task is not None and not self._task.done()

    def start(self, coro):
        """Starts coro as a tracked task, unless already busy. Returns the
        created task (which the caller may await, to serialize, or ignore,
        to fire-and-forget), or None if a job was already running."""
        if self.busy:
            log(self.tag, "Start rejected for " + self.name + ", already running.")
            return None
        self._task = asyncio.create_task(self._run(coro))
        return self._task

    async def _run(self, coro):
        try:
            await coro
        except asyncio.CancelledError:
            log(self.tag, "Job for " + self.name + " cancelled.")
            raise
        finally:
            self._task = None

    def stop(self):
        """Cancels the running task, if any. Returns True if a cancel was
        actually issued, False if nothing was running."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            log(self.tag, "Stop requested for " + self.name + ".")
            return True
        log(self.tag, "Stop requested for " + self.name + " but nothing is running.")
        return False
