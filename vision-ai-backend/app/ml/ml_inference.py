import asyncio
import os
import time
import uuid
import logging
import yaml
from typing import Dict, Any, Optional
import cv2

import concurrent.futures

try:
    from duck_analyzer import DuckAnalyzer
except ImportError:
    try:
        from app.ml.duck_analyzer.analyzer import DuckAnalyzer
    except ImportError:
        DuckAnalyzer = None

from app.ml import app_state  # shared GPU mutual-exclusion flag with training_service.py
                               # AND with duck_camera_inference.py (video vs camera)

logger = logging.getLogger("ml-inference")

class VideoInferenceService:
    def __init__(self):
        self.sessions: Dict[str, Dict[str, Any]] = {}
        # Global lock to ensure only one GPU inference runs at a time
        self._gpu_lock = asyncio.Lock()
        # Non-blocking async background executor for saving raw/anomaly frames to disk without stalling inference
        self._io_executor = concurrent.futures.ThreadPoolExecutor(max_workers=2)
        
        # Resolve config path relative to this file
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.config_path = os.path.join(base_dir, "ml", "config.yaml")

    def stop_all_sessions(self):
        for sid, session in self.sessions.items():
            if not session["stop_event"].is_set():
                session["stop_event"].set()

    def create_session(self, expected_ducks: int = 18, original_filename: Optional[str] = None) -> str:
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
        
        session_id = str(uuid.uuid4())
        self.sessions[session_id] = {
            "status": "queued",
            "queue": asyncio.Queue(maxsize=2),
            "expected_ducks": expected_ducks,
            "original_filename": original_filename,
            "analyzer": None,
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

    def stop_session(self, session_id: str):
        session = self.sessions.get(session_id)
        if session:
            session["stop_event"].set()

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
            analyzer = session.get("analyzer")
            if analyzer:
                analyzer.set_expected_duck_count(count)

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
            "other_ids": [],
            "reasons": [],
            "thumbnails": [],
            "detections": [],
        })

    def _resolve_model_path(self, configured_path, ml_dir, base_dir):
        """Find best.pt reliably on whatever machine this process is running
        on. Always tries the portable, project-relative locations first
        (these are the ones that travel with the install/repo on any PC);
        the value written in config.yaml is only used as a last-resort
        fallback, and only if it actually exists on THIS machine. Raises
        FileNotFoundError with every path it checked if nothing is found,
        so a missing model fails loudly and clearly instead of a confusing
        error from deep inside the wheel.
        """
        import sys
        candidates = [
            os.path.join(ml_dir, "models", "best.pt"),
            os.path.join(os.path.dirname(os.path.abspath(__file__)), "models", "best.pt"),
            os.path.join(base_dir, "ml", "models", "best.pt"),
        ]
        if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
            candidates.insert(0, os.path.join(sys._MEIPASS, "app", "ml", "models", "best.pt"))
            candidates.insert(1, os.path.join(sys._MEIPASS, "models", "best.pt"))

        if configured_path:
            # Used as-is if it's absolute (and exists here); resolved
            # relative to ml_dir otherwise. Either way it's checked LAST,
            # behind the portable candidates above.
            if os.path.isabs(configured_path):
                candidates.append(configured_path)
            else:
                candidates.append(os.path.join(ml_dir, configured_path))
            candidates.append(os.path.abspath(configured_path))

        for cand in candidates:
            if cand and os.path.exists(cand):
                return cand

        checked = "\n  ".join(candidates)
        raise FileNotFoundError(
            "Could not find best.pt on this machine. Checked:\n  " + checked +
            f"\n\nPlace the model weights at {os.path.join(ml_dir, 'models', 'best.pt')} "
            "-- that path works on any PC without editing config.yaml."
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
            
            logger.info(f"Starting inference for session {session_id}")
            
            cap = None
            analyzer = None
            out_writer = None
            try:
                if DuckAnalyzer is None:
                    raise RuntimeError("DuckAnalyzer package is not installed.")

                # Unified session output directory under backend/ml/output/{session_id}/
                ml_dir = os.path.dirname(self.config_path)
                session_dir = os.path.join(ml_dir, "output", session_id)
                os.makedirs(session_dir, exist_ok=True)

                # Determine annotated video filename using original filename
                orig_name = original_filename or session.get("original_filename")
                if orig_name:
                    base_name = os.path.splitext(os.path.basename(orig_name))[0]
                    if base_name.startswith("annotated_"):
                        video_filename = f"{base_name}.mp4"
                    else:
                        video_filename = f"annotated_{base_name}.mp4"
                else:
                    video_filename = f"annotated_{session_id}.mp4"

                output_path = os.path.join(session_dir, video_filename)
                results_json_path = os.path.join(session_dir, "results.json")
                thumbnail_dir = os.path.join(session_dir, "thumbnails")
                # frames_dir / anomaly_frames_dir are NOT written by DuckAnalyzer --
                # DuckAnalyzer only writes results_json_path and thumbnail_dir.
                # Dumping every raw frame to disk is something this service adds
                # on top, and it's expensive (one .jpg per frame, every run), so
                # it's opt-in via config.yaml's save_raw_frames flag, default off.
                frames_dir = None
                anomaly_frames_dir = None
                os.makedirs(thumbnail_dir, exist_ok=True)

                # Prepare session-specific config so DuckAnalyzer outputs directly into this session's folder
                with open(self.config_path, "r") as f:
                    session_cfg = yaml.safe_load(f) or {}

                # DuckAnalyzer.__init__ reads these with NO default -- a
                # missing key raises a bare KeyError deep inside the wheel,
                # after the model may have already started loading. Fail
                # fast, with a clear message, before that happens.
                required_cfg_keys = ["model_path", "duck_class_id", "other_class_id", "conf", "row_tolerance_frac"]
                missing_keys = [k for k in required_cfg_keys if k not in session_cfg]
                if missing_keys:
                    raise ValueError(f"config.yaml is missing required key(s): {missing_keys}")

                session_cfg["results_json_path"] = results_json_path
                session_cfg["thumbnail_dir"] = thumbnail_dir
                # config.yaml's annotated_dir is a local dev path (and on the
                # ML team's machine, save_local:true). If left as-is, analyzer.py's
                # _finish() does a synchronous cv2.imwrite() to annotated_dir on
                # EVERY frame, on top of the video already being written by
                # out_writer below -- pure redundant disk I/O, and on a Linux
                # server the Windows-style path isn't even absolute (no leading
                # "/"), so it'd silently create a stray "C:" folder instead of
                # failing loudly. Disable just this half of save_local; leave
                # thumbnail saving on since that's real per-event output.
                session_cfg["annotated_dir"] = None

                # Resolve model_path in a way that works on ANY machine this
                # runs on, not just the one config.yaml happens to describe.
                #
                # The old logic only searched project-relative candidates
                # when os.path.isabs(model_path) was False -- but
                # "C:/Users/EmageVision/..." IS absolute on Windows (drive
                # letter), so on every Windows PC except the original ML
                # dev laptop, that check passed and the search was skipped
                # entirely, going straight for a path that only exists on
                # one machine. It only "worked" by accident on Linux, where
                # that same string isn't recognized as absolute.
                #
                # Fix: always check the portable, project-relative locations
                # FIRST. Only fall back to whatever config.yaml says --
                # absolute or not -- if none of those exist, and only if
                # that configured path actually exists on THIS machine.
                base_dir = os.path.dirname(ml_dir)
                model_path = self._resolve_model_path(session_cfg.get("model_path"), ml_dir, base_dir)
                session_cfg["model_path"] = model_path

                # Only save anomaly frames for desktop archive (avoid disk I/O bottleneck)
                save_raw_frames = False
                frames_dir = os.path.join(session_dir, "raw_frames")
                anomaly_frames_dir = os.path.join(session_dir, "anomaly_frames")
                os.makedirs(frames_dir, exist_ok=True)
                os.makedirs(anomaly_frames_dir, exist_ok=True)

                # Dynamic hardware detection: use GPU if CUDA is available, else fallback cleanly to CPU
                try:
                    import torch
                    if torch.cuda.is_available() and torch.cuda.device_count() > 0:
                        session_cfg["device"] = 0
                    else:
                        session_cfg["device"] = "cpu"
                except Exception:
                    session_cfg["device"] = "cpu"

                session_config_path = os.path.join(session_dir, "config.yaml")
                with open(session_config_path, "w") as f:
                    yaml.dump(session_cfg, f)

                analyzer = DuckAnalyzer(
                    session_config_path, 
                    expected_duck_count=session["expected_ducks"]
                )
                
                session["analyzer"] = analyzer
                session["stats"]["output_dir"] = session_dir
                session["stats"]["results_json_path"] = results_json_path
                session["stats"]["thumbnail_dir"] = thumbnail_dir
                if save_raw_frames:
                    session["stats"]["frames_dir"] = frames_dir
                    session["stats"]["anomaly_frames_dir"] = anomaly_frames_dir
                
                cap = cv2.VideoCapture(temp_file_path)
                if not cap.isOpened():
                    # Some browser-uploaded codecs are not readable by the
                    # OpenCV build, but their browser-safe H.264 copy is.
                    fallback_path = session.get("browser_video_path")
                    if fallback_path and fallback_path != temp_file_path:
                        cap.release()
                        cap = cv2.VideoCapture(fallback_path)
                        if cap.isOpened():
                            logger.warning("OpenCV could not open raw upload; using browser transcode fallback")
                    if not cap.isOpened():
                        raise ValueError(f"Could not open video file: {temp_file_path}")

                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                session["stats"]["total_frames"] = total_frames
                
                # Setup VideoWriter to save annotated output
                video_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
                width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
                
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                out_writer = cv2.VideoWriter(output_path, fourcc, video_fps, (width, height))
                session["stats"]["output_file"] = output_path
                
                frame_idx = 0
                start_time = time.time()
                consecutive_failures = 0
                max_consecutive_failures = 10  # abort the session if the model is failing on every frame, not just a bad one

                while not session["stop_event"].is_set() and self._is_current_run(session_id, run_seq):
                    ret, frame = cap.read()
                    # A Stop -> Start may replace this task while OpenCV was
                    # decoding. Never publish even one old frame/stat update
                    # into the replacement run.
                    if not self._is_current_run(session_id, run_seq):
                        break
                    if not ret:
                        # End of video
                        session["status"] = "completed"
                        session["stats"]["status"] = "completed"
                        break
                    
                    height, width = frame.shape[:2]
                    frame_idx += 1
                    
                    # Run the heavy ML inference in a background thread so the
                    # FastAPI event loop stays free to serve the MJPEG stream.
                    loop = asyncio.get_running_loop()
                    annotated_frame = frame.copy()
                    try:
                        result = await loop.run_in_executor(
                            None, analyzer.process_frame, annotated_frame
                        )
                        if not self._is_current_run(session_id, run_seq):
                            break
                        consecutive_failures = 0
                    except Exception as frame_err:
                        # One bad frame (corrupt decode, transient CUDA hiccup,
                        # etc.) should not kill an otherwise-healthy session --
                        # log it, skip it, keep the un-annotated frame in the
                        # stream/output so timing stays in sync, and only bail
                        # out if failures are piling up.
                        consecutive_failures += 1
                        logger.warning(
                            f"Session {session_id}: frame {frame_idx} inference failed "
                            f"({consecutive_failures}/{max_consecutive_failures}): {frame_err}"
                        )
                        if consecutive_failures >= max_consecutive_failures:
                            raise RuntimeError(
                                f"Aborting session: {max_consecutive_failures} consecutive frame failures"
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
                    if isinstance(result, dict) and "annotated_frame" in result and result["annotated_frame"] is not None:
                        annotated_frame = result["annotated_frame"]

                    # ---- pull the fields analyzer.py actually returns ----
                    # NOTE: analyzer._finish() returns "other_count", not
                    # "detected_other_species_count" / "detected_other_toy_count" --
                    # those old key names never matched, so other_count was
                    # always reading as 0 before this fix.
                    other_count = result.get("other_count", 0)

                    # "anchor_locked" is NOT part of the result dict -- it's a
                    # live attribute on the analyzer instance itself.
                    anchor_locked = bool(getattr(analyzer, "anchor_locked", False))

                    missing_ids = result.get("missing_ids", [])
                    added_ids = result.get("added_ids", [])
                    other_ids = result.get("other_ids", [])
                    hand_detected = result.get("hand_detected", False)
                    new_thumbnails = result.get("thumbnails", [])

                    # analyzer.py computes "reasons" internally but never puts
                    # it in the returned dict, so we rebuild an equivalent
                    # summary here from the fields it DOES return.
                    reasons = []
                    if hand_detected:
                        reasons.append("hand_in_frame")
                    if missing_ids:
                        reasons.append("missing_ducks")
                    if other_count > 0:
                        reasons.append("other_species_present")
                    if anchor_locked:
                        detected_now = result.get("detected_duck_count", 0)
                        expected_now = result.get("expected_duck_count", 0)
                        if detected_now < expected_now:
                            reasons.append("too_few_ducks")
                        elif detected_now > expected_now:
                            reasons.append("too_many_ducks")

                    # Write the fully annotated frame to the MP4 file
                    if out_writer:
                        out_writer.write(annotated_frame)
                    
                    # 1. Save all raw frames sequentially (un-annotated), only if
                    # save_raw_frames is enabled -- this is not something
                    # DuckAnalyzer itself needs or writes.
                    if save_raw_frames:
                        self._io_executor.submit(
                            cv2.imwrite, 
                            os.path.join(frames_dir, f"frame_{frame_idx:05d}.jpg"), 
                            frame.copy()
                        )
                    
                    # 2. Save anomaly-only frames into dedicated anomaly_frames folder,
                    # also gated behind save_raw_frames.
                    is_anomaly_frame = (
                        result.get("status") == "ANOMALY" or
                        other_count > 0 or
                        (anchor_locked and result.get("detected_duck_count", 0) != result.get("expected_duck_count", 0))
                    )
                    if is_anomaly_frame:
                        self._io_executor.submit(
                            cv2.imwrite, 
                            os.path.join(anomaly_frames_dir, f"anomaly_frame_{frame_idx:05d}.jpg"), 
                            annotated_frame.copy()
                        )
                    
                    # Stream the raw frame (un-annotated) to the frontend so frontend renders boxes cleanly
                    success, buffer = cv2.imencode('.jpg', frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
                    if success:
                        frame_bytes = buffer.tobytes()
                        session["last_frame_bytes"] = frame_bytes
                        try:
                            # If queue is full, drop frame to keep real-time behavior and avoid memory bloat
                            if session["queue"].full():
                                session["queue"].get_nowait()
                            session["queue"].put_nowait(frame_bytes)
                        except asyncio.QueueFull:
                            pass

                    # Update stats
                    elapsed = time.time() - start_time
                    fps = frame_idx / elapsed if elapsed > 0 else 0
                    progress = (frame_idx / total_frames * 100) if total_frames > 0 else 0
                    
                    # thumbnails are one-shot events (confirmed/added/other/etc,
                    # emitted once each by analyzer.py) -- accumulate them for
                    # the whole session instead of overwriting each frame.
                    if new_thumbnails:
                        existing = {
                            (str(t.get("id")), str(t.get("event")))
                            for t in session["stats"]["thumbnails"]
                        }
                        session["stats"]["thumbnails"].extend(
                            t for t in new_thumbnails
                            if (str(t.get("id")), str(t.get("event"))) not in existing
                        )

                    session["stats"].update({
                        "status": result.get("status", session["status"]),
                        "frames_processed": frame_idx,
                        "progress": round(progress, 1),
                        "fps": round(fps, 1),
                        "detected_duck_count": result.get("detected_duck_count", 0),
                        "other_count": other_count,
                        "detected_other_toy_count": other_count,  # alias, kept for older frontend code
                        "expected_duck_count": result.get("expected_duck_count", 0),
                        "anchor_locked": anchor_locked,
                        "hand_detected": hand_detected,
                        "missing_ids": missing_ids,
                        "added_ids": added_ids,
                        "other_ids": other_ids,
                        "reasons": reasons,
                        "detections": result.get("detections", []),
                        "video_width": width,
                        "video_height": height,
                    })
                    
                    # Yield control back to loop so FastAPI can serve other requests
                    # Pace the inference to match original video framerate for smooth frontend playback
                    expected_playback_time = frame_idx / (video_fps if video_fps > 0 else 30.0)
                    current_playback_time = time.time() - start_time
                    if expected_playback_time > current_playback_time:
                        await asyncio.sleep(expected_playback_time - current_playback_time)
                    else:
                        await asyncio.sleep(0.001)

                if session["stop_event"].is_set() and self._is_current_run(session_id, run_seq):
                    session["status"] = "stopped"
                    session["stats"]["status"] = "stopped"

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
                session["stats"]["reasons"] = [str(e)]
            finally:
                if analyzer:
                    analyzer.close()
                if out_writer:
                    out_writer.release()
                if cap:
                    cap.release()

                # Release the cross-kind GPU claim exactly once, only if this
                # task actually acquired it.
                if claimed_inference_lock:
                    app_state.exit_inference("video")

                # ── Archive permanent copy to Desktop/inference_results ──
                try:
                    from datetime import datetime
                    import shutil
                    from app.core.app_paths import get_desktop_dir

                    desktop = get_desktop_dir()
                    today_str = datetime.now().strftime("%Y-%m-%d")
                    if desktop:
                        archive_dir = os.path.join(str(desktop), "inference_results", today_str, session_id)
                    else:
                        base_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
                        archive_dir = os.path.join(base_root, "storage", "inference_results", today_str, session_id)

                    os.makedirs(archive_dir, exist_ok=True)

                    if os.path.exists(output_path):
                        shutil.copy2(output_path, archive_dir)
                    if os.path.exists(results_json_path):
                        shutil.copy2(results_json_path, archive_dir)
                    if os.path.exists(thumbnail_dir) and os.listdir(thumbnail_dir):
                        dest_thumb = os.path.join(archive_dir, "thumbnails")
                        if os.path.exists(dest_thumb):
                            shutil.rmtree(dest_thumb)
                        shutil.copytree(thumbnail_dir, dest_thumb)
                    if os.path.exists(frames_dir) and os.listdir(frames_dir):
                        dest_raw = os.path.join(archive_dir, "raw_frames")
                        if os.path.exists(dest_raw):
                            shutil.rmtree(dest_raw)
                        shutil.copytree(frames_dir, dest_raw)
                    if os.path.exists(anomaly_frames_dir) and os.listdir(anomaly_frames_dir):
                        dest_anom = os.path.join(archive_dir, "anomaly_frames")
                        if os.path.exists(dest_anom):
                            shutil.rmtree(dest_anom)
                        shutil.copytree(anomaly_frames_dir, dest_anom)

                    session["stats"]["archived_results_dir"] = archive_dir
                    logger.info(f"[ARCHIVE] Successfully copied finalized inference results to: {archive_dir}")
                except Exception as arch_err:
                    logger.warning(f"[ARCHIVE] Could not copy inference results to Desktop archive: {arch_err}")

                # Never close the replacement stream or schedule expiry for a
                # newer run of this same session.
                if self._is_current_run(session_id, run_seq):
                    await self._cleanup_session(session_id)
                logger.info(f"Finished inference for session {session_id} with status {session['status']}")

    async def _cleanup_session(self, session_id: str):
        session = self.sessions.get(session_id)
        if not session:
            return
            
        # Send graceful termination to the stream generator
        try:
            if not session["queue"].full():
                session["queue"].put_nowait(None)
            else:
                session["queue"].get_nowait()
                session["queue"].put_nowait(None)
        except Exception:
            pass

        # Keep session source file available for restart/replay
        # Schedule cleanup of the session after 15 minutes
        async def expire_session():
            await asyncio.sleep(900)
            temp_path = session.get("temp_file")
            if temp_path and os.path.exists(temp_path):
                try:
                    os.unlink(temp_path)
                except Exception:
                    pass
            if session_id in self.sessions:
                del self.sessions[session_id]
                logger.info(f"Expired session {session_id} from memory")
                
        asyncio.create_task(expire_session())

ml_inference_service = VideoInferenceService()
