import asyncio
import logging
import time
from typing import Optional

import cv2
import numpy as np
from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.models.camera_model import Camera
from app.services import camera_service
from app.services.oak_camera_service import oak_camera_service

logger = logging.getLogger("oak-camera")

router = APIRouter()



class StartCameraPayload(BaseModel):
    camera_id: Optional[int] = None
    ip_address: Optional[str] = None

# ==================== App Lifecycle ====================

@router.post("/start")
async def start_camera(payload: Optional[StartCameraPayload] = None, db: Session = Depends(get_db)):
    """App startup — connect device and build pipeline."""
    camera = None
    if payload and payload.camera_id:
        camera = db.query(Camera).filter(Camera.id == payload.camera_id).first()
    elif payload and payload.ip_address:
        camera = db.query(Camera).filter(Camera.ip_address == payload.ip_address).first()

    if not camera:
        camera = camera_service.get_camera_config(db)

    if not camera or not camera.ip_address:
        return {"status": "error", "message": "Camera not configured"}
    result = await oak_camera_service.start(camera)
    return result


@router.post("/stop")
async def stop_camera():
    """App shutdown — stop everything and disconnect."""
    result = await oak_camera_service.stop()
    return result


# ==================== Stream Lifecycle ====================

@router.post("/stream/start")
async def start_stream():
    """User clicks Start Streaming — starts the 3 capture threads."""
    result = await oak_camera_service.start_streaming()
    return result


@router.post("/stream/stop")
async def stop_stream():
    """User clicks Stop Streaming — stops capture threads and clears queues."""
    result = await oak_camera_service.stop_streaming()
    return result


# ==================== MJPEG Stream ====================

@router.get("/stream")
@router.get("/stream/live")
@router.get("/inference/stream/live")
@router.get("/inference/stream/{session_id}")
async def stream(request: Request, session_id: Optional[str] = None):
    """MJPEG HTTP stream — connect to this after stream/start or on canvas load.
    Subscribes a client frame queue, yields frames until client disconnects,
    then unsubscribes cleanly without interrupting other clients.
    """
    if not oak_camera_service._is_running:
        # Auto-start camera from database if configured
        try:
            from app.core.database import SessionLocal
            db = SessionLocal()
            try:
                cam = camera_service.get_camera_config(db)
                if cam:
                    logger.info(f"[STREAM] Auto-starting camera ID {cam.id} ({cam.ip_address}) for stream")
                    await oak_camera_service.start(cam)
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"[STREAM] Auto-start camera failed: {e}")

    if not oak_camera_service._is_running:
        return Response(
            content=b"Camera not started",
            status_code=503,
            media_type="text/plain",
        )

    # Ensure capture threads are active so frames are actively produced
    if not (oak_camera_service._mjpeg_thread and oak_camera_service._mjpeg_thread.is_alive()):
        logger.info("[STREAM] Capture threads not running — starting them now")
        oak_camera_service._start_capture_threads()

    client_queue = oak_camera_service.subscribe_stream()

    async def generate():
        frames_sent = 0
        start_time = time.time()

        try:
            while True:
                if await request.is_disconnected():
                    logger.info(f"[STREAM] Client disconnected — frames sent: {frames_sent}")
                    break

                if not oak_camera_service._is_streaming:
                    logger.info(f"[STREAM] Stream stopped — ending client stream cleanly (frames sent: {frames_sent})")
                    break

                try:
                    jpeg = await asyncio.wait_for(client_queue.get(), timeout=2.0)
                except asyncio.TimeoutError:
                    if not oak_camera_service._is_running or not oak_camera_service._is_streaming:
                        break
                    # If capture threads died while streaming is active, attempt restart
                    if oak_camera_service._is_streaming and not (oak_camera_service._mjpeg_thread and oak_camera_service._mjpeg_thread.is_alive()):
                        oak_camera_service._start_capture_threads()
                    continue

                if jpeg is None:
                    if not oak_camera_service._is_streaming:
                        break
                    continue

                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + jpeg +
                    b"\r\n"
                )

                frames_sent += 1

                if frames_sent % 150 == 0:
                    elapsed = time.time() - start_time
                    logger.info(
                        f"[STREAM] Frames sent: {frames_sent} | "
                        f"avg FPS: {frames_sent / max(elapsed, 1):.1f}"
                    )

        except Exception as e:
            logger.error(f"[STREAM] Generator error: {e}")
        finally:
            elapsed = time.time() - start_time
            logger.info(
                f"[STREAM] Ended — frames sent: {frames_sent} | "
                f"duration: {elapsed:.1f}s | "
                f"avg FPS: {frames_sent / max(elapsed, 1):.1f}"
            )
            oak_camera_service.unsubscribe_stream(client_queue)

    return StreamingResponse(
        generate(),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "close",
        },
    )


# ==================== Snapshot ====================

@router.get("/snapshot")
async def snapshot():
    """Single JPEG frame — returns latest live frame or fallback to prevent UI errors."""
    # 1. Try from stream queue / buffer
    frame = await oak_camera_service.get_stream_frame(timeout=0.5)
    if frame is not None:
        return Response(content=frame, media_type="image/jpeg")

    # 2. Try latest BGR frame from converter
    bgr = oak_camera_service.get_bgr_frame()
    if bgr is not None:
        success, buf = cv2.imencode(".jpg", bgr, [cv2.IMWRITE_JPEG_QUALITY, 80])
        if success:
            return Response(content=buf.tobytes(), media_type="image/jpeg")

    # 3. If capture threads are alive, wait briefly for frame
    if oak_camera_service._mjpeg_thread and oak_camera_service._mjpeg_thread.is_alive():
        frame = await oak_camera_service.get_stream_frame(timeout=1.0)
        if frame is not None:
            return Response(content=frame, media_type="image/jpeg")

    # 4. Fallback: 1280x720 dark frame to avoid 503 error triggering frontend disconnect
    blank = np.zeros((720, 1280, 3), dtype=np.uint8)
    _, buf = cv2.imencode(".jpg", blank, [cv2.IMWRITE_JPEG_QUALITY, 50])
    return Response(content=buf.tobytes(), media_type="image/jpeg")


# ==================== Health ====================

@router.get("/health")
def health():
    return oak_camera_service.health()


# ==================== Camera Controls ====================

@router.post("/controls")
def update_controls(
    exposure: int | None = None,
    gain: int | None = None,
    focus: int | None = None,
    brightness: int | None = None,
    contrast: int | None = None,
):
    oak_camera_service.update_controls(
        exposure=exposure,
        gain=gain,
        focus=focus,
        brightness=brightness,
        contrast=contrast,
    )
    return {"status": "ok"}


# ==================== Inference ====================

class InferenceStartBody(BaseModel):
    offline: bool = False
    videoPath: Optional[str] = None   # absolute path to a single video file
    folderPath: Optional[str] = None  # absolute path to a folder of videos
    videoId: Optional[str] = None     # legacy field — treated as videoPath if videoPath is absent


@router.post("/inference/start/{session_id}")
async def start_inference(session_id: str, body: InferenceStartBody = InferenceStartBody()):
    loop = asyncio.get_running_loop()
    # Resolve legacy videoId field as videoPath so old frontend builds still work
    resolved_video_path = body.videoPath or body.videoId or None
    result = oak_camera_service.start_inference(
        session_id, loop,
        offline=body.offline,
        video_path=resolved_video_path,
        folder_path=body.folderPath,
    )
    return result


@router.post("/inference/stop")
def stop_inference():
    result = oak_camera_service.stop_inference()
    return result

class ExpectedCountUpdate(BaseModel):
    count: int

@router.post("/inference/update_expected/{session_id}")
async def update_expected(session_id: str, payload: ExpectedCountUpdate):
    from app.ml.duck_inference_service import update_expected_ducks
    update_expected_ducks(session_id, payload.count)
    return {"message": "Expected duck count updated."}


@router.get("/inference/status/{session_id}")
async def get_camera_inference_status(session_id: str):
    from app.ml.duck_inference_service import get_session_status
    status = get_session_status(session_id)
    if not status:
        return {"status": "idle", "session_id": session_id}
    return status


@router.websocket("/inference/ws/{session_id}")
async def inference_ws(websocket: WebSocket, session_id: str):
    """WebSocket that streams inference results to the UI.
    UI handles the MJPEG stream separately — this only sends JSON results.
    """
    await websocket.accept()
    logger.info(f"[INFERENCE WS] ✅ Client connected — session: {session_id}")
    logger.info(f"[INFERENCE WS] Waiting for inference results — inference_running: {oak_camera_service._inference_thread is not None and oak_camera_service._inference_thread.is_alive()}")

    results_sent = 0

    try:
        while True:
            result = await oak_camera_service.get_inference_result(timeout=1.0)

            if result is None:
                # Inference has stopped — queue was cleared; exit so the event loop is not starved
                if oak_camera_service._inference_result_queue is None:
                    logger.info(f"[INFERENCE WS] Inference stopped — closing")
                    break
                continue

            results_sent += 1

            # Terminal result — send it, then close cleanly
            if result.get("done"):
                logger.info(f"[INFERENCE WS] Terminal result received (status={result.get('status')}) — closing")
                await websocket.send_json(result)
                break

            logger.info(
                f"[INFERENCE WS] #{results_sent} → status={result.get('status')} | "
                f"record={result.get('record')} | "
                f"detections={len(result.get('detections', []))} | "
                f"fps={result.get('metrics', {}).get('fps', '?')} | "
                f"gpu={result.get('metrics', {}).get('gpu_pct', '?')}% | "
                f"latency={result.get('metrics', {}).get('latency_ms', '?')}ms"
            )

            if "_raw_frame" in result:
                import cv2, base64, asyncio
                frame = result.pop("_raw_frame")
                loop = asyncio.get_running_loop()
                def _encode():
                    _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 60])
                    return "data:image/jpeg;base64," + base64.b64encode(buf).decode()
                result["frame"] = await loop.run_in_executor(None, _encode)

            await websocket.send_json(result)

    except WebSocketDisconnect:
        logger.info(f"[INFERENCE WS] Client disconnected — session: {session_id} | total sent: {results_sent}")
    except Exception as e:
        logger.error(f"[INFERENCE WS] Error: {e}", exc_info=True)
    finally:
        logger.info(f"[INFERENCE WS] Closed — session: {session_id}")
        try:
            await websocket.close()
        except RuntimeError:
            pass