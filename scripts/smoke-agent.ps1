param([Parameter(Mandatory = $true)][string]$Agent)

$ErrorActionPreference = "Stop"
$resolvedAgent = (Resolve-Path -LiteralPath $Agent).Path
$token = [Guid]::NewGuid().ToString("N")
$workRoot = Join-Path $env:TEMP "modal-inference-hub-smoke-$token"
$handshake = Join-Path $workRoot "agent.port"
$dataDir = Join-Path $workRoot "data"
New-Item -ItemType Directory -Force -Path $workRoot, $dataDir | Out-Null
$env:MODAL_HUB_SESSION_TOKEN = $token
$env:MODAL_HUB_HANDSHAKE = $handshake
$env:MODAL_HUB_DATA_DIR = $dataDir
$process = Start-Process -FilePath $resolvedAgent -WindowStyle Hidden -PassThru
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(60)
    while (-not (Test-Path -LiteralPath $handshake) -and [DateTime]::UtcNow -lt $deadline) {
        if ($process.HasExited) { throw "Hub Agent exited before the handshake." }
        Start-Sleep -Milliseconds 100
    }
    if (-not (Test-Path -LiteralPath $handshake)) { throw "Hub Agent handshake timed out." }
    $port = Get-Content -Raw -LiteralPath $handshake
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:$port/health" -Headers @{ "X-Modal-Hub-Session" = $token } -TimeoutSec 10
    if (-not $health.ok) { throw "Hub Agent health check failed." }
    Write-Host "Hub Agent build and smoke check passed: $resolvedAgent"
}
finally {
    if (-not $process.HasExited) { & taskkill /PID $process.Id /T /F | Out-Null }
    Remove-Item Env:MODAL_HUB_SESSION_TOKEN -ErrorAction SilentlyContinue
    Remove-Item Env:MODAL_HUB_HANDSHAKE -ErrorAction SilentlyContinue
    Remove-Item Env:MODAL_HUB_DATA_DIR -ErrorAction SilentlyContinue
    if (Test-Path -LiteralPath $workRoot) {
        $resolvedWork = (Resolve-Path -LiteralPath $workRoot).Path
        $resolvedTemp = (Resolve-Path -LiteralPath $env:TEMP).Path
        if (-not $resolvedWork.StartsWith($resolvedTemp, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean a smoke directory outside TEMP: $resolvedWork"
        }
        Remove-Item -LiteralPath $resolvedWork -Recurse -Force
    }
}
