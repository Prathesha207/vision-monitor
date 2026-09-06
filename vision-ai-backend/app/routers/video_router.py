import logging
import tempfile
import asyncio
from fastapi import APIRouter, UploadFile, File, BackgroundTasks, Request
from fastapi.responses import StreamingResponse, JSONResponse, Response

from app.ml.ml_inference import ml_inference_service

logger = logging.getLogger("video-router")
router = APIRouter()

from fastapi import APIRouter, UploadFile, File, BackgroundTasks, Request, Form

import os

import subprocess
import imageio_ffmpeg

@router.post("/upload")
async def upload_video(file: UploadFile = File(...), expected_ducks: int = Form(18)):
    # NEW: create_session() now raises RuntimeError while a training job
    # owns the GPU -- surface that as 409 instead of letting it 500.
    try:
        session_id = ml_inference_service.create_session(expected_ducks, original_filename=file.filename)
    except RuntimeError as e:
        return JSONResponse(status_code=409, content={"message": str(e)})

    # Save uploaded video directly into its dedicated session folder under ml/output/{session_id}/
    session_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ml", "output", session_id)
    os.makedirs(session_dir, exist_ok=True)
    
    ext = os.path.splitext(file.filename or "video.mp4")[1] or ".mp4"
    browser_video_path = os.path.join(session_dir, "source.mp4")
    
    is_camera_recording = bool(file.filename and file.filename.startswith("recorded_camera"))
    if is_camera_recording:
        from datetime import datetime
        today = datetime.now().strftime("%Y-%m-%d")
        from app.core.app_paths import get_desktop_dir
        desktop = get_desktop_dir()
        desktop_rec_dir = desktop / "recordings" / today
        desktop_rec_dir.mkdir(parents=True, exist_ok=True)
        raw_save_path = str(desktop_rec_dir / file.filename)
    else:
        raw_save_path = os.path.join(session_dir, f"raw_upload{ext}")
    
    try:
        content = await file.read()
        if len(content) < 5000:
            logger.warning(f"[UPLOAD] Video file {file.filename} is too small ({len(content)} bytes)")
            return JSONResponse(
                status_code=400,
                content={"message": "Recorded video is empty or too short. Please record for at least 2-3 seconds."}
            )
        with open(raw_save_path, "wb") as f:
            f.write(content)
        if is_camera_recording:
            logger.info(f"[RECORD] Saved recorded camera video directly to Desktop: {raw_save_path}")
    except Exception as e:
        logger.error(f"Error saving uploaded file: {e}")
        return JSONResponse(status_code=500, content={"message": "Failed to save uploaded video."})

    # Transcode to browser-safe H.264 baseline, regardless of source codec
    try:
        import imageio_ffmpeg
        ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        if ffmpeg_exe and not os.path.exists(ffmpeg_exe):
            logger.warning(f"ffmpeg_exe path {ffmpeg_exe} does not exist. Falling back to raw video.")
            ffmpeg_exe = None
    except Exception as e:
        logger.warning(f"Could not locate ffmpeg, falling back to raw video: {e}")
        ffmpeg_exe = None

    transcode_ok = False
    if ffmpeg_exe:
        def run_transcode():
            return subprocess.run(
                [
                    ffmpeg_exe, "-y", "-i", raw_save_path,
                    "-c:v", "libx264", "-profile:v", "baseline", "-level", "3.0",
                    "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-movflags", "+faststart",
                    browser_video_path,
                ],
                capture_output=True, text=True, timeout=180,
            )

        try:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, run_transcode)
            if result.returncode == 0 and os.path.exists(browser_video_path) and os.path.getsize(browser_video_path) > 1000:
                transcode_ok = True
                logger.info(f"[UPLOAD] Transcoded cleanly to H.264 MP4: {browser_video_path}")
            else:
                logger.warning(f"ffmpeg transcode failed; using raw upload: {result.stderr}")
                browser_video_path = raw_save_path
            
        except Exception as e:
            logger.warning(f"ffmpeg execution failed; using raw upload: {e}")
            browser_video_path = raw_save_path
    else:
        browser_video_path = raw_save_path
    
    # Prefer clean H.264 MP4 for OpenCV inference when available for maximum compatibility
    effective_inference_path = browser_video_path if transcode_ok else raw_save_path

    # Save path in session state so it's ready to start when user commands
    session = ml_inference_service.sessions.get(session_id)
    if session:
        session["inference_video_path"] = effective_inference_path
        session["browser_video_path"] = browser_video_path
        session["status"] = "ready"
        session["stats"]["status"] = "ready"
    
    return {"session_id": session_id, "status": "ready"}

from fastapi.responses import FileResponse

@router.get("/raw/{session_id}")
async def get_raw_video(session_id: str):
    session = ml_inference_service.sessions.get(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"message": "Session not found."})
    video_save_path = session.get("browser_video_path") or session.get("inference_video_path")
    if not video_save_path or not os.path.exists(video_save_path):
        return JSONResponse(status_code=404, content={"message": "Raw video file not found."})
    return FileResponse(video_save_path, media_type="video/mp4")

@router.post("/start/{session_id}")
async def start_video_inference(session_id: str, background_tasks: BackgroundTasks):
    session = ml_inference_service.sessions.get(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"message": "Session not found."})
    
    video_save_path = session.get("inference_video_path")
    if not video_save_path or not os.path.exists(video_save_path):
        return JSONResponse(status_code=400, content={"message": "Video file not found for session."})
    
    # Always create a new run.  A Stop request is asynchronous, so checking
    # only `status != processing` used to let a quick Stop -> Start silently
    # keep the old run alive.  start_run invalidates that old task and clears
    # its queued MJPEG frames before the new task begins.
    run_seq = ml_inference_service.start_run(session_id)
    background_tasks.add_task(
        ml_inference_service.process_video_task,
        session_id,
        video_save_path,
        session.get("original_filename"),
        run_seq,
    )
        
    return {"status": "started", "session_id": session_id}

@router.get("/stream/{session_id}")
async def stream_video(session_id: str, request: Request):
    status = ml_inference_service.get_status(session_id)
    if not status:
        return JSONResponse(status_code=404, content={"message": "Session not found."})

    return StreamingResponse(
        ml_inference_service.get_stream_generator(session_id),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
            "Connection": "close",
        },
    )

@router.get("/status/{session_id}")
async def get_status(session_id: str):
    status = ml_inference_service.get_status(session_id)
    if not status:
        return JSONResponse(status_code=404, content={"message": "Session not found."})
    return status

@router.post("/stop/{session_id}")
async def stop_video(session_id: str):
    status = ml_inference_service.get_status(session_id)
    if not status:
        return JSONResponse(status_code=404, content={"message": "Session not found."})
    
    ml_inference_service.stop_session(session_id)
    return {"message": "Stop signal sent."}

@router.get("/last_frame/{session_id}")
async def get_last_frame(session_id: str):
    session = ml_inference_service.sessions.get(session_id)
    if not session:
        return JSONResponse(status_code=404, content={"message": "Session not found."})
    frame_bytes = session.get("last_frame_bytes")
    if not frame_bytes:
        session_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "ml", "output", session_id)
        raw_frames_dir = os.path.join(session_dir, "raw_frames")
        if os.path.exists(raw_frames_dir):
            frames = sorted(os.listdir(raw_frames_dir))
            if frames:
                last_file = os.path.join(raw_frames_dir, frames[-1])
                try:
                    with open(last_file, "rb") as f:
                        frame_bytes = f.read()
                except Exception:
                    pass
    if not frame_bytes:
        return JSONResponse(status_code=404, content={"message": "No frame available."})
    return Response(
        content=frame_bytes,
        media_type="image/jpeg",
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
    )

from pydantic import BaseModel

class ExpectedCountUpdate(BaseModel):
    count: int

@router.post("/update_expected/{session_id}")
async def update_expected(session_id: str, payload: ExpectedCountUpdate):
    status = ml_inference_service.get_status(session_id)
    if not status:
        return JSONResponse(status_code=404, content={"message": "Session not found."})
    
    ml_inference_service.update_expected_ducks(session_id, payload.count)
    return {"message": "Expected duck count updated."}


class StartPathInferenceRequest(BaseModel):
    video_path: str
    expected_ducks: int = 18

@router.post("/inference/path")
async def start_path_inference(data: StartPathInferenceRequest, background_tasks: BackgroundTasks):
    video_path = os.path.abspath(data.video_path.strip('"').strip("'"))
    if not os.path.exists(video_path) or not os.path.isfile(video_path):
        return JSONResponse(status_code=400, content={"message": f"File does not exist: {video_path}"})
    
    ext = os.path.splitext(video_path)[1].lower()
    valid_exts = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v"}
    if ext not in valid_exts:
        return JSONResponse(status_code=400, content={"message": f"Unsupported video extension: {ext}"})

    filename = os.path.basename(video_path)
    try:
        session_id = ml_inference_service.create_session(data.expected_ducks, original_filename=filename)
    except RuntimeError as e:
        return JSONResponse(status_code=409, content={"message": str(e)})

    session = ml_inference_service.sessions.get(session_id)
    if session:
        session["inference_video_path"] = video_path
        session["status"] = "ready"
        session["stats"]["status"] = "ready"

    return {"session_id": session_id, "status": "ready", "video_name": filename}

