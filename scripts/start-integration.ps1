param([switch]$SkipBuild)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location $taskRoot
try {
    if (-not (Test-Path -LiteralPath (Join-Path $taskRoot '.env'))) {
        & (Join-Path $PSScriptRoot 'setup-integration.ps1')
    }

    $taskCompose = @('-f', 'docker-compose.yml', '-f', 'docker-compose.admin.yml')
    docker compose @taskCompose config --quiet
    if ($LASTEXITCODE -ne 0) { throw 'Invalid integration configuration. Check required keys in .env.' }

    $taskUp = @('up', '-d', '--wait', '--wait-timeout', '180')
    if (-not $SkipBuild) { $taskUp += '--build' }
    docker compose @taskCompose @taskUp
    if ($LASTEXITCODE -ne 0) { throw 'Integration startup failed. Inspect docker compose logs with both configuration files.' }

    # Go containers can start before their gRPC dependencies accept requests.
    $taskReady = $false
    for ($taskAttempt = 0; $taskAttempt -lt 15; $taskAttempt++) {
        try {
            $taskResponse = Invoke-WebRequest -Uri 'http://localhost:8080/_readyz' -UseBasicParsing -TimeoutSec 6
            if ($taskResponse.StatusCode -eq 200 -and $taskResponse.Content.Trim() -eq 'ready') {
                $taskReady = $true
                break
            }
        } catch {
            # Retry only until the bounded startup window ends.
        }
        if ($taskAttempt -lt 14) { Start-Sleep -Seconds 2 }
    }
    if (-not $taskReady) { throw 'Containers started, but frontend dependencies are not ready at http://localhost:8080/_readyz.' }

    Write-Output 'PostgreSQL integration ready: http://localhost:8080/admin/login'
    Write-Output 'Use ADMIN_EMAIL and ADMIN_PASSWORD from .env to sign in.'
} finally {
    Pop-Location
}
