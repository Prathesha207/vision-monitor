# app/routers/cameras.py
from unittest import result
from urllib import request
from datetime import datetime
import base64
import cv2
import numpy as np
from app.logger import log_info, log_warning, log_error , log_critical
from app.services.audit_log_service import AuditLogService
from app.app_context import AppContext
from fastapi import APIRouter, HTTPException, Depends, Request
from sqlmodel import Session, select
import time
import asyncio
from pathlib import Path
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status, Body, UploadFile, File
from fastapi.responses import StreamingResponse
from app.config.settings import DEFAULT_MAX_CARDS
from app.services.applicationManager import app_manager
from app.services.settings_service import APP_CONFIG
from app.utils.path_utils import win_to_wsl
from app.utils.disk_space import check_free_space
from app.schemas.inference_schemas import UnifiedInferenceRequest, InferenceParams
from app.schemas.training_schemas import UnifiedTrainingRequest, NormalTrainingParams, InstantTrainingParams
from app.schemas.dataClass_requests import TrainingRoiStillRequest
from app.database import SessionLocal
from app.schemas.camera_schema import CameraModel
from app.services.appStateStore import AppStateStore
router = APIRouter(prefix="/cards", tags=["Cameras"])


VIDEO_EXTENSIONS = {".mp4", ".avi", ".mkv", ".mov", ".mpeg", ".mpg", ".wmv"}

#-------------Helpers-----------------
def validate_video_file(video_path_str: str) -> Path:
    """Validate video file"""
    wsl_path = win_to_wsl(video_path_str)
    video_path = Path(wsl_path)
    
    if not video_path.exists():
        raise HTTPException(status_code=404, detail=f"Video file not found: {video_path_str}")
    
    if video_path.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported video format: {video_path.suffix}")
    
    return video_path

def validate_disk_space(camera_id: int, camera_name: str , operation: str):
    """Validate disk space"""
    data_root = Path(APP_CONFIG.camera_stream_training_data)
    data_res = check_free_space(data_root, required_gb=5.0)
    if not data_res.ok:
        msg = (
            f"Not enough disk space for {operation} dataset. "
            f"Free: {data_res.free_gb:.2f} GB, "
            f"required: {data_res.required_gb:.2f} GB."
        )
        log_warning(
            camera_id,
            "card_routes",
            f"{operation} start denied (dataset disk space) for camera {camera_id} ({camera_name}): {msg}",
            role="System",
            name=camera_name,
        )
        raise HTTPException(status_code=507, detail=f"Insufficient disk space for {operation} data")
    
    models_root = Path(APP_CONFIG.camera_stream_training_models)
    models_res = check_free_space(models_root, required_gb=5.0)
    if not models_res.ok:
        msg = (
            f"Not enough disk space for {operation} models. "
            f"Free: {models_res.free_gb:.2f} GB, "
            f"required: {models_res.required_gb:.2f} GB."
        )
        log_warning(
            camera_id,
            "card_routes",
            f"{operation} start denied (models disk space) for camera {camera_id} ({camera_name}): {msg}",
            role="System",
            name=camera_name,
        )
        raise HTTPException(status_code=507, detail=f"Insufficient disk space for {operation} models")

def validate_instant_fields(body: UnifiedTrainingRequest):
    if not body.session_name:
        raise HTTPException(status_code=400, detail="session_name is required for instant mode")
    if body.model_dir is None and not body.is_online:
        raise HTTPException(status_code=400, detail="model_dir is required for offline instant mode")

    if body.roi_names is not None:
        if not isinstance(body.roi_names, list):
            raise HTTPException(status_code=400, detail="roi_names must be a list")
        for n in body.roi_names:
            if not isinstance(n, str) or not n.strip():
                raise HTTPException(status_code=400, detail="roi_names contains invalid name")

    
#-------------Routes-----------------

#------------- Camera Connect Route -----------------
@router.get("/{slot_Number}/connect")
async def connect_camera(slot_Number: int):
    """Return camera connection status and persist it to DB & AppStateStore."""

    if not (1 <= slot_Number <= DEFAULT_MAX_CARDS):
        raise HTTPException(status_code=400, detail="Invalid slot number")

    card = app_manager.get_card_by_slot(slot_Number)
    if not card:
        raise HTTPException(status_code=404, detail="Card instance not found")

    if not card.camera:
        raise HTTPException(status_code=400, detail="No camera configured for this card")

    try:
        # 1️⃣ Read runtime state ONLY
        is_connected = bool(card.camera.is_device_connected)
        new_status = "online" if is_connected else "offline"

        

        with SessionLocal() as session:
            cam = session.get(CameraModel, card.card_id)
            if cam:
                cam.status = new_status
                session.add(cam)
                session.commit()
                session.refresh(cam)

                # 3️⃣ Update AppStateStore so frontend sees it immediately
                AppStateStore.set_camera(cam)

        # 4️⃣ Log only when disconnected
        if not is_connected:
            log_critical(
                slot_Number,
                "card_routes",
                f"Camera not connected for slot {slot_Number} ({card.name})",
                role="System",
                name=card.name,
            )
            AuditLogService.add_log(
                message=f"CAMERA CONNECT FAILED: Slot {slot_Number} ({card.name}) - Camera not connected",
                level="WARNING"
            )
        else:
            AuditLogService.add_log(
                message=f"CAMERA CONNECT SUCCESS: Slot {slot_Number} ({card.name}) - Online",
                level="INFO"
            )

        # 5️⃣ Return simple response
        response_data = {
            "slot_number": slot_Number,
            "is_connected": is_connected,
            "status": new_status,
            "message": f"Camera {'connected' if is_connected else 'not connected'} for slot {slot_Number}",
            "model_status": getattr(card, "model_status", "idle"),
            "model_message": getattr(card, "model_message", ""),

        }
        
        log_info(
            slot_Number,
            "card_routes",
            f"Camera connection status response for slot {slot_Number} ({card.name}): {response_data}",
            role="System",
            name=card.name,
        )
        
        return response_data

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{slot_Number}/camera/restart")
async def restart_camera(slot_Number: int):
    """Restart camera runtime and return only after success/failure."""
    if not (1 <= slot_Number <= DEFAULT_MAX_CARDS):
        log_critical(
            slot_Number,
            "card_routes",
            f"Invalid slot number for camera restart: {slot_Number}",
            role="System",
            name="camera",
        )
        raise HTTPException(status_code=400, detail="Invalid slot number")

    card_instance = app_manager.get_card_by_slot(slot_Number)
    if not card_instance:
        log_error(
            slot_Number,
            "card_routes",
            f"Card instance not found for camera restart on slot {slot_Number}",
            role="System",
            name="camera",
        )
        raise HTTPException(status_code=404, detail="Card instance not found")

    if not card_instance.camera:
        log_error(
            slot_Number,
            "card_routes",
            f"No camera configured for restart on slot {slot_Number}",
            role="System",
            name=card_instance.name,
        )
        raise HTTPException(status_code=400, detail="No camera configured for this card")

    if (
        card_instance._is_recording_active
        or card_instance._is_training_active
        or card_instance._is_inference_active
        or card_instance.camera._is_streaming_active
    ):
        msg = (
            "Cannot restart camera while any operation is active "
            f"(recording={card_instance._is_recording_active}, "
            f"training={card_instance._is_training_active}, "
            f"inference={card_instance._is_inference_active}, "
            f"streaming={card_instance.camera._is_streaming_active}, "
        )
        log_warning(
            slot_Number,
            "card_routes",
            f"Camera restart denied for slot {slot_Number} ({card_instance.name}): {msg}",
            role="System",
            name=card_instance.name,
        )
        raise HTTPException(status_code=409, detail=msg)

    try:
        result = await card_instance.camera.restart_runtime(settle_seconds=1.0)
        if not result.success:
            AuditLogService.add_log(
                message=f"CAMERA RESTART FAILED: Slot {slot_Number} ({card_instance.name}) - {result.message}",
                level="ERROR",
            )
            log_error(
                slot_Number,
                "card_routes",
                f"Camera restart failed for slot {slot_Number} ({card_instance.name}): {result.message}",
                role="System",
                name=card_instance.name,
            )
            raise HTTPException(status_code=500, detail=result.message or "Camera restart failed")

        AuditLogService.add_log(
            message=f"CAMERA RESTART SUCCESS: Slot {slot_Number} ({card_instance.name})",
            level="INFO",
        )
        log_info(
            slot_Number,
            "card_routes",
            f"Camera restart completed for slot {slot_Number} ({card_instance.name})",
            role="System",
            name=card_instance.name,
        )

        return {
            "success": True,
            "slot_number": slot_Number,
            "message": result.message or "Camera restarted successfully",
        }
    except HTTPException:
        raise
    except Exception as e:
        AuditLogService.add_log(
            message=f"CAMERA RESTART FAILED: Slot {slot_Number} ({card_instance.name}) - {str(e)}",
            level="ERROR",
        )
        log_error(
            slot_Number,
            "card_routes",
            f"Failed to schedule camera restart for slot {slot_Number} ({card_instance.name}): {str(e)}",
            role="System",
            name=card_instance.name,
        )
        raise HTTPException(status_code=500, detail=f"Failed to restart camera: {str(e)}")


#------------- Camera Stream Routes -----------------
@router.get("/{slot_Number}/stream/start")
async def start_camera_stream(
    slot_Number: int,
):
    """Start camera stream for a given slot number."""
    if not (1 <= slot_Number <= DEFAULT_MAX_CARDS):
        log_critical(
            slot_Number,"card_routes", f"Invalid slot number for stream start: {slot_Number}", role="System", name="camera")
        raise HTTPException(status_code=400, detail="Invalid slot number")
    
    # card_instance =  request.app.state.app_manager.cards[slot_Number - 1]
    card_instance = app_manager.get_card_by_slot(slot_Number)
    if not card_instance:
        log_critical(
            slot_Number,"card_routes", f"Card instance not found for stream start on slot {slot_Number}", role="System", name="camera")    
        raise HTTPException(status_code=404, detail="Card instance not found")
    try:
        if not card_instance.camera:
            AuditLogService.add_log(
                message=f"CAMERA STREAM START FAILED: Slot {slot_Number} - No camera configured",
                level="WARNING"
            )
            log_error(slot_Number,"card_routes", f"No camera configured for stream start on slot {slot_Number}", role="System", name=card_instance.name)
            raise HTTPException(status_code=400, detail="No camera configured for this card")
       
        start_result = await card_instance.camera.start_streaming()
        if not start_result.success:
            log_error(
                slot_Number,
                "card_routes",
                f"Camera stream start failed for slot {slot_Number} ({card_instance.name}): {start_result.message}",
                role="System",
                name=card_instance.name,
            )
            raise HTTPException(status_code=500, detail=start_result.message or "Failed to start camera stream")
        log_info(slot_Number,"card_routes", f"Camera stream started for slot {slot_Number} ({card_instance.name})", role="System", name=card_instance.name)
        return{
            "message": f"Camera stream started for slot {slot_Number}"           
        }
    except HTTPException:
        raise
    except Exception as e:
        AuditLogService.add_log(
            message=f"CAMERA STREAM START FAILED: Slot {slot_Number} ({card_instance.name}) - {str(e)}",
            level="ERROR"
        )
        log_error(slot_Number,"card_routes", f"Failed to start camera stream for slot {slot_Number} ({card_instance.name}): {str(e)}", role="System", name=card_instance.name)
        raise HTTPException(status_code=500, detail=str(e))
    
@router.post("/{slot_number}/stream/stop")
async def stop_streaming(
    slot_number: int,
):
    """Stop streaming for a camera."""
    # Validate slot number
    if not (1 <= slot_number <= DEFAULT_MAX_CARDS):
        log_critical(
            slot_number,
            "card_routes",
            f"Invalid slot number for stream stop: {slot_number}. Must be between 1 and {DEFAULT_MAX_CARDS}",
            role="System",
            name="camera",
        )
        raise HTTPException(
            status_code=400, 
            detail=f"Slot number must be between 1 and {DEFAULT_MAX_CARDS}"
        )
    # Get card instance from ApplicationManager
    card_instance = app_manager.get_card_by_slot(slot_number)
    
    if not card_instance:
        log_critical(
            slot_number,
            "card_routes",
            f"Card instance not found for stream stop on slot {slot_number}",
            role="System",
            name="camera",
        )
        raise HTTPException(
            status_code=404,
            detail=f"Card in slot {slot_number} not found"
        )
    
    # Check if camera exists
    if not card_instance.camera:
        log_error(
            slot_number,
            "card_routes",
            f"No camera configured for stream stop on slot {slot_number}",
            role="System",
            name=card_instance.name,
        )

        raise HTTPException(
            status_code=400,
            detail=f"No camera configured for slot {slot_number}"
        )
    
    # Stop streaming
    try:
        await card_instance.camera.stop_streaming()
        log_info(slot_number,"card_routes", f"Camera stream stopped for slot {slot_number} ({card_instance.name})", role="System", name=card_instance.name)
        return {
            "success": True,
            "message": f"Streaming stopped for {card_instance.name}",
            "slot_number": slot_number
        }
    except Exception as e:
        AuditLogService.add_log(
            message=f"CAMERA STREAM STOP FAILED: Slot {slot_number} ({card_instance.name}) - {str(e)}",
            level="ERROR"
        )
        log_error(
            slot_number,
            "card_routes",
            f"Failed to stop camera stream for slot {slot_number} ({card_instance.name}): {str(e)}",
            role="System",
            name=card_instance.name,
        )
        raise HTTPException(
            status_code=500,
            detail=f"Failed to stop streaming: {str(e)}"
        )


@router.get("/{slot_Number}/preview")
async def get_camera_preview(
    slot_Number: int,
):
    """
    Get latest preview frame 
    """
    if not (1 <= slot_Number <= DEFAULT_MAX_CARDS):
        log_critical(
            slot_Number,
            "card_routes",
            f"Invalid slot number for preview request: {slot_Number}. Must be between 1 and {DEFAULT_MAX_CARDS}",
            role="System",
            name="camera",
        )
        raise HTTPException(status_code=400, detail="Invalid slot number")
    
    # card_instance =  request.app.state.app_manager.cards[slot_Number - 1]
    card_instance = app_manager.get_card_by_slot(slot_Number)
    if not card_instance:
        log_error(
            slot_Number,
            "card_routes",
            f"Preview request failed - Card instance not found for slot {slot_Number}",
            role="System",
            name="Unknown",
        )
        raise HTTPException(status_code=404, detail="Card instance not found")
    try:
        if not card_instance.camera:
            log_error(
                slot_Number,
                "card_routes",
                f"Preview request failed - No camera configured for slot {slot_Number}",
                role="System",
                name=card_instance.name,
            )

            raise HTTPException(status_code=400, detail="No camera configured for this card")
        frame = await card_instance.camera.get_preview_frame()
        if frame is None:
            log_warning(
                slot_Number,
                "card_routes",
                f"Preview frame not available for slot {slot_Number} ({card_instance.name})",
                role="System",
                name=card_instance.name,
            )
        return Response(
            content=frame,
            media_type="image/jpeg",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
            },
        )
    except Exception as e:
        log_error(
            slot_Number,
            "card_routes",
            f"Preview request failed - {str(e)}",
            role="System",
            name=card_instance.name if card_instance else "Unknown",
        )
        raise HTTPException(status_code=500, detail=str(e))
    

@router.get("/{slot_Number}/stream")
async def get_camera_mjpeg_stream(
    slot_Number: int,
    request: Request,
):
    """
    MJPEG streaming for modal - compatible with your existing frontend
    GET /cameras/1/stream?ip_address=192.168.1.100&t=timestamp
    """
    if not (1 <= slot_Number <= DEFAULT_MAX_CARDS):
        log_critical(
            slot_Number,
            "card_routes",
            f"Invalid slot number for stream request: {slot_Number}. Must be between 1 and {DEFAULT_MAX_CARDS}",
            role="System",
            name="camera",
        )
        raise HTTPException(status_code=400, detail="Invalid slot number")
    
    # card_instance =  request.app.state.app_manager.cards[slot_Number - 1]
    card_instance = app_manager.get_card_by_slot(slot_Number)
    if not card_instance:
        log_error(
            slot_Number,
            "card_routes",
            f"Modal stream request failed - Card instance not found for slot {slot_Number}",
            role="System",
            name="Unknown",
        )
        raise HTTPException(status_code=404, detail="Card instance not found")
    try:
        if not card_instance.camera:
            log_error(
            slot_Number,
                "card_routes",
                f"Modal stream request failed - No camera configured for slot {slot_Number}",
                role="System",
                name=card_instance.name if card_instance else "Unknown",
            )
            raise HTTPException(status_code=400, detail="No camera configured for this card")
        
        if not await card_instance.camera.start_model_streaming():
            log_error(slot_Number,"card_routes", f"Failed to start modal stream for camera {slot_Number} ({card_instance.name}) - Stream may already be active", role="System", name=card_instance.name)
            raise HTTPException(status_code=409, detail="Modal stream already active or failed to start")
        async def modal_mjpeg_generator():
            frames_sent = 0
            start_time = time.time()
            last_log_time = start_time
            no_frame_count = 0

            try:
                while True:
                    if await request.is_disconnected():
                        log_info(
                            slot_Number,
                            "card_routes",
                            f"Modal stream closed by client for camera {slot_Number} ({card_instance.name})",
                            role="System",
                            name=card_instance.name,
                        )
                        break

                    frame_data = await card_instance.camera.get_model_stream_frame()

                    if frame_data:
                        frames_sent += 1
                        no_frame_count = 0
                        yield (
                            b"--frame\r\n"
                            b"Content-Type: image/jpeg\r\n"
                            b"Content-Length: " + str(len(frame_data)).encode() + b"\r\n\r\n" + frame_data + b"\r\n"
                        )

                        current_time = time.time()
                        if current_time - last_log_time >= 5.0:
                            fps = frames_sent / max(current_time - start_time, 1e-6)
                            last_log_time = current_time
                    else:
                        no_frame_count += 1
                        await asyncio.sleep(0.033)
                        if no_frame_count > 1800:  # ~60 seconds
                            log_warning(
                                slot_Number,
                                "card_routes",
                                f"No frames for 60s, ending modal stream for camera {slot_Number} ({card_instance.name})",
                                role="System",
                                name=card_instance.name,
                            )
                            break

            except asyncio.CancelledError:
                log_info(
                    slot_Number,
                    "card_routes",
                    f"Modal stream cancelled for camera {slot_Number} ({card_instance.name})",
                    role="System",
                    name=card_instance.name,
                )
            except Exception as exc:
                log_error(
                    slot_Number,
                    "card_routes",
                    f"Modal stream error for camera {slot_Number} ({card_instance.name}): {exc}",
                    role="System",
                    name=card_instance.name,
                )
            finally:
                await card_instance.camera.stop_model_streaming()
                total_time = time.time() - start_time
                avg_fps = frames_sent / total_time if total_time > 0 else 0

                log_info(
                    slot_Number,
                    "card_routes",
                    f"Modal stream ended for camera {slot_Number} ({card_instance.name}) frames={frames_sent} "
                    f"duration={total_time:.1f}s avg_fps={avg_fps:.1f}",
                    role="System",
                    name=card_instance.name,
                )

        return StreamingResponse(
            modal_mjpeg_generator(),
            media_type="multipart/x-mixed-replace; boundary=frame",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "Connection": "close",
            },
        )
    except Exception as e:
        log_critical(slot_Number,"card_routes", f"Modal stream failed for camera {slot_Number} ({card_instance.name if card_instance else 'Unknown'}): {str(e)}", role="System", name=card_instance.name if card_instance else "Unknown")
        raise HTTPException(status_code=500, detail=str(e))
    
#------------- ML Inference Routes -----------------
@router.post("/{slot_Number}/inference/start")
async def start_ml_inference(
    slot_Number: int,
    body: UnifiedInferenceRequest = Body(default=None)
):
    """Start ML inference for a given slot number (online or offline mode)."""
    # Initialize defaults
    card_instance = None
    
    try:
        # Validate slot number
        if not (1 <= slot_Number <= DEFAULT_MAX_CARDS):
            log_warning(
                slot_Number,
                "card_routes",
                f"Invalid slot number attempted: {slot_Number}. Must be between 1 and {DEFAULT_MAX_CARDS}",
                role="System",
                name="Unknown",
            )
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid slot number. Must be between 1 and {DEFAULT_MAX_CARDS}"
            )
        
        # Get card instance
        card_instance = app_manager.get_card_by_slot(slot_Number)
        if not card_instance:
            log_error(
                slot_Number,
                "card_routes",
                f"Card instance not found for slot {slot_Number}",
                role="System",
                name="Unknown",
            )
            raise HTTPException(
                status_code=404, 
                detail=f"No card configured in slot {slot_Number}"
            )
        
        # Set default body if not provided (online mode by default)
        body = body or UnifiedInferenceRequest(is_online=True)
        operation_type = "online" if body.is_online else "offline"
        
        log_info(
            slot_Number,
            "card_routes",
            f"Inference start request received - Type: {operation_type}, Card: {card_instance.name}",
            role="System",
            name=card_instance.name,
        )
        sources_path = None
        # Validate offline mode requirements
        if not body.is_online:
            # Check mode validity
            if not body.mode or body.mode.lower() not in ("video", "frames"):
                log_error(
                    slot_Number,
                    "card_routes",
                    f"Invalid offline inference mode: '{body.mode}'. Must be 'video' or 'frames'",
                    role="System",
                    name=card_instance.name,
                )
                raise HTTPException(
                    status_code=400, 
                    detail="Offline inference requires 'mode' to be either 'video' or 'frames'"
                )
            
            # Check source_path validity
            if not body.source_path:
                log_error(
                    slot_Number,
                    "card_routes",
                    f"Missing source_path for offline {body.mode} inference",
                    role="System",
                    name=card_instance.name,
                )
                raise HTTPException(
                    status_code=400, 
                    detail=f"Offline inference requires 'source_path' pointing to {body.mode} location"
                )
            
            # Validate source path based on mode
            try:
                sources_path = validate_video_file(body.source_path) if body.mode.lower() == "video" else Path(win_to_wsl(body.source_path))
                
                if body.mode.lower() == "video":
                    if not sources_path.exists() or not sources_path.is_file():
                        log_error(
                            slot_Number,
                            "card_routes",
                            f"Video file does not exist: {body.source_path}",
                            role="System",
                            name=card_instance.name,
                        )
                        raise HTTPException(
                            status_code=404, 
                            detail=f"Video file not found at path: {body.source_path}"
                        )
                    
                    log_info(
                        slot_Number,
                        "card_routes",
                        f"Offline video inference validated - Source: {sources_path}",
                        role="System",
                        name=card_instance.name,
                    )
                    
                elif body.mode.lower() == "frames":
                    if not sources_path.exists() or not sources_path.is_dir():
                        log_error(
                            slot_Number,
                            "card_routes",
                            f"Frames directory does not exist: {body.source_path}",
                            role="System",
                            name=card_instance.name,
                        )
                        raise HTTPException(
                            status_code=404, 
                            detail=f"Frames directory not found at path: {body.source_path}"
                        )
                    
                    log_info(
                        slot_Number,
                        "card_routes",
                        f"Offline frames inference validated - Source: {sources_path}",
                        role="System",
                        name=card_instance.name,
                    )
                    
            except HTTPException:
                raise
            except Exception as e:
                log_error(
                    slot_Number,
                    "card_routes",
                    f"Error validating source path '{body.source_path}': {str(e)}",
                    role="System",
                    name=card_instance.name,
                )
                raise HTTPException(
                    status_code=400, 
                    detail=f"Invalid source path: {str(e)}"
                )
        else:
            # Online mode validation
            if not card_instance.camera:
                log_error(
                    slot_Number,
                    "card_routes",
                    f"No camera configured for online inference on slot {slot_Number}",
                    role="System",
                    name=card_instance.name,
                )
                raise HTTPException(
                    status_code=400, 
                    detail="Online inference requires a configured camera for this card"
                )
            
            log_info(
                slot_Number,
                "card_routes",
                f"Online inference validated - Camera ready for card: {card_instance.name}",
                role="System",
                name=card_instance.name,
            )
        
        # Validate disk space
        log_info(
            slot_Number,
            "card_routes",
            f"Validating disk space for {operation_type} inference on slot {slot_Number}",
            role="System",
            name=card_instance.name,
        )
        validate_disk_space(slot_Number, card_instance.name, "inference")
        
        log_info(
            slot_Number,
            "card_routes",
            f"Disk space validation passed for {operation_type} inference",
            role="System",
            name=card_instance.name,
        )
        offline_source = None
        if not body.is_online:
            # sources_path is a Path already (WSL path). Convert to string for safety.
            offline_source = str(sources_path)
        # Start inference
        result = await card_instance.start_inference(
            body.is_online, 
            offline_source, 
            body.mode if not body.is_online else None,
            wait_for_completion=False,
            job_id=None
        )

        if result.success:
            AuditLogService.add_log(
                message=f"INFERENCE STARTED: Slot {slot_Number} ({card_instance.name}) - Mode: {operation_type}",
                level="INFO"
            )
            log_info(
                slot_Number,
                "card_routes",
                f"{operation_type.capitalize()} inference started successfully on slot {slot_Number} ({card_instance.name})",
                role="System",
                name=card_instance.name,
            )
            return result
        else:
            message = (result.message or "").lower()
            status_code = 409 if ("limit" in message or "already" in message) else 500
            AuditLogService.add_log(
                message=f"INFERENCE START FAILED: Slot {slot_Number} ({card_instance.name}) - {result.message}",
                level="ERROR"
            )
            log_error(
                slot_Number,
                "card_routes",
                f"Unexpected error starting inference for slot {slot_Number}: {result.message}",
                role="System",
                name=card_instance.name if card_instance else "Unknown",
            )
            raise HTTPException(
                status_code=status_code, 
                detail=f"Inference failed to start: {result.message}"
            )
        
    except HTTPException:
        raise
    except Exception as e:
        AuditLogService.add_log(
            message=f"INFERENCE START FAILED: Slot {slot_Number} - {str(e)}",
            level="ERROR"
        )
        log_error(
            slot_Number,
            "card_routes",
            f"Unexpected error starting inference for slot {slot_Number}: {str(e)}",
            role="System",
            name=card_instance.name if card_instance else "Unknown",
        )
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to start inference: {str(e)}"
        )
    

@router.post("/{slot_Number}/inference/frame")
async def run_single_frame_inference(
    slot_Number: int,
    frame: UploadFile = File(...),
):
    """Run inference on a single provided frame (no camera capture)."""
    if not (1 <= slot_Number <= DEFAULT_MAX_CARDS):
        raise HTTPException(status_code=400, detail="Invalid slot number")

    card_instance = app_manager.get_card_by_slot(slot_Number)
    if not card_instance:
        raise HTTPException(status_code=404, detail="Card instance not found")
    if card_instance.ml_workflow is None:
        raise HTTPException(status_code=500, detail="ML workflow not available for this card")
    if card_instance.ml_workflow._inference_thread and card_instance.ml_workflow._inference_thread.is_alive():
        msg = "Continuous inference is active. Stop it before single-frame inference."
        log_warning(
            slot_Number,
            "card_routes",
            f"Single-frame inference denied for slot {slot_Number}: {msg}",
            role="System",
            name=card_instance.name,
        )
        raise HTTPException(status_code=409, detail=msg)

    try:
        payload = await frame.read()
        if not payload:
            raise HTTPException(status_code=400, detail="Empty frame payload")

        np_buf = np.frombuffer(payload, dtype=np.uint8)
        img = cv2.imdecode(np_buf, cv2.IMREAD_COLOR)
        if img is None:
            raise HTTPException(status_code=400, detail="Failed to decode frame bytes")

        log_info(
            slot_Number,
            "card_routes",
            f"Single-frame inference request received for slot {slot_Number} ({card_instance.name})",
            role="System",
            name=card_instance.name,
        )
        result = await card_instance.ml_workflow.run_single_frame_inference(img)
        processed_frame = result.get("processed_frame")
        if processed_frame is None:
            raise HTTPException(status_code=500, detail="Inference did not return a processed frame")

        ok_e, jpg = cv2.imencode(".jpg", processed_frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok_e:
            raise HTTPException(status_code=500, detail="Failed to encode result image")

        h, w = processed_frame.shape[:2]
        response = {
            "slot_number": slot_Number,
            "camera_id": card_instance.card_id,
            "ts": datetime.utcnow().isoformat(timespec="milliseconds") + "Z",
            "source": {
                "camera_id": card_instance.card_id,
                "id": card_instance.ip,
                "name": card_instance.name,
                "slot_number": card_instance.slot_number,
            },
            "frame": {
                "w": int(w),
                "h": int(h),
            },
            "result": result.get("result"),
            "model": result.get("model"),
            "image_b64": "data:image/jpeg;base64," + base64.b64encode(jpg.tobytes()).decode("ascii"),
        }

        AuditLogService.add_log(
            message=f"SINGLE-FRAME INFERENCE COMPLETED: Slot {slot_Number} ({card_instance.name})",
            level="INFO"
        )
        log_info(
            slot_Number,
            "card_routes",
            f"Single-frame inference completed for slot {slot_Number} ({card_instance.name})",
            role="System",
            name=card_instance.name,
        )
        return response
    except HTTPException:
        raise
    except RuntimeError as e:
        msg = str(e)
        if "Continuous inference is active" in msg:
            AuditLogService.add_log(
                message=f"SINGLE-FRAME INFERENCE FAILED: Slot {slot_Number} ({card_instance.name}) - {msg}",
                level="WARNING"
            )
            raise HTTPException(status_code=409, detail=msg)
        if "Invalid frame" in msg:
            AuditLogService.add_log(
                message=f"SINGLE-FRAME INFERENCE FAILED: Slot {slot_Number} ({card_instance.name}) - {msg}",
                level="WARNING"
            )
            raise HTTPException(status_code=400, detail=msg)
        AuditLogService.add_log(
            message=f"SINGLE-FRAME INFERENCE FAILED: Slot {slot_Number} ({card_instance.name}) - {msg}",
            level="ERROR"
        )
        raise HTTPException(status_code=500, detail=msg)
    except Exception as e:
        AuditLogService.add_log(
            message=f"SINGLE-FRAME INFERENCE FAILED: Slot {slot_Number} ({card_instance.name}) - {str(e)}",
            level="ERROR"
        )
        log_error(
            slot_Number,
            "card_routes",
            f"Single-frame inference failed for slot {slot_Number}: {e}",
            role="System",
            name=card_instance.name if card_instance else "Unknown",
        )
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{slot_Number}/inference/stop")
async def stop_ml_inference(
    slot_Number: int,
):
    """Stop ML inference for a given slot number."""
    if not (1 <= slot_Number <= DEFAULT_MAX_CARDS):
        raise HTTPException(status_code=400, detail="Invalid slot number")
    
    # card_instance =  request.app.state.app_manager.cards[slot_Number - 1]
    card_instance = app_manager.get_card_by_slot(slot_Number)
    if not card_instance:
        raise HTTPException(status_code=404, detail="Card instance not found")
    try:
        await card_instance.stop_inference()
        AuditLogService.add_log(
            message=f"INFERENCE STOPPED: Slot {slot_Number} ({card_instance.name})",
            level="INFO"
        )
        return{
            "message": f"ML inference stopped for slot {slot_Number}"           
        }
    except Exception as e:
        AuditLogService.add_log(
            message=f"INFERENCE STOP FAILED: Slot {slot_Number} ({card_instance.name}) - {str(e)}",
            level="ERROR"
        )
        raise HTTPException(status_code=500, detail=str(e))    
    
#-----------------ML Training Routes -----------------
@router.post("/{slot_Number}/training/start")
async def start_ml_training(
    slot_Number: int,
    body: UnifiedTrainingRequest = Body(default=None)
):
    """Start ML training for a given slot number."""
    card_instance = None
    video_path = None
    try:
        # Validate slot number
        if not (1 <= slot_Number <= DEFAULT_MAX_CARDS):
            log_warning(
                slot_Number,
                "card_routes",
                f"Invalid slot number attempted: {slot_Number}. Must be between 1 and {DEFAULT_MAX_CARDS}",
                role="System",
                name="Unknown",
            )
            raise HTTPException(
                status_code=400, 
                detail=f"Invalid slot number. Must be between 1 and {DEFAULT_MAX_CARDS}"
            )
        
        # Get card instance
        card_instance = app_manager.get_card_by_slot(slot_Number)
        if not card_instance:
            log_error(
                slot_Number,
                "card_routes",
                f"Card instance not found for slot {slot_Number}",
                role="System",
                name="Unknown",
            )
            raise HTTPException(
                status_code=404, 
                detail=f"No card configured in slot {slot_Number}"
            )
        
        body = body or UnifiedTrainingRequest(is_online=True)
        operation_type = "online" if body.is_online else "offline"
        log_info(
            slot_Number,
            "card_routes",
            f"Training start request received - Type: {operation_type}, Card: {card_instance.name}",
            role="System",
            name=card_instance.name,
        )
        # Validate disk space
        log_info(
            slot_Number,
            "card_routes",
            f"Validating disk space for {operation_type} training on slot {slot_Number}",
            role="System",
            name=card_instance.name,
        )
        validate_disk_space(slot_Number, card_instance.name, "training")
        log_info(
            slot_Number,
            "card_routes",
            f"Disk space validation passed for {operation_type} training",
            role="System",
            name=card_instance.name,
        )

        #validate offline training video path
        if not body.is_online:
            if not body.video_path:
                log_error(
                    slot_Number,
                    "card_routes",
                    f"Missing video_path for offline training on slot {slot_Number} ({card_instance.name})",
                    role="System",
                    name=card_instance.name,
                )
                raise HTTPException(
                    status_code=400, 
                    detail="Offline training requires 'video_path' pointing to video location"
                )
            try:
                video_path = validate_video_file(body.video_path)
                log_info(
                    slot_Number,
                    "card_routes",
                    f"Offline training video path validated - Source: {video_path}",
                    role="System",
                    name=card_instance.name,
                )
            except HTTPException:
                raise
            except Exception as e:
                log_error(
                    slot_Number,
                    "card_routes",
                    f"Error validating training video path '{body.video_path}' on slot {slot_Number} ({card_instance.name}): {str(e)}",
                    role="System",
                    name=card_instance.name,
                )
                raise HTTPException(
                    status_code=400, 
                    detail=f"Invalid training video path: {str(e)}"
                )
        if body.mode == "instant":
            try:
                # Only validate instant fields for offline mode
                if not body.is_online:
                    validate_instant_fields(body)
                    
                    # Validate model directory for offline mode
                    model_dir = Path(win_to_wsl(body.model_dir))
                    if not model_dir.exists() or not model_dir.is_dir():
                        log_error(
                            slot_Number,
                            "card_routes",
                            f"Model directory does not exist: {body.model_dir}",
                            role="System",
                            name=card_instance.name,
                        )
                        raise HTTPException(
                            status_code=404, 
                            detail=f"Model directory not found at path: {body.model_dir}"
                        )
                    log_info(
                        slot_Number,
                        "card_routes",
                        f"Instant training model directory validated - Source: {model_dir}",
                        role="System",
                        name=card_instance.name,
                    )
                else:
                    # For online mode, model_dir is not required
                    model_dir = None

                result = await card_instance.start_instant_training(
                    is_online=body.is_online,
                    model_dir=str(model_dir) if model_dir is not None else None,  
                    session_name=body.session_name,
                    roi_names=body.roi_names,   
                    video_path=video_path if not body.is_online else None,
                    frames_to_capture=body.frames or 10,
                    sampling_size=body.sampling_size or 0.02,
                )

                if result.success:
                    AuditLogService.add_log(
                        message=f"INSTANT TRAINING COMPLETED: Slot {slot_Number} ({card_instance.name})",
                        level="INFO"
                    )
                    log_info(
                    slot_Number,
                    "card_routes",
                    f"Instant ML training Completed successfully on slot {slot_Number} ({card_instance.name})",
                    role="System",
                    name=card_instance.name,
                    )
                    return result
                else:
                    message = (result.message or "").lower()
                    status_code = 409 if ("limit" in message or "already" in message) else 500
                    AuditLogService.add_log(
                        message=f"INSTANT TRAINING FAILED: Slot {slot_Number} ({card_instance.name}) - {result.message}",
                        level="ERROR"
                    )
                    log_error(
                    slot_Number,
                    "card_routes",
                    f"Instant ML training failed on slot {slot_Number} ({card_instance.name}): {result.message}",
                    role="System",
                    name=card_instance.name,
                    )
                    raise HTTPException(
                    status_code=status_code, 
                    detail=f"Instant training failed: {result.message}"
                    )
            except HTTPException:
                raise
            except Exception as e:
                AuditLogService.add_log(
                    message=f"INSTANT TRAINING START FAILED: Slot {slot_Number} ({card_instance.name}) - {str(e)}",
                    level="ERROR"
                )
                log_error(
                    slot_Number,
                    "card_routes",
                    f"Error starting instant training: {str(e)}",
                    role="System",
                    name=card_instance.name,
                )
                raise HTTPException(
                    status_code=500, 
                    detail=f"Failed to start instant training: {str(e)}"
                )
            
        else:
            log_info(
                slot_Number,
                "card_routes",
                f"Proceeding with normal training workflow on slot {slot_Number} ({card_instance.name})",
                role="System",
                name=card_instance.name,
            )    
            # Start training
            result = await card_instance.start_normal_training(
                is_online=body.is_online,
                job_id=None,
                video_path=video_path if not body.is_online else None,
            )
            if not result.success:
                message = (result.message or "").lower()
                status_code = 409 if ("limit" in message or "already" in message) else 500
                AuditLogService.add_log(
                    message=f"NORMAL TRAINING START FAILED: Slot {slot_Number} ({card_instance.name}) - {result.message}",
                    level="ERROR"
                )
                log_error(
                    slot_Number,
                    "card_routes",
                    f"Normal ML training failed to start on slot {slot_Number} ({card_instance.name}): {result.message}",
                    role="System",
                    name=card_instance.name,
                )
                raise HTTPException(
                    status_code=status_code, 
                    detail=f"Normal training failed to start: {result.message}"
                )
            else:
                AuditLogService.add_log(
                    message=f"NORMAL TRAINING STARTED: Slot {slot_Number} ({card_instance.name})",
                    level="INFO"
                )
                log_info(
                    slot_Number,
                    "card_routes",
                    f"Normal ML training started successfully on slot {slot_Number} ({card_instance.name})",
                    role="System",
                    name=card_instance.name,
                )
                return result
        
        
    except HTTPException:
        raise
    except Exception as e:
        AuditLogService.add_log(
            message=f"TRAINING START FAILED: Slot {slot_Number} - {str(e)}",
            level="ERROR"
        )
        log_error(
            slot_Number,
            "card_routes",
            f"Unexpected error starting training for slot {slot_Number}: {str(e)}",
            role="System",
            name=card_instance.name if card_instance else "Unknown",
        )
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to start training: {str(e)}"
        )   

@router.post("/{slot_Number}/training/stop")
async def stop_ml_training(
    slot_Number: int,
):
    """Stop ML training for a given slot number."""
    try:
        if not (1 <= slot_Number <= DEFAULT_MAX_CARDS):
            log_error(
                slot_Number,
                "card_routes",
                f"Invalid slot number attempted for training stop: {slot_Number}. Must be between 1 and {DEFAULT_MAX_CARDS}",
                role="System",
                name="Unknown",
            )
            raise HTTPException(status_code=400, detail="Invalid slot number")
        
        # card_instance =  request.app.state.app_manager.cards[slot_Number - 1]
        card_instance = app_manager.get_card_by_slot(slot_Number)
        if not card_instance:
            log_error(
                slot_Number,
                "card_routes",
                f"Card instance not found for training stop on slot {slot_Number}",
                role="System",
                name="Unknown",
            )
            raise HTTPException(status_code=404, detail="Card instance not found")
        try:
            await card_instance.stop_training()
            AuditLogService.add_log(
                message=f"TRAINING STOPPED: Slot {slot_Number} ({card_instance.name})",
                level="INFO"
            )
            return{
                "message": f"ML training stopped for slot {slot_Number}"           
            }
        except Exception as e:
            AuditLogService.add_log(
                message=f"TRAINING STOP FAILED: Slot {slot_Number} ({card_instance.name}) - {str(e)}",
                level="ERROR"
            )
            raise HTTPException(status_code=500, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        AuditLogService.add_log(
            message=f"TRAINING STOP FAILED: Slot {slot_Number} - {str(e)}",
            level="ERROR"
        )
        log_error(
            slot_Number,
            "card_routes",
            f"Unexpected error stopping training for slot {slot_Number}: {str(e)}",
            role="System",
            name=card_instance.name if card_instance else "Unknown",
        )
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to stop training: {str(e)}"
        )    


#----------------Recording Routes -----------------
@router.post("/{slot_Number}/recording/start")
async def start_recording(slot_Number: int):
    """Start recording for a given slot number."""
    card_instance = None
    try:
        if not (1 <= slot_Number <= DEFAULT_MAX_CARDS):
            log_warning(
                slot_Number,
                "card_routes",
                f"Invalid slot number attempted: {slot_Number}. Must be between 1 and {DEFAULT_MAX_CARDS}",
                role="System",
                name="Unknown",
            )
            raise HTTPException(status_code=400, detail="Invalid slot number")
        
        # card_instance =  request.app.state.app_manager.cards[slot_Number - 1]
        card_instance = app_manager.get_card_by_slot(slot_Number)
        if not card_instance:
            log_error(
                slot_Number,
                "card_routes",
                f"Card instance not found for slot {slot_Number}",
                role="System",
                name="Unknown",
            )
            raise HTTPException(status_code=404, detail="Card instance not found")
        try:
            if card_instance.camera is None:
                log_error(
                    slot_Number,
                    "card_routes",
                    f"No camera configured for recording on slot {slot_Number} ({card_instance.name})",
                    role="System",
                    name=card_instance.name,
                )
                raise HTTPException(status_code=400, detail="No camera configured for this card")
            result = await card_instance.camera.start_recording()
            if not result.success:
                message = (result.message or "").lower()
                if "limit" in message or "already" in message:
                    status_code = 409
                else:
                    status_code = 500

                AuditLogService.add_log(
                    message=f"RECORDING START FAILED: Slot {slot_Number} ({card_instance.name}) - {result.message}",
                    level="WARNING"
                )
                log_error(
                    slot_Number,
                    "card_routes",
                    f"Recording start failed on slot {slot_Number} ({card_instance.name}): {result.message}",
                    role="System",
                    name=card_instance.name,
                )
                raise HTTPException(status_code=status_code, detail=result.message)
            AuditLogService.add_log(
                message=f"RECORDING STARTED: Slot {slot_Number} ({card_instance.name})",
                level="INFO"
            )
            log_info(
                slot_Number,
                "card_routes",
                f"Recording started successfully on slot {slot_Number} ({card_instance.name})",
                role="System",
                name=card_instance.name,
            )
            return{
                "message": result.message or f"Recording started for slot {slot_Number}"          
            }
        except HTTPException:
            raise
        except Exception as e:
            AuditLogService.add_log(
                message=f"RECORDING START FAILED: Slot {slot_Number} ({card_instance.name}) - {str(e)}",
                level="ERROR"
            )
            log_error(
                slot_Number,
                "card_routes",
                f"Error starting recording on slot {slot_Number} ({card_instance.name}): {str(e)}",
                role="System",
                name=card_instance.name,
            )
            raise HTTPException(status_code=500, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        AuditLogService.add_log(
            message=f"RECORDING START FAILED: Slot {slot_Number} - {str(e)}",
            level="ERROR"
        )
        log_error(
            slot_Number,
            "card_routes",
            f"Unexpected error starting recording for slot {slot_Number}: {str(e)}",
            role="System",
            name=card_instance.name if card_instance else "Unknown",
        )
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to start recording: {str(e)}"
        )

@router.post("/{slot_Number}/recording/stop")
async def stop_recording(
    slot_Number: int,
):
    """Stop recording for a given slot number."""
    try:
        if not (1 <= slot_Number <= DEFAULT_MAX_CARDS):
            log_warning(
                slot_Number,
                "card_routes",
                f"Invalid slot number attempted: {slot_Number}. Must be between 1 and {DEFAULT_MAX_CARDS}",
                role="System",
                name="Unknown",
            )
            raise HTTPException(status_code=400, detail="Invalid slot number")
        
        # card_instance =  request.app.state.app_manager.cards[slot_Number - 1]
        card_instance = app_manager.get_card_by_slot(slot_Number)
        if not card_instance:
            log_error(
                slot_Number,
                "card_routes",
                f"Card instance not found for slot {slot_Number}",
                role="System",
                name="System",
            )
            raise HTTPException(status_code=404, detail="Card instance not found")
        
        try:
            if card_instance.camera is None:
                log_error(
                    slot_Number,
                    "card_routes",
                    f"No camera configured for recording on slot {slot_Number} ({card_instance.name})",
                    role="System",
                    name=card_instance.name,
                )
                raise HTTPException(status_code=400, detail="No camera configured for this card")
            result = await card_instance.camera.stop_recording()
            if not result.success:
                AuditLogService.add_log(
                    message=f"RECORDING STOP FAILED: Slot {slot_Number} ({card_instance.name}) - {result.message}",
                    level="WARNING"
                )
                log_warning(
                    slot_Number,
                    "card_routes",
                    f"Recording stop failed on slot {slot_Number} ({card_instance.name}): {result.message}",
                    role="System",
                    name=card_instance.name,
                )
                raise HTTPException(status_code=500, detail=result.message)
            AuditLogService.add_log(
                message=f"RECORDING STOPPED: Slot {slot_Number} ({card_instance.name})",
                level="INFO"
            )
            return{
                "message": result.message or f"Recording stopped for slot {slot_Number}"          
            }
        except HTTPException:
            raise
        except Exception as e:
            AuditLogService.add_log(
                message=f"RECORDING STOP FAILED: Slot {slot_Number} ({card_instance.name}) - {str(e)}",
                level="ERROR"
            )
            raise HTTPException(status_code=500, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        AuditLogService.add_log(
            message=f"RECORDING STOP FAILED: Slot {slot_Number} - {str(e)}",
            level="ERROR"
        )
        log_error(
            slot_Number,
            "card_routes",
            f"Unexpected error stopping recording for slot {slot_Number}: {str(e)}",
            role="System",
            name=card_instance.name if card_instance else "Unknown",
        )
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to stop recording: {str(e)}"
        )    
    

#=====================Training Roi still =====================

@router.post("/{slot_Number}/training/roi/still")
async def capture_training_roi_still(
    slot_Number: int,
    body: TrainingRoiStillRequest = Body(default=None)
):
    """Capture still images for training ROI."""
    try:
        if not (1 <= slot_Number <= DEFAULT_MAX_CARDS):
            raise HTTPException(status_code=400, detail="Invalid slot number")
        
        # card_instance =  request.app.state.app_manager.cards[slot_Number - 1]
        card_instance = app_manager.get_card_by_slot(slot_Number)
        if not card_instance:
            raise HTTPException(status_code=404, detail="Card instance not found")
        
        try:
            ok , payload = await card_instance.capture_training_roi_still(
                is_online=body.is_online,
                video_path=body.video_path if not body.is_online else None,
            )
            if not ok:
                AuditLogService.add_log(
                    message=f"TRAINING ROI STILL CAPTURE FAILED: Slot {slot_Number} ({card_instance.name}) - {payload}",
                    level="ERROR"
                )
                log_error(
                    slot_Number,
                    "card_routes",
                    f"Failed to capture training ROI stills for slot {slot_Number} ({card_instance.name}): {payload}",
                    role="System",
                    name=card_instance.name,
                )
                raise HTTPException(status_code=500, detail=payload)
            
            AuditLogService.add_log(
                message=f"TRAINING ROI STILL CAPTURED: Slot {slot_Number} ({card_instance.name})",
                level="INFO"
            )
            return payload
        except HTTPException:
            raise
        except Exception as e:
            AuditLogService.add_log(
                message=f"TRAINING ROI STILL CAPTURE FAILED: Slot {slot_Number} ({card_instance.name}) - {str(e)}",
                level="ERROR"
            )
            raise HTTPException(status_code=500, detail=str(e))
    except HTTPException:
        raise
    except Exception as e:
        AuditLogService.add_log(
            message=f"TRAINING ROI STILL CAPTURE FAILED: Slot {slot_Number} - {str(e)}",
            level="ERROR"
        )
        log_error(
            slot_Number,
            "card_routes",
            f"Unexpected error capturing training ROI stills for slot {slot_Number}: {str(e)}",
            role="System",
            name=card_instance.name if card_instance else "Unknown",
        )
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to capture training ROI stills: {str(e)}"
        ) 


@router.get("/{slot_Number}/training/reference-frame")
async def get_training_reference_frame(slot_Number: int):
    """Fetch ref.png from configured model path for a slot."""
    try:
        if not (1 <= slot_Number <= DEFAULT_MAX_CARDS):
            log_warning(
                slot_Number,
                "card_routes",
                f"Invalid slot number for reference frame request: {slot_Number}",
                role="System",
                name=f"slot-{slot_Number}",
            )
            raise HTTPException(status_code=400, detail="Invalid slot number")

        cam_state = AppStateStore.get_camera(slot_Number) or {}
        cam_name = str(cam_state.get("name") or f"Camera_{slot_Number}")
        if not cam_state:
            log_warning(
                slot_Number,
                "card_routes",
                f"Reference frame request failed - camera not found for slot {slot_Number}",
                role="System",
                name=cam_name,
            )
            raise HTTPException(status_code=404, detail="Camera not found for slot")

        model_path = str(cam_state.get("model_path") or "").strip()
        if not model_path:
            log_warning(
                slot_Number,
                "card_routes",
                f"Reference frame request failed - model_path is empty for slot {slot_Number} ({cam_name})",
                role="System",
                name=cam_name,
            )
            raise HTTPException(status_code=400, detail="Model path is empty for this slot")

        model_path_obj = Path(model_path)
        if not model_path_obj.exists():
            log_warning(
                slot_Number,
                "card_routes",
                f"Reference frame request failed - model_path not found: {model_path_obj}",
                role="System",
                name=cam_name,
            )
            raise HTTPException(status_code=404, detail="Model path not found")

        if model_path_obj.is_file():
            models_dir = model_path_obj.parent
        elif model_path_obj.name.lower() == "models":
            models_dir = model_path_obj
        else:
            candidate = model_path_obj / "models"
            models_dir = candidate if candidate.exists() else model_path_obj

        if not models_dir.exists() or not models_dir.is_dir():
            log_warning(
                slot_Number,
                "card_routes",
                f"Reference frame request failed - models directory not found: {models_dir}",
                role="System",
                name=cam_name,
            )
            raise HTTPException(status_code=404, detail="Models directory not found")

        ref_frame_path = models_dir / "ref.png"
        if not ref_frame_path.exists() or not ref_frame_path.is_file():
            log_warning(
                slot_Number,
                "card_routes",
                f"Reference frame request failed - ref.png not found: {ref_frame_path}",
                role="System",
                name=cam_name,
            )
            raise HTTPException(status_code=404, detail="Reference frame ref.png not found")

        log_info(
            slot_Number,
            "card_routes",
            f"Reference frame request started for slot {slot_Number} ({cam_name}) from {ref_frame_path}",
            role="System",
            name=cam_name,
        )

        image_bytes = await asyncio.to_thread(ref_frame_path.read_bytes)
        image_b64_bytes = await asyncio.to_thread(base64.b64encode, image_bytes)
        image_b64 = image_b64_bytes.decode("utf-8")

        log_info(
            slot_Number,
            "card_routes",
            f"Reference frame request success for slot {slot_Number} ({cam_name}) bytes={len(image_bytes)}",
            role="System",
            name=cam_name,
        )

        return {
            "slot_number": slot_Number,
            "model_path": str(models_dir),
            "image_b64": f"data:image/png;base64,{image_b64}",
        }
    except HTTPException:
        raise
    except Exception as e:
        log_error(
            slot_Number,
            "card_routes",
            f"Unexpected error fetching reference frame for slot {slot_Number}: {str(e)}",
            role="System",
            name=f"slot-{slot_Number}",
        )
        raise HTTPException(status_code=500, detail=f"Failed to fetch reference frame: {str(e)}")
