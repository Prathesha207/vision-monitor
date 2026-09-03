import os
import uuid
from app.services.realtime_log_service import realtime_log_service


UPLOAD_DIR = "storage/uploads/videos"


def save_uploaded_video(video_file):
    os.makedirs(UPLOAD_DIR, exist_ok=True)

    # ================= DELETE OLD VIDEOS =================
    for file_name in os.listdir(UPLOAD_DIR):
        file_path = os.path.join(UPLOAD_DIR, file_name)

        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
                print(f"Deleted old video: {file_path}")
        except Exception as e:
            print(f"Failed to delete {file_path}: {e}")

    # ================= SAVE NEW VIDEO =================
    try:

        video_id = str(uuid.uuid4())

        # keep original extension if available
        ext = video_file.filename.split(".")[-1] if "." in video_file.filename else "mp4"

        video_filename = f"{video_id}.{ext}"
        video_path = os.path.join(UPLOAD_DIR, video_filename)

        with open(video_path, "wb") as buffer:
            buffer.write(video_file.file.read())

        realtime_log_service.add_log(
            "system",
            "UPLOAD",
            f"Video uploaded successfully: {video_filename}",
            "success"
        )    

        return {
            "video_id": video_id,
            "video_path": video_path.replace("\\", "/"),  # for URL compatibility
        }
        
    except Exception as e:
        realtime_log_service.add_log(
            "system",
            "ERROR",
            "Video upload failed",
            "error"
        )

        raise e