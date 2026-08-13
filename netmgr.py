# netmgr.py
# Shared Wi-Fi Manager + Time/NTP Manager. Both services rely on this for
# network connectivity and for keeping the RTC in sync (with a daily
# drift-correction resync), instead of each maintaining their own.

import network
import ntptime
# import socket
import time
import uasyncio as asyncio

import config
import athens_time
from logger import log

wlan = network.WLAN(network.STA_IF)

_last_sync_day = 0
_next_retry_ticks = 0
last_sync_ok = False


def ntp_sync():
    """Attempts to sync time with a primary NTP server, falling back if needed."""
    global last_sync_ok
    ntp_servers = config.NTP_SERVERS

    # Force a short network timeout to stay safe from the watchdog
    # socket.setdefaulttimeout(3.0)
    try:
        for server in ntp_servers:
            config.wdt.feed()
            try:
                log("net", "Trying NTP sync via {}...".format(server))
                ntptime.host = server
                ntptime.settime()
                log("net", "Successfully synced via {}!".format(server))
                last_sync_ok = True
                return True
            except Exception as inner_error:
                log("net", "Server {} failed: {}. Trying next fallback...".format(server, inner_error))
                continue
        last_sync_ok = False
        return False
    except Exception:
        print("NTP sync failed")
    # socket.setdefaulttimeout(None)


async def connect_and_sync():
    """Blocks (asynchronously) until Wi-Fi connects and initial NTP sync succeeds."""
    global _last_sync_day

    wlan.active(True)
    # log("net", "Configuring Static IP profile...")
    # wlan.ifconfig(config.STATIC_IP_SETTINGS)

    while not wlan.isconnected():
        log("net", "Attempting Wi-Fi Connection...")
        wlan.connect(config.WIFI_SSID, config.WIFI_PASSWORD)
        for _ in range(15):
            if wlan.isconnected():
                break
            await asyncio.sleep(1)
            config.wdt.feed()

        if not wlan.isconnected():
            log("net", "Link down. Retrying router in 30 seconds...")
            for _ in range(30):
                await asyncio.sleep(1)
                config.wdt.feed()

    log("net", "Connected successfully! System Address: http://" + str(wlan.ifconfig()[0]))

    while True:
        if ntp_sync():
            t = athens_time.localtime()
            log("net", "NTP Time Synchronised: {:02d}:{:02d}".format(t[3], t[4]))
            _last_sync_day = t[2]
            return True
        else:
            log("net", "Initial NTP sync failed. Retrying in 10s...")
            for _ in range(10):
                await asyncio.sleep(1)
                config.wdt.feed()


async def daily_resync_loop():
    """Background task: re-verifies NTP drift once a day. Shared by both
    services so neither has to run its own copy of this logic."""
    global _last_sync_day, _next_retry_ticks

    while True:
        await asyncio.sleep(30)
        config.wdt.feed()

        t = athens_time.localtime()
        current_day = t[2]
        now_ticks = time.ticks_ms()

        if current_day != _last_sync_day:
            if time.ticks_diff(now_ticks, _next_retry_ticks) >= 0:
                if ntp_sync():
                    _last_sync_day = athens_time.localtime()[2]
                    log("net", "Daily Time Drift Sync Completed.")
                else:
                    log("net", "Daily NTP sync failed. Postponing for 5 minutes.")
                    _next_retry_ticks = time.ticks_add(now_ticks, 300000)
