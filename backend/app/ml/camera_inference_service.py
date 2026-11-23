"""
camera_inference_service.py
===========================
Handles live, real-time per-frame inference (camera feeds, OAK streams, RTSP).

When to use this file:
- A caller (e.g. `oak_camera_service.py`) captures one frame at a time from a camera
  and needs real-time duck detection, counting, and anomaly classification returned immediately.
- Manages fast in-memory sessions, per-frame tracking states, anchor counts, and idle session eviction.

When NOT to use this file:
- If you are processing a full uploaded/recorded video file (MP4/AVI) in the background,
  use `video_inference_service.py` instead!

Main functions:
- `run_inference(frame, session_id, expected_duck_count, video_name)`
- `get_session_status(session_id)`
- `update_expected_ducks(session_id, new_count)`
- `clear_session(session_id)`
- `reset_session_for_next_video(session_id)`
"""

import os
import sys
import time
import logging
import threading
import yaml
from typing import Dict, Any, Optional, Tuple

try:
    from traycount import TrayCounter
except ImportError:
    TrayCounter = None

from app.ml import app_state  # shared GPU mutual-exclusion flag (video vs camera vs training)
try:
    import torch
except ImportError:
    torch = None

from app.core.logger import setup_logger

logger = setup_logger("camera-inference")

_shared_tray_counter: Optional[Any] = None

def _set_camera_tray_counter(engine: Any) -> None:
    global _shared_tray_counter
    _shared_tray_counter = engine
    logger.info("[CAMERA] Shared TrayCounter registered successfully.")

# Backward compatibility alias
_set_camera_analyzer = _set_camera_tray_counter


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
        return False

    for attempt in range(1, 4):
        try:
            if not torch.cuda.is_initialized():
                torch.cuda.init()
            torch.cuda.set_device(0)
            
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

_ML_DIR = os.path.dirname(os.path.abspath(__file__))
_candidates = [
    os.path.join(_ML_DIR, "config", "config.yaml"),
    os.path.join(_ML_DIR, "config.yaml"),
    os.path.join(os.path.dirname(_ML_DIR), "config.yaml"),
]
if getattr(sys, "_MEIPASS", None):
    _candidates.insert(0, os.path.join(sys._MEIPASS, "app", "ml", "config", "config.yaml"))
    _candidates.insert(1, os.path.join(sys._MEIPASS, "app", "ml", "config.yaml"))
_CONFIG_PATH = next((c for c in _candidates if os.path.exists(c)), _candidates[0])
_SESSION_IDLE_TIMEOUT_SEC = 300

_sessions: Dict[str, Dict[str, Any]] = {}

_sessions_lock = threading.Lock()

# Shared DuckAnalyzer instance: loaded once at app startup (by main.py lifespan)
# and reused for every camera session. Each session resets the analyzer state
# (anchor, trackers, warmup) but keeps the YOLO weights in GPU memory.
# If startup preload hasn't run yet (dev mode, or preload failed), falls back
# to per-session construction (original behavior).


def _get_or_create_session(session_id: str, expected_duck_count: int,
                            original_filename: Optional[str]) -> Dict[str, Any]:
    # Fast path: session already exists, no need to touch the lock at all.
    existing = _sessions.get(session_id)
    if existing is not None and "tray_counter" in existing:
        return existing

    with _sessions_lock:
        if session_id in _sessions and "tray_counter" in _sessions[session_id]:
            return _sessions[session_id]
        
        # If there is a shell session holding the expected count, grab it
        shell = _sessions.get(session_id)
        if shell and "expected_duck_count" in shell:
            expected_duck_count = shell["expected_duck_count"]

        if not os.path.isfile(_CONFIG_PATH):
            raise RuntimeError(
                f"config.yaml not found at {_CONFIG_PATH} -- camera inference needs "
                "config.yaml.")

        if not app_state.try_enter_inference("camera"):
            active_kind = app_state.get_active_inference_kind()
            if app_state.get_mode() == "TRAINING":
                raise RuntimeError(
                    "Training is in progress -- camera inference cannot start")
            raise RuntimeError(
                f"GPU is currently in use by {active_kind or 'another process'} "
                "-- try again shortly")

        try:
            global _shared_tray_counter
            if _shared_tray_counter is not None:
                tray_counter = _shared_tray_counter
            else:
                logger.info(f"Creating fresh TrayCounter for camera session {session_id}.")
                tray_counter = TrayCounter(_CONFIG_PATH)
                tray_counter.load_model()
                _shared_tray_counter = tray_counter

            logger.info(f"Resetting TrayCounter for camera session {session_id} (fps=30.0, expected={expected_duck_count})")
            tray_counter.reset(fps=30.0, expected_duck_count=expected_duck_count, session=session_id)

        except Exception as e:
            logger.error(f"Failed to initialize TrayCounter for camera: {e}", exc_info=True)
            app_state.exit_inference("camera")
            raise

        session = {
            "tray_counter": tray_counter,
            "analyzer": tray_counter,  # compatibility alias
            "frames_processed": 0,
            "expected_duck_count": expected_duck_count,
            "original_filename": original_filename,
            "last_active": time.time(),
            "inference_claimed": True,
            "inference_lock": threading.Lock(),
            "thumbnails": [],
        }
        _sessions[session_id] = session
        return session


def cleanup_stale_sessions(max_idle_seconds: int = _SESSION_IDLE_TIMEOUT_SEC) -> None:
    """Call this periodically (e.g. from a FastAPI startup background task
    running every few minutes) -- without it, _sessions grows forever for
    any camera client that disconnects without calling clear_session()."""
    now = time.time()
    stale = [sid for sid, s in _sessions.items()
             if now - s.get("last_active", now) > max_idle_seconds]
    for sid in stale:
        logger.info(f"Evicting stale camera session {sid}")
        clear_session(sid)


def run_inference(frame, session_id: str, expected_duck_count: int = 18,
                   video_name: Optional[str] = None) -> Tuple[Dict[str, Any], Any]:
    """
    Returns (stats, raw_frame) as a TUPLE, not one dict -- stats is JSON-safe
    on its own (no numpy arrays inside it), raw_frame is yours to encode/send
    however your camera transport needs (matches what VideoInferenceService
    does: stats go one way, pixels go a separate way).
    """
    if TrayCounter is None:
        logger.error("traycount package not installed.")
        return {"session_id": session_id, "status": "error",
                "reasons": ["traycount package not installed"], "done": True}, None

    if app_state.get_mode() == "TRAINING":
        return {"session_id": session_id, "status": "paused",
                "reasons": ["Training is in progress -- camera inference paused"],
                "frames_processed": 0}, frame

    try:
        session = _get_or_create_session(session_id, expected_duck_count, video_name)
    except RuntimeError as e:
        logger.warning(f"Could not start camera session {session_id}: {e}")
        return {"session_id": session_id, "status": "error",
                "reasons": [str(e)], "frames_processed": 0}, frame

    session["last_active"] = time.time()
    tray_counter = session["tray_counter"]
    session["frames_processed"] += 1

    annotated_frame = frame.copy()
    t_infer_start = time.perf_counter()
    try:
        inference_lock = session.get("inference_lock")
        if torch is not None:
            with torch.inference_mode():
                if inference_lock:
                    with inference_lock:
                        result = tray_counter.process_frame(annotated_frame)
                else:
                    result = tray_counter.process_frame(annotated_frame)
        else:
            if inference_lock:
                with inference_lock:
                    result = tray_counter.process_frame(annotated_frame)
            else:
                result = tray_counter.process_frame(annotated_frame)
    except Exception as e:
        logger.error(f"Error in TrayCounter for session {session_id}: {e}", exc_info=True)
        return {"session_id": session_id, "status": "error",
                "reasons": [str(e)], "frames_processed": session["frames_processed"]}, frame

    infer_latency_ms = (time.perf_counter() - t_infer_start) * 1000
    infer_fps = round(1000.0 / infer_latency_ms, 1) if infer_latency_ms > 0 else 0.0

    try:
        annotated_frame = tray_counter.draw(frame)
    except Exception as draw_err:
        logger.warning(f"TrayCounter draw failed on camera frame {session['frames_processed']}: {draw_err}")
        annotated_frame = frame

    status = result.get("status", "NORMAL")
    counts = result.get("counts") or {}
    detected_ducks = counts.get("ducks", 0) if counts else session["expected_duck_count"]
    detected_others = counts.get("non_ducks", 0) if counts else 0
    missing_ids   = list(result.get("missing_ids", []))
    added_ids     = list(result.get("added_ids", []))
    excess_ids    = list(result.get("excess_ids", []))
    excess_count  = result.get("excess_count", len(excess_ids))
    other_ids     = list(result.get("other_ids", []))
    reasons       = list(result.get("reasons", []))
    mask_anomalies = list(result.get("mask_anomalies", []))
    false_mask_ids = [int(i) for m in mask_anomalies if m.get("type") == "false_mask" for i in m.get("ids", [])]
    mask_missing_ids = [int(i) for m in mask_anomalies if m.get("type") == "mask_missing" for i in m.get("ids", [])]
    hand_detected = bool(result.get("paused") or status == "HAND")
    anchor_locked = bool(result.get("state") != "start" and status != "WARMING")
    is_anomaly    = (status == "ANOMALY")

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

    # 3. Update session thumbnails with all available crops
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
            "frame": session["frames_processed"],
        }
        existing_idx = next((i for i, t in enumerate(session["thumbnails"]) if t.get("id") == toy_id), None)
        if existing_idx is not None:
            session["thumbnails"][existing_idx]["event"] = evt
            if b64_uri:
                session["thumbnails"][existing_idx]["thumbnail"] = b64_uri
        else:
            session["thumbnails"].append(thumb_entry)

    stats = {
        "session_id":              session_id,
        "original_filename":       session.get("original_filename"),
        "status":                  status,
        "frame":                   result.get("frame_index", session["frames_processed"]),
        "frames_processed":        session["frames_processed"],
        "fps":                     infer_fps if infer_fps > 0 else (result.get("fps") or 0),
        "latency_ms":              (result.get("latency_ms", {}).get("total", infer_latency_ms) if isinstance(result.get("latency_ms"), dict) else (result.get("latency_ms") if result.get("latency_ms") is not None else infer_latency_ms)),
        "latency_breakdown":       result.get("latency_ms"),
        "detected_duck_count":     detected_ducks,
        "expected_duck_count":     session["expected_duck_count"],
        "other_count":             detected_others,
        "detected_other_toy_count": detected_others,   # alias for older frontend code
        "anchor_locked":           anchor_locked,
        "hand_detected":           hand_detected,
        "missing_ids":             missing_ids,
        "missing_count":           len(missing_ids),
        "added_ids":               added_ids,
        "added_count":             len(added_ids),
        "excess_ids":              excess_ids,
        "excess_count":            excess_count,
        "other_ids":               other_ids,
        "mask_anomalies":          mask_anomalies,
        "reasons":                 reasons,
        "detections":              detections,
        "thumbnails":              session["thumbnails"],
        "is_anomaly_frame":        is_anomaly,
        "video_width":             frame.shape[1],
        "video_height":            frame.shape[0],
    }

    session["last_stats"] = stats
    return stats, annotated_frame


def get_session_status(session_id: str) -> Optional[Dict[str, Any]]:
    session = _sessions.get(session_id)
    if not session:
        return None
    stats = session.get("last_stats")
    if stats and isinstance(stats, dict):
        import numpy as np
        return {
            k: v for k, v in stats.items()
            if not k.startswith("_") and not isinstance(v, np.ndarray)
        }
    return stats


def update_expected_ducks(session_id: str, count: int) -> None:
    with _sessions_lock:
        session = _sessions.get(session_id)
        if not session:
            _sessions[session_id] = {
                "expected_duck_count": count,
                "last_active": time.time()
            }
            return
        session["expected_duck_count"] = count
        session["last_active"] = time.time()
        tray_counter = session.get("tray_counter")
        if tray_counter:
            tray_counter.expected = count
            tray_counter._expected_fixed = count


def clear_session(session_id: str) -> None:
    with _sessions_lock:
        session = _sessions.pop(session_id, None)
        if session:
            if session.get("inference_claimed"):
                app_state.exit_inference("camera")
            if "expected_duck_count" in session:
                if session_id not in _sessions:
                    _sessions[session_id] = {
                        "expected_duck_count": session["expected_duck_count"],
                        "last_active": time.time()
                    }
            logger.info(f"Cleared camera session {session_id}")


def reset_session_for_next_video(session_id: str) -> None:
    clear_session(session_id)