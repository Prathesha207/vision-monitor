from __future__ import annotations

from typing import TYPE_CHECKING, Optional, Tuple
import asyncio
import threading
import queue
import time
from datetime import datetime
from pathlib import Path

from app.services.card_opertions.data_Models import OperationResult
from app.services.appStateStore import AppStateStore
from app.logger import log_info, log_error, log_warning
from app.services.settings_service import APP_CONFIG

# Import centralized socket emitters for recording events
from app.services.card_opertions.recording_socket_emitters import (
    emit_recording_started,
    emit_recording_finalizing,
    emit_recording_stopped,
)

if TYPE_CHECKING:
    from app.services.card_opertions.camera import Camera


def _get_recording_manager(camera: "Camera"):
    return getattr(getattr(camera, "card", None), "manager", None)


def _reserve_recording_slot(camera: "Camera", job_id: str = None, context: str = "recording_writter"):
    manager = _get_recording_manager(camera)
    if manager is None:
        log_warning(
            camera.card.slot_number,
            "recording_writter",
            f"Recording manager unavailable for camera '{camera.name}', skipping slot reservation",
            name=camera.name,
        )
        return True, "manager_unavailable"

    return manager.try_reserve_recording_slot(
        camera.card.slot_number,
        job_id=job_id,
        context=context,
    )


def _release_recording_slot(camera: "Camera", context: str, reason: str, job_id: str = None) -> None:
    manager = _get_recording_manager(camera)
    if manager is None:
        log_warning(
            camera.card.slot_number,
            "recording_writter",
            f"Recording manager unavailable for camera '{camera.name}', skipping slot release ({reason})",
            name=camera.name,
        )
        return

    manager.release_recording_slot(
        slot_number=camera.card.slot_number,
        context=context,
        reason=reason,
        job_id=job_id,
    )


def _open_writer(camera: "Camera", base_name: Path, fps: float, size: Tuple[int, int]):
    """
    Wrapper for your camera writer open call.
    Expected: returns (writer_obj, final_path)
    """
    writer_obj, final_path = camera._open_record_writer(base_name, fps, size)
    if not writer_obj or not writer_obj.is_open():
        raise RuntimeError("Failed to open video writer")
    return writer_obj, final_path


# =================== Frame Production Control ===================

def _stop_frame_production(camera: "Camera") -> None:
    """
    Stop all frame production so the writer thread's queue drains naturally.
    Called from stop_recording_writter BEFORE waiting for the writer thread.
    """
    try:
        camera.stop_capture()
    except Exception as e:
        log_error(camera.card.slot_number, "recording_writter",
                  f"Error stopping capture for '{camera.name}': {e}", name=camera.name)

    try:
        camera._set_hires_forwarding(False)
    except Exception:
        pass

    try:
        camera._refresh_hires_forwarding()
    except Exception:
        pass


# =================== Cleanup Functions ===================

def _reset_recording_state(camera: "Camera", reason: str = "") -> None:
    """
    Light cleanup — resets state flags + releases slot.
    Called after 30s timeout to unblock writer thread's exit condition
    and free the slot so it can be reused.

    Does NOT close the video writer — thread handles that in its finally.
    """
    log_info(camera.card.slot_number, "recording_writter",
             f"Resetting recording state for '{camera.name}' ({reason})",
             name=camera.name)

    cleanup_job_id = getattr(camera.card, "_current_job_id", None)
    camera.card._is_recording_active = False
    camera.card._current_job_id = None

    _release_recording_slot(
        camera,
        context="recording_writter_reset",
        reason=reason,
        job_id=cleanup_job_id,
    )


def _cleanup_recording_resources(camera: "Camera", reason: str = "") -> None:
    """
    FULL cleanup — releases writer, resets all state, drains queues, releases slot.
    Called from writer thread's finally block, or from start_recording_writter
    when startup fails before the thread is launched.

    Idempotent: safe to call even if partially cleaned up already.
    Note: stop_capture / hi-res are NOT called here — _stop_frame_production
    handles that in the stop path, and start_failed path doesn't need it.
    """
    log_info(camera.card.slot_number, "recording_writter",
             f"Recording cleanup started for '{camera.name}' ({reason})",
             name=camera.name)

    # 1. Release video writer
    try:
        vw = getattr(camera, "_writer_vw", None)
        if vw and getattr(vw, "is_open", lambda: False)():
            if hasattr(vw, "release"):
                vw.release()
            if hasattr(vw, "close"):
                vw.close()
            log_info(camera.card.slot_number, "recording_writter",
                     f"Video writer released for '{camera.name}'", name=camera.name)
    except Exception as e:
        log_error(camera.card.slot_number, "recording_writter",
                  f"Error releasing video writer for '{camera.name}': {e}", name=camera.name)

    # 2. Reset all writer state
    camera._writer_vw = None
    camera._writer_started_at = None
    camera._writer_type = None
    camera._writer_path = None
    camera._writer_thread = None
    cleanup_job_id = getattr(camera.card, "_current_job_id", None)
    camera.card._is_recording_active = False
    camera.card._current_job_id = None

    # 3. Clean ALL queues to prevent memory leaks
    try:
        camera.clear_hi_res_queues()
    except Exception:
        pass

    # try:
    #     camera.drain_dai_queue(camera._cam_raw_queue)
    # except Exception:
    #     pass

    # try:
    #     camera.drain_dai_queue(camera._cam_mjpeg_queue)
    # except Exception:
    #     pass

    # 4. Release recording slot
    _release_recording_slot(
        camera,
        context="recording_writter_cleanup",
        reason=reason,
        job_id=cleanup_job_id,
    )

    log_info(camera.card.slot_number, "recording_writter",
             f"Recording cleanup completed ({reason}) for '{camera.name}'",
             name=camera.name)


# =================== Path Helper ===================

def _build_recording_path(camera: "Camera") -> Path:
    camera_dict = AppStateStore.get_camera(camera.card.slot_number) or {}
    duration = camera_dict.get("video_duration_seconds", 30)
    duration_suffix = f"_{duration}s" if duration else ""

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    recording_dir = Path(APP_CONFIG.recording_path) / camera.name
    recording_dir.mkdir(parents=True, exist_ok=True)

    base = f"{camera.name}_{ts}{duration_suffix}_high_res"
    base_name = recording_dir / base
    return camera._basename_with_codec(base_name)


# =================== Start Recording ===================

async def start_recording_writter(camera: "Camera") -> OperationResult:
    """
    Start recording:
    1. Guard: already active / conflicting ops → reject
    2. Reserve slot + set active flag
    3. Connect device + start pipeline if needed
    4. Enable hi-res forwarding + start capture threads
    5. Open video writer + start writer thread
    6. Emit started event

    On failure before thread starts: _cleanup_recording_resources in finally.
    """
    started = False
    state_acquired = False
    job_id = getattr(camera.card, "_current_job_id", None)

    try:
        log_info(
            camera.card.slot_number,
            "recording_writter",
            f"Start recording requested for '{camera.name}' (job_id={job_id})",
            name=camera.name,
        )

        # Guard: already recording
        if getattr(camera.card, "_is_recording_active", False):
            log_error(
                camera.card.slot_number,
                "recording_writter",
                f"Recording already active for '{camera.name}'",
                name=camera.name,
            )
            return OperationResult(success=False, message="Recording already in progress")

        # Guard: conflicting operations
        if getattr(camera.card, "_is_inference_active", False) or getattr(camera.card, "_is_training_active", False):
            log_error(
                camera.card.slot_number,
                "recording_writter",
                f"Cannot start recording during training/inference for '{camera.name}'",
                name=camera.name,
            )
            return OperationResult(success=False, message="Cannot start recording during training/inference")

        # Reserve recording slot
        reserved, reserve_msg = _reserve_recording_slot(
            camera, job_id=job_id, context="start_recording_writter",
        )
        if not reserved:
            log_error(
                camera.card.slot_number,
                "recording_writter",
                f"Failed to reserve recording slot for '{camera.name}': {reserve_msg}",
                name=camera.name,
            )
            return OperationResult(success=False, message=reserve_msg)

        # Set active flag immediately to reject concurrent starts
        camera.card._is_recording_active = True
        state_acquired = True

        # Create fresh stop event for this recording session
        camera._writer_stop_event = threading.Event()

        # Check connection, connect if needed
        if not camera.is_device_connected:
            if not await camera.connect_device():
                log_error(camera.card.slot_number, "recording_writter",
                          f"Device not connected for '{camera.name}'", name=camera.name)
                return OperationResult(success=False, message="Device not connected")
        else:
            log_info(camera.card.slot_number, "recording_writter",
                     f"Device already connected for '{camera.name}'", name=camera.name)

        # Check pipeline, start if needed
        if not camera.is_pipeline_running:
            if not await camera.start_pipeline():
                log_error(camera.card.slot_number, "recording_writter",
                          f"Failed to start pipeline for '{camera.name}'", name=camera.name)
                return OperationResult(success=False, message="Failed to start pipeline")
        else:
            log_info(camera.card.slot_number, "recording_writter",
                     f"Pipeline already running for '{camera.name}'", name=camera.name)

        # Verify queues are alive — recover if device disconnected silently
        if not camera._verify_queue_health():
            log_warning(camera.card.slot_number, "recording_writter",
                        f"Queue dead before recording for '{camera.name}', restarting runtime",
                        name=camera.name)
            restart_result = await camera.restart_runtime()
            if not restart_result.success:
                return OperationResult(success=False, message=f"Runtime restart failed: {restart_result.message}")

        # Start capture threads (will start only dead ones if some alive)
        if not camera.start_capture():
            log_error(camera.card.slot_number, "recording_writter",
                      f"Failed to start capture for '{camera.name}'", name=camera.name)
            return OperationResult(success=False, message="Failed to start capture")
        await asyncio.sleep(0.2)

        # Enable hi-res forwarding (recording needs hi-res frames)
        camera._set_hires_forwarding(True)

        # Open video writer
        base_name = _build_recording_path(camera)
        fps = float(camera.target_fps)
        size = (int(camera.width), int(camera.height))
        writer_obj, final_path = _open_writer(camera, base_name, fps, size)

        # Bind writer state
        camera._writer_vw = writer_obj
        camera._writer_path = final_path
        camera._writer_type = "hires"
        camera._writer_started_at = time.time()

        # Start writer thread
        t = threading.Thread(
            target=_recording_writter_thread,
            args=(camera,),
            name=f"RecWriter-Cam{camera.id}-Slot{camera.card.slot_number}",
            daemon=True,
        )
        camera._writer_thread = t
        t.start()
        started = True

        # Emit started event
        emit_recording_started(camera)
        log_info(
            camera.card.slot_number,
            "recording_writter",
            f"Recording started for '{camera.name}', saving to '{final_path}'",
            name=camera.name,
        )
        return OperationResult(success=True, message="Recording started")

    except Exception as e:
        log_error(camera.card.slot_number, "recording_writter",
                  f"Start recording failed for '{camera.name}': {e}",
                  name=camera.name)
        try:
            emit_recording_stopped(camera, status="failed")
        except Exception:
            pass
        return OperationResult(success=False, message=str(e))
    finally:
        # If startup failed before writer thread is running, cleanup immediately
        # so recording can be retried. Otherwise thread finally handles cleanup.
        if state_acquired and not started:
            _cleanup_recording_resources(camera, reason="start_failed")


# =================== Stop Recording ===================

async def stop_recording_writter(camera: "Camera") -> OperationResult:
    """
    Stop recording — clean shutdown:

    1. Stop frame production (capture + hi-res) → queue stops growing
    2. Set stop event → thread knows to drain and exit
    3. Wait 30s → thread drains remaining frames to disk
    4. If thread still alive after 30s → reset state + release slot
       (_reset_recording_state sets _is_recording_active = False which
        unblocks the thread's exit condition, thread exits, thread's
        finally calls _cleanup_recording_resources for writer close)

    The root cause of the old bug was that capture threads were never stopped,
    so the queue never drained and the thread never exited. Now with
    _stop_frame_production called first, 30s is more than enough.
    """
    try:
        job_id = getattr(camera.card, "_current_job_id", None)

        if not getattr(camera.card, "_is_recording_active", False):
            log_info(
                camera.card.slot_number,
                "recording_writter",
                f"Stop recording requested but already inactive for '{camera.name}'",
                name=camera.name,
            )
            emit_recording_stopped(camera, status="stopped")
            return OperationResult(success=True, message="Recording already stopped")

        log_info(
            camera.card.slot_number,
            "recording_writter",
            f"Stopping recording for '{camera.name}' (job_id={job_id})",
            name=camera.name,
        )

        # Emit finalizing event immediately so UI can show "Finalizing..." status
        emit_recording_finalizing(camera)

        # Step 1: Stop frame production — no more frames enter the queue
        _stop_frame_production(camera)

        await asyncio.sleep(1.0)  # brief pause to allow capture threads to stop and queue to stabilize

        # Step 2: Signal writer thread to stop
        stop_ev = getattr(camera, "_writer_stop_event", None)
        if stop_ev is not None:
            stop_ev.set()

        # Step 3: Wait 30s for thread to drain remaining frames
        writer_thread = getattr(camera, "_writer_thread", None)
        if writer_thread is not None and writer_thread.is_alive():
            log_info(
                camera.card.slot_number,
                "recording_writter",
                f"Waiting 30s for writer thread to drain for '{camera.name}'",
                name=camera.name,
            )
            await asyncio.to_thread(writer_thread.join, 30.0)

            # Step 4: If still alive, reset state to unblock thread exit condition
            if writer_thread.is_alive():
                log_warning(
                    camera.card.slot_number,
                    "recording_writter",
                    f"Writer thread still alive after 30s for '{camera.name}', "
                    f"resetting state and releasing slot",
                    name=camera.name,
                )
                _reset_recording_state(camera, reason="stop_timeout_30s")

        # Emit stopped event AFTER cleanup
        emit_recording_stopped(camera, status="stopped")

        log_info(
            camera.card.slot_number,
            "recording_writter",
            f"Recording stop completed for '{camera.name}'",
            name=camera.name,
        )
        return OperationResult(success=True, message="Recording stopped")

    except Exception as e:
        log_error(camera.card.slot_number, "recording_writter",
                  f"Stop recording error for '{camera.name}': {e}",
                  name=camera.name)
        # On exception, reset state so slot isn't stuck forever
        try:
            _reset_recording_state(camera, reason="stop_exception")
        except Exception:
            pass
        return OperationResult(success=False, message=str(e))


# =================== Writer Thread ===================

def _recording_writter_thread(camera: "Camera") -> None:
    """
    Writer thread: reads BGR frames from hi-res queue, writes to video file.

    Exit condition:
      (stop_event is set AND queue is empty) AND (not _is_recording_active)

    - stop_event + queue empty: all frames drained after stop requested
    - not _is_recording_active: fallback — if queue never empties within 30s,
      stop_recording_writter calls _reset_recording_state which sets this to False,
      breaking the loop even if queue isn't fully drained.

    Cleanup ALWAYS runs in finally via _cleanup_recording_resources.
    """
    log_info(camera.card.slot_number, "recording_writter",
             f"Writer thread started for '{camera.name}'",
             name=camera.name)

    hi_res_queue = getattr(camera.card, "_cam_hires_frame_queue", None)
    writer = getattr(camera, "_writer_vw", None)
    stop_ev = getattr(camera, "_writer_stop_event", None)
    frames_written = 0

    try:
        if hi_res_queue is None or writer is None or stop_ev is None:
            raise RuntimeError("hi-res queue, writer, or stop event missing")


        while True:
            
            # Exit when: (stop requested AND queue drained) AND recording deactivated
            if (stop_ev.is_set() and hi_res_queue.empty()) or (not camera.card._is_recording_active):
                log_info(
                    camera.card.slot_number,
                    "recording_writter",
                    f"Writer thread exit condition met for '{camera.name}' | "
                    f"stop_event={stop_ev.is_set()} | "
                    f"queue_size={hi_res_queue.qsize()} | "
                    f"is_recording_active={camera.card._is_recording_active} | "
                    f"frames_written={frames_written}",
                    name=camera.name
                )
                break

            try:
                frame = hi_res_queue.get(timeout=0.5)
            except queue.Empty:
                # Log queue status while draining after stop
                if stop_ev.is_set():
                    qsize = hi_res_queue.qsize() if hi_res_queue else 0
                    log_info(camera.card.slot_number, "recording_writter",
                             f"[DEBUG] Writer thread waiting for drain '{camera.name}' | "
                             f"queue_size={qsize} | is_recording_active={camera.card._is_recording_active}",
                             name=camera.name)
                continue

            if frame is None:
                continue

            try:
                writer.write(frame)
                frames_written += 1
            except Exception as e:
                log_error(camera.card.slot_number, "recording_writter",
                          f"Frame write error for '{camera.name}': {e}", name=camera.name)

    except Exception as e:
        log_error(camera.card.slot_number, "recording_writter",
                  f"Writer thread error for '{camera.name}': {e}",
                  name=camera.name)

    finally:
        log_info(camera.card.slot_number, "recording_writter",
                 f"Writer thread ending for '{camera.name}' (frames_written={frames_written})",
                 name=camera.name)
        _cleanup_recording_resources(camera, reason="thread_exit")
