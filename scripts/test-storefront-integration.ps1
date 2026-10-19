$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskValues = @{}
foreach ($taskLine in Get-Content -LiteralPath (Join-Path $taskRoot '.env')) {
    if ($taskLine -match '^([A-Z_]+)=(.*)$') { $taskValues[$Matches[1]] = $Matches[2] }
}
if (-not $taskValues['ADMIN_PASSWORD']) { throw 'Missing local ADMIN_PASSWORD in .env' }
Push-Location $taskRoot
try {
    Get-Content scripts/test-storefront-integration.py -Raw | docker compose -f docker-compose.yml -f docker-compose.admin.yml exec -T `
        -e "ADMIN_EMAIL=$($taskValues['ADMIN_EMAIL'])" -e "ADMIN_PASSWORD=$($taskValues['ADMIN_PASSWORD'])" catalogservice python -
    if ($LASTEXITCODE -ne 0) { throw 'Frontend integration failed.' }
} finally { Pop-Location }
