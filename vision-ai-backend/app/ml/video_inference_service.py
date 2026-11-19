"""
video_inference_service.py
==========================
Handles batch / offline video file inference.

When to use this file:
- An MP4/AVI video has been uploaded by the user, OR
- A recorded camera video file is being processed asynchronously.
- Manages long-running video decoding sessions, Server-Sent Events (SSE) / stream queues,
  saving annotated videos to disk, and progress tracking.

When NOT to use this file:
- If you are receiving live frames in real-time from an OAK camera or RTSP stream,
  use `camera_inference_service.py` instead!

Main exports:

- `video_inference_service = VideoInferenceService()`
- `ml_inference_service` (backward compatibility alias)
"""

import tempfile
import asyncio
import os
import sys
import time
import uuid
import logging
import yaml
from typing import Dict, Any, Optional
import cv2

import concurrent.futures

try:
    from traycount import TrayCounter
except ImportError:
    TrayCounter = None

try:
    import torch
except ImportError:
    torch = None

from app.ml import app_state  # shared GPU mutual-exclusion flag with training_service.py
                               # AND with camera_inference_service.py (video vs camera)
from app.core.logger import setup_logger

logger = setup_logger("video-inference")

_shared_tray_counter: Optional[Any] = None

def _set_shared_tray_counter(engine: Any) -> None:
    global _shared_tray_counter
    _shared_tray_counter = engine
    logger.info("[VIDEO] Shared TrayCounter registered successfully.")

# Backward compatibility alias
_set_shared_analyzer = _set_shared_tray_counter


import time

_cuda_operational_cached = None

def is_cuda_operational() -> bool:
    """Verifies that PyTorch can actually execute CUDA kernels on GPU 0,
    rather than just checking if the driver library is queryable."""
    global _cuda_operational_cached
    if _cuda_operational_cached is not None:
        return _cuda_operational_cached
    if torch is None:
        return False

    if not (torch.cuda.is_available() and torch.cuda.device_count() > 0):
        # Do not log here on every call; setup scripts handle the "no cuda" warning
        return False

    # Robust retry loop to wake up GPU from power-saving / D3 sleep
    for attempt in range(1, 4):
        try:
            if not torch.cuda.is_initialized():
                torch.cuda.init()
            torch.cuda.set_device(0)
            
            # Enable high-performance flags for modern GPUs (RTX 5050/4000/3000)
            if hasattr(torch.backends, "cuda") and hasattr(torch.backends.cuda, "matmul"):
                torch.backends.cuda.matmul.allow_tf32 = True
            if hasattr(torch.backends, "cudnn"):
                torch.backends.cudnn.allow_tf32 = True
                torch.backends.cudnn.benchmark = True

            t = torch.zeros((1, 1), device="cuda:0")
            _ = t + 1.0
            del t
            torch.cuda.synchronize(0)
            _cuda_operational_cached = True
            return True
        except Exception as e:
            logger.warning(f"CUDA initialization attempt {attempt}/3 encountered: {e}. Retrying...")
            time.sleep(0.3)
    
    logger.error("CUDA is present but kernel execution failed after 3 attempts; falling back to CPU.")
    _cuda_operational_cached = False
    return False

class VideoInferenceService:
    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}
        # Global lock to ensure only one GPU inference runs at a time
        self._gpu_lock = asyncio.Lock()
        # Single-thread executor for synchronous inference to avoid blocking the asyncio event loop
        self._ml_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        # Non-blocking async background executor for saving anomaly frames to disk without stalling inference
        self._io_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)

        # Resolve config path — look in app/ml/config/config.yaml first (canonical),
        # then fall back to app/ml/config.yaml (legacy) and PyInstaller bundle.
        ml_dir = os.path.dirname(os.path.abspath(__file__))
        candidates = [
            os.path.join(ml_dir, "config", "config.yaml"),   # canonical: app/ml/config/
            os.path.join(ml_dir, "config.yaml"),              # legacy flat layout
        ]
        if getattr(sys, "_MEIPASS", None):
            candidates.insert(0, os.path.join(sys._MEIPASS, "app", "ml", "config", "config.yaml"))
            candidates.insert(1, os.path.join(sys._MEIPASS, "app", "ml", "config.yaml"))
        self.config_path = next((c for c in candidates if os.path.exists(c)), candidates[0])

    def _get_or_create_tray_counter(self, config_path: str) -> Any:
        global _shared_tray_counter
        if _shared_tray_counter is not None:
            return _shared_tray_counter
        logger.info("Initializing a fresh TrayCounter instance for video session...")
        tray_counter = TrayCounter(config_path)
        tray_counter.load_model()
        _shared_tray_counter = tray_counter
        return tray_counter

    # Backward compatibility
    def _get_or_create_analyzer(self, *args, **kwargs) -> Any:
        return self._get_or_create_tray_counter(self.config_path)

    def stop_all_sessions(self):
        for sid, session in self.sessions.items():
            if not session["stop_event"].is_set():
                session["stop_event"].set()

    def create_session(self, expected_ducks: int = 18, original_filename: Optional[str] = None, session_id: Optional[str] = None) -> str:
        # Fast, best-effort rejection up front so the caller gets an immediate
        # error instead of a session that will fail later. This is NOT the
        # real claim on the GPU -- that happens in process_video_task, right
        # before frames actually start flowing, and is released when that
        # task ends. A session sitting here queued (created but never
        # uploaded/started) must not hold the lock indefinitely.
        if app_state.get_mode() == "TRAINING":
            raise RuntimeError("Training is in progress -- please wait for it to finish before starting inference")
        if app_state.get_active_inference_kind() == "camera":
            raise RuntimeError("Live camera inference is currently active -- please stop it before starting a video upload")

        # Preemptively stop any currently running jobs so the GPU lock is freed immediately
        self.stop_all_sessions()
        
        session_id = session_id or str(uuid.uuid4())
        self.sessions[session_id] = {
            "status": "queued",
            "queue": asyncio.Queue(maxsize=2),
            "expected_ducks": expected_ducks,
            "original_filename": original_filename,
            "analyzer": None,
            "temp_files_to_cleanup": [],
            "stats": {
                "session_id": session_id,
                "original_filename": original_filename,
                "status": "queued",
                "frames_processed": 0,
                "total_frames": 0,
                "progress": 0.0,
                "fps": 0.0,
                "detected_duck_count": 0,
                "expected_duck_count": expected_ducks,
                "other_count": 0,
                "detected_other_toy_count": 0,  # kept as an alias for older frontend code
                "anchor_locked": False,
                "hand_detected": False,
                "missing_ids": [],
                "added_ids": [],
                "excess_ids": [],
                "excess_count": 0,
                "other_ids": [],
                "reasons": [],
                "thumbnails": [],   # accumulates across the whole session (one-shot events)
                "detections": []
            },
            "stop_event": asyncio.Event(),
            "run_seq": 0,
            "temp_file": None
        }
        return session_id

    def get_status(self, session_id: str) -> Optional[Dict[str, Any]]:
        session = self.sessions.get(session_id)
        if not session:
            return None
        return session["stats"]

    async def get_stream_generator(self, session_id: str):
        session = self.sessions.get(session_id)
        if not session:
            return

        # Immediately yield pre-extracted frame 0 (if available) so the client's
        # <img> tag gets an instant frame without waiting for ML processing
        if session.get("last_frame_bytes"):
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n"
                + session["last_frame_bytes"] +
                b"\r\n"
            )

        queue = session["queue"]
        try:
            while True:
                frame_bytes = await queue.get()
                if frame_bytes is None:
                    # Graceful completion sentinel received
                    break
                
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + frame_bytes +
                    b"\r\n"
                )
        except asyncio.CancelledError:
            logger.info(f"Stream disconnected for session {session_id}")

    async def stop_session(self, session_id: str, timeout: float = 2.5):
        session = self.sessions.get(session_id)
        if session:
            session["stop_event"].set()
            session["status"] = "stopped"
            session["stats"]["status"] = "stopped"
            last_bytes = session.get("last_frame_bytes")
            session_dir = session.get("session_dir")
            if last_bytes and session_dir and os.path.isdir(session_dir):
                try:
                    with open(os.path.join(session_dir, "last_frame.jpg"), "wb") as f:
                        f.write(last_bytes)
                except Exception as e:
                    logger.warning(f"Could not write last_frame.jpg on stop_session: {e}")

            # Gracefully wait for the active frame processing task to exit and release _gpu_lock
            start_wait = time.time()
            while session.get("is_task_active", False) and (time.time() - start_wait < timeout):
                await asyncio.sleep(0.01)

    def clear_session_files(self, session_id: str):
        """Explicitly called only when the user clears/resets the video session."""
        session = self.sessions.get(session_id)
        if not session:
            return
        temp_files = list(session.get("temp_files_to_cleanup", []))
        for tf in temp_files:
            try:
                if tf and os.path.exists(tf):
                    if "Desktop" in tf and "recordings" in tf:
                        continue
                    os.remove(tf)
                    logger.info(f"Cleaned up temporary upload file on session clear: {tf}")
            except Exception as e:
                logger.warning(f"Could not remove temporary file {tf}: {e}")
        session["temp_files_to_cleanup"] = []

    def start_run(self, session_id: str) -> int:
        """Invalidate any previous task and prepare an entirely fresh stream."""
        session = self.sessions[session_id]
        session["run_seq"] += 1
        session["stop_event"].set()  # tells an older task to stop promptly
        old_queue = session["queue"]
        try:
            if old_queue.full():
                old_queue.get_nowait()
            old_queue.put_nowait(None)  # close old MJPEG consumers
        except (asyncio.QueueEmpty, asyncio.QueueFull):
            pass
        session["queue"] = asyncio.Queue(maxsize=2)
        session["stop_event"] = asyncio.Event()
        session["status"] = "processing"
        self._reset_session_stats(session_id)
        return session["run_seq"]

    def _is_current_run(self, session_id: str, run_seq: int) -> bool:
        session = self.sessions.get(session_id)
        return bool(session and session.get("run_seq") == run_seq)

    def update_expected_ducks(self, session_id: str, count: int):
        session = self.sessions.get(session_id)
        if session:
            session["expected_ducks"] = count
            session["stats"]["expected_duck_count"] = count
            tray_counter = session.get("tray_counter") or session.get("analyzer")
            if tray_counter:
                if hasattr(tray_counter, "expected"):
                    tray_counter.expected = int(count)
                if hasattr(tray_counter, "_expected_fixed"):
                    tray_counter._expected_fixed = int(count)

    def _reset_session_stats(self, session_id: str):
        """Single source of truth for what a 'fresh run' of a session looks
        like. Used at the start of every process_video_task call, so a
        stop -> restart (replay) never leaks stale thumbnails/ids/reasons
        from a previous run into the new one. video_router.py should NOT
        hand-reset individual stat fields itself -- call this instead, so
        the two never drift out of sync."""
        session = self.sessions.get(session_id)
        if not session:
            return
        expected = session.get("expected_ducks", session["stats"].get("expected_duck_count", 18))
        session["stats"].update({
            "status": "processing",
            "frames_processed": 0,
            "total_frames": session["stats"].get("total_frames", 0),
            "progress": 0.0,
            "fps": 0.0,
            "detected_duck_count": 0,
            "expected_duck_count": expected,
            "other_count": 0,
            "detected_other_toy_count": 0,
            "anchor_locked": False,
            "hand_detected": False,
            "missing_ids": [],
            "added_ids": [],
            "excess_ids": [],
            "excess_count": 0,
            "other_ids": [],
            "reasons": [],
            "thumbnails": [],
            "detections": [],
        })

    def _resolve_model_path(self, configured_path: str, ml_dir: str) -> str:
        """Return the absolute path to best.pt, working on ANY machine.
        Priority (first existing file wins):
          1. app/ml/model/best.pt      <- canonical production location
          2. app/ml/models/best.pt     <- legacy location
          3. Frozen-bundle paths       <- PyInstaller _MEIPASS
          4. configured_path from config.yaml (relative resolved vs ml_dir,
             or absolute if it exists on THIS machine)  <- last resort
        """
        candidates = [
            os.path.join(ml_dir, "model", "best.pt"),         # canonical
            os.path.join(ml_dir, "debug", "best.pt"),         # debug folder weights
            os.path.join(ml_dir, "models", "best.pt"),        # legacy
        ]
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            candidates.insert(0, os.path.join(sys._MEIPASS, "app", "ml", "model", "best.pt"))
            candidates.insert(1, os.path.join(sys._MEIPASS, "app", "ml", "models", "best.pt"))

        if configured_path:
            rel = os.path.join(ml_dir, configured_path)
            if os.path.exists(rel):
                candidates.append(rel)
            if os.path.isabs(configured_path) and os.path.exists(configured_path):
                candidates.append(configured_path)

        for cand in candidates:
            if cand and os.path.exists(cand):
                logger.info(f"[MODEL] Using weights: {cand}")
                return cand

        checked = "\n  ".join(c for c in candidates if c)
        raise FileNotFoundError(
            "Could not find best.pt. Checked:\n  " + checked +
            f"\n\nPlace your YOLO weights at: {os.path.join(ml_dir, 'model', 'best.pt')}"
        )

    async def process_video_task(self, session_id: str, temp_file_path: str,
                                 original_filename: Optional[str] = None,
                                 run_seq: Optional[int] = None):
        session = self.sessions.get(session_id)
        if not session or (run_seq is not None and not self._is_current_run(session_id, run_seq)):
            return

        # Direct callers without a router generation still get a generation.
        if run_seq is None:
            run_seq = session.get("run_seq", 0)

        session["temp_file"] = temp_file_path
        if original_filename:
            session["original_filename"] = original_filename
            session["stats"]["original_filename"] = original_filename

        # Always start a run with a clean stats slate -- see _reset_session_stats
        # docstring for why this can't be left to the caller (router).
        # start_run has already reset these fields.  Do not let a superseded
        # task reset statistics belonging to its replacement.
        if not self._is_current_run(session_id, run_seq):
            return
        
        # Tracks whether THIS task successfully claimed the cross-kind
        # inference lock, so the finally block releases it exactly once,
        # and only if it was actually acquired.
        claimed_inference_lock = False

        async with self._gpu_lock:
            if not self._is_current_run(session_id, run_seq):
                return
            # Closes the race where an already-queued session could start
            # running just as a training job claims the GPU. create_session()
            # blocks NEW sessions, this blocks ones that were queued just before
            # training set the flag.
            if app_state.get_mode() == "TRAINING":
                session["status"] = "error"
                session["stats"]["status"] = "error"
                session["stats"]["reasons"] = ["Training is in progress -- try again after it finishes"]
                await self._cleanup_session(session_id)
                return

            if session["stop_event"].is_set():
                session["status"] = "stopped"
                session["stats"]["status"] = "stopped"
                await self._cleanup_session(session_id)
                return

            # Real claim on the GPU, right before we actually start loading a
            # model / running frames. Rejects if a camera session is active
            # (or training raced in between the check above and here).
            if not app_state.try_enter_inference("video"):
                session["status"] = "error"
                session["stats"]["status"] = "error"
                session["stats"]["reasons"] = [
                    "GPU is currently in use by another inference session (camera or training) -- try again shortly"
                ]
                await self._cleanup_session(session_id)
                return
            claimed_inference_lock = True

            session["status"] = "processing"
            session["stats"]["status"] = "processing"
            session["is_task_active"] = True
            
            logger.info(f"Starting inference for session {session_id}")
            
            cap = None
            out_writer = None
            try:
                if TrayCounter is None:
                    raise RuntimeError("traycount package is not installed.")

                # ml_dir = folder that contains this service file (app/ml/)
                ml_dir = os.path.dirname(os.path.abspath(__file__))

                # Session output directory
                try:
                    from app.core.app_paths import get_ml_output_dir
                    base_output_dir = str(get_ml_output_dir())
                except Exception:
                    base_output_dir = os.path.join(tempfile.gettempdir(), "vision_monitor_output")
                session_dir = os.path.join(base_output_dir, session_id)
                os.makedirs(session_dir, exist_ok=True)
                session["session_dir"] = session_dir

                # Annotated video output filename
                orig_name = original_filename or session.get("original_filename")
                if orig_name:
                    base_name = os.path.splitext(os.path.basename(orig_name))[0]
                    video_filename = f"{base_name}.mp4" if base_name.startswith("annotated_") else f"annotated_{base_name}.mp4"
                else:
                    video_filename = f"annotated_{session_id}.mp4"

                output_path = os.path.join(session_dir, video_filename)
                results_json_path = os.path.join(session_dir, "results.json")

                # Force FFmpeg backend. The default MSMF backend on Windows often deadlocks in PyInstaller.
                cap = cv2.VideoCapture(temp_file_path, cv2.CAP_FFMPEG)
                if not cap.isOpened():
                    # Some browser-uploaded codecs are not readable by the
                    # OpenCV build, but their browser-safe H.264 copy is.
                    fallback_path = session.get("browser_video_path")
                    if fallback_path and fallback_path != temp_file_path:
                        cap.release()
                        cap = cv2.VideoCapture(fallback_path, cv2.CAP_FFMPEG)
                        if cap.isOpened():
                            logger.warning("OpenCV could not open raw upload; using browser transcode fallback")
                    if not cap.isOpened():
                        raise ValueError(f"Could not open video file: {temp_file_path}")

                raw_fps = cap.get(cv2.CAP_PROP_FPS)
                video_fps = raw_fps if (raw_fps and 10.0 <= raw_fps <= 120.0) else 30.0

                tray_counter = self._get_or_create_tray_counter(self.config_path)
                logger.info(f"Resetting TrayCounter for video session {session_id} (fps={video_fps}, expected={session['expected_ducks']})")
                tray_counter.reset(
                    fps=video_fps,
                    expected_duck_count=session["expected_ducks"],
                    session=session_id,
                )

                session["tray_counter"] = tray_counter
                session["analyzer"] = tray_counter  # compatibility
                session["stats"]["output_dir"] = session_dir
                session["stats"]["results_json_path"] = results_json_path

                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
                if total_frames <= 0:
                    cached_total = session.get("stats", {}).get("total_frames", 0)
                    if cached_total and cached_total > 0:
                        total_frames = cached_total
                    else:
                        try:
                            import imageio_ffmpeg
                            nframes, _ = imageio_ffmpeg.count_frames_and_secs(temp_file_path)
                            if nframes and nframes > 0:
                                total_frames = int(nframes)
                        except Exception:
                            pass
                session["stats"]["total_frames"] = max(1, total_frames)
                
                # Setup VideoWriter to save annotated output
                raw_fps = cap.get(cv2.CAP_PROP_FPS)
                video_fps = raw_fps if (raw_fps and 10.0 <= raw_fps <= 120.0) else 30.0
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                out_writer = cv2.VideoWriter(output_path, fourcc, video_fps, (width, height))
                session["stats"]["output_file"] = output_path
                
                frame_idx = 0
                start_time = time.time()
                consecutive_failures = 0
                pending = None
                # Abort only after 30 consecutive frame failures (not 10) so transient CUDA
                # errors on fresh user installs (driver warm-up, OOM spikes) don't kill the
                # session prematurely. The per-frame CPU fallback will already have fired by
                # then if the GPU was recoverable, so 30 is still a reasonable hard ceiling.
                max_consecutive_failures = 30
                # Hard ceiling on a single frame's inference time. Without this, a frame that
                # trips an infinite loop / native-library stall inside DuckAnalyzer.process_frame
                # (no exception raised, no return -- just stuck) hangs the whole session forever
                # with no log line and no way to recover. A timeout raises asyncio.TimeoutError,
                # which flows into the SAME frame_err handling below (CPU fallback, consecutive
                # failure counting, eventual clean abort) instead of freezing indefinitely.
                per_frame_timeout_s = 20.0

                while not session["stop_event"].is_set() and self._is_current_run(session_id, run_seq):
                    ret, frame = cap.read()
                    # A Stop -> Start may replace this task while OpenCV was
                    # decoding. Never publish even one old frame/stat update
                    # into the replacement run.
                    if not self._is_current_run(session_id, run_seq):
                        break

                    if not ret or frame is None:
                        # Transient decode drop recovery: try reading up to 5 times
                        # before declaring genuine EOF to prevent premature cutoff (e.g. at frame 716)
                        recovered_frame = None
                        for _ in range(5):
                            r_retry, f_retry = cap.read()
                            if r_retry and f_retry is not None:
                                recovered_frame = f_retry
                                break
                        if recovered_frame is not None:
                            frame = recovered_frame
                            ret = True
                        else:
                            # Genuine End of Video
                            logger.info(f"Session {session_id}: reached true EOF at frame {frame_idx} (estimated total: {total_frames})")
                            if frame_idx > 0:
                                session["stats"]["total_frames"] = frame_idx
                            session["status"] = "completed"
                            session["stats"]["status"] = "completed"
                            session["stats"]["progress"] = 100.0
                            break
                    
                    height, width = frame.shape[:2]
                    frame_idx += 1
                    if frame_idx > session["stats"]["total_frames"]:
                        session["stats"]["total_frames"] = frame_idx

                    if frame_idx % 50 == 0:
                        logger.info(
                            f"Session {session_id}: heartbeat -- frame {frame_idx}/{total_frames} "
                            f"({round(time.time() - start_time, 1)}s elapsed)"
                        )

                    # Run heavy ML inference in a background thread with torch.inference_mode()
                    # so no computation graphs or gradient memory leak across long video runs
                    loop = asyncio.get_running_loop()
                    annotated_frame = frame.copy()

                    def _infer_frame(engine, frame_in):
                        if torch is not None:
                            with torch.inference_mode():
                                return engine.process_frame(frame_in)
                        return engine.process_frame(frame_in)

                    try:
                        pending = loop.run_in_executor(
                            self._ml_executor, _infer_frame, tray_counter, annotated_frame
                        )
                        result = await asyncio.wait_for(
                            pending,
                            timeout=per_frame_timeout_s,
                        )
                        if not self._is_current_run(session_id, run_seq):
                            break
                        consecutive_failures = 0
                    except asyncio.TimeoutError as frame_err:
                        consecutive_failures += 1
                        logger.warning(
                            f"Session {session_id}: frame {frame_idx} inference TIMED OUT after "
                            f"{per_frame_timeout_s}s ({consecutive_failures}/{max_consecutive_failures}) "
                            "-- treating as a failed frame; processing continues with the next frame."
                        )
                        if consecutive_failures >= max_consecutive_failures:
                            raise RuntimeError(
                                f"Aborting session: {max_consecutive_failures} consecutive frame failures "
                                f"(last: timeout after {per_frame_timeout_s}s on frame {frame_idx})"
                            ) from frame_err
                        result = {
                            "status": session["stats"].get("status", "processing"),
                            "detected_duck_count": session["stats"].get("detected_duck_count", 0),
                            "expected_duck_count": session["stats"].get("expected_duck_count", 0),
                            "other_count": session["stats"].get("other_count", 0),
                            "hand_detected": False,
                            "missing_ids": [], "added_ids": [], "other_ids": [],
                            "detections": [], "thumbnails": [],
                            "annotated_frame": annotated_frame,
                        }
                    except Exception as frame_err:
                        consecutive_failures += 1
                        logger.warning(
                            f"Session {session_id}: frame {frame_idx} inference failed "
                            f"({consecutive_failures}/{max_consecutive_failures}) "
                            f"[{type(frame_err).__name__}]: {frame_err}",
                            exc_info=True
                        )
                        if consecutive_failures >= max_consecutive_failures:
                            raise RuntimeError(
                                f"Aborting session: {max_consecutive_failures} consecutive frame failures (last error: {frame_err})"
                            ) from frame_err

                        result = {
                            "status": session["stats"].get("status", "processing"),
                            "detected_duck_count": session["stats"].get("detected_duck_count", 0),
                            "expected_duck_count": session["stats"].get("expected_duck_count", 0),
                            "other_count": session["stats"].get("other_count", 0),
                            "hand_detected": False,
                            "missing_ids": [], "added_ids": [], "other_ids": [],
                            "detections": [], "thumbnails": [],
                            "annotated_frame": annotated_frame,
                        }
                    
                    # Extract the annotated frame from analyzer result if present
                    if not self._is_current_run(session_id, run_seq):
                        break

                    try:
                        annotated_frame = tray_counter.draw(frame)
                    except Exception as draw_err:
                        logger.warning(f"TrayCounter draw failed on video frame {frame_idx}: {draw_err}")
                        annotated_frame = frame

                    # Write the fully annotated frame (drawn by TrayCounter) to the MP4
                    if out_writer:
                        out_writer.write(annotated_frame)

                    # Stream raw (un-annotated) frame to frontend; frontend renders detection boxes itself
                    success, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                    if success:
                        frame_bytes = buffer.tobytes()
                        session["last_frame_bytes"] = frame_bytes
                        if frame_idx % 30 == 1 and session_dir and os.path.isdir(session_dir):
                            try:
                                with open(os.path.join(session_dir, "last_frame.jpg"), "wb") as lf:
                                    lf.write(frame_bytes)
                            except Exception:
                                pass
                        try:
                            if session["queue"].full():
                                session["queue"].get_nowait()
                            session["queue"].put_nowait(frame_bytes)
                        except asyncio.QueueFull:
                            pass

                    status = result.get("status", session["status"])
                    counts = result.get("counts") or {}
                    detected_ducks = counts.get("ducks", 0) if counts else session["expected_ducks"]
                    other_count = counts.get("non_ducks", 0) if counts else 0
                    missing_ids  = list(result.get("missing_ids", []))
                    added_ids    = list(result.get("added_ids", []))
                    excess_ids   = list(result.get("excess_ids", []))
                    excess_count = result.get("excess_count", len(excess_ids))
                    other_ids    = list(result.get("other_ids", []))
                    reasons      = list(result.get("reasons", []))
                    mask_anomalies = list(result.get("mask_anomalies", []))
                    false_mask_ids = [int(i) for m in mask_anomalies if m.get("type") == "false_mask" for i in m.get("ids", [])]
                    mask_missing_ids = [int(i) for m in mask_anomalies if m.get("type") == "mask_missing" for i in m.get("ids", [])]
                    hand_detected = bool(result.get("paused") or status == "HAND")
                    anchor_locked = bool(result.get("state") != "start" and status != "WARMING")
                    is_anomaly_frame = (status == "ANOMALY")

                    # 1. Collect all toy templates from TrayCounter (TrayCounter caches templates for every visible duck)
                    all_templates = {}
                    if hasattr(tray_counter, "templates"):
                        try:
                            all_templates = tray_counter.templates() or {}
                        except Exception:
                            all_templates = {}

                    raw_thumbs = {}
                    if isinstance(all_templates, dict):
                        for tid, tdata in all_templates.items():
                            if isinstance(tdata, dict) and tdata.get("b64"):
                                raw_thumbs[int(tid)] = tdata["b64"]
                            elif isinstance(tdata, str):
                                raw_thumbs[int(tid)] = tdata

                    frame_thumbs = result.get("thumbnails", {})
                    if isinstance(frame_thumbs, dict):
                        for tid, b64_val in frame_thumbs.items():
                            if b64_val:
                                raw_thumbs[int(tid)] = b64_val

                    # Include any image crops provided directly in mask_anomalies
                    for m in mask_anomalies:
                        m_img = m.get("image")
                        if m_img:
                            for fid in m.get("ids", []):
                                if int(fid) not in raw_thumbs:
                                    raw_thumbs[int(fid)] = m_img

                    # 2. Map toys to detections with computed bounding boxes for frontend
                    detections = []
                    toys = result.get("toys", [])
                    for t in toys:
                        outline = t.get("outline", [])
                        all_pts = [pt for poly in outline for pt in poly] if outline else []
                        if all_pts:
                            xs = [p[0] for p in all_pts]
                            ys = [p[1] for p in all_pts]
                            bbox = [round(min(xs), 1), round(min(ys), 1), round(max(xs), 1), round(max(ys), 1)]
                        else:
                            c = t.get("center", [0, 0])
                            bbox = [c[0] - 20, c[1] - 20, c[0] + 20, c[1] + 20]
                        
                        toy_id = t.get("id")
                        is_duck = (t.get("kind") == "duck")
                        is_excess = toy_id in excess_ids
                        is_false_mask = toy_id in false_mask_ids
                        is_mask_missing = toy_id in mask_missing_ids
                        is_toy_anomaly = is_excess or (not is_duck) or (toy_id in other_ids) or is_false_mask or is_mask_missing

                        thumb_uri = None
                        if toy_id in raw_thumbs:
                            raw_b = raw_thumbs[toy_id]
                            thumb_uri = raw_b if str(raw_b).startswith("data:image") else f"data:image/jpeg;base64,{raw_b}"

                        detections.append({
                            "id": toy_id,
                            "species": "Duck" if is_duck else "Unknown",
                            "class_name": t.get("kind"),
                            "confidence": t.get("confidence", 0.9),
                            "bbox": bbox,
                            "outline": outline,
                            "isAnomaly": is_toy_anomaly,
                            "excess": is_excess,
                            "false_mask": is_false_mask,
                            "mask_anomaly": "false_mask" if is_false_mask else ("mask_missing" if is_mask_missing else None),
                            "provisional": not anchor_locked,
                            "thumbnail": thumb_uri,
                        })

                    # 3. Accumulate thumbnails for gallery
                    for toy_id, b64_str in raw_thumbs.items():
                        if not b64_str:
                            continue
                        b64_uri = b64_str if str(b64_str).startswith("data:image") else f"data:image/jpeg;base64,{b64_str}"
                        if toy_id in missing_ids:
                            evt = "missing"
                        elif toy_id in false_mask_ids:
                            evt = "false_mask"
                        elif toy_id in mask_missing_ids:
                            evt = "mask_missing"
                        elif toy_id in excess_ids:
                            evt = "excess"
                        elif toy_id in added_ids:
                            evt = "added"
                        elif toy_id in other_ids:
                            evt = "other_present"
                        else:
                            evt = "confirmed"

                        thumb_entry = {
                            "id": toy_id,
                            "event": evt,
                            "thumbnail": b64_uri,
                            "frame": frame_idx,
                        }
                        existing_entry = next((t for t in session["stats"]["thumbnails"] if t.get("id") == toy_id), None)
                        if existing_entry is not None:
                            existing_entry["event"] = evt
                            if b64_uri:
                                existing_entry["thumbnail"] = b64_uri
                        else:
                            session["stats"]["thumbnails"].append(thumb_entry)

                    elapsed  = time.time() - start_time
                    fps      = frame_idx / elapsed if elapsed > 0 else 0
                    if total_frames > 0 and frame_idx < total_frames:
                        progress = min(99.0, round((frame_idx / total_frames) * 100, 1))
                    elif total_frames > 0 and frame_idx >= total_frames:
                        progress = 99.0
                    else:
                        progress = 0.0

                    # Update stats — forward whl output keys directly without rounding
                    raw_latency = result.get("latency_ms")
                    if isinstance(raw_latency, dict):
                        latency_val = raw_latency.get("total", 0)
                    elif isinstance(raw_latency, (int, float)):
                        latency_val = raw_latency
                    else:
                        latency_val = 0

                    session["stats"].update({
                        "status":                 status,
                        "frames_processed":       frame_idx,
                        "frame":                  result.get("frame_index", frame_idx),
                        "progress":               round(progress, 1),
                        "fps":                    round(fps, 1),
                        "latency_ms":             latency_val,
                        "latency_breakdown":      raw_latency,
                        "detected_duck_count":    detected_ducks,
                        "expected_duck_count":    session.get("expected_ducks") or session.get("expected_duck_count", 18),
                        "other_count":            other_count,
                        "detected_other_toy_count": other_count,   # alias for older frontend code
                        "anchor_locked":          anchor_locked,
                        "hand_detected":          hand_detected,
                        "missing_ids":            missing_ids,
                        "missing_count":          len(missing_ids),
                        "added_ids":              added_ids,
                        "added_count":            len(added_ids),
                        "excess_ids":             excess_ids,
                        "excess_count":           excess_count,
                        "other_ids":              other_ids,
                        "mask_anomalies":          mask_anomalies,
                        "reasons":                reasons,
                        "detections":             detections,
                        "is_anomaly_frame":       is_anomaly_frame,
                        "video_width":            width,
                        "video_height":           height,
                    })
                    
                    # Yield control back to loop so FastAPI can serve other requests and check for stop signals
                    if session["stop_event"].is_set():
                        break
                    await asyncio.sleep(0.001)

                if session.get("status") == "completed" and self._is_current_run(session_id, run_seq):
                    # True EOF was reached; maintain completed status even if a stop event was set afterwards
                    session["stats"]["status"] = "completed"
                    session["stats"]["progress"] = 100.0
                    if frame_idx > 0:
                        session["stats"]["total_frames"] = frame_idx
                        session["stats"]["frames_processed"] = frame_idx
                elif session["stop_event"].is_set() and self._is_current_run(session_id, run_seq):
                    session["status"] = "stopped"
                    session["stats"]["status"] = "stopped"
                elif self._is_current_run(session_id, run_seq):
                    session["status"] = "completed"
                    session["stats"]["status"] = "completed"
                    session["stats"]["progress"] = 100.0
                    if frame_idx > 0:
                        session["stats"]["total_frames"] = frame_idx
                        session["stats"]["frames_processed"] = frame_idx

                # Persist the final frame on disk so get_last_frame can serve it even across reloads
                last_bytes = session.get("last_frame_bytes")
                if last_bytes and session_dir and os.path.isdir(session_dir):
                    try:
                        with open(os.path.join(session_dir, "last_frame.jpg"), "wb") as f:
                            f.write(last_bytes)
                    except Exception as e:
                        logger.warning(f"Could not persist last_frame.jpg for session {session_id}: {e}")

                # analyzer.py does not write results.json itself -- that path
                # was being set on session_cfg but nothing ever wrote to it.
                # Write the final session summary here instead.
                try:
                    import json
                    with open(results_json_path, "w") as f:
                        json.dump(session["stats"], f, indent=2, default=str)
                except Exception as e:
                    logger.warning(f"Could not write results.json for session {session_id}: {e}")
                    
            except Exception as e:
                logger.error(f"Inference error in session {session_id}: {e}", exc_info=True)
                session["status"] = "error"
                session["stats"]["status"] = "error"
                cause = getattr(e, "__cause__", None)
                if cause and str(cause) not in str(e):
                    session["stats"]["reasons"] = [f"{e} (Root cause: {cause})"]
                else:
                    session["stats"]["reasons"] = [str(e)]
            finally:
                session["is_task_active"] = False
                if out_writer:
                    out_writer.release()
                if cap:
                    cap.release()

                # NOTE: Source video files (temp_file_path / inference_video_path)
                # are preserved so the user can pause, stop, and restart inference
                # at any time without receiving "Video file not found".
                # Cleanup is only performed when the session is explicitly cleared.

                # Release per-session engine references while keeping model weights in GPU for next session
                session["tray_counter"] = None
                session["analyzer"] = None

                try:
                    if pending:
                        # Give the previous frame a hard limit to finish before declaring the thread deadlocked
                        await asyncio.wait_for(pending, timeout=2.0)
                except asyncio.TimeoutError:
                    logger.error("FATAL: Previous ML frame is STILL running after timeout! The ML thread is deadlocked.")
                    raise RuntimeError("ML thread deadlocked. Session aborted to prevent application freeze.")
                except Exception:
                    pass

                # Release the cross-kind GPU claim exactly once, only if this
                # task actually acquired it.
                if claimed_inference_lock:
                    app_state.exit_inference("video")

                # Never close the replacement stream or schedule expiry for a
                # newer run of this same session.
                if self._is_current_run(session_id, run_seq):
                    await self._cleanup_session(session_id)
                logger.info(f"Finished inference for session {session_id} with status {session['status']}")

        # ── End of async with self._gpu_lock ──
        # Offload archiving to background I/O executor OUTSIDE of _gpu_lock,
        # and ONLY when inference genuinely finished/completed (never stall a restart on stop/pause).
        if session.get("status") == "completed":
            self._archive_completed_session(
                session_id=session_id,
                session_dir=session_dir,
                output_path=output_path,
                video_filename=video_filename,
                results_json_path=results_json_path,
            )

    def _archive_completed_session(
        self,
        session_id: str,
        session_dir: str,
        output_path: str,
        video_filename: str,
        results_json_path: str,
    ):
        """Asynchronously archives final video and artifacts in the background
        without blocking the GPU lock or delaying new inference runs."""
        def _do_archive():
            try:
                from datetime import datetime
                import shutil
                from app.core.app_paths import get_desktop_dir

                desktop = get_desktop_dir()
                today_str = datetime.now().strftime("%Y-%m-%d")
                archive_dir = os.path.join(str(desktop), "inference_results", today_str, session_id)
                os.makedirs(archive_dir, exist_ok=True)

                if os.path.abspath(session_dir) != os.path.abspath(archive_dir):
                    if output_path and os.path.exists(output_path):
                        dest_video = os.path.join(archive_dir, video_filename)
                        shutil.copy2(output_path, dest_video)
                        session = self.sessions.get(session_id)
                        if session and "stats" in session:
                            session["stats"]["output_file"] = dest_video
                        logger.info(f"[ARCHIVE] Saved annotated video copy to Desktop: {dest_video}")

                    if results_json_path and os.path.exists(results_json_path):
                        shutil.copy2(results_json_path, archive_dir)

                    last_frame_file = os.path.join(session_dir, "last_frame.jpg")
                    if os.path.exists(last_frame_file):
                        shutil.copy2(last_frame_file, archive_dir)
                else:
                    session = self.sessions.get(session_id)
                    if session and "stats" in session:
                        session["stats"]["output_file"] = output_path
                    logger.info(f"[ARCHIVE] Video and artifacts saved directly on Desktop: {session_dir}")

                session = self.sessions.get(session_id)
                if session and "stats" in session:
                    session["stats"]["archived_results_dir"] = archive_dir
                logger.info(f"[ARCHIVE] Successfully finalized inference results in: {archive_dir}")
            except Exception as arch_err:
                logger.warning(f"[ARCHIVE] Could not finalize inference results to Desktop: {arch_err}")

        self._io_executor.submit(_do_archive)

    async def _cleanup_session(self, session_id: str):
        session = self.sessions.get(session_id)
        if not session:
            return
            
        # Send graceful termination to the stream generator.
        # Drain a slot if full to make room, then always put None sentinel in sequence.
        try:
            q = session.get("queue")
            if q is not None:
                if q.full():
                    try:
                        q.get_nowait()
                    except (asyncio.QueueEmpty, ValueError):
                        pass
                q.put_nowait(None)
        except Exception:
            pass

# Primary export for video inference
video_inference_service = VideoInferenceService()

# Backward compatibility alias
ml_inference_service = video_inference_service