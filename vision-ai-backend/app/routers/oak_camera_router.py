import asyncio
import logging
import time

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response, StreamingResponse
from typing import Optional
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.services import camera_service
from app.services.oak_camera_service import oak_camera_service

logger = logging.getLogger("oak-camera")

router = APIRouter()



# ==================== App Lifecycle ====================

@router.post("/start")
async def start_camera(db: Session = Depends(get_db)):
    """App startup — connect device and build pipeline."""
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
async def stream(request: Request):
    """MJPEG HTTP stream — connect to this after stream/start.
    Opens the frame queue, yields frames until client disconnects,
    then closes the queue.
    """
    if not oak_camera_service._is_running:
        return Response(
            content=b"Camera not started",
            status_code=503,
            media_type="text/plain",
        )

    oak_camera_service._open_stream_queue()

    async def generate():
        frames_sent = 0
        start_time = time.time()

        try:
            while True:
                if await request.is_disconnected():
                    logger.info(f"[STREAM] Client disconnected — frames sent: {frames_sent}")
                    break

                jpeg = await oak_camera_service.get_stream_frame(timeout=1.0)

                if jpeg is None:
                    # Queue was closed externally (POST /stream/stop called)
                    if oak_camera_service._stream_queue is None:
                        logger.info(f"[STREAM] Stream stopped externally — frames sent: {frames_sent}")
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
                        f"avg FPS: {frames_sent / elapsed:.1f}"
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
            oak_camera_service._close_stream_queue()

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
    """Single JPEG frame — camera must be started first."""
    frame = await oak_camera_service.get_stream_frame(timeout=2.0)
    if frame is None:
        return Response(status_code=503)
    return Response(content=frame, media_type="image/jpeg")


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