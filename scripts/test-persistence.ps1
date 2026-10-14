$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$taskValues = @{}
foreach ($taskLine in Get-Content -LiteralPath (Join-Path $taskRoot '.env')) {
    if ($taskLine -match '^([A-Z_]+)=(.*)$') { $taskValues[$Matches[1]] = $Matches[2] }
}
$taskJob = Start-Job -ArgumentList $taskRoot,$taskValues['ADMIN_EMAIL'],$taskValues['ADMIN_PASSWORD'] -ScriptBlock {
    param($taskDirectory,$taskEmail,$taskPassword)
    Set-Location -LiteralPath $taskDirectory
    Get-Content scripts/test-persistence.py -Raw | docker compose -f docker-compose.yml -f docker-compose.admin.yml exec -T `
        -e "ADMIN_EMAIL=$taskEmail" -e "ADMIN_PASSWORD=$taskPassword" recommendationservice python -
    if ($LASTEXITCODE -ne 0) { throw 'Persistence probe failed' }
}
Push-Location $taskRoot
try {
    $taskReady = $false
    for ($taskAttempt = 0; $taskAttempt -lt 15; $taskAttempt++) {
        $taskOutput = Receive-Job -Job $taskJob -Keep
        if ($taskOutput -contains 'PERSISTENCE_READY') { $taskReady = $true; break }
        if ($taskJob.State -eq 'Failed') { throw 'Persistence preparation failed' }
        Start-Sleep -Seconds 1
    }
    if (-not $taskReady) { throw 'Persistence probe did not become ready' }
    docker compose -f docker-compose.yml -f docker-compose.admin.yml restart postgres catalogservice frontend
    if ($LASTEXITCODE -ne 0) { throw 'Restart failed' }
    Wait-Job -Job $taskJob -Timeout 50 | Out-Null
    Receive-Job -Job $taskJob
    if ($taskJob.State -ne 'Completed') { throw 'Persistence test failed' }
} finally {
    if ($taskJob.State -eq 'Running') { Stop-Job -Job $taskJob }
    Remove-Job -Job $taskJob -Force
    Pop-Location
}
