# modal-3D Client

Windows-first desktop client for local 2D preprocessing and Modal-backed 3D generation.

## Data flow

```text
Source image (local only)
      │
      ├── preprocessing starts automatically after import
      ▼
rembg / birefnet-general / CPU
      │
      ▼
Full RGBA
      │
      ▼
foreground bbox
      │
      ▼
preserve aspect ratio → scale → transparent letterbox → center
      │
      ▼
1024×1024 Canonical PNG, 8-bit RGBA
      │
      └── upload once when generation starts
             │
             ▼
       modal-3D Volume
             │
             ├── FastSAM3D++
             ├── Hermite-TRELLIS2++
             ├── Hunyuan2.1++
             └── Pixal3D
             │
             ▼
            GLB
```

The original image, rembg matte, cropping and canonicalization stay on the user's machine. Modal receives only the final canonical RGBA used for generation.

After global rembg matting, the client performs local 8-connected component analysis on Alpha. All meaningful components are selected by default, so the canonical output remains identical to the complete rembg foreground. Users can deselect individual components or drag a box over the matte to keep matching components; only then is Alpha filtered and the union bounding box re-letterboxed locally.

## Local preprocessing

- Engine: `rembg`
- Model: `birefnet-general`
- Provider preference: CPU by default; optional Windows GPU through ONNXRuntime DirectML
- GPU safety: if the GPU provider cannot initialize, rembg falls back to CPU without sending the image to cloud
- GPU runtime: Windows uses ONNXRuntime DirectML without bundling CUDA/cuDNN; Linux and macOS currently use CPU
- Model cache: application data directory under `rembg/`
- First-run model preparation: explicit progress for the ~973 MB `birefnet-general` model
- Optional prefetch: Settings can prepare/verify the model before any image is imported
- Download recovery: `.partial` files are resumed with HTTP Range requests after interruption
- Integrity: the downloaded model is checked against rembg's pinned MD5 before an ONNX session is created
- Canonical contract: PNG, 1024×1024, 8-bit RGBA
- Component rule: 8-connected Alpha analysis; default all selected; tiny fragments remain preserved while all components are selected
- Interaction: checkbox/click selection and drag-box component selection are local-only
- Box modifiers: drag replaces the selection, `Shift+drag` adds components, and `Alt+drag` removes components
- Selection history: up to 50 local selection states with Undo/Redo and `Ctrl/Cmd+Z` / `Ctrl/Cmd+Shift+Z`
- Selection performance: decoded matte/label data uses a 64 MiB process-local LRU cache; oversized images automatically bypass the cache
- Interactive canonical PNGs use fast compression so selection updates do not spend most of their time in PNG compression
- Geometry rule: preserve original aspect ratio; use transparent letterbox padding to center the remaining foreground

The client does not use BRIA RMBG-2.0. The current rembg session is `birefnet-general`.

## Cloud generation

The public `modal-3D` repository is the cloud inference layer. It does not perform background removal, segmentation, subject selection, cropping or canonicalization. It validates and consumes only the standard canonical input stored in the shared `modal-3d-artifacts` Volume.

Model discovery is capability-driven. The client does not hard-code the active model registry. Current workers are FastSAM3D++, Hermite-TRELLIS2++, Hunyuan2.1++ and Pixal3D.

## Project workspace

Each imported source image creates a local project in app-data and immediately starts local rembg preprocessing. If preprocessing fails, the source project remains intact and can be retried without re-importing. The project stores:

- original source image
- local `matte.png`
- local `canonical.png`
- model/profile selection
- Modal FunctionCall job state
- validated GLB metadata

The canonical image is uploaded lazily only when generation starts. Its SHA-256 and byte count are verified before the remote path is persisted.

## Credentials

Modal credentials are handled by the local Agent. On Windows, the desktop client can store them in Windows Credential Manager. Credentials are not reloaded into the React UI after restart.

## Archived SAM 3.1 implementation

The retired SAM 3.1 cloud/local preprocessing implementation is preserved under `archive/sam3_1/`. It is not imported, packaged, deployed or exposed by the active client runtime.

## Development

```bash
npm ci
uv sync --locked
npm run build
uv run pytest -q
```

Windows packaging and smoke tests are performed by GitHub Actions. The release workflow builds NSIS and MSI installers and publishes SHA-256 checksums.

See `docs/PRODUCT_ARCHITECTURE.md` for the active architecture.


## Local development lock discipline

The repository requires `uv >=0.12.5,<0.13` (see `uv.toml`). On a clean checkout use `uv sync --locked --group dev --group build`. If a locked sync says the lockfile needs updating, update uv and check `git status --short` before regenerating the lock; Windows CI verifies both `uv lock --check` and locked sync.
