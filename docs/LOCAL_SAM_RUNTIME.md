# Local SAM 3.1 Runtime

## Boundary

Local SAM is an optional Windows runtime, not part of the main PyInstaller Agent.

```text
React
  -> Python Agent
     -> SAM Provider Router
        -> Cloud SAM (Modal)
        -> Local SAM runtime (optional child process)
```

The main Agent continues to own projects, Modal credentials, artifact uploads and 3D generation jobs. The Local SAM process owns only GPU segmentation state and local SAM selection files.

This avoids placing Torch, CUDA runtime wheels and the multi-gigabyte SAM checkpoint inside the desktop installer.

## Pinned inference stack

The local runtime intentionally matches the production Cloud SAM worker:

- Python 3.12
- Torch 2.10.0
- torchvision 0.25.0
- CUDA 12.8 wheels
- `facebookresearch/sam3@8f0b7f4d4e7eda2ed606ebde6702c93359ad01da`
- `facebook/sam3.1@daa63191845a41281374e725f4c9e51c7a824460`
- checkpoint `sam3.1_multiplex.pt`

The image-backbone adapter is the same three-effective-FPN-level layout already validated on the cloud worker. The legacy `predict_inst` path remains intentionally unsupported.

## Distribution

The GitHub-built bootstrap contains only:

- official Python 3.12 embedded runtime;
- pinned `uv.exe`;
- pinned SAM3 source;
- `local_sam_runtime` server/engine;
- install script and manifest.

The bootstrap does **not** contain Torch/CUDA wheels or model weights. Its Windows GitHub workflow installs the exact pinned wheel set into a disposable validation copy and runs an import smoke before uploading the bootstrap artifact.

At client install time, the bootstrap install script downloads only prebuilt wheels. It must never compile CUDA/native dependencies on the user's machine.

## Checkpoint source

The desktop user already authenticates to the same Modal workspace, so Local SAM reuses that trust boundary instead of introducing a Hugging Face token.

The Agent downloads:

```text
Modal Volume: modal-3d-sam31-weights
  sam31/sam3.1_multiplex.pt  3,502,755,717 bytes
  sam31/config.json                 25,843 bytes
```

The checkpoint is stored under the client's app-data Local SAM directory and is not duplicated in GitHub Releases or the NSIS installer.

## Runtime protocol

The runtime binds only to `127.0.0.1` on a random port and requires an independent session token in `X-Modal-3D-Local-SAM`.

It exposes four internal calls:

- `GET /health`
- `POST /segment`
- `POST /refine`
- `POST /materialize`

`segment` accepts only source paths inside the Project Workspace. Images are not copied through React or Tauri IPC.

Selection data is local and persistent:

```text
local-sam/
  scenes/<sha256>/input.bin
  selections/<scene>/<selection>/
    result.json
    masks.bin
    c00/
      mask.png
      canonical.png
```

The candidate/result schema intentionally matches Cloud SAM so React does not need a second interaction model.

## Canonical handoff

Local materialization creates a local canonical PNG. The main Agent then uploads that one PNG to `modal-3d-artifacts`, after which all four 3D workers use the same existing cloud path contract.

```text
Local SAM
  -> local canonical.png
  -> Agent uploads canonical once
  -> modal-3d-artifacts path
  -> FastSAM / Hermite / Hunyuan / Pixal3D
```

No 3D worker needs Local-SAM-specific logic.

## Availability rules

`Auto` may choose Local only after all of these are true:

1. NVIDIA GPU is present;
2. minimum VRAM policy passes;
3. bootstrap dependencies are installed;
4. the exact checkpoint is present and size/hash validation passes;
5. child runtime starts successfully;
6. runtime `/health` confirms CUDA and the model load.

Until then, `Auto` must fall back to Cloud. Explicit `Local` must fail clearly rather than silently using Cloud.

## Release and integrity pins

The first validated bootstrap is published as GitHub Release `local-sam-runtime-v1`.

- bootstrap asset: `modal-3D-local-sam-bootstrap-windows-x86_64-v1.zip`
- bootstrap bytes: `34,709,811`
- bootstrap SHA256: `1392402accd8985cbecabe62f766a847aee613c868e3dfb5191253bb4db0d73d`
- checkpoint bytes: `3,502,755,717`
- checkpoint SHA256: `0567debeec80ba4ac6369540c6c248025283cb3ff2b92827509e57e2b3541cb6`

The Agent embeds both hashes. Runtime installation refuses a changed bootstrap, and checkpoint synchronization writes through a `.part` file, validates bytes + SHA256, then atomically promotes the file.

## Current milestone

Implemented:

- pure Local SAM engine/server;
- pinned runtime manifest;
- Windows bootstrap builder;
- exact hash-pinned Torch 2.10.0 + cu128 and torchvision 0.25.0 Windows wheels;
- Windows bootstrap import/CUDA-version validation in GitHub Actions;
- stable GitHub Release bootstrap;
- Agent-managed background install lifecycle;
- checkpoint streaming from the existing Modal Volume with progress and content validation;
- child-process start/stop, parent watchdog and health endpoint;
- runtime log capture for startup diagnostics;
- Project provider routing: Auto prefers a valid Local install and falls back to Cloud only if Local startup fails;
- local materialize -> canonical PNG -> single upload to `modal-3d-artifacts` -> unchanged 3D worker contract;
- UI install/progress/start-and-verify controls.

GPU engine validation:

The exact client `local_sam_runtime/engine.py` was mounted into the production SAM3.1 CUDA environment on an L40S and completed `segment -> positive-box refine -> materialize` on the same test image used by the client workflow.

- model load: `12.155 s`
- segment image encode: `0.479 s`
- segment text prompt: `0.921 s`
- refine prompt: `0.129 s` with scene-cache hit
- peak allocated: `4.377 GiB`
- peak reserved: `4.441 GiB`
- canonical: RGBA `1024 x 1024`, `609,707` bytes
- canonical SHA256: `78ca93bbdd6109fc30ecfb1be787847a02b69da8d6f7c5171887300c8e82968e`

Still requiring a physical validation target:

- the same end-to-end runtime process on a real Windows x86_64 NVIDIA machine. GitHub Windows runners validate packaging/imports and the exact engine has been GPU-validated on L40S, but the combined Windows + NVIDIA environment is not available in hosted CI.
