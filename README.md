# modal-3D-client

Windows-first hybrid local/cloud client for `modal-3D`.

## Stack

- Tauri 2
- React + TypeScript
- Python 3.12 + FastAPI local agent
- `uv` for Python environments
- latest `modal[api-proxy-support]`

Cloud model workers remain in the private `modal-3D` repository. Local SAM 3.1 will be optional and is not installed by the base client.

## Development

Frontend:

```bash
npm install
npm run dev
```

Local agent:

```bash
uv sync --upgrade-package modal
uv run uvicorn agent.main:app --host 127.0.0.1 --port 8765
```

Port `8765` is only a manual development example. Tauri-managed Agent sessions already use a random loopback port plus an ephemeral session token.

The Modal dependency is intentionally unpinned. `--upgrade-package modal` keeps the environment on the latest available Modal release while the rest of the environment remains lockable.

See `ARCHITECTURE.md` for boundaries and milestones.
