from fastapi import APIRouter, Response, Depends, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session
import cv2
import time

from app.services.camera_live_service import camera_live_service
from app.dependencies import get_db
from app.models.camera_model import Camera

router = APIRouter(prefix="", tags=["Camera Live"])


@router.get("/health")
def health():
    return {"running": camera_live_service.is_running()}


#  START CAMERA USING DB IP
@router.post("/start")
def start(db: Session = Depends(get_db)):
    camera = db.query(Camera).filter(Camera.is_enabled == True).first()

    if not camera:
        raise HTTPException(status_code=404, detail="No enabled camera found")

    if not camera.ip_address:
        raise HTTPException(status_code=400, detail="Camera IP not configured")

    return {"message": camera_live_service.start(camera.ip_address)}


@router.post("/stop")
def stop():
    return {"message": camera_live_service.stop()}


@router.get("/snapshot")
def snapshot():
    frame = camera_live_service.get_frame()

    if frame is None:
        return Response(status_code=404)

    _, jpg = cv2.imencode(".jpg", frame)
    return Response(content=jpg.tobytes(), media_type="image/jpeg")


@router.get("/stream")
def stream():

    def generator():
        while True:
            frame = camera_live_service.get_frame()

            if frame is None:
                time.sleep(0.1)
                continue

            _, jpg = cv2.imencode(".jpg", frame)

            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" +
                jpg.tobytes() + b"\r\n"
            )

    return StreamingResponse(
        generator(),
        media_type="multipart/x-mixed-replace; boundary=frame"
    )