# deploy.ps1
# Pushes local project files to the Pico over Tailscale (via subnet routing
# to its LAN IP - the Pico itself doesn't run a Tailscale client), then
# commits. Requires the Pico to have a stable address (static IP or a DHCP
# reservation), since the deploy target below is a plain LAN IP, not a
# Tailscale MagicDNS name.
#
# Setup (once per PowerShell session, or persist via SetEnvironmentVariable):
#   $env:PICO_DEPLOY_TOKEN = "your-secret-here"
#
# Usage:
#   .\deploy.ps1
#   .\deploy.ps1 -PicoHost "192.168.0.50" -ProjectDir "C:\path\to\project"

param(
    [string]$PicoHost = "192.168.0.50",
    [string]$ProjectDir = "."
)

$Files = @(
    "main.py", "config.py", "logger.py", "netmgr.py", "athens_time.py",
    "scheduler_core.py", "watering_service.py", "pump_service.py",
    "deploy_service.py",
    "watering.html", "pump.html", "landing.html", "logs.html"
)

$Token = $env:PICO_DEPLOY_TOKEN
if (-not $Token) {
    Write-Error "Set `$env:PICO_DEPLOY_TOKEN before running this script."
    exit 1
}

$BaseUrl = "http://$PicoHost"
$Headers = @{ "X-Deploy-Token" = $Token }

Write-Host "Staging $($Files.Count) file(s) to $PicoHost..."

foreach ($file in $Files) {
    $path = Join-Path $ProjectDir $file
    if (-not (Test-Path $path)) {
        Write-Error "Missing local file: $path"
        exit 1
    }

    $bytes = [System.IO.File]::ReadAllBytes($path)

    try {
        Invoke-RestMethod -Uri "$BaseUrl/deploy/stage?filename=$file" -Method Post `
            -Body $bytes -Headers $Headers -ContentType "application/octet-stream" | Out-Null
        Write-Host "  Staged: $file ($($bytes.Length) bytes)"
    } catch {
        Write-Error "Failed to stage $file : $_"
        exit 1
    }
}

Write-Host "All files staged. Committing..."

try {
    $result = Invoke-RestMethod -Uri "$BaseUrl/deploy/commit" -Method Post -Headers $Headers
    Write-Host "Commit accepted. Device is rebooting into the new code."
    Write-Host "Committed files: $($result.committed -join ', ')"
} catch {
    Write-Error "Commit failed (device may be busy, or refused): $_"
    exit 1
}
