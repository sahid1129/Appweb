# recover_admin.ps1
# Interactive admin password recovery for Launchpad on Render.
#
# This script:
#   1. Tries the master-key password reset.
#   2. If RENDER_ADMIN_KEY is not set on Render (404), it falls back
#      to the wipe-users endpoint and tells the operator that the
#      next /api/auth/status call will recreate _admin/admin (or
#      whatever BOOTSTRAP_ADMIN_PASSWORD is set to).
#
# Both endpoints are gated by RENDER_ADMIN_KEY, so the operator
# must add that env var to Render BEFORE running this script.

$ErrorActionPreference = "Stop"
$ServerUrl = "https://appweb-o7pl.onrender.com"

function Write-Section($msg) {
    Write-Host ""
    Write-Host "=== $msg ===" -ForegroundColor Cyan
    Write-Host ""
}

function Confirm($prompt) {
    do {
        $answer = Read-Host "$prompt [y/n]"
    } while ($answer -notin @("y", "n", "Y", "N"))
    return ($answer -in @("y", "Y"))
}

Write-Section "Launchpad admin recovery"

Write-Host "This script helps you reset the admin password on Render."
Write-Host ""
Write-Host "BEFORE running this script you must:" -ForegroundColor Yellow
Write-Host "  1. Go to https://dashboard.render.com/  (your service)" -ForegroundColor Yellow
Write-Host "  2. Click Environment" -ForegroundColor Yellow
Write-Host "  3. Add env var:  RENDER_ADMIN_KEY  =  (paste a long random string)" -ForegroundColor Yellow
Write-Host "  4. Save and wait for the auto-redeploy (1-3 minutes)" -ForegroundColor Yellow
Write-Host ""
Write-Host "If you do not have a key yet, generate one with:" -ForegroundColor Yellow
Write-Host '  python -c "import secrets; print(secrets.token_hex(32))"' -ForegroundColor Yellow
Write-Host ""

$MasterKey = Read-Host "Paste your RENDER_ADMIN_KEY"
if ([string]::IsNullOrWhiteSpace($MasterKey)) {
    Write-Host "Empty key. Aborting." -ForegroundColor Red
    exit 1
}

# === STEP 1: Try password reset (preserves any non-admin users) ===
Write-Section "Step 1: Try password reset (preserves users)"

$Username = Read-Host "Username to reset (default: _admin)"
if ([string]::IsNullOrWhiteSpace($Username)) { $Username = "_admin" }

$NewPassword = Read-Host "New password (min 4 chars)"
if ($NewPassword.Length -lt 4) {
    Write-Host "Password too short. Aborting." -ForegroundColor Red
    exit 1
}

$Headers = @{ "X-Admin-Key" = $MasterKey; "Content-Type" = "application/json" }
$Body = @{ username = $Username; new_password = $NewPassword } | ConvertTo-Json

$ResetResponse = $null
try {
    $ResetResponse = Invoke-WebRequest -Uri "$ServerUrl/api/auth/admin/reset-password" `
        -Method POST -Headers $Headers -Body $Body -UseBasicParsing -TimeoutSec 15
} catch {
    $StatusCode = $_.Exception.Response.StatusCode.value__
    $ErrorBody = ""
    try {
        $Stream = $_.Exception.Response.GetResponseStream()
        $Reader = New-Object System.IO.StreamReader($Stream)
        $ErrorBody = $Reader.ReadToEnd()
    } catch {}

    if ($StatusCode -eq 404) {
        # Feature not enabled: RENDER_ADMIN_KEY is not set on Render
        # (or the redeploy hasn't finished).
        Write-Host "Server returned 404." -ForegroundColor Red
        Write-Host "  -> RENDER_ADMIN_KEY is probably not set on Render," -ForegroundColor Red
        Write-Host "     or the latest redeploy is still in progress." -ForegroundColor Red
        Write-Host "  -> Check the Render dashboard and try again in 1-2 minutes." -ForegroundColor Red
        exit 1
    }
    elseif ($StatusCode -eq 403) {
        Write-Host "Server returned 403 — invalid RENDER_ADMIN_KEY." -ForegroundColor Red
        Write-Host "  -> The value you pasted does not match what is in Render." -ForegroundColor Red
        $retry = Confirm "Try again?"
        if ($retry) {
            & $PSCommandPath
        }
        exit 1
    }
    elseif ($StatusCode -eq 429) {
        Write-Host "Server returned 429 — too many failed attempts." -ForegroundColor Red
        Write-Host "  -> Wait an hour, then try again." -ForegroundColor Red
        exit 1
    }
    else {
        Write-Host "Server returned $StatusCode $ErrorBody" -ForegroundColor Red
        exit 1
    }
}

Write-Host "OK: password for $Username was reset." -ForegroundColor Green
Write-Host ""
Write-Host "You can now log in with:" -ForegroundColor Cyan
Write-Host "  Username: $Username" -ForegroundColor White
Write-Host "  Password: $NewPassword" -ForegroundColor White
Write-Host ""
Write-Host "If you have other users in the system, they are preserved." -ForegroundColor Green

# === OPTIONAL: Wipe everything and start over ===
Write-Host ""
$wipe = Confirm "Do you also want to wipe ALL users (nuclear option)?"
if ($wipe) {
    Write-Section "Wiping all users"

    $WipeResponse = $null
    try {
        $WipeResponse = Invoke-WebRequest -Uri "$ServerUrl/api/auth/admin/wipe-users" `
            -Method POST -Headers @{ "X-Admin-Key" = $MasterKey } -UseBasicParsing -TimeoutSec 15
    } catch {
        $StatusCode = $_.Exception.Response.StatusCode.value__
        $ErrorBody = ""
        try {
            $Stream = $_.Exception.Response.GetResponseStream()
            $Reader = New-Object System.IO.StreamReader($Stream)
            $ErrorBody = $Reader.ReadToEnd()
        } catch {}
        Write-Host "Server returned $StatusCode $ErrorBody" -ForegroundColor Red
        exit 1
    }
    Write-Host "OK: $($WipeResponse.Content)" -ForegroundColor Green
    Write-Host ""
    Write-Host "All users have been wiped. The next request to the server" -ForegroundColor Cyan
    Write-Host "will trigger the bootstrap admin (default: _admin / admin)." -ForegroundColor Cyan
    Write-Host ""
    Write-Host "If you have BOOTSTRAP_ADMIN_PASSWORD set on Render, the admin" -ForegroundColor Cyan
    Write-Host "will be created with that password instead of the default." -ForegroundColor Cyan
    Write-Host ""
    Write-Host "Visit https://sahid1129.github.io/Appweb/ and do a HARD REFRESH" -ForegroundColor Cyan
    Write-Host "(Ctrl+Shift+R) to clear the old session from localStorage." -ForegroundColor Cyan
}

Write-Host ""
Write-Host "Done." -ForegroundColor Green
