$ErrorActionPreference = "Stop"

$agent = (Get-ChildItem -LiteralPath "src-tauri/binaries" -Filter "modal-3d-agent-*.exe" -File |
  Select-Object -First 1).FullName
if (-not $agent) { throw "找不到已构建的本地代理可执行文件。" }
$handshake = Join-Path $env:TEMP ("modal-3d-agent-smoke-" + [guid]::NewGuid().ToString("N") + ".port")
[byte[]]$tokenBytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($tokenBytes)
$token = [BitConverter]::ToString($tokenBytes).Replace("-", "").ToLowerInvariant()
$env:MODAL_3D_AGENT_TOKEN = $token
$env:MODAL_3D_AGENT_HANDSHAKE = $handshake

function Invoke-HttpAllowError {
  param(
    [string]$Uri,
    [string]$Method = "Get",
    [hashtable]$Headers,
    [string]$Body
  )
  $parameters = @{ Uri = $Uri; Method = $Method; Headers = $Headers; UseBasicParsing = $true }
  if ((Get-Command Invoke-WebRequest).Parameters.ContainsKey("SkipHttpErrorCheck")) {
    $parameters.SkipHttpErrorCheck = $true
  }
  if ($Body) {
    $parameters.ContentType = "application/json"
    $parameters.Body = $Body
  }
  try {
    $response = Invoke-WebRequest @parameters
    return [pscustomobject]@{ StatusCode = [int]$response.StatusCode; Content = $response.Content }
  } catch {
    if (-not $_.Exception.Response) { throw }
    $response = $_.Exception.Response
    $reader = New-Object System.IO.StreamReader($response.GetResponseStream())
    try { $content = $reader.ReadToEnd() } finally { $reader.Dispose() }
    return [pscustomobject]@{ StatusCode = [int]$response.StatusCode; Content = $content }
  }
}

$process = Start-Process -FilePath $agent -PassThru -WindowStyle Hidden
try {
  $deadline = (Get-Date).AddSeconds(30)
  while (-not (Test-Path $handshake)) {
    if ($process.HasExited) { throw "本地代理在启动期间退出：$($process.ExitCode)" }
    if ((Get-Date) -ge $deadline) { throw "本地代理启动握手超时。" }
    Start-Sleep -Milliseconds 100
  }

  $port = [int](Get-Content $handshake -Raw).Trim()
  $base = "http://127.0.0.1:$port"
  $unauth = Invoke-HttpAllowError "$base/health"
  if ($unauth.StatusCode -ne 401) { throw "未授权请求应返回 401，实际为 $($unauth.StatusCode)。" }

  $headers = @{ "X-Modal-3D-Session" = $token }
  $health = Invoke-RestMethod "$base/health" -Headers $headers
  if (-not $health.ok) { throw "本地代理健康检查失败。" }

  $bad = Invoke-HttpAllowError "$base/modal/connect" "Post" $headers '{"token_id":"bad","token_secret":"bad"}'
  if ($bad.StatusCode -ne 401) { throw "无效 Modal 凭据应返回 401，实际为 $($bad.StatusCode)。" }

  Write-Host "本地代理冒烟测试通过，随机端口：$port"
}
finally {
  if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
  Remove-Item $handshake -ErrorAction SilentlyContinue
  Remove-Item Env:MODAL_3D_AGENT_TOKEN -ErrorAction SilentlyContinue
  Remove-Item Env:MODAL_3D_AGENT_HANDSHAKE -ErrorAction SilentlyContinue
}
