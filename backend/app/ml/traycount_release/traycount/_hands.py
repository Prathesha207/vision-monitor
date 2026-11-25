"""traycount/_hands.py - MediaPipe hand helpers copied unchanged from the project's scripts/13_propagate_v2.py."""
import cv2
import numpy as np


def hands_in_frame(hands, bgr):
    import mediapipe as mp
    h, w = bgr.shape[:2]
    res = hands.detect(mp.Image(image_format=mp.ImageFormat.SRGB, data=cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)))
    return [np.array([[l.x * w, l.y * h] for l in lms], np.float32) for lms in (res.hand_landmarks or [])]


def hand_in_tray(hand_pts, tray_poly, cfg):
    if tray_poly is None:
        return False
    t = tray_poly.reshape(-1, 1, 2).astype(np.int32)
    margin = cfg["hand_tray_margin_frac"] * np.sqrt(cv2.contourArea(t))
    return any(cv2.pointPolygonTest(t, (float(x), float(y)), True) >= -margin for pts in hand_pts for x, y in pts)
