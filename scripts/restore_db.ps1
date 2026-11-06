<#
.SYNOPSIS
  Restore a dump created by scripts\backup_db.ps1. DESTRUCTIVE.
.DESCRIPTION
  Existing objects in the target database are dropped and recreated. Safety rails:
  the exact confirmation text "RESTORE <DB_NAME>" is required, and non-local hosts
  need -AllowRemote. Stop the web/worker/beat processes before restoring.
.EXAMPLE
  .\scripts\restore_db.ps1 -DumpFile .\backups\mobofirm_20260101T000000Z.dump -ConfirmText "RESTORE mobofirm"
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$DumpFile,
    [Parameter(Mandatory)][string]$ConfirmText,
    [string]$FilesArchive,
    [switch]$AllowRemote
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent

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

if (-not (Test-Path $DumpFile)) { throw "Dump file not found: $DumpFile" }
if ($ConfirmText -ne "RESTORE $($env:DB_NAME)") { throw "Refusing to restore: pass -ConfirmText 'RESTORE $($env:DB_NAME)'." }
if ($dbHost -notin '127.0.0.1', 'localhost', '::1' -and -not $AllowRemote) {
    throw "Refusing to restore to remote host '$dbHost' without -AllowRemote."
}
if (-not (Get-Command pg_restore -ErrorAction SilentlyContinue)) { throw 'pg_restore is not on PATH.' }

$env:PGPASSWORD = $env:DB_PASSWORD
try {
    & pg_restore --host $dbHost --port $dbPort --username $env:DB_USER --dbname $env:DB_NAME --clean --if-exists --no-owner --exit-on-error $DumpFile
    if ($LASTEXITCODE -ne 0) { throw "pg_restore failed with exit code $LASTEXITCODE." }
}
finally {
    Remove-Item Env:\PGPASSWORD -ErrorAction SilentlyContinue
}
Write-Host "Database restored from $DumpFile"

if ($FilesArchive) {
    if (-not (Test-Path $FilesArchive)) { throw "Files archive not found: $FilesArchive" }
    Expand-Archive -Path $FilesArchive -DestinationPath $repoRoot -Force
    Write-Host "Files restored from $FilesArchive"
}
