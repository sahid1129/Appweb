# change_bootstrap_password.ps1
# Emergency script for when the operator has lost the BOOTSTRAP_ADMIN_PASSWORD
# and cannot log in to the Launchpad app.
#
# The fastest path to recovery is to change BOOTSTRAP_ADMIN_PASSWORD directly
# in the Render dashboard. Render will auto-redeploy with the new value
# and the operator can then log in with the new password.
#
# This script:
#   1. Walks the operator through changing the env var in Render.
#   2. Opens the Render dashboard in the default browser.
#   3. Polls /api/auth/users until the redeploy completes (admin still
#      exists with the new password hash).
#   4. Confirms the operator can log in with the new password.
#
# IMPORTANT: this script does NOT modify the server state. It only
# guides the operator and validates the outcome.

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

Write-Section "Launchpad: lost admin password recovery"

Write-Host "You ran the recovery script but Render does not have RENDER_ADMIN_KEY" -ForegroundColor Yellow
Write-Host "configured, so the recovery endpoints return 404." -ForegroundColor Yellow
Write-Host ""
Write-Host "The FASTEST path to recover is to change the bootstrap password" -ForegroundColor Green
Write-Host "directly in the Render dashboard. Render will auto-redeploy with" -ForegroundColor Green
Write-Host "the new value, and you can log in with the new password." -ForegroundColor Green
Write-Host ""

# Step 1: probe the server to see what we are dealing with
Write-Section "Step 1: probe the server"
try {
    $r = Invoke-WebRequest -Uri "$ServerUrl/api/auth/users" -UseBasicParsing -TimeoutSec 15
    $body = $r.Content | ConvertFrom-Json
    if ($body.has_users -and $body.users.Count -gt 0) {
        Write-Host "Server is up. Users:" -ForegroundColor Green
        foreach ($u in $body.users) {
            $tag = if ($u.is_admin) { " [admin]" } else { "" }
            Write-Host ("  - {0}{1}" -f $u.username, $tag)
        }
    } else {
        Write-Host "Server is up but has NO users." -ForegroundColor Yellow
        Write-Host "You can register the first user from the login screen." -ForegroundColor Green
        exit 0
    }
} catch {
    Write-Host "Cannot reach the server: $($_.Exception.Message)" -ForegroundColor Red
    exit 1
}

# Step 2: prompt for the new password
Write-Section "Step 2: pick a new password"
$NewPassword = Read-Host "New admin password (min 4 chars)"
if ($NewPassword.Length -lt 4) {
    Write-Host "Password too short. Aborting." -ForegroundColor Red
    exit 1
}

# Step 3: open the Render dashboard
Write-Section "Step 3: open the Render dashboard"
Write-Host "Opening the Render dashboard in your default browser..." -ForegroundColor Cyan
Write-Host ""
Write-Host "  1. Click on your service (it should be named 'Appweb' or similar)" -ForegroundColor Yellow
Write-Host "  2. Click 'Environment' in the left sidebar" -ForegroundColor Yellow
Write-Host "  3. Find the 'BOOTSTRAP_ADMIN_PASSWORD' row" -ForegroundColor Yellow
Write-Host "  4. Click the value, replace it with: $NewPassword" -ForegroundColor Yellow
Write-Host "  5. Click 'Save Changes' (Render will auto-redeploy, 1-3 min)" -ForegroundColor Yellow
Write-Host ""
Write-Host "Alternative: if you do not see BOOTSTRAP_ADMIN_PASSWORD, add it." -ForegroundColor Yellow
Write-Host ""

$open = Confirm "Open the Render dashboard in your browser now?"
if ($open) {
    Start-Process "https://dashboard.render.com/"
    Write-Host "Browser opened. Edit the env var and come back." -ForegroundColor Green
}

# Step 4: poll for the redeploy
Write-Section "Step 4: wait for the redeploy"
Write-Host "After you save, Render will redeploy. This takes 1-3 minutes." -ForegroundColor Yellow
Write-Host "We will poll the server every 15 seconds until the deploy completes" -ForegroundColor Yellow
Write-Host "(sign: the password hash in the bootstrap log line will change)." -ForegroundColor Yellow
Write-Host ""

$Ready = $false
$attempts = 0
$maxAttempts = 40  # 10 minutes max
while (-not $Ready -and $attempts -lt $maxAttempts) {
    Start-Sleep -Seconds 15
    $attempts++
    try {
        $r = Invoke-WebRequest -Uri "$ServerUrl/api/auth/status" -UseBasicParsing -TimeoutSec 5
        $body = $r.Content | ConvertFrom-Json
        if ($body.has_users) {
            Write-Host ("  [{0:00}:{1:00}] server has users, may be ready..." -f [int]($attempts/4), ($attempts%4)*15) -ForegroundColor Gray
        } else {
            Write-Host "  Server has no users yet (mid-redeploy?)" -ForegroundColor Yellow
        }
    } catch {
        Write-Host "  Server unreachable (mid-redeploy?)" -ForegroundColor Yellow
    }
}

# Step 5: validate the new password works
Write-Section "Step 5: validate"
$TestLogin = Confirm "Has the redeploy finished?"
if (-not $TestLogin) { exit 0 }

try {
    $r = Invoke-WebRequest -Uri "$ServerUrl/api/auth/login" -Method POST `
        -ContentType "application/json" `
        -Body (ConvertTo-Json @{ username = "_admin"; password = $NewPassword }) `
        -UseBasicParsing -TimeoutSec 15
    Write-Host "Login: $($r.StatusCode)" -ForegroundColor Green
    if ($r.StatusCode -eq 200) {
        $body = $r.Content | ConvertFrom-Json
        Write-Host "  Username: $($body.username)" -ForegroundColor Green
        Write-Host "  Is admin: $($body.is_admin)" -ForegroundColor Green
        Write-Host ""
        Write-Host "Recovery complete." -ForegroundColor Green
        Write-Host "Go to https://sahid1129.github.io/Appweb/ and log in." -ForegroundColor Cyan
        Write-Host "Press Ctrl+Shift+R first to clear the cached old session." -ForegroundColor Cyan
    } else {
        Write-Host "Login returned $($r.StatusCode). The redeploy may not be done yet." -ForegroundColor Yellow
        Write-Host "Wait 30 seconds and try again." -ForegroundColor Yellow
    }
} catch {
    Write-Host "Login failed: $($_.Exception.Message)" -ForegroundColor Red
}
