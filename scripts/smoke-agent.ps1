$ErrorActionPreference = "Stop"

$agent = Resolve-Path "src-tauri/resources/modal-3d-agent.exe"
$handshake = Join-Path $env:TEMP ("modal-3d-agent-smoke-" + [guid]::NewGuid().ToString("N") + ".port")
$token = [Convert]::ToHexString([Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).ToLowerInvariant()
$env:MODAL_3D_AGENT_TOKEN = $token
$env:MODAL_3D_AGENT_HANDSHAKE = $handshake

$process = Start-Process -FilePath $agent -PassThru -WindowStyle Hidden
try {
  $deadline = (Get-Date).AddSeconds(30)
  while (-not (Test-Path $handshake)) {
    if ($process.HasExited) { throw "Agent exited during startup: $($process.ExitCode)" }
    if ((Get-Date) -ge $deadline) { throw "Agent handshake timed out" }
    Start-Sleep -Milliseconds 100
  }

  $port = [int](Get-Content $handshake -Raw).Trim()
  $base = "http://127.0.0.1:$port"
  $unauth = Invoke-WebRequest "$base/health" -SkipHttpErrorCheck
  if ($unauth.StatusCode -ne 401) { throw "Expected unauthenticated 401, got $($unauth.StatusCode)" }

  $headers = @{ "X-Modal-3D-Session" = $token }
  $health = Invoke-RestMethod "$base/health" -Headers $headers
  if (-not $health.ok) { throw "Agent health check failed" }

  $bad = Invoke-WebRequest "$base/modal/connect" `
    -Method Post `
    -Headers $headers `
    -ContentType "application/json" `
    -Body '{"token_id":"bad","token_secret":"bad"}' `
    -SkipHttpErrorCheck
  if ($bad.StatusCode -ne 401) { throw "Expected invalid Modal credentials to return 401, got $($bad.StatusCode)" }
  if ($bad.Content -notmatch "Modal authentication failed") { throw "Unexpected Modal auth error body" }

  Write-Host "Agent smoke OK on random port $port"
}
finally {
  if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
  Remove-Item $handshake -ErrorAction SilentlyContinue
  Remove-Item Env:MODAL_3D_AGENT_TOKEN -ErrorAction SilentlyContinue
  Remove-Item Env:MODAL_3D_AGENT_HANDSHAKE -ErrorAction SilentlyContinue
}
