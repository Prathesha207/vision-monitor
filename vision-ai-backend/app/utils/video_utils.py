import cv2
import os


def open_camera(camera_url):

    cap = cv2.VideoCapture(camera_url)

    if not cap.isOpened():
        raise Exception("Cannot open camera")

    return cap


def extract_frames(video_path: str, output_dir: str, interval: int = 5):

    os.makedirs(output_dir, exist_ok=True)

    cap = cv2.VideoCapture(video_path)

    frame_count = 0
    saved_frame_count = 0
    frame_paths = []

    while True:

        success, frame = cap.read()

        if not success:
            break

        frame_count += 1

        if frame_count % interval == 0:

            frame = cv2.resize(frame, (640, 480))

            saved_frame_count += 1

            frame_name = f"frame_{saved_frame_count}.jpg"
            frame_path = os.path.join(output_dir, frame_name)

            cv2.imwrite(frame_path, frame)

            frame_paths.append(frame_path.replace("\\", "/"))

    cap.release()

    return {
        "frame_count": saved_frame_count,
        "frames": frame_paths
    }