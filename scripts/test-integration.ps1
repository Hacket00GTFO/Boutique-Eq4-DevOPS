$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Push-Location $taskRoot
try {
    $taskCompose = @('-f','docker-compose.yml','-f','docker-compose.admin.yml','-f','docker-compose.test.yml')
    $taskDatabaseExists = docker compose @taskCompose exec -T postgres psql -U postgres -tAc "SELECT 1 FROM pg_database WHERE datname='boutique_test'"
    if ($LASTEXITCODE -ne 0) { throw 'PostgreSQL must be running before tests.' }
    if ($taskDatabaseExists -ne '1') {
        docker compose @taskCompose exec -T postgres psql -U postgres -c 'CREATE DATABASE boutique_test'
        if ($LASTEXITCODE -ne 0) { throw 'Cannot create isolated test database.' }
    }
    docker compose @taskCompose run --rm --no-deps integration-tests
    if ($LASTEXITCODE -ne 0) { throw 'Integration tests failed.' }
} finally { Pop-Location }
