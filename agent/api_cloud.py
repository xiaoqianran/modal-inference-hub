from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Body, HTTPException, Query
from fastapi.responses import FileResponse

from agent.cloud import artifacts, sam
from agent.cloud_jobs import create as create_job, get as get_job
from agent.schemas import GenerationRequest, SamMaterializeRequest, SamRefineRequest

router = APIRouter()


def _invoke(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except RuntimeError as exc:
        if str(exc) == "Modal is not connected":
            raise HTTPException(status_code=409, detail="Modal 尚未连接") from exc
        raise HTTPException(status_code=502, detail=f"云端调用失败: {type(exc).__name__}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"云端调用失败: {type(exc).__name__}") from exc


@router.post("/sam/segment")
def sam_segment(concept: str = Query(min_length=1, max_length=160), max_candidates: int = Query(default=16, ge=1, le=24), image: bytes = Body(media_type="application/octet-stream")) -> dict:
    if not image:
        raise HTTPException(status_code=400, detail="图片不能为空")
    return _invoke(sam.segment, image, concept, max_candidates)


@router.post("/sam/refine")
def sam_refine(req: SamRefineRequest) -> dict:
    return _invoke(sam.refine, req.scene_id, req.concept, [box.model_dump() for box in req.boxes], req.max_candidates)


@router.post("/sam/materialize")
def sam_materialize(req: SamMaterializeRequest) -> dict:
    return _invoke(sam.materialize, req.scene_id, req.selection_id, req.candidate_id, req.output_size)


@router.put("/artifacts/input")
def artifact_put(suffix: str = Query(default=".png", min_length=2, max_length=16), data: bytes = Body(media_type="application/octet-stream")) -> dict:
    return _invoke(artifacts.put, data, suffix)


@router.get("/artifacts/file")
def artifact_get(background_tasks: BackgroundTasks, path: str = Query(min_length=1)) -> FileResponse:
    local_path = _invoke(artifacts.download_to_temp, path)
    background_tasks.add_task(local_path.unlink, missing_ok=True)
    media_type = "model/gltf-binary" if path.lower().endswith(".glb") else "application/octet-stream"
    return FileResponse(local_path, media_type=media_type, filename=local_path.name)


@router.post("/generation")
def generation_submit(req: GenerationRequest) -> dict:
    return _invoke(create_job, req.model, req.input_path, req.options)


@router.get("/generation/{job_id}")
def generation_result(job_id: str) -> dict:
    return _invoke(get_job, job_id)
