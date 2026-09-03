import cv2


def stream_camera(camera_url):

    cap = cv2.VideoCapture(camera_url)

    while True:

        success, frame = cap.read()

        if not success:
            break

        _, buffer = cv2.imencode(".jpg", frame)

        frame_bytes = buffer.tobytes()

        yield (
            b"--frame\r\n"
            b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
        )