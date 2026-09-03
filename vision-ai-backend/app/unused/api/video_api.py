from fastapi import APIRouter, UploadFile, File
from app.services.video_service import save_uploaded_video

router = APIRouter()

BASE_URL = "http://127.0.0.1:8000"

@router.post("/upload-video")
async def upload_video(file: UploadFile = File(...)):
    result = save_uploaded_video(file)

    video_url = f"{BASE_URL}/{result['video_path']}"

    return {
        "message": "Video uploaded successfully",
        "video_id": result["video_id"],
        "video_url": video_url,
    }