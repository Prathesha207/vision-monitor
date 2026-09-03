# import cv2
# import numpy as np
# import base64

# # ✅ NEW IMPORT
# from tube_detection.inference import TubePipeline


# # ================= LOAD MODEL =================
# MODEL1_PATH = "tube_detection/models/best.pt"
# MODEL2_PATH = "tube_detection/models/best_1.pt"

# pipeline = TubePipeline(MODEL1_PATH, MODEL2_PATH)


# # ================= UTILS =================
# def encode_image(img):
#     if img is None:
#         return None

#     _, buffer = cv2.imencode(".jpg", img)
#     return base64.b64encode(buffer).decode()


# # ================= MAIN =================
# def run_inference(frame):

#     if frame is None:
#         return None

#     # 🔥 MODEL 1
#     det1 = pipeline.run_model1(frame)

#     if not det1:
#         return {
#             "detections": [],
#             "frame": encode_image(frame),
#             "status": "NO_DETECTION"
#         }

#     # 🔥 MODEL 2
#     det2 = pipeline.run_model2(frame)

#     # 🔥 DRAW
#     final_frame = pipeline.draw(frame.copy(), det1, det2)

#     # ================= FORMAT =================
#     detections = []

#     for d in det1:
#         detections.append({
#             "bbox": [int(v) for v in d["box"]],
#             "class": int(d["class"]),
#             "confidence": float(d["score"]),
#             "model": "model1"
#         })

#     for d in det2:
#         detections.append({
#             "bbox": [int(v) for v in d["box"]],
#             "class": int(d["class"]),
#             "confidence": float(d["score"]),
#             "model": "model2"
#         })

#     return {
#         "detections": detections,
#         "frame": encode_image(final_frame),
#         "status": "NORMAL"
#     }s