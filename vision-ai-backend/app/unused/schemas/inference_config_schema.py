from pydantic import BaseModel, model_validator
from typing import Optional
from datetime import datetime


# ================= CREATE SCHEMA =================

class InferenceConfigCreate(BaseModel):

    # -------- MODEL --------
    trained_model: Optional[str] = None

    # -------- DETECTION --------
    min_detection_area: int = 0
    conf_threshold: float = 0.5

    # -------- FLAGS --------
    use_dynamic_threshold: bool = False
    processed_video: bool = True
    raw_video: bool = False
    save_failed_frame: bool = False
    mode: str = "production"
    inference_video_path: Optional[str] = None
    inference_video_testing_path: Optional[str] = None


    # -------- ADVANCED --------
    nms_iou_threshold: float = 0.5
    max_detections: int = 100
    frame_skip: int = 0
    inference_batch_size: int = 1

    # -------- RECORDING --------
    recording_format: str = "FFV1"  # "MJPEG" or "FFV1"
    inference_format: str = "FFV1"  # "MJPEG" or "FFV1"

    # ================= VALIDATION =================
    @model_validator(mode="after")
    def validate_ranges(self):

        if self.recording_format not in ("MJPEG", "FFV1"):
            raise ValueError("recording_format must be 'MJPEG' or 'FFV1'")

        if self.inference_format not in ("MJPEG", "FFV1"):
            raise ValueError("inference_format must be 'MJPEG' or 'FFV1'")

        return self


# ================= UPDATE SCHEMA =================

class InferenceConfigUpdate(BaseModel):

    trained_model: Optional[str] = None

    min_detection_area: Optional[int] = None
    conf_threshold: Optional[float] = None

    use_dynamic_threshold: Optional[bool] = None
    processed_video: Optional[bool] = None
    raw_video: Optional[bool] = None
    save_failed_frame: Optional[bool] = None
    mode: Optional[str] = None
    inference_video_path: Optional[str] = None
    inference_video_testing_path: Optional[str] = None


    nms_iou_threshold: Optional[float] = None
    max_detections: Optional[int] = None
    frame_skip: Optional[int] = None
    inference_batch_size: Optional[int] = None

    recording_format: Optional[str] = None  # "MJPEG" or "FFV1"
    inference_format: Optional[str] = None  # "MJPEG" or "FFV1"

    # ================= VALIDATION =================
    @model_validator(mode="after")
    def validate_ranges(self):
        
        if self.recording_format is not None and self.recording_format not in ("MJPEG", "FFV1"):
            raise ValueError("recording_format must be 'MJPEG' or 'FFV1'")

        if self.inference_format is not None and self.inference_format not in ("MJPEG", "FFV1"):
            raise ValueError("inference_format must be 'MJPEG' or 'FFV1'")

        return self


# ================= RESPONSE SCHEMA =================

class InferenceConfigResponse(BaseModel):
    id: int

    # -------- MODEL --------
    trained_model: Optional[str]

    # -------- DETECTION --------
    min_detection_area: int
    conf_threshold: float

    # -------- FLAGS --------
    use_dynamic_threshold: bool
    processed_video: bool
    raw_video: bool
    save_failed_frame: bool
    mode: str
    inference_video_path: Optional[str]
    inference_video_testing_path: Optional[str]

    # -------- ADVANCED --------
    nms_iou_threshold: float
    max_detections: int
    frame_skip: int
    inference_batch_size: int

    # -------- RECORDING --------
    recording_format: str
    inference_format: str

    # -------- METADATA --------
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True