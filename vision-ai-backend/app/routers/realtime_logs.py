from fastapi import APIRouter
from app.services.realtime_log_service import realtime_log_service

router = APIRouter(prefix="/logs", tags=["Realtime Logs"])


@router.get("/{category}")
def get_logs(category: str):
    return {
        "status": "success",
        "category": category,
        "logs": realtime_log_service.get_logs(category)
    }