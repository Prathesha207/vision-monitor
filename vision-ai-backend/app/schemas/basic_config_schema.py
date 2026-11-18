# app/schemas/basic_config_schema.py

from typing import Optional
from pydantic import BaseModel, ConfigDict

from app.schemas.camera_schema import CameraUpdate


class CameraBasicResponse(BaseModel):
    name: Optional[str] = None
    ip_address: Optional[str] = None
    resolution: Optional[str] = None
    fps: Optional[int] = None
    rotation_angle: Optional[int] = None
    recording_video_path: Optional[str] = None
    recording_video_testing_path: Optional[str] = None
    recording_format: Optional[str] = None

    control_mode: Optional[str] = None

    exposure: Optional[float] = None
    gain: Optional[float] = None
    focus: Optional[int] = None

    brightness: Optional[int] = None
    contrast: Optional[int] = None

    auto_exposure: Optional[bool] = None
    auto_focus: Optional[bool] = None

    stream_url: Optional[str] = None
    is_inference_enabled: Optional[bool] = None
    roi_enabled: Optional[bool] = None

    is_enabled: Optional[bool] = None

    model_config = ConfigDict(from_attributes=True)


class InferenceBasicConfig(BaseModel):
    mode: Optional[str] = None
    processed_video: Optional[bool] = None
    raw_video: Optional[bool] = None
    inference_video_path: Optional[str] = None
    inference_video_testing_path: Optional[str] = None
    recording_format: Optional[str] = None
    inference_format: Optional[str] = None


class BasicConfigResponse(BaseModel):
    camera: Optional[CameraBasicResponse] = None
    inference: InferenceBasicConfig = InferenceBasicConfig(mode="production")


class BasicConfigUpdate(BaseModel):
    camera: Optional[CameraUpdate] = None
    inference: Optional[InferenceBasicConfig] = None
