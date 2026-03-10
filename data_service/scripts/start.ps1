<#
PowerShell startup script: starts docker compose and optionally initializes Postgres schema.
Usage (PowerShell):
  .\scripts\start.ps1
#>
param()

$Root = Split-Path -Parent $PSScriptRoot
# walk up until we find docker-compose.yml (or stop at drive root)
while (-not (Test-Path (Join-Path $Root 'docker-compose.yml')) -and ($Root -ne (Split-Path $Root -Parent))) {
    $Root = Split-Path -Parent $Root
}
$EnvFile = Join-Path $Root '.env'

if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        if ($_ -and -not ($_ -match '^\s*#')) {
            $parts = $_ -split '=', 2
            if ($parts.Count -eq 2) {
                $name = $parts[0].Trim()
                $value = $parts[1].Trim()
                # Remove surrounding quotes
                $value = $value.Trim("'\"")
                Set-Item -Path Env:$name -Value $value
            }
        }
    }
} else {
    Write-Warning ".env not found, using current environment variables"
}

$postgresContainer = $env:POSTGRES_CONTAINER_NAME
if (-not $postgresContainer) { $postgresContainer = 'sttt-postgres' }
$runDbInit = $env:RUN_DB_INIT
if (-not $runDbInit) { $runDbInit = 'false' }

Write-Host "Starting docker compose services..."
docker compose up -d --remove-orphans

if ($runDbInit.ToLower() -in @('true','1','yes')) {
    Write-Host "RUN_DB_INIT is true — initializing database schema (destructive)."
    Write-Host "Waiting for Postgres to be ready..."
    $attempts = 0
    $maxAttempts = 30
    while ($true) {
        docker exec $postgresContainer pg_isready -U $env:POSTGRES_USER -d $env:POSTGRES_DB > $null 2>&1
        if ($LASTEXITCODE -eq 0) { break }
        $attempts++
        if ($attempts -ge $maxAttempts) { Write-Error "Postgres did not become ready after $maxAttempts attempts."; exit 1 }
        Start-Sleep -Seconds 2
    }

    # try some candidate schema locations
    $candidates = @(
        (Join-Path $Root 'data_service\schema.sql'),
        (Join-Path $Root 'schema.sql'),
        (Join-Path $PSScriptRoot '..\data_service\schema.sql')
    ) | ForEach-Object { (Resolve-Path -Path $_ -ErrorAction SilentlyContinue).ProviderPath } | Where-Object { $_ }

    if (-not $candidates) { Write-Error "Schema file not found in candidate locations."; exit 1 }

    $schemaFile = $candidates[0]

    # Copy schema into container then run psql -f
    docker cp $schemaFile ("$postgresContainer:/tmp/schema.sql")
    docker exec $postgresContainer psql -U $env:POSTGRES_USER -d $env:POSTGRES_DB -f /tmp/schema.sql -v ON_ERROR_STOP=1
    Write-Host "Schema applied successfully."
} else {
    Write-Host "RUN_DB_INIT is not enabled — skipping schema initialization."
}

Write-Host "Done."
