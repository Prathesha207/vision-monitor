import logging

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.schemas.recording_schema import StartRecordingRequest, StopRecordingRequest
from app.services import camera_service
from app.services.oak_camera_service import oak_camera_service

logger = logging.getLogger("recording-api")

router = APIRouter()


@router.post("/start")
def start_recording(data: StartRecordingRequest, db: Session = Depends(get_db)):
    camera_config = camera_service.get_camera_config(db)
    if not camera_config:
        return {"status": "error", "message": "Camera config not found"}

    resolution = camera_config.resolution or "1920x1080"
    try:
        sep = "x" if "x" in resolution else "*"
        width, height = map(int, resolution.split(sep))
    except Exception:
        logger.warning(f"[RECORD] Invalid resolution '{resolution}' — fallback 1920x1080")
        width, height = 1920, 1080

    # fps is passed for compatibility but PyAV recording uses per-frame
    # wall-clock timestamps, so the declared fps has no effect on duration.
    path = oak_camera_service.start_recording(
        session_id=data.session_id,
        width=width,
        height=height,
        fps=float(camera_config.fps or 10),
    )
    return {"status": "recording", "recording_path": path}


@router.post("/stop")
def stop_recording(data: StopRecordingRequest):
    oak_camera_service.stop_recording(data.session_id)
    return {"status": "done"}
