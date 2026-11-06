<#
.SYNOPSIS
  Back up the Mobofirm PostgreSQL database (and optionally uploaded files).
.DESCRIPTION
  Credentials come from the environment (DB_NAME, DB_USER, DB_PASSWORD, DB_HOST,
  DB_PORT) or from the repository's .env file; nothing is hard-coded. Requires
  pg_dump on PATH. Only files created by this script are pruned.
.EXAMPLE
  .\scripts\backup_db.ps1 -WithFiles -RetentionDays 30
#>
[CmdletBinding()]
param(
    [switch]$WithFiles,
    [string]$OutputDir = $(if ($env:BACKUP_DIR) { $env:BACKUP_DIR } else { Join-Path (Split-Path $PSScriptRoot -Parent) 'backups' }),
    [int]$RetentionDays = $(if ($env:BACKUP_RETENTION_DAYS) { [int]$env:BACKUP_RETENTION_DAYS } else { 14 })
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent

# Read only the DB_* keys from .env; values already in the environment win.
$envFile = Join-Path $repoRoot '.env'
if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
        if ($line -match '^(DB_NAME|DB_USER|DB_PASSWORD|DB_HOST|DB_PORT)=(.*)$') {
            if (-not [Environment]::GetEnvironmentVariable($Matches[1])) {
                [Environment]::SetEnvironmentVariable($Matches[1], $Matches[2].Trim())
            }
        }
    }
}
foreach ($required in 'DB_NAME', 'DB_USER', 'DB_PASSWORD') {
    if (-not [Environment]::GetEnvironmentVariable($required)) { throw "$required is required." }
}
$dbHost = if ($env:DB_HOST) { $env:DB_HOST } else { '127.0.0.1' }
$dbPort = if ($env:DB_PORT) { $env:DB_PORT } else { '5432' }
if (-not (Get-Command pg_dump -ErrorAction SilentlyContinue)) { throw 'pg_dump is not on PATH.' }

New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString('yyyyMMddTHHmmssZ')
$dumpFile = Join-Path $OutputDir "$($env:DB_NAME)_$stamp.dump"

$env:PGPASSWORD = $env:DB_PASSWORD
try {
    & pg_dump --host $dbHost --port $dbPort --username $env:DB_USER --format custom --no-owner --file $dumpFile $env:DB_NAME
    if ($LASTEXITCODE -ne 0) { throw "pg_dump failed with exit code $LASTEXITCODE." }
}
finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
Write-Host "Database dump written: $dumpFile"

if ($WithFiles) {
    $filesArchive = Join-Path $OutputDir "files_$stamp.zip"
    $paths = @('media', 'storage') | ForEach-Object { Join-Path $repoRoot $_ } | Where-Object { Test-Path $_ }
    if ($paths) {
        Compress-Archive -Path $paths -DestinationPath $filesArchive
        Write-Host "Files archive written: $filesArchive"
    }
}

$cutoff = (Get-Date).AddDays(-$RetentionDays)
Get-ChildItem -Path $OutputDir -File |
    Where-Object { ($_.Name -like "$($env:DB_NAME)_*.dump" -or $_.Name -like 'files_*.zip') -and $_.LastWriteTime -lt $cutoff } |
    ForEach-Object { Write-Host "Pruning $($_.FullName)"; Remove-Item $_.FullName }
