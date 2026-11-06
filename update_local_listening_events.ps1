$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
$encrypted = "data\listening_events.csv.enc"
$local = "data\listening_events.csv"
$keyFile = Join-Path $PSScriptRoot ".secrets\listening_events.key"

git pull --ff-only
if ($LASTEXITCODE -ne 0) { throw "git pull failed; resolve it and run again." }

$key = $env:SPOTY_LISTENING_EVENTS_KEY
if (-not $key -and (Test-Path $keyFile)) {
    $key = (Get-Content -Raw $keyFile).Trim()
}
if (-not $key) {
    $secure = Read-Host "Encryption key" -AsSecureString
    $key = [System.Net.NetworkCredential]::new("", $secure).Password
}

if (Test-Path $local) {
    Copy-Item $local "data\listening_events.backup.csv" -Force
}

$previousKey = $env:SPOTY_LISTENING_EVENTS_KEY
$env:SPOTY_LISTENING_EVENTS_KEY = $key
try {
    & $python -m services.encrypted_listening_events merge $encrypted $local
    if ($LASTEXITCODE -ne 0) { throw "Merge failed." }
}
finally {
    $env:SPOTY_LISTENING_EVENTS_KEY = $previousKey
}
