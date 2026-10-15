param([switch]$VerifyFrontendFiles)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location $taskRoot
try {
    $taskCompose = @('-f', 'docker-compose.yml', '-f', 'docker-compose.admin.yml')
    $taskIds = @(docker compose @taskCompose ps --all --quiet frontend productcatalogservice checkoutservice catalogservice)
    if ($LASTEXITCODE -ne 0 -or $taskIds.Count -ne 4) { throw 'The four integration services must exist. Run scripts/start-integration.ps1.' }
    $taskInspect = docker inspect @taskIds
    if ($LASTEXITCODE -ne 0) { throw 'Cannot inspect integration containers.' }
    $taskContainers = $taskInspect | ConvertFrom-Json
    $taskExpected = @{
        frontend = @{ADMIN_DATA_MODE='backend'; ADMIN_API_BASE_URL='http://catalogservice:8080'; DEMO_DATA_ENABLED='true'}
        productcatalogservice = @{CATALOG_API_BASE_URL='http://catalogservice:8080'}
        checkoutservice = @{ORDER_API_BASE_URL='http://catalogservice:8080'}
        catalogservice = @{DEMO_DATA_ENABLED='true'}
    }
    foreach ($taskContainer in $taskContainers) {
        $taskService = $taskContainer.Config.Labels.'com.docker.compose.service'
        if (-not $taskContainer.State.Running) { throw "$taskService is not running." }
        $taskEnvironment = @{}
        foreach ($taskEntry in $taskContainer.Config.Env) {
            $taskPair = $taskEntry -split '=', 2
            $taskEnvironment[$taskPair[0]] = $taskPair[1]
        }
        foreach ($taskKey in $taskExpected[$taskService].Keys) {
            if ($taskEnvironment[$taskKey] -cne $taskExpected[$taskService][$taskKey]) {
                throw "$taskService has an incorrect $taskKey. Recreate with scripts/start-integration.ps1."
            }
        }
        $taskImage = docker image inspect $taskContainer.Config.Image --format '{{.Id}}'
        if ($LASTEXITCODE -ne 0 -or $taskImage -ne $taskContainer.Image) {
            throw "$taskService is not using the current image. Rebuild and recreate it."
        }
        Write-Output "PASS $taskService runs the current image with the integration configuration."
    }

    if ($VerifyFrontendFiles) {
        $taskFrontend = $taskContainers | Where-Object { $_.Config.Labels.'com.docker.compose.service' -eq 'frontend' }
        $taskTempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
        $taskCheckRoot = Join-Path $taskTempBase ('boutique-frontend-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $taskCheckRoot | Out-Null
        try {
            foreach ($taskGroup in @('templates', 'static')) {
                $taskCopyDir = Join-Path $taskCheckRoot $taskGroup
                New-Item -ItemType Directory -Path $taskCopyDir | Out-Null
                docker cp "$($taskFrontend.Id):/src/$taskGroup/admin/." $taskCopyDir
                if ($LASTEXITCODE -ne 0) { throw "Cannot read frontend $taskGroup from the container." }
                $taskSourceDir = Join-Path $taskRoot "src/frontend/$taskGroup/admin"
                foreach ($taskFile in Get-ChildItem -LiteralPath $taskSourceDir -File) {
                    $taskCopy = Join-Path $taskCopyDir $taskFile.Name
                    if (-not (Test-Path -LiteralPath $taskCopy) -or (Get-FileHash -LiteralPath $taskCopy).Hash -ne (Get-FileHash -LiteralPath $taskFile.FullName).Hash) {
                        throw "The frontend container contains an outdated $taskGroup/admin/$($taskFile.Name)."
                    }
                }
                Write-Output "PASS frontend $taskGroup/admin files match the workspace (SHA256)."
            }
        } finally {
            $taskResolved = [IO.Path]::GetFullPath($taskCheckRoot)
            if ([IO.Path]::GetDirectoryName($taskResolved) -eq $taskTempBase.TrimEnd([char]92, [char]47) -and [IO.Path]::GetFileName($taskResolved).StartsWith('boutique-frontend-')) {
                Remove-Item -LiteralPath $taskResolved -Recurse -Force
            } else { throw 'Temporary directory cleanup target is outside the expected directory.' }
        }
    }

    $taskResponse = Invoke-WebRequest -Uri 'http://localhost:8080/_readyz' -UseBasicParsing -TimeoutSec 8
    if ($taskResponse.StatusCode -ne 200 -or $taskResponse.Content.Trim() -ne 'ready') { throw 'Frontend dependencies are not ready.' }
    Write-Output 'PASS frontend readiness, backend and PostgreSQL.'
} finally {
    Pop-Location
}
