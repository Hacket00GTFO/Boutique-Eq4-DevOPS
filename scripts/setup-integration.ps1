$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskEnvPath = Join-Path $taskRoot '.env'
if (Test-Path -LiteralPath $taskEnvPath) {
    Write-Output '.env already exists; preserve it and fill any missing keys from .env.example.'
    exit 0
}
function New-TaskSecret {
    $taskBytes = New-Object byte[] 32
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($taskBytes)
    return [Convert]::ToHexString($taskBytes).ToLowerInvariant()
}
$taskKeys = @('POSTGRES_PASSWORD','APP_DB_PASSWORD','ADMIN_PASSWORD','ADMIN_API_TOKEN','CATALOG_READ_TOKEN','CHECKOUT_API_TOKEN')
$taskLines = @('ADMIN_EMAIL=admin@boutique.com')
foreach ($taskKey in $taskKeys) { $taskLines += "$taskKey=$(New-TaskSecret)" }
[IO.File]::WriteAllLines($taskEnvPath, $taskLines)
Write-Output 'Created local .env with independent random credentials. Read ADMIN_EMAIL/ADMIN_PASSWORD there to sign in.'
