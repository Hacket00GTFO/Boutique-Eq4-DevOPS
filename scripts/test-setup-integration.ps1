# Credential bootstrap tests use fake Docker metadata and a disposable directory.
$ErrorActionPreference = 'Stop'
$taskExitCodeBefore = $global:LASTEXITCODE
$taskTempBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath())
$taskFixture = Join-Path $taskTempBase ('boutique-setup-test-' + [guid]::NewGuid().ToString('N'))
$taskScripts = Join-Path $taskFixture 'scripts'
New-Item -ItemType Directory -Path $taskScripts -Force | Out-Null
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'setup-integration.ps1') -Destination $taskScripts
$taskSetup = Join-Path $taskScripts 'setup-integration.ps1'
$taskEnv = Join-Path $taskFixture '.env'
$taskMockState = @{Containers=@(); Volume=$false; Failure=$false}

function docker {
    $global:LASTEXITCODE = 0
    if ($taskMockState.Failure) { $global:LASTEXITCODE = 1; return }
    switch ($args[0]) {
        'ps' { if ($taskMockState.Containers.Count -gt 0) { 'fixture-container' }; return }
        'inspect' { ConvertTo-Json -InputObject @($taskMockState.Containers) -Depth 12; return }
        'volume' { if ($taskMockState.Volume) { 'fixture-postgres-volume' }; return }
        default { throw 'Unexpected Docker operation in bootstrap test.' }
    }
}
function Read-TaskValues {
    $taskResult = @{}
    foreach ($taskLine in Get-Content -LiteralPath $taskEnv) {
        if ($taskLine -match '^([A-Z_]+)=(.*)$') { $taskResult[$Matches[1]] = $Matches[2] }
    }
    return $taskResult
}
function Assert-Task($condition, $message) { if (-not $condition) { throw $message } }
function New-TaskContainer($service, $values) {
    return @{Config=@{Labels=@{'com.docker.compose.service'=$service; 'com.docker.compose.project'='fixture-project'}; Env=@($values)}}
}
try {
    & $taskSetup | Out-Null
    $taskValues = Read-TaskValues
    $taskKeys = @('POSTGRES_PASSWORD','APP_DB_PASSWORD','ADMIN_PASSWORD','ADMIN_API_TOKEN','CATALOG_READ_TOKEN','CHECKOUT_API_TOKEN')
    foreach ($taskKey in $taskKeys) { Assert-Task ($taskValues[$taskKey] -match '^[a-f0-9]{64}$') 'Fresh credential format invalid.' }
    Assert-Task (@($taskKeys | ForEach-Object { $taskValues[$_] } | Select-Object -Unique).Count -eq 6) 'Fresh credentials must be independent.'
    Write-Output 'PASS fresh environment generates independent credentials.'

    $taskBefore = [IO.File]::ReadAllText($taskEnv)
    $taskMockState.Failure = $true
    & $taskSetup | Out-Null
    Assert-Task ([IO.File]::ReadAllText($taskEnv) -ceq $taskBefore) 'Complete .env was modified.'
    $taskMockState.Failure = $false
    Write-Output 'PASS complete environment is preserved without requiring Docker.'

    $taskExpected = @{POSTGRES_PASSWORD=('1'*64); APP_DB_PASSWORD=('2'*64); ADMIN_PASSWORD=('3'*64);
                      ADMIN_API_TOKEN=('4'*64); CATALOG_READ_TOKEN=('5'*64); CHECKOUT_API_TOKEN=('6'*64); ADMIN_EMAIL='fixture@example.invalid'}
    $taskMockState.Containers = @(
        (New-TaskContainer 'postgres' @("POSTGRES_PASSWORD=$($taskExpected.POSTGRES_PASSWORD)", "APP_DB_PASSWORD=$($taskExpected.APP_DB_PASSWORD)")),
        (New-TaskContainer 'migrate' @("ADMIN_EMAIL=$($taskExpected.ADMIN_EMAIL)", "ADMIN_PASSWORD=$($taskExpected.ADMIN_PASSWORD)")),
        (New-TaskContainer 'catalogservice' @("ADMIN_API_TOKEN=$($taskExpected.ADMIN_API_TOKEN)", "CATALOG_READ_TOKEN=$($taskExpected.CATALOG_READ_TOKEN)", "CHECKOUT_API_TOKEN=$($taskExpected.CHECKOUT_API_TOKEN)"))
    )
    $taskMockState.Volume = $true
    # Unix dotfiles are hidden to PowerShell; remove only this disposable fixture.
    Remove-Item -LiteralPath $taskEnv -Force
    & $taskSetup | Out-Null
    $taskRecovered = Read-TaskValues
    foreach ($taskKey in $taskExpected.Keys) { Assert-Task ($taskRecovered[$taskKey] -ceq $taskExpected[$taskKey]) "Missing .env did not recover $taskKey." }
    Write-Output 'PASS missing environment recovers all original container credentials.'

    [IO.File]::WriteAllLines($taskEnv, @('# Preserve this comment', 'CUSTOM_SETTING=keep', "POSTGRES_PASSWORD=$($taskExpected.POSTGRES_PASSWORD)", 'ADMIN_PASSWORD='))
    & $taskSetup | Out-Null
    $taskRecovered = Read-TaskValues
    foreach ($taskKey in $taskExpected.Keys) { Assert-Task ($taskRecovered[$taskKey] -ceq $taskExpected[$taskKey]) 'Partial environment recovery changed credentials.' }
    Assert-Task ($taskRecovered.CUSTOM_SETTING -eq 'keep' -and [IO.File]::ReadAllText($taskEnv).Contains('# Preserve this comment')) 'Recovery removed custom settings.'
    Write-Output 'PASS partial environment is repaired while preserving custom settings.'

    $taskMockState.Containers = @()
    [IO.File]::WriteAllLines($taskEnv, @('CUSTOM_SETTING=keep'))
    $taskBefore = [IO.File]::ReadAllText($taskEnv)
    $taskBlocked = $false
    try { & $taskSetup | Out-Null } catch { $taskBlocked = $_.Exception.Message -like 'Existing PostgreSQL data detected.*' }
    Assert-Task ($taskBlocked -and [IO.File]::ReadAllText($taskEnv) -ceq $taskBefore) 'Existing volume must block unrecoverable credential generation.'
    Write-Output 'PASS existing PostgreSQL data prevents unsafe password regeneration.'

    $taskMockState.Volume = $false
    $taskMockState.Failure = $true
    $taskBlocked = $false
    try { & $taskSetup | Out-Null } catch { $taskBlocked = $_.Exception.Message -like 'Start Docker Desktop*' }
    Assert-Task ($taskBlocked -and [IO.File]::ReadAllText($taskEnv) -ceq $taskBefore) 'Docker inspection failure must preserve the environment.'
    Write-Output 'PASS Docker failure leaves existing configuration untouched.'
} finally {
    $global:LASTEXITCODE = [int]$taskExitCodeBefore
    $taskResolved = [IO.Path]::GetFullPath($taskFixture)
    if ([IO.Path]::GetDirectoryName($taskResolved) -eq $taskTempBase.TrimEnd([char]92, [char]47) -and [IO.Path]::GetFileName($taskResolved).StartsWith('boutique-setup-test-')) {
        Remove-Item -LiteralPath $taskResolved -Recurse -Force
    } else { throw 'Refusing cleanup outside the disposable test directory.' }
}
