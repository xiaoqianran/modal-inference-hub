# modal-3D-client product architecture

```text
┌──────────────────────── modal-3D-client / local ────────────────────────┐
│                                                                        │
│  Source image                                                         │
│      │  import triggers local preprocessing automatically              │
│      ▼                                                                 │
│  rembg / birefnet-general / CPU or Windows GPU                         │
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

The active local pipeline now performs 8-connected Alpha component analysis after rembg. All meaningful components are selected by default, preserving the complete rembg matte. Checkbox/click selection and drag-box component selection filter Alpha locally, recompute the selected union bbox, and regenerate the 1024×1024 Canonical RGBA without another AI inference or cloud round trip. RemBG defaults to CPU but can use ONNXRuntime DirectML on Windows when a compatible GPU is available; initialization failure falls back to CPU. Interactive selection keeps up to 64 MiB of decoded matte/label data in an Agent-local LRU cache and uses fast PNG compression for canonical refreshes.

The retired SAM 3.1 implementation is preserved under `archive/sam3_1/` and is not part of the active build or runtime.
