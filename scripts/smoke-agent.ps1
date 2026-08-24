$ErrorActionPreference = "Stop"

$agent = (Get-ChildItem -LiteralPath "src-tauri/binaries" -Filter "modal-3d-agent-*.exe" -File |
  Select-Object -First 1).FullName
if (-not $agent) { throw "找不到已构建的本地代理可执行文件。" }
$handshake = Join-Path $env:TEMP ("modal-3d-agent-smoke-" + [guid]::NewGuid().ToString("N") + ".port")
$dataDir = Join-Path $env:TEMP ("modal-3d-agent-smoke-data-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $dataDir | Out-Null
[byte[]]$tokenBytes = New-Object byte[] 32
[Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($tokenBytes)
$token = [BitConverter]::ToString($tokenBytes).Replace("-", "").ToLowerInvariant()
$env:MODAL_3D_AGENT_TOKEN = $token
$env:MODAL_3D_AGENT_HANDSHAKE = $handshake
$env:MODAL_3D_AGENT_DATA_DIR = $dataDir

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

  $models = Invoke-RestMethod "$base/v1/models" -Headers $headers
  if ($models.Count -ne 4) { throw "模型 registry 应返回 4 个模型，实际为 $($models.Count)。" }

  $capabilities = Invoke-RestMethod "$base/v1/capabilities" -Headers $headers
  if ($capabilities.sam.mode -ne "auto") { throw "默认 SAM 模式应为 auto。" }

  $localSam = Invoke-RestMethod "$base/v1/local-sam/status" -Headers $headers
  if ($localSam.runtime_installed) { throw "CI 临时目录不应预装 Local SAM runtime。" }
  $localInstall = Invoke-HttpAllowError "$base/v1/local-sam/install" "Post" $headers
  if ($localInstall.StatusCode -ne 409) { throw "未连接 Modal 时 Local SAM 安装应返回 409，实际为 $($localInstall.StatusCode)。" }

  $image = Join-Path $dataDir "smoke.png"
  [IO.File]::WriteAllBytes($image, [byte[]](1, 2, 3, 4))
  $project = Invoke-RestMethod "$base/v1/projects" -Method Post -Headers $headers -Form @{ file = Get-Item $image }
  if ($project.status -ne "draft") { throw "新 Project 状态应为 draft，实际为 $($project.status)。" }

  $projects = Invoke-RestMethod "$base/v1/projects" -Headers $headers
  if ($projects.Count -ne 1 -or $projects[0].id -ne $project.id) { throw "Project 列表未返回刚创建的项目。" }

  $deleted = Invoke-RestMethod "$base/v1/projects/$($project.id)" -Method Delete -Headers $headers
  if ($deleted.deleted -ne $project.id) { throw "Project 删除返回的 ID 不匹配。" }
  $missing = Invoke-HttpAllowError "$base/v1/projects/$($project.id)" "Get" $headers
  if ($missing.StatusCode -ne 404) { throw "已删除 Project 应返回 404，实际为 $($missing.StatusCode)。" }

  Write-Host "本地代理冒烟测试通过，随机端口：$port"
}
finally {
  if (-not $process.HasExited) { Stop-Process -Id $process.Id -Force }
  Remove-Item $handshake -ErrorAction SilentlyContinue
  Remove-Item Env:MODAL_3D_AGENT_TOKEN -ErrorAction SilentlyContinue
  Remove-Item Env:MODAL_3D_AGENT_HANDSHAKE -ErrorAction SilentlyContinue
  Remove-Item Env:MODAL_3D_AGENT_DATA_DIR -ErrorAction SilentlyContinue
  Remove-Item $dataDir -Recurse -Force -ErrorAction SilentlyContinue
}
