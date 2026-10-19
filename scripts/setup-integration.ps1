$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskEnvPath = Join-Path $taskRoot '.env'
$taskKeys = @('POSTGRES_PASSWORD','APP_DB_PASSWORD','ADMIN_PASSWORD','ADMIN_API_TOKEN','CATALOG_READ_TOKEN','CHECKOUT_API_TOKEN')
$taskAllKeys = @('ADMIN_EMAIL') + $taskKeys
$taskLines = @()
$taskValues = @{}
if (Test-Path -LiteralPath $taskEnvPath) {
    $taskLines = @(Get-Content -LiteralPath $taskEnvPath)
    foreach ($taskLine in $taskLines) {
        if ($taskLine -match '^([A-Z_]+)=(.*)$') { $taskValues[$Matches[1]] = $Matches[2] }
    }
}
$taskMissing = @($taskAllKeys | Where-Object { -not $taskValues[$_] -or $taskValues[$_].Trim() -in @('', "''", '""') })
if ($taskMissing.Count -eq 0) {
    Write-Output '.env is complete; existing credentials preserved.'
    return
}

# A fresh random password would not match an already initialized PostgreSQL volume.
# Inspect only containers from this workspace; values are never printed.
$taskIds = @(docker ps --all --quiet --filter "label=com.docker.compose.project.working_dir=$taskRoot")
if ($LASTEXITCODE -ne 0) { throw 'Start Docker Desktop before initializing or recovering .env.' }
$taskContainers = @()
if ($taskIds.Count -gt 0) {
    $taskInspect = docker inspect @taskIds
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect existing containers; .env was not changed.' }
    $taskContainers = @($taskInspect | ConvertFrom-Json)
}
$taskSources = @{
    POSTGRES_PASSWORD = @('postgres')
    APP_DB_PASSWORD = @('postgres','migrate')
    ADMIN_EMAIL = @('migrate')
    ADMIN_PASSWORD = @('migrate')
    ADMIN_API_TOKEN = @('catalogservice','frontend')
    CATALOG_READ_TOKEN = @('catalogservice','productcatalogservice')
    CHECKOUT_API_TOKEN = @('catalogservice','checkoutservice')
}
$taskRecovered = @()
foreach ($taskKey in $taskMissing) {
    foreach ($taskSource in $taskSources[$taskKey]) {
        $taskCandidates = @($taskContainers | Where-Object { $_.Config.Labels.'com.docker.compose.service' -eq $taskSource })
        $taskFound = @($taskCandidates | ForEach-Object { $_.Config.Env } | Where-Object { $_.StartsWith($taskKey + '=') } | ForEach-Object { ($_ -split '=',2)[1] } | Where-Object { $_ } | Select-Object -Unique)
        if ($taskFound.Count -gt 1) { throw "Conflicting $taskKey values in existing containers; .env was not changed." }
        if ($taskFound.Count -eq 1) {
            # Generated credentials are URL-safe. Do not write ambiguous dotenv values.
            if ($taskFound[0] -match '[\s$#''"]') { throw "Recover $taskKey manually using dotenv quoting; .env was not changed." }
            $taskValues[$taskKey] = $taskFound[0]
            $taskRecovered += $taskKey
            break
        }
    }
}
$taskProjects = @($taskContainers | ForEach-Object { $_.Config.Labels.'com.docker.compose.project' } | Select-Object -Unique)
if ($taskProjects.Count -eq 0) {
    $taskProjectName = if ($taskValues['COMPOSE_PROJECT_NAME']) { $taskValues['COMPOSE_PROJECT_NAME'] } elseif ($env:COMPOSE_PROJECT_NAME) { $env:COMPOSE_PROJECT_NAME } else { (Split-Path -Leaf $taskRoot).ToLowerInvariant() -replace '[^a-z0-9_-]', '' }
    $taskProjects = @($taskProjectName)
}
$taskDatabaseExists = @($taskContainers | Where-Object { $_.Config.Labels.'com.docker.compose.service' -eq 'postgres' }).Count -gt 0
foreach ($taskProject in $taskProjects) {
    $taskVolumes = @(docker volume ls --quiet --filter "label=com.docker.compose.project=$taskProject" --filter 'label=com.docker.compose.volume=boutique-postgres')
    if ($LASTEXITCODE -ne 0) { throw 'Cannot check existing PostgreSQL volumes; .env was not changed.' }
    if ($taskVolumes.Count -gt 0) { $taskDatabaseExists = $true }
}
$taskStillMissing = @($taskKeys | Where-Object { -not $taskValues[$_] -or $taskValues[$_].Trim() -in @('', "''", '""') })
$taskUnrecoverable = @($taskAllKeys | Where-Object { -not $taskValues[$_] -or $taskValues[$_].Trim() -in @('', "''", '""') })
if ($taskDatabaseExists -and $taskUnrecoverable.Count -gt 0) {
    throw "Existing PostgreSQL data detected. Restore these keys from the original .env: $($taskUnrecoverable -join ', '). No passwords were regenerated and .env was not changed."
}
function New-TaskSecret {
    $taskBytes = New-Object byte[] 32
    $taskGenerator = [System.Security.Cryptography.RandomNumberGenerator]::Create()
    try { $taskGenerator.GetBytes($taskBytes) } finally { $taskGenerator.Dispose() }
    return [BitConverter]::ToString($taskBytes).Replace('-', '').ToLowerInvariant()
}
foreach ($taskKey in $taskStillMissing) { $taskValues[$taskKey] = New-TaskSecret }
if (-not $taskValues['ADMIN_EMAIL'] -or $taskValues['ADMIN_EMAIL'].Trim() -in @('', "''", '""')) { $taskValues['ADMIN_EMAIL'] = 'admin@boutique.com' }
$taskOutput = @($taskLines | Where-Object { $_ -notmatch '^(ADMIN_EMAIL|POSTGRES_PASSWORD|APP_DB_PASSWORD|ADMIN_PASSWORD|ADMIN_API_TOKEN|CATALOG_READ_TOKEN|CHECKOUT_API_TOKEN)=' })
foreach ($taskKey in $taskAllKeys) { $taskOutput += "$taskKey=$($taskValues[$taskKey])" }
[IO.File]::WriteAllLines($taskEnvPath, $taskOutput)
if ($taskRecovered.Count -gt 0) { Write-Output "Recovered $($taskRecovered.Count) missing keys from this project's existing containers without changing their values." }
if ($taskStillMissing.Count -gt 0) { Write-Output 'Generated independent credentials for a fresh database.' }
Write-Output '.env is ready. Read ADMIN_EMAIL/ADMIN_PASSWORD there to sign in; do not commit this file.'
