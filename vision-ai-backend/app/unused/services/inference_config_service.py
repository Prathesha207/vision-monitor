# app/services/inference_config_service.py

from sqlalchemy.orm import Session
from fastapi import HTTPException

from app.models.inference_config_model import InferenceConfig


# ================= CREATE OR UPDATE =================
def create_or_update_config(db: Session, data):
    config = db.query(InferenceConfig).first()

    if config:
        # -------- UPDATE --------
        for key, value in data.dict(exclude_unset=True).items():
            setattr(config, key, value)
    else:
        # -------- CREATE --------
        config = InferenceConfig(**data.dict())
        db.add(config)

    db.commit()
    db.refresh(config)

    return config


# ================= GET CONFIG =================
def get_config(db: Session):
    config = db.query(InferenceConfig).first()

    # auto create if not exists (important for frontend)
    if not config:
        config = InferenceConfig()
        db.add(config)
        db.commit()
        db.refresh(config)

    return config