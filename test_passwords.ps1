# test_passwords.ps1
# Helper script: try a list of common passwords to find which one the
# _admin user on Render has. If none match, the operator must
# change BOOTSTRAP_ADMIN_PASSWORD in the Render dashboard.

$ErrorActionPreference = "Stop"
$ServerUrl = "https://appweb-o7pl.onrender.com"

function Test-Password($pw) {
    $body = @{ username = "_admin"; password = $pw } | ConvertTo-Json
    try {
        $r = Invoke-WebRequest -Uri "$ServerUrl/api/auth/login" `
            -Method POST -ContentType "application/json" -Body $body `
            -UseBasicParsing -TimeoutSec 10
        if ($r.StatusCode -eq 200) {
            return $true
        }
    } catch {
        $sc = $_.Exception.Response.StatusCode.value__
        if ($sc -ne 401) {
            Write-Host "  [warn] $pw -> HTTP $sc" -ForegroundColor Yellow
        }
    }
    return $false
}

Write-Host ""
Write-Host "=== Testing common passwords against _admin on Render ===" -ForegroundColor Cyan
Write-Host ""

# Common defaults and likely user choices
$candidates = @(
    "admin",
    "admin123",
    "admin1234",
    "Admin123",
    "Admin1234",
    "password",
    "Password",
    "Password123",
    "Password1234",
    "1234",
    "12345",
    "123456",
    "sa",
    "sa123",
    "sahid",
    "Sahid",
    "Sahid123",
    "sahid123",
    "launchpad",
    "Launchpad",
    "Launchpad123",
    "demo",
    "demo123",
    "test",
    "test123",
    "root",
    "root123",
    "admin@123",
    "Admin@123",
    "12345678",
    "qwerty",
    "qwerty123",
    "letmein",
    "welcome",
    "master",
    "master123",
    "key",
    "keynotebook",
    "Keynotebook",
    "Keynotebook123",
    "Key",
    "KEY",
    "notebook",
    "Notebook",
    "Notebook123",
    "Render",
    "render",
    "render123",
    "RENDER",
    "RENDER123"
)

foreach ($pw in $candidates) {
    Write-Host -NoNewline "  trying '$pw'... "
    if (Test-Password $pw) {
        Write-Host "SUCCESS" -ForegroundColor Green
        Write-Host ""
        Write-Host "Your admin password is: $pw" -ForegroundColor Green
        Write-Host ""
        Write-Host "Log in at: $ServerUrl" -ForegroundColor Cyan
        Write-Host "  Username: _admin" -ForegroundColor White
        Write-Host "  Password: $pw" -ForegroundColor White
        exit 0
    }
    Write-Host "no"
}

Write-Host ""
Write-Host "None of the common passwords worked." -ForegroundColor Red
Write-Host "You will need to change BOOTSTRAP_ADMIN_PASSWORD in the Render dashboard." -ForegroundColor Red
exit 1
