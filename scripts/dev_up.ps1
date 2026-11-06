<#
.SYNOPSIS
  Windows local runbook: prepare and start the Mobofirm development server.
.DESCRIPTION
  Verifies the virtual environment, .env and local PostgreSQL/Redis reachability,
  builds the frontend when needed, runs Django checks, and starts runserver.
  It never changes the database unless you pass -Migrate (schema) or -Bootstrap
  (Site, AppRegistry, groups, automation user); both are idempotent but are only
  ever run against the target named in .env, so confirm that target first.
.EXAMPLE
  .\scripts\dev_up.ps1                      # checks + runserver
  .\scripts\dev_up.ps1 -Migrate -Bootstrap  # first run against a fresh local database
  .\scripts\dev_up.ps1 -NoServer            # checks only
#>
[CmdletBinding()]
param(
    [switch]$Migrate,
    [switch]$Bootstrap,
    [switch]$SkipFrontend,
    [switch]$NoServer,
    [int]$Port = 8000
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $repoRoot

function Invoke-Step([string]$Title, [scriptblock]$Action) {
    Write-Host "==> $Title" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$Title failed with exit code $LASTEXITCODE." }
}

$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) {
    throw "Missing .venv. Create it with: py -3.12 -m venv .venv; .venv\Scripts\python.exe -m pip install -e '.[dev]'"
}
if (-not (Test-Path (Join-Path $repoRoot '.env'))) {
    throw 'Missing .env. Copy .env.example to .env and set DJANGO_SECRET_KEY, DB_PASSWORD and the encryption keys.'
}

# Reachability of the local services (warn for Redis: only async work needs it).
$dbHost = '127.0.0.1'; $dbPort = 5432
foreach ($line in Get-Content (Join-Path $repoRoot '.env')) {
    if ($line -match '^DB_HOST=(.+)$') { $dbHost = $Matches[1].Trim() }
    if ($line -match '^DB_PORT=(\d+)$') { $dbPort = [int]$Matches[1] }
}
if (-not (Test-NetConnection -ComputerName $dbHost -Port $dbPort -InformationLevel Quiet -WarningAction SilentlyContinue)) {
    throw "PostgreSQL is not reachable at ${dbHost}:${dbPort}. Start the local PostgreSQL service first."
}
if (-not (Test-NetConnection -ComputerName '127.0.0.1' -Port 6379 -InformationLevel Quiet -WarningAction SilentlyContinue)) {
    Write-Warning 'Redis is not reachable on 127.0.0.1:6379: Celery workers and shared rate limits are unavailable (local settings fall back to per-process caching).'
}

if (-not $SkipFrontend -and -not (Test-Path (Join-Path $repoRoot 'static\build'))) {
    Invoke-Step 'Install and build the frontend' { npm ci; if ($LASTEXITCODE -eq 0) { npm run build } }
}

Invoke-Step 'Django system check' { & $python manage.py check }
Invoke-Step 'Migration consistency check' { & $python manage.py makemigrations --check --dry-run }

if ($Migrate) {
    Invoke-Step 'Apply migrations (target: the database in .env)' { & $python manage.py migrate --noinput }
}
if ($Bootstrap) {
    Invoke-Step 'Bootstrap site data' { & $python manage.py bootstrap_site }
}

if (-not $NoServer) {
    Write-Host "==> Starting http://127.0.0.1:$Port/ (Ctrl+C to stop)" -ForegroundColor Green
    & $python manage.py runserver "127.0.0.1:$Port"
}
