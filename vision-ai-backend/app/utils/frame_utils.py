import cv2


def encode_frame(frame):

    _, buffer = cv2.imencode(".jpg", frame)

    return buffer.tobytes()