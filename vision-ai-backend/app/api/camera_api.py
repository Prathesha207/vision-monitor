import logging

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import List, Literal
from sqlalchemy.orm import Session
from app.dependencies import get_db
from app.schemas.camera_schema import CameraCreate, CameraResponse, CameraUpdate
from app.schemas.basic_config_update_schema import BasicConfigUpdate
from app.schemas.basic_config_response_schema import BasicConfigResponse

from app.services import camera_service
from app.schemas.camera_live_control_schema import CameraLiveControl
from app.services.oak_camera_service import oak_camera_service


class InferenceModeUpdate(BaseModel):
    mode: Literal["testing", "production"]

logger = logging.getLogger("camera-api")

router = APIRouter()


@router.post("/create", response_model=CameraResponse)
def create_camera(data: CameraCreate, db: Session = Depends(get_db)):
    return camera_service.create_camera(db, data)


@router.put("/update/{camera_id}", response_model=CameraResponse)
def update_camera(camera_id: int, data: CameraUpdate, db: Session = Depends(get_db)):
    return camera_service.update_camera_partial(db, camera_id, data)


@router.post("/enable/{camera_id}", response_model=CameraResponse)
def enable_camera(camera_id: int, db: Session = Depends(get_db)):
    return camera_service.enable_camera(db, camera_id)


@router.post("/disable/{camera_id}", response_model=CameraResponse)
def disable_camera(camera_id: int, db: Session = Depends(get_db)):
    return camera_service.disable_camera(db, camera_id)


@router.get("/", response_model=List[CameraResponse])
def get_cameras(db: Session = Depends(get_db)):
    return camera_service.get_cameras(db)


@router.put("/basic-config-update")
def update_basic_config(
    data: BasicConfigUpdate,
    db: Session = Depends(get_db)
):
    return camera_service.update_basic_config(db, data)


@router.get("/config", response_model=BasicConfigResponse)
def get_basic_config(db: Session = Depends(get_db)):
    return camera_service.get_basic_config(db)


@router.patch("/inference-mode")
def update_inference_mode(data: InferenceModeUpdate, db: Session = Depends(get_db)):
    return camera_service.update_inference_mode(db, data.mode)


@router.patch("/live-controls/{camera_id}")
def update_live_controls(camera_id: int, data: CameraLiveControl):
    logger.info(
        f"[LIVE CONTROLS] camera_id={camera_id} | "
        f"exposure={data.exposure}, gain={data.gain}, focus={data.focus}, "
        f"brightness={data.brightness}, contrast={data.contrast}"
    )

    if not oak_camera_service._is_running:
        logger.warning("[LIVE CONTROLS] Rejected — pipeline not running")
        return {"status": "error", "message": "Camera is not running"}

    try:
        oak_camera_service.update_controls(
            exposure=data.exposure,
            gain=data.gain,
            focus=data.focus,
            brightness=data.brightness,
            contrast=data.contrast,
        )
        logger.info("[LIVE CONTROLS] Applied successfully")
        return {"status": "success", "message": "Live controls applied"}

    except Exception as e:
        logger.error(f"[LIVE CONTROLS] Failed: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}