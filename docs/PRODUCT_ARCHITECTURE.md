# modal-3D-client product architecture

```text
┌──────────────────────── modal-3D-client / local ────────────────────────┐
│                                                                        │
│  Source image                                                         │
│      │                                                                 │
│      ▼                                                                 │
│  rembg / birefnet-general / CPU                                       │
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

Current test-stage behavior keeps the complete foreground produced by global rembg matting. Connected-component selection and interactive box filtering are intentionally deferred to the next local-only iteration.

The retired SAM 3.1 implementation is preserved under `archive/sam3_1/` and is not part of the active build or runtime.
