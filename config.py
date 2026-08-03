# config.py
# Shared configuration for Wi-Fi, time sync, and the web server,
# plus hardware settings for each individual service.

import machine

# --- WI-FI / NETWORK ---
WIFI_SSID = "ATHLON"
WIFI_PASSWORD = "Your_WiFi_Password"

# Format: (Static_IP, Subnet_Mask, Gateway_IP, DNS_Server)
STATIC_IP_SETTINGS = ("192.168.0.50", "255.255.255.0", "192.168.0.1", "1.1.1.1")

# --- TIME / NTP ---
NTP_SERVERS = ["pool.ntp.org", "time.windows.com"]

# --- WEB SERVER ---
WEB_PORT = 80

# --- WATCHDOG ---
# NOTE: kept identical to the original watering project's call. Double-check
# this against your MicroPython port's machine.WDT signature (some ports use
# timeout=ms rather than out=ms) before relying on it.
wdt = machine.WDT(timeout=10000)

# --- WATERING SERVICE HARDWARE ---
# sb-components relay board, 2 zones, 2 valves per zone
ZONE_A_PINS = [18, 19]
ZONE_B_PINS = [20, 21]

# --- HOT WATER PUMP SERVICE HARDWARE ---
PUMP_RELAY_PIN = 22
PUMP_RELAY_ACTIVE_LOW = False
PUMP_NUM_SCHEDULES = 16
PUMP_SCHEDULER_INTERVAL_SECONDS = 10
