# Architecture

`modal-3D-client` is the user-facing client. Cloud model code stays in `modal-3D`.

```text
React / TypeScript
        │
      Tauri 2
        │
  Local Python Agent
     ├─ hardware detection
     ├─ Modal credentials/client
     ├─ optional local SAM 3.1
     └─ cloud SAM / 3D workers
```

## Boundaries

- UI never owns Modal credentials.
- Tauri owns desktop lifecycle and encrypted credential storage.
- The Python Agent owns AI/runtime integrations.
- The Agent binds only to `127.0.0.1` on a random port; Tauri supplies a per-launch session token.
- Local SAM 3.1 is optional; cloud fallback uses the same UI contract.
- `modal-3D` remains the source of truth for cloud workers and their API contract.
- Large models are never bundled into the installer.

## First milestones

1. ✅ Tauri starts/stops a localhost Agent sidecar on a random port with a per-launch session token.
2. ✅ Connect Modal Token ID/Secret through the local Agent; credentials stay in Agent memory for the session.
3. Add encrypted credential persistence without exposing secrets to frontend storage.
4. Add `SAM: Auto / Local / Cloud` with hardware detection.
5. Add upload → select object → confirm canonical RGBA → choose 3D profile → generate.
6. Reuse the React client for a later Web build.
