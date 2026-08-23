$ErrorActionPreference = "Stop"

uv sync --upgrade-package modal
uv run --with pyinstaller pyinstaller `
  --noconfirm `
  --clean `
  --onefile `
  --name modal-3d-agent `
  --paths . `
  --collect-all modal `
  --distpath build/agent-dist `
  --workpath build/agent-work `
  --specpath build/agent-spec `
  agent/server.py

New-Item -ItemType Directory -Force src-tauri/resources | Out-Null
Copy-Item build/agent-dist/modal-3d-agent.exe src-tauri/resources/modal-3d-agent.exe -Force
Write-Host "Agent:" (Get-Item src-tauri/resources/modal-3d-agent.exe).Length "bytes"
