# app/api/inference_config_api.py

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.dependencies import get_db
from app.schemas.inference_config_schema import (
    InferenceConfigCreate,
    InferenceConfigResponse,
)
from app.services import inference_config_service

router = APIRouter()


# ================= CREATE OR UPDATE =================
@router.post("/create", response_model=InferenceConfigResponse)
def create_or_update_config(
    data: InferenceConfigCreate,
    db: Session = Depends(get_db)
):
    return inference_config_service.create_or_update_config(db, data)


# ================= GET CONFIG =================
@router.get("/", response_model=InferenceConfigResponse)
def get_config(db: Session = Depends(get_db)):
    return inference_config_service.get_config(db)