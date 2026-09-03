# from fastapi import APIRouter, UploadFile, File, HTTPException
# import cv2
# import numpy as np

# # ✅ UPDATED IMPORT (very important)
# from app.ml.tube_inference_service import run_inference

# router = APIRouter()


# @router.post("/frame")
# async def inference(file: UploadFile = File(...)):

#     try:
#         contents = await file.read()

#         nparr = np.frombuffer(contents, np.uint8)
#         frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)

#         if frame is None:
#             raise HTTPException(status_code=400, detail="Invalid image")

#         result = run_inference(frame)

#         return result

#     except Exception as e:
#         raise HTTPException(status_code=500, detail=str(e))