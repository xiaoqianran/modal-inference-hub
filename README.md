# modal-3D Client

Windows-first desktop client for **local 2D preprocessing** and Modal-backed 3D generation.

The active product boundary is simple:

```text
local machine                                    Modal cloud

source image
    │
    ▼
rembg / birefnet-general-lite
CPU or Windows NVIDIA CUDA GPU
    │
    ▼
Full RGBA
    │
    ▼
8-connected Alpha components
    │
    ├── click / checkbox
    ├── drag = replace selection
    ├── Shift+drag = add
    ├── Alt+drag = remove
    └── Undo / Redo
    │
    ▼
active-selection RGBA
    │
    ▼
union bbox
    │
    ▼
preserve aspect ratio
transparent letterbox + center
    │
    ▼
1024×1024 / 8-bit RGBA PNG
Canonical
    │
    │ upload once when 3D generation starts
    └──────────────────────────────────────────► modal-3D Volume
                                                     │
                                                     ├── FastSAM3D++
                                                     ├── Hermite-TRELLIS2++
                                                     ├── Hunyuan2.1++
                                                     └── Pixal3D
                                                     │
                                                     ▼
                                                    GLB
```

The original image, rembg matte, component selection, crop and canonicalization stay on the user's machine. Modal receives only the final Canonical RGBA used for generation.

## Local preprocessing

- Engine: `rembg`
- Model: `birefnet-general-lite`
- Default provider: Windows NVIDIA GPU when CUDA is available, with automatic CPU fallback
- Windows GPU provider: ONNXRuntime CUDA（cuDNN）
- CUDA 随 Agent 打包 NVIDIA CUDA / cuDNN 运行库，仅支持 NVIDIA GPU
- If GPU initialization fails, preprocessing falls back to CPU
- Linux and macOS currently use CPU
- Canonical contract: PNG, 1024×1024, 8-bit RGBA
- Geometry: preserve source aspect ratio and center with transparent letterbox padding

Importing an image automatically creates a local project and starts preprocessing. If preprocessing fails, the original project remains available and can be retried without importing the image again.

### First-run model preparation

`birefnet-general-lite` is approximately 224 MB. The Agent owns the model download instead of leaving it as an opaque rembg operation.

```text
idle
 │
 ▼
downloading ───────────────┐
 │                         │
 ▼                         │
verifying                  │
 │                         │
 ├── checksum OK ──► ready │
 │                         │
 └── failure ──────► failed
                         │
                         └── retry / HTTP Range resume
```

The client provides:

- byte-level download progress
- downloaded / total MiB display
- `.partial` files for interrupted downloads
- HTTP Range resume on retry
- pinned MD5 verification before ONNX session creation
- corrupt completed partials are deleted instead of promoted
- optional **Prepare model** action in Settings before importing any image

The model cache lives under the application's local data directory in `rembg/`.

## Multi-object foreground selection

After global rembg matting, the client performs local **8-connected Alpha component analysis**.

All meaningful components are selected by default, so the initial Canonical output remains equivalent to the complete rembg foreground. Tiny Alpha fragments are not exposed as individual UI objects while the default all-selected result still preserves them.

Supported editing:

```text
click / checkbox       toggle one component
Drag                   replace selection
Shift + Drag           add matched components
Alt + Drag             remove matched components
Ctrl/Cmd + Z           undo
Ctrl/Cmd + Shift + Z   redo
```

The client keeps up to 50 local selection-history states. At least one foreground component must remain selected.

Selection edits do **not** rerun rembg and do **not** call Modal. They update local `selection.png`, recompute the union bbox and regenerate the Canonical PNG locally.

For interaction performance, decoded matte/label data uses a bounded 64 MiB process-local LRU cache. Oversized images automatically bypass that cache instead of growing Agent memory without bound.

## Cloud generation

The separate `modal-3D` repository is the cloud inference layer. It does not perform background removal, segmentation, subject selection, cropping or Canonical generation.

The client discovers workers through the cloud capability registry rather than maintaining a hard-coded active model list. Current workers are:

- FastSAM3D++
- Hermite-TRELLIS2++
- Hunyuan2.1++
- Pixal3D

The Canonical PNG is uploaded lazily only when generation starts. The local SHA-256 and byte count are verified before the remote path is persisted.

## Local project workspace

Each project keeps local preprocessing state under the application data directory, including:

```text
project/
├── source.*          original image
├── matte.png         complete rembg RGBA
├── selection.png     current selected RGBA in source coordinates
├── canonical.png     current 1024×1024 Canonical RGBA
└── components.json   component metadata + selected component IDs
```

The project database also tracks model/profile choice, Modal FunctionCall job state, optional remote Canonical path and validated GLB metadata. Every generation is appended to a per-project model history, so earlier successful GLBs remain selectable after image edits or later generation attempts.

Old projects that predate `selection.png` can rebuild it locally from `matte.png` plus saved component state; rembg does not have to run again.

## Credentials

Modal credentials are handled by the local Agent. On Windows, the desktop client can store them in Windows Credential Manager. Credentials are not reloaded into the React UI after restart.

## Windows development

### Required tool versions

The repository currently expects:

```text
Node.js   24.x in CI
Python    3.12
uv        >=0.12.5,<0.13
Rust      stable
```

The uv requirement is enforced by `uv.toml`. Windows CI uses uv `0.12.5` and performs a real locked install.

### Clean checkout / update

PowerShell:

```powershell
git pull

uv --version
npm ci
uv sync --locked --group dev --group build

npm run build
uv run pytest -q
```

For a clean environment rebuild:

```powershell
Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue
uv sync --locked --group dev --group build
```

Do not run `uv lock` unless you intentionally changed Python dependencies in `pyproject.toml`.

### If `uv sync --locked` says the lockfile needs updating

First inspect the workspace instead of regenerating the lock immediately:

```powershell
uv --version
git status --short
git diff -- pyproject.toml uv.lock
```

Make sure uv satisfies:

```text
>=0.12.5,<0.13
```

If `pyproject.toml` / `uv.lock` were changed unintentionally, restore them and rebuild the environment:

```powershell
git restore pyproject.toml uv.lock
git pull
Remove-Item -Recurse -Force .venv -ErrorAction SilentlyContinue
uv sync --locked --group dev --group build
```

The committed lockfile is cross-platform. It resolves the platform-specific ONNXRuntime dependency as:

```text
Windows  -> onnxruntime-gpu[cuda,cudnn] 1.24.x
Linux    -> onnxruntime 1.25.x
macOS    -> onnxruntime 1.25.x
```

So a lockfile error on an otherwise clean checkout should be treated as an environment/version issue first, not as a reason to casually regenerate `uv.lock`.

## Validation and Windows packaging

The Windows workflow validates the same locked environment used for development:

```text
npm ci
npm run build

uv python install 3.12
uv lock --check
uv sync --locked --group dev --group build
python compileall
Python unit tests
PyInstaller Agent build
Agent smoke test

cargo fmt --check
cargo test
cargo check
Tauri NSIS build
```

The normal Windows CI uploads an NSIS installer. The release workflow builds both NSIS and MSI installers and publishes SHA-256 checksums.

## Archived SAM 3.1 implementation

The retired SAM 3.1 cloud/local preprocessing implementation is preserved under `archive/sam3_1/`.

It is not imported, packaged, deployed or exposed by the active runtime.

## Architecture reference

See `docs/PRODUCT_ARCHITECTURE.md` for the detailed active architecture and cloud/client boundary.
