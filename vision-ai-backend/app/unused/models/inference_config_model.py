# app/models/inference_config_model.py

from sqlalchemy import Column, Integer, Float, DateTime, String, Boolean
from datetime import datetime
from app.core.database import Base



class InferenceConfig(Base):
    __tablename__ = "inference_configs"

    id = Column(Integer, primary_key=True, index=True)

    # ================= INFERENCE CONFIG =================

    # Model selection
    trained_model = Column(String, nullable=True)

    # Detection tuning
    min_detection_area = Column(Integer, default=0)  # 0–1000 pixels
    conf_threshold = Column(Float, default=0.5)

    # Dynamic thresholding
    use_dynamic_threshold = Column(Boolean, default=False)

    # Video outputs
    processed_video = Column(Boolean, default=True)
    raw_video = Column(Boolean, default=False)
    save_failed_frame = Column(Boolean, default=False)

    # Inference mode: "testing" or "production"
    mode = Column(String, default="production")

    inference_video_path = Column(String, nullable=True)
    inference_video_testing_path = Column(String, nullable=True)
    
    # ================= ADVANCED INFERENCE (RECOMMENDED) =================

    # Non-Max Suppression threshold
    nms_iou_threshold = Column(Float, default=0.5)

    # Max detections per frame
    max_detections = Column(Integer, default=100)

    # Skip frames (performance optimization)
    frame_skip = Column(Integer, default=0)

    # Batch inference
    inference_batch_size = Column(Integer, default=1)

    # Recording format: "MJPEG" or "FFV1"
    recording_format = Column(String, default="FFV1")

    # Inference format: "MJPEG" or "FFV1"
    inference_format = Column(String, default="FFV1")

    # ================= METADATA =================

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)