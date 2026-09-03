from pydantic import BaseModel


# ================= START =================
class StartRecordingRequest(BaseModel):
    session_id: str


# ================= FRAME (optional / keep if needed) =================
class FrameRequest(BaseModel):
    session_id: str
    frame: str


# ================= STOP =================
class StopRecordingRequest(BaseModel):
    session_id: str