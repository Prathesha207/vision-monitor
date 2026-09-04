"""
duck_camera_inference.py - live, per-frame inference (camera or any caller
that pushes one frame at a time and wants that frame's result back
immediately). Lives in app/ml/, next to ml_inference.py, duck_analyzer.py,
and shares the SAME config.yaml as the video-file service on purpose -- see
note below on why the old dummy-config approach was dangerous.

Splitting this from VideoInferenceService is the right call: a live camera
loop and "decode a whole file in a background task" are genuinely different
execution shapes. What matters is that both paths call DuckAnalyzer with the
SAME field names and SAME config, so they can't drift out of sync the way
the previous version had (see the field-name bug fixed below), and that both
paths respect the SAME shared GPU ownership rules in app_state, so they
can't drift out of sync with EACH OTHER either (see the two fixes below).
"""

import os
import sys
import time
import logging
import threading
import yaml
from typing import Dict, Any, Optional, Tuple

try:
    from app.ml.duck_analyzer.analyzer import DuckAnalyzer
except ImportError:
    try:
        from duck_analyzer import DuckAnalyzer
    except ImportError:
        DuckAnalyzer = None

from app.ml import app_state  # shared GPU-mode flag used by ml_inference.py too

logger = logging.getLogger("duck-camera-inference")

_sessions: Dict[str, Dict[str, Any]] = {}

_sessions_lock = threading.Lock()

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.yaml")
if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
    _meipass_config = os.path.join(sys._MEIPASS, "app", "ml", "config.yaml")
    if os.path.exists(_meipass_config):
        _CONFIG_PATH = _meipass_config

# Idle sessions get evicted after this many seconds without a frame --
_SESSION_IDLE_TIMEOUT_SEC = 900


def _get_or_create_session(session_id: str, expected_duck_count: int,
                            original_filename: Optional[str]) -> Dict[str, Any]:
    # Fast path: session already exists, no need to touch the lock at all.
    existing = _sessions.get(session_id)
    if existing is not None and "analyzer" in existing:
        return existing

    with _sessions_lock:
        if session_id in _sessions and "analyzer" in _sessions[session_id]:
            return _sessions[session_id]
        
        # If there is a shell session holding the expected count, grab it
        shell = _sessions.get(session_id)
        if shell and "expected_duck_count" in shell:
            expected_duck_count = shell["expected_duck_count"]

        if not os.path.isfile(_CONFIG_PATH):
            raise RuntimeError(
                f"config.yaml not found at {_CONFIG_PATH} -- camera inference needs "
                "the same real config the video pipeline uses, not a stub.")

        if not app_state.try_enter_inference("camera"):
            active_kind = app_state.get_active_inference_kind()
            if app_state.get_mode() == "TRAINING":
                raise RuntimeError(
                    "Training is in progress -- camera inference cannot start")
            raise RuntimeError(
                f"GPU is currently in use by {active_kind or 'another process'} "
                "-- try again shortly")

        try:
            with open(_CONFIG_PATH, "r") as f:
                cfg = yaml.safe_load(f) or {}

            # Resolve absolute model path
            model_path = cfg.get("model_path", "app/ml/models/best.pt")
            if not os.path.isabs(model_path):
                ml_dir = os.path.dirname(os.path.abspath(__file__))
                candidates = [
                    os.path.join(ml_dir, "models", "best.pt"),
                    os.path.abspath(model_path)
                ]
                if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
                    candidates.insert(0, os.path.join(sys._MEIPASS, "app", "ml", "models", "best.pt"))
                    candidates.insert(1, os.path.join(sys._MEIPASS, "models", "best.pt"))
                for cand in candidates:
                    if os.path.exists(cand):
                        model_path = cand
                        break
            cfg["model_path"] = model_path
            
            # Temporary session config path with resolved model_path
            session_cfg_dir = os.path.join(os.path.dirname(_CONFIG_PATH), "sessions")
            os.makedirs(session_cfg_dir, exist_ok=True)
            session_cfg_path = os.path.join(session_cfg_dir, f"camera_config_{session_id}.yaml")
            with open(session_cfg_path, "w") as f:
                yaml.dump(cfg, f)

            analyzer = DuckAnalyzer(session_cfg_path, expected_duck_count=expected_duck_count)
        except Exception:
            app_state.exit_inference("camera")
            raise

        session = {
            "analyzer": analyzer,
            "frames_processed": 0,
            "expected_duck_count": expected_duck_count,
            "original_filename": original_filename,
            "last_active": time.time(),
            "inference_claimed": True,  # so clear_session knows to release it
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
    if DuckAnalyzer is None:
        logger.error("DuckAnalyzer not installed. Run 'pip install ml/duck_analyzer-1.0.0-py3-none-any.whl'")
        return {"session_id": session_id, "status": "error",
                "reasons": ["DuckAnalyzer not installed"], "done": True}, None

    # Same shared flag ml_inference.py checks -- refuse to burn GPU on
    # camera frames while a training job owns it, instead of silently
    # fighting it for VRAM. Cheap per-frame check, no lock contention with
    # session creation since get_mode() only reads.
    if app_state.get_mode() == "TRAINING":
        return {"session_id": session_id, "status": "paused",
                "reasons": ["Training is in progress -- camera inference paused"],
                "frames_processed": 0}, frame

    try:
        session = _get_or_create_session(session_id, expected_duck_count, video_name)
    except RuntimeError as e:
        # Covers both "config.yaml missing" and "GPU claimed by video/training"
        logger.warning(f"Could not start camera session {session_id}: {e}")
        return {"session_id": session_id, "status": "error",
                "reasons": [str(e)], "frames_processed": 0}, frame

    session["last_active"] = time.time()
    analyzer = session["analyzer"]
    session["frames_processed"] += 1

    annotated_frame = frame.copy()
    try:
        result = analyzer.process_frame(annotated_frame)
    except Exception as e:
        logger.error(f"Error in DuckAnalyzer for session {session_id}: {e}", exc_info=True)
        return {"session_id": session_id, "status": "error",
                "reasons": [str(e)], "frames_processed": session["frames_processed"]}, frame

    # ── Read the CORRECT keys from DuckAnalyzer._finish() ──
    # _finish() returns: detected_duck_count, other_count, missing_ids,
    # added_ids, other_ids, hand_detected, thumbnails, fps, detections, status.
    # It does NOT return "detected_other_toy_count", "anchor_locked", or "reasons".
    detected_ducks = result.get("detected_duck_count", 0)
    detected_others = result.get("other_count", 0)          # was wrong: "detected_other_toy_count"
    anchor_locked = getattr(analyzer, "anchor_locked", False)  # attribute on analyzer, not in result
    missing_ids = result.get("missing_ids", [])
    added_ids = result.get("added_ids", [])
    other_ids = result.get("other_ids", [])
    hand_detected = result.get("hand_detected", False)

    is_anomaly = (
        result.get("status") == "ANOMALY"
        or detected_others > 0
        or (anchor_locked and detected_ducks != session["expected_duck_count"])
    )

    stats = {
        "session_id": session_id,
        "original_filename": session.get("original_filename"),
        "status": result.get("status", "processing"),
        "frames_processed": session["frames_processed"],
        "fps": result.get("fps", 0),
        "detected_duck_count": detected_ducks,
        "expected_duck_count": session["expected_duck_count"],
        "detected_other_toy_count": detected_others,
        "anchor_locked": anchor_locked,
        "hand_detected": hand_detected,
        "missing_ids": missing_ids,
        "added_ids": added_ids,
        "other_ids": other_ids,
        "missing_count": result.get("missing_count", 0),
        "added_count": result.get("added_count", 0),
        "detections": result.get("detections", []),
        "thumbnails": result.get("thumbnails", []),
        "is_anomaly_frame": is_anomaly,
        "video_width": frame.shape[1],
        "video_height": frame.shape[0],
    }

    return stats, annotated_frame


def update_expected_ducks(session_id: str, count: int) -> None:
    session = _sessions.get(session_id)
    if not session:
        _sessions[session_id] = {"expected_duck_count": count}
        return
    session["expected_duck_count"] = count
    analyzer = session.get("analyzer")
    if analyzer:
        analyzer.set_expected_duck_count(count)


def clear_session(session_id: str) -> None:
    with _sessions_lock:
        session = _sessions.pop(session_id, None)
    if session:
        analyzer = session.get("analyzer")
        if analyzer and hasattr(analyzer, "close"):
            analyzer.close()
        # NEW: release the cross-kind GPU claim this session took at
        # creation time, so video-upload inference (or training) can start
        # once the last camera session is gone.
        if session.get("inference_claimed"):
            app_state.exit_inference("camera")


def reset_session_for_next_video(session_id: str) -> None:
    clear_session(session_id)