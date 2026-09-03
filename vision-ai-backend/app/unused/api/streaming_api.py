import cv2
import base64
import asyncio
import time
import numpy as np

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Depends
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.services.oak_camera_service import oak_camera_service
from app.ml.duck_inference_service import run_inference
from app.services.inference_recording_service import InferenceRecorder
from app.core.logger import setup_logger

# ================= LOGGER =================
logger = setup_logger("duck-streaming")

router = APIRouter()


# ---------------------------------------------------------------------------
# Public draw_overlay (Duck version)
# ---------------------------------------------------------------------------
def draw_overlay(frame, detections, status):
    overlay_frame = frame.copy()

    # ── Bounding boxes ─────────────────────────────────────────────────
    for d in detections:
        # Support both old contour style and new bbox style
        contour = d.get("contour")
        if contour and len(contour) > 2:
            pts = np.array(contour, np.int32)
            cv2.polylines(overlay_frame, [pts], isClosed=True, color=(0, 255, 0), thickness=2)
            
        bbox = d.get("bbox") or d.get("box")
        if bbox and len(bbox) == 4:
            x1, y1, x2, y2 = map(int, bbox)
            
            is_anomaly = d.get("isAnomaly", False) or d.get("species") not in ["Duck", "duck"]
            color = (0, 0, 255) if is_anomaly else (0, 255, 0)
            
            cv2.rectangle(overlay_frame, (x1, y1), (x2, y2), color, 2)
            
            label = d.get("species", "Duck")
            conf = d.get("confidence", d.get("conf", 0.0))
            text = f"{label} {int(conf * 100)}%"
            cv2.putText(overlay_frame, text, (x1, max(y1 - 5, 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)

    # ── Status text ────────────────────────────────────────
    cv2.putText(
        overlay_frame,
        status,
        (20, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        1,
        (0, 255, 0),
        2
    )

    return overlay_frame


# ================= WEBSOCKET =================
@router.websocket("/ws/stream/{video_id}")
async def video_stream(
    websocket: WebSocket,
    video_id: str,
    db: Session = Depends(get_db)
):
    await websocket.accept()
    logger.info(f"[VIDEO WS CONNECT] video_id={video_id}")

    video_path = f"storage/uploads/videos/{video_id}.mp4"
    cap = cv2.VideoCapture(video_path)

    frame_count = 0
    last_result = None
    recorder = None
    recording_active = False

    try:
        from app.services import inference_config_service
        config = inference_config_service.get_config(db)

        fps = cap.get(cv2.CAP_PROP_FPS)
        if not fps or fps <= 0:
            fps = 25

        safe_fps = int(max(1, min(fps, 30)))

        while True:
            success, frame = cap.read()
            if not success:
                break

            frame_count += 1

            processed_frame = frame

            # ================= IMAGE ADJUST =================
            if oak_camera_service.control_mode == "manual":
                processed_frame = oak_camera_service.apply_image_adjustments(
                    frame.copy(),
                    brightness=oak_camera_service.current_brightness,
                    contrast=oak_camera_service.current_contrast
                )

            success, buffer = cv2.imencode(".jpg", processed_frame)
            if not success:
                continue

            image_base64 = base64.b64encode(buffer).decode()

            # ================= DEFAULT FROM LAST RESULT =================
            detections = last_result.get("detections", []) if last_result else []
            status = last_result.get("status", "STREAMING") if last_result else "STREAMING"
            record_flag = last_result.get("record", False) if last_result else False

            video_inference_enabled = oak_camera_service.inference_enabled

            # ================= INFERENCE =================
            if video_inference_enabled and frame_count % 5 == 0:
                try:
                    result = run_inference(frame, session_id=video_id)

                    if result:
                        last_result = result

                        detections = result.get("detections", [])
                        record_flag = result.get("record", False)
                        status = result.get("status", "PROCESSING")
                        
                        if frame_count % 30 == 0:
                            logger.info(
                                f"[VIDEO WS DATA] ducks={len(detections)} "
                                f"status={status}"
                            )

                except Exception as e:
                    logger.error(f"[VIDEO INFERENCE ERROR] {e}", exc_info=True)

            # ================= SYNC LAST RESULT (FIX UI DELAY) =================
            elif last_result:
                detections = last_result.get("detections", [])
                record_flag = last_result.get("record", False)
                status = last_result.get("status", "PROCESSING")

            # ================= RECORDER CONTROL =================
            try:
                should_record = (
                    video_inference_enabled
                    and record_flag
                    and config
                    and (config.processed_video or config.raw_video)
                )

                if frame_count % 30 == 0:
                    logger.info(
                        f"[RECORDER CHECK] should_record={should_record} "
                        f"recording_active={recording_active}"
                    )

                # ================= START =================
                if should_record and not recording_active:
                    logger.info("[RECORDER] START")

                    h, w = frame.shape[:2]

                    recorder = InferenceRecorder(
                        session_id=video_id + f"_{int(time.time())}",
                        config=config,
                        width=w,
                        height=h,
                        fps=safe_fps
                    )

                    recording_active = True

                # ================= STOP =================
                elif (not should_record) and recording_active:
                    logger.info("[RECORDER] STOP")

                    try:
                        recorder.stop()
                    except Exception as e:
                        logger.error(f"[RECORDER STOP ERROR] {e}", exc_info=True)

                    recorder = None
                    recording_active = False

                # ================= WRITE =================
                if recording_active and recorder:
                    try:
                        overlay_frame = draw_overlay(
                            processed_frame,
                            detections,
                            status
                        )
                        # We pass a faux 'result' dict as recorder.write expects (frame, result) 
                        # where result is parsed internally by InferenceRecorder
                        recorder.write(frame, {"detections": detections, "status": status, "record": record_flag})

                    except Exception as e:
                        logger.error(f"[OVERLAY ERROR] {e}", exc_info=True)

            except Exception as e:
                logger.error(f"[VIDEO RECORDING ERROR] {e}", exc_info=True)

            # ================= SEND =================
            await websocket.send_json({
                "image": image_base64,
                "detections": detections,
                "status": status,
                "record": record_flag
            })

            await asyncio.sleep(1 / safe_fps)

    except WebSocketDisconnect:
        logger.info(f"[VIDEO WS DISCONNECT] video_id={video_id}")

    finally:
        cap.release()

        if recorder:
            recorder.stop()

        logger.info(f"[VIDEO WS CLEANUP] video_id={video_id}")