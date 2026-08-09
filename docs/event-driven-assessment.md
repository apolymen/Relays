## Would an event driven implementation be better for this project?

Short answer: not really, and here's the reasoning split by which part of the system you mean, since "event-driven" could apply to different pieces here.

### The scheduling loops (the part most people mean by this question)

Right now both services poll: check "is it due?" every 5–30 seconds. A true event-driven version would instead compute "how many seconds until the next scheduled trigger," `asyncio.sleep()` for exactly that long, then fire directly, no repeated checking in between.

That sounds cleaner, but it trades a simple problem for a genuinely harder one on this specific device:

- Your clock isn't static. `athens_time.py` handles DST twice a year, and NTP resyncs (including the daily one in `netmgr.py`) can nudge the RTC forward or backward at any moment. A "sleep for N computed seconds" approach has to recompute and reschedule that sleep every time the clock moves underneath it, or risk firing early, late, or twice. The current poll loop is naturally immune to this: it just re-evaluates "is it due right now?" fresh on every tick, however the clock got there.
- Any schedule edit from the web UI (which happens live, via POST) would need to cancel and recompute the pending sleep. With polling, an edit is just visible on the very next tick, no extra plumbing.
- You'd still need *some* periodic task running anyway, since `config.wdt.feed()` has to be called regularly regardless of how far away the next real event is. So you don't actually eliminate the polling loop, you just add a second, more complex mechanism alongside it.

Given the device is mains-powered (not battery), the CPU/power savings from avoiding a wakeup every 5–10 seconds are negligible. So for this specific piece, polling isn't a compromise, it's the better fit.

### Where the system is already event-driven, correctly

- The web server (`uasyncio.start_server` in `main.py`) is already fully event-driven: it reacts to incoming connections rather than polling a socket.
- `scheduler_core.Job` is event-driven in the sense that matters: `/watering/stop` cancels a running task directly via `asyncio.CancelledError`, rather than some flag being polled and noticed later.

### Where it actually would help

If you ever add physical sensors, a soil moisture probe, a flow sensor, a door/motion sensor, that's the case where GPIO interrupts (`machine.Pin.irq(...)`) genuinely beat polling, since you'd otherwise be sampling a pin that changes unpredictably rather than checking a clock that changes predictably. Time-based scheduling and sensor-triggered actions are different problems, and it's fine, even preferable, for them to use different techniques within the same codebase.
