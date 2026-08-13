# config.py
# Shared configuration for Wi-Fi, time sync, and the web server,
# plus hardware settings for each individual service.

import machine

# --- WI-FI / NETWORK ---
WIFI_SSID = "Your SSID"
WIFI_PASSWORD = "Your WiFi Password"

# Format: (Static_IP, Subnet_Mask, Gateway_IP, DNS_Server)
# STATIC_IP_SETTINGS = ("192.168.0.50", "255.255.255.0", "192.168.0.1", "1.1.1.1")

# --- TIME / NTP ---
NTP_SERVERS = ["pool.ntp.org", "time.windows.com"]

# --- WEB SERVER ---
WEB_PORT = 80

# --- WATCHDOG ---
# RP2's machine.WDT has a hardware ceiling of 8388ms; 8000ms stays safely under it.
#
# Set WATCHDOG_ENABLED = False while developing in Thonny. Thonny's "Run"
# workflow interrupts the running script to get a fresh REPL, which stops
# anything from calling wdt.feed() - but the watchdog itself can't be paused
# or cancelled once created, so it fires ~8s later and resets the board,
# right back into the same script, in a loop. Flip this back to True before
# any real/unattended deployment (plain power-cycle boot, no IDE attached).
WATCHDOG_ENABLED = False

if WATCHDOG_ENABLED:
    wdt = machine.WDT(timeout=8000)
else:
    class _DummyWDT:
        """Stand-in with the same .feed() interface as machine.WDT, so every
        other module can keep calling config.wdt.feed() unconditionally."""
        def feed(self):
            pass

    wdt = _DummyWDT()

# --- WATERING SERVICE HARDWARE ---
# sb-components relay board, 2 zones, 2 valves per zone
ZONE_A_PINS = [18, 19]
ZONE_B_PINS = [20, 21]

# --- HOT WATER PUMP SERVICE HARDWARE ---
PUMP_RELAY_PIN = 22
PUMP_RELAY_ACTIVE_LOW = False
PUMP_NUM_SCHEDULES = 16
PUMP_SCHEDULER_INTERVAL_SECONDS = 10
