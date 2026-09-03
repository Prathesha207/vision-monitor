from pydantic import BaseModel


class InferenceRequest(BaseModel):
    camera_id: int


class DetectionBox(BaseModel):
    label: str
    confidence: float
    x: int
    y: int
    width: int
    height: int


class InferenceResponse(BaseModel):
    detections: list[DetectionBox]