param(
    [switch]$Workers,
    [switch]$Down
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker Desktop is required for the local development environment."
}
& docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Docker Compose v2 is required (the 'docker compose' command is unavailable)."
}

if (-not (Test-Path .env.local)) {
    Copy-Item .env.local.example .env.local
    $jwt = -join ((1..48) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
    $actionsKey = -join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
    $litellm = -join ((1..32) | ForEach-Object { '{0:x}' -f (Get-Random -Maximum 16) })
    (Get-Content .env.local) `
        -replace 'replace-with-a-random-local-jwt-secret', $jwt `
        -replace 'replace-with-a-random-local-actions-master-key', $actionsKey `
        -replace 'replace-with-a-random-local-litellm-key', "sk-local-$litellm" `
        -replace 'replace-with-a-local-root-password', "local-root-$litellm" `
        -replace 'replace-with-a-local-database-password', "local-db-$litellm" |
        Set-Content .env.local -Encoding utf8
    Write-Host "Created .env.local with generated local-only secrets." -ForegroundColor Green
}

$composeArgs = @("compose", "--env-file", ".env.local", "-f", "deploy/compose/dev/compose.yml")
$backendPort = 8000
$backendPortSetting = Get-Content .env.local |
    Where-Object { $_ -match '^DEV_BACKEND_PORT=(\d+)$' } |
    Select-Object -First 1
if ($backendPortSetting -match '^DEV_BACKEND_PORT=(\d+)$') {
    $backendPort = [int]$Matches[1]
}

function Show-BackendFailureDiagnostics {
    Write-Host "[ERROR] Backend is unhealthy or did not become ready." -ForegroundColor Red
    $backendContainer = (& docker @composeArgs ps -q backend).Trim()
    if ($backendContainer) {
        $backendState = (& docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' $backendContainer).Trim()
        Write-Host "Backend state: $backendState" -ForegroundColor Red
    }
    & docker @composeArgs ps backend
    Write-Host "Recent backend logs:" -ForegroundColor Yellow
    & docker @composeArgs logs --tail 120 backend
}

if ($Workers) { $composeArgs += @("--profile", "workers") }
if ($Down) {
    & docker @composeArgs down
    if ($LASTEXITCODE -ne 0) {
        throw "Local development services failed to stop."
    }
} else {
    & docker @composeArgs up --build -d
    if ($LASTEXITCODE -ne 0) {
        throw "Local development services failed to start."
    }
    $seeded = $false
    for ($attempt = 1; $attempt -le 12; $attempt++) {
        & docker @composeArgs exec -T backend python database/seed_local_demo.py
        if ($LASTEXITCODE -eq 0) {
            $seeded = $true
            break
        }
        Start-Sleep -Seconds 5
    }
    if (-not $seeded) {
        Show-BackendFailureDiagnostics
        throw "Local demo data seeding failed. Check the backend container logs."
    }

    $healthUrl = "http://127.0.0.1:$backendPort/health"
    $healthy = $false
    for ($attempt = 1; $attempt -le 24; $attempt++) {
        try {
            $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 3
            if ($response.StatusCode -eq 200) {
                $healthy = $true
                break
            }
        } catch {
            # The container may still be starting or restarting; diagnostics
            # below will expose the actual state after the bounded wait.
        }
        Start-Sleep -Seconds 5
    }
    if (-not $healthy) {
        Show-BackendFailureDiagnostics
        throw "Local backend health check failed: $healthUrl"
    }

    Write-Host "Dashboard: http://localhost:3000" -ForegroundColor Green
    Write-Host "API docs:  http://localhost:$backendPort/docs" -ForegroundColor Green
    Write-Host "Demo login: admin / admin123" -ForegroundColor Yellow
}
