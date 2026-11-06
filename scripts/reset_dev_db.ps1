<#
.SYNOPSIS
  Rebuild the LOCAL Mobofirm development database from scratch. DESTRUCTIVE.
.DESCRIPTION
  1. Refuses unless DB_HOST is a loopback address and -ConfirmDatabase names the database.
  2. Backs up the current database with pg_dump (PostgreSQL tools from Program Files).
  3. Drops and recreates the application schema (DB_SCHEMA, default "mobofirm_local",
     the search_path used by app.settings.local).
  4. Runs migrate, then seed_platform --confirm (catalogue, firmware listings,
     tools, guides, automated articles, editorial articles, comments, SEO inventory).
  5. Creates the superuser from DJANGO_SUPERUSER_EMAIL / DJANGO_SUPERUSER_PASSWORD,
     or prompts for them, and prints a summary.
  Credentials come from the environment or .env (DB_NAME, DB_USER, DB_PASSWORD,
  DB_HOST, DB_PORT). Dropping a schema may need more rights than the application
  role has; set PG_ADMIN_USER / PG_ADMIN_PASSWORD to use a different role for the
  backup, drop and create steps only. Nothing is hard-coded.
.EXAMPLE
  .\scripts\reset_dev_db.ps1 -ConfirmDatabase mobofirm
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$ConfirmDatabase,
    [string]$BackupDir,
    [int]$LimitBrands = 0,
    [switch]$SkipSeo
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
Set-Location $repoRoot
if (-not $BackupDir) { $BackupDir = if ($env:BACKUP_DIR) { $env:BACKUP_DIR } else { Join-Path $repoRoot 'backups' } }

$envFile = Join-Path $repoRoot '.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^(DB_NAME|DB_USER|DB_PASSWORD|DB_HOST|DB_PORT|DJANGO_SUPERUSER_EMAIL|DJANGO_SUPERUSER_PASSWORD|PG_ADMIN_USER|PG_ADMIN_PASSWORD)=(.*)$') {
            if (-not [Environment]::GetEnvironmentVariable($Matches[1])) {
                [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim())
            }
        }
    }
}
foreach ($required in 'DB_NAME', 'DB_USER', 'DB_PASSWORD') {
    if (-not [Environment]::GetEnvironmentVariable($required)) { throw "$required is required." }
}
$dbHost = if ($env:DB_HOST) { $env:DB_HOST.Trim() } else { '127.0.0.1' }
$dbPort = if ($env:DB_PORT) { $env:DB_PORT } else { '5432' }
if (@('127.0.0.1', 'localhost', '::1') -notcontains $dbHost) {
    throw "Refusing to reset a non-local database (DB_HOST=$dbHost). Only 127.0.0.1, localhost and ::1 are accepted."
}
if ($ConfirmDatabase -ne $env:DB_NAME) {
    throw "Pass -ConfirmDatabase $($env:DB_NAME) to acknowledge that every table in that database will be dropped."
}

$schema = if ($env:DB_SCHEMA) { $env:DB_SCHEMA.Trim() } else { 'mobofirm_local' }
if ($schema -notmatch '^[A-Za-z_][A-Za-z0-9_]*$') { throw "DB_SCHEMA '$schema' is not a valid schema name." }
$python = Join-Path $repoRoot '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Missing .venv; create it before resetting the database.' }

function Find-PgTool([string]$Name) {
    $onPath = Get-Command $Name -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    $found = Get-ChildItem 'C:\Program Files\PostgreSQL' -Directory -ErrorAction SilentlyContinue |
        Sort-Object { [int]($_.Name -replace '\D', '') } -Descending |
        ForEach-Object { Join-Path $_.FullName "bin\$Name.exe" } |
        Where-Object { Test-Path $_ } | Select-Object -First 1
    if (-not $found) { throw "$Name was not found on PATH or under C:\Program Files\PostgreSQL." }
    return $found
}
$pgDump = Find-PgTool 'pg_dump'
$psql = Find-PgTool 'psql'

function Invoke-Step([string]$Title, [scriptblock]$Action) {
    Write-Host "==> $Title" -ForegroundColor Cyan
    & $Action
    if ($LASTEXITCODE -ne 0) { throw "$Title failed with exit code $LASTEXITCODE." }
}

$adminUser = if ($env:PG_ADMIN_USER) { $env:PG_ADMIN_USER } else { $env:DB_USER }
$adminPassword = if ($env:PG_ADMIN_PASSWORD) { $env:PG_ADMIN_PASSWORD } else { $env:DB_PASSWORD }
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$dumpFile = Join-Path $BackupDir "$($env:DB_NAME)_before_reset_$stamp.dump"
New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null

$started = Get-Date
$env:PGPASSWORD = $adminPassword
try {
    Invoke-Step "Back up $($env:DB_NAME) to $dumpFile" {
        & $pgDump --host $dbHost --port $dbPort --username $adminUser --format custom --no-owner --file $dumpFile $env:DB_NAME
    }
    if (-not (Test-Path $dumpFile) -or (Get-Item $dumpFile).Length -eq 0) { throw 'The backup file is missing or empty; nothing was dropped.' }
    Invoke-Step "Drop and recreate schema $schema" {
        & $psql --host $dbHost --port $dbPort --username $adminUser --dbname $env:DB_NAME -v ON_ERROR_STOP=1 `
            -c "DROP SCHEMA IF EXISTS `"$schema`" CASCADE;" -c "CREATE SCHEMA `"$schema`";" `
            -c "GRANT ALL ON SCHEMA `"$schema`" TO `"$($env:DB_USER)`";"
    }
}
finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}

Invoke-Step 'Apply migrations' { & $python manage.py migrate --noinput }

$seedArgs = @('manage.py', 'seed_platform', '--confirm')
if ($LimitBrands -gt 0) { $seedArgs += @('--limit-brands', "$LimitBrands") }
if ($SkipSeo) { $seedArgs += '--skip-seo' }
Invoke-Step 'Seed the platform' { & $python @seedArgs }

if (-not $env:DJANGO_SUPERUSER_EMAIL) { $env:DJANGO_SUPERUSER_EMAIL = Read-Host 'Superuser email' }
if (-not $env:DJANGO_SUPERUSER_PASSWORD) {
    $secure = Read-Host 'Superuser password' -AsSecureString
    $env:DJANGO_SUPERUSER_PASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringUni([Runtime.InteropServices.Marshal]::SecureStringToGlobalAllocUnicode($secure))
}
Invoke-Step 'Create the superuser' { & $python manage.py createsuperuser --noinput }

$verifyEmail = @'
import os
from allauth.account.models import EmailAddress
from django.contrib.auth import get_user_model
user = get_user_model().objects.get(email__iexact=os.environ["DJANGO_SUPERUSER_EMAIL"])
user.mark_email_verified()
EmailAddress.objects.update_or_create(user=user, email=user.email, defaults={"verified": True, "primary": True})
print("Superuser email marked as verified:", user.email)
'@
Invoke-Step 'Verify the superuser email' { & $python manage.py shell -c $verifyEmail }
Remove-Item Env:\DJANGO_SUPERUSER_PASSWORD -ErrorAction SilentlyContinue

$summary = @'
from django.apps import apps
for label, name in (
    ("firmwares.Brand", "brands"), ("firmwares.Model", "models"),
    ("firmwares.Chipset", "chipsets"), ("firmwares.OfficialFirmware", "official firmware listings"),
    ("firmwares.FlashingTool", "flashing tools"), ("blog.Post", "blog posts"),
    ("tags.Tag", "tags"), ("comments.Comment", "comments"), ("users.CustomUser", "accounts"),
):
    print(f"  {name}: {apps.get_model(label).objects.count()}")
'@
Write-Host '==> Summary' -ForegroundColor Cyan
& $python manage.py shell -c $summary
Write-Host ("Reset complete in {0:N0}s. Backup: {1}" -f ((Get-Date) - $started).TotalSeconds, $dumpFile) -ForegroundColor Green
