from __future__ import annotations

SAM_APP = "modal-3d-sam31"
SAM_CLASS = "Model"
SAM_MATERIALIZE_FUNCTION = "materialize"
GATEWAY_APP = "modal-3d-gateway"
GATEWAY_SUBMIT_FUNCTION = "submit_job"
GATEWAY_RESULT_FUNCTION = "result_job"
ARTIFACTS_VOLUME = "modal-3d-artifacts"

SUPPORTED_MODELS = (
    "hunyuan2.1-plus-plus",
    "fastsam3d-plus-plus",
    "hermit-trellis2-plus-plus",
    "pixal3d",
)
