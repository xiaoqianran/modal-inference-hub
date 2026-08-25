# modal-3D-client product architecture

```text
┌──────────────────────── modal-3D-client / local ────────────────────────┐
│                                                                        │
│  Source image                                                         │
│      │  import triggers local preprocessing automatically              │
│      ▼                                                                 │
│  rembg / birefnet-general-lite / CPU or Windows GPU                    │
│      │                                                                 │
│      ▼                                                                 │
│  Full RGBA                                                             │
│      │                                                                 │
│      ▼                                                                 │
│  foreground union bbox                                                 │
│      │                                                                 │
│      ▼                                                                 │
│  preserve aspect ratio → scale → transparent letterbox → center        │
│      │                                                                 │
│      ▼                                                                 │
│  Canonical PNG: 1024×1024, 8-bit RGBA                                 │
│      │                                                                 │
│      └── uploaded once, only when generation starts                    │
└──────────────────────────────┬─────────────────────────────────────────┘
                               │ client-inputs/<sha256>.png
                               ▼
┌──────────────────────────── modal-3D / cloud ──────────────────────────┐
│  Gateway validates canonical contract only                             │
│      │                                                                 │
│      ├── FastSAM3D++                                                   │
│      ├── TRELLIS2++                                                    │
│      ├── Hunyuan2.1++                                                  │
│      └── Pixal3D                                                       │
│                                                                        │
│  No rembg / no SAM / no subject selection / no canonicalization        │
│      │                                                                 │
│      ▼                                                                 │
│  GLB                                                                   │
└────────────────────────────────────────────────────────────────────────┘
```

The active local pipeline now performs 8-connected Alpha component analysis after rembg. All meaningful components are selected by default, preserving the complete rembg matte. Checkbox/click selection and drag-box component selection filter Alpha locally, recompute the selected union bbox, and regenerate the 1024×1024 Canonical RGBA without another AI inference or cloud round trip. Drag replaces the current component set, Shift+drag adds matches, Alt+drag removes matches, and the client keeps a bounded 50-state Undo/Redo history for local selection edits. RemBG defaults to GPU on Windows when ONNX Runtime CUDA is available; CUDA initialization failure falls back to CPU. Interactive selection keeps up to 64 MiB of decoded matte/label data in an Agent-local LRU cache and uses fast PNG compression for canonical refreshes.

The retired SAM 3.1 implementation is preserved under `archive/sam3_1/` and is not part of the active build or runtime.

## First-run model preparation

`birefnet-general-lite` is prepared locally before the first rembg session. The Agent owns the download so the UI can poll byte progress. The same preparation can be started explicitly from Settings before importing an image. Interrupted downloads remain as `.partial` and resume with HTTP Range on retry. A complete file is promoted only after the pinned rembg MD5 checksum succeeds; corrupt complete partials are deleted before the next retry.
