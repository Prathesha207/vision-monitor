import threading
import time
import logging
from fractions import Fraction
from datetime import datetime
from pathlib import Path

import cv2
import av

try:
    import depthai as dai
except ImportError:
    dai = None

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("camera-service")


class CameraLiveService:
    def __init__(self):
        self.latest_frame = None
        self.running = False
        self.thread = None
        self.lock = threading.Lock()

        self.camera_ip = None
        self.device = None

        self.video_w = 1920
        self.video_h = 1080
        self.fps = 10.0

        self.record_dir = Path("recordings_ffv1")
        self.record_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- PIPELINE ----------------
    def build_pipeline(self):
        pipeline = dai.Pipeline()

        cam = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
        out = cam.requestOutput(
            (self.video_w, self.video_h),
            dai.ImgFrame.Type.NV12,
            fps=self.fps
        )

        return pipeline, out.createOutputQueue(maxSize=6, blocking=True)

    # ---------------- WORKER ----------------
    def _worker(self):
        try:
            if dai is None:
                logger.error("DepthAI not installed")
                return

            logger.info(f"Connecting to camera: {self.camera_ip}")

            info = dai.DeviceInfo(self.camera_ip)
            self.device = dai.Device(info)

            pipeline, queue = self.build_pipeline()
            self.device.startPipeline(pipeline)

            fr = Fraction.from_float(self.fps).limit_denominator()
            time_base = Fraction(fr.denominator, fr.numerator)

            container = None
            stream = None

            self.running = True

            while self.running:
                msg = queue.get()
                if msg is None:
                    continue

                frame = msg.getCvFrame()

                with self.lock:
                    self.latest_frame = frame.copy()

                # -------- RECORDING --------
                if container is None:
                    h, w = frame.shape[:2]
                    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                    out_path = self.record_dir / f"video_{ts}.mkv"

                    container = av.open(str(out_path), mode="w")
                    stream = container.add_stream("ffv1", rate=fr)
                    stream.time_base = time_base
                    stream.pix_fmt = "bgr0"
                    stream.width = w
                    stream.height = h

                frame_av = av.VideoFrame.from_ndarray(frame, format="bgr24").reformat("bgr0")

                for packet in stream.encode(frame_av):
                    container.mux(packet)

        except Exception as e:
            logger.error(f"Camera error: {str(e)}")

        finally:
            self.running = False

            if self.device:
                self.device.close()

            logger.info("Camera stopped")

    # ---------------- PUBLIC METHODS ----------------
    def start(self, camera_ip: str):
        if self.running:
            return "Camera already running"

        self.camera_ip = camera_ip
        self.thread = threading.Thread(target=self._worker, daemon=True)
        self.thread.start()

        time.sleep(1)
        return "Camera started"

    def stop(self):
        if not self.running:
            return "Camera already stopped"

        self.running = False
        return "Camera stopping"

    def get_frame(self):
        with self.lock:
            return self.latest_frame

    def is_running(self):
        return self.running


# Singleton instance
camera_live_service = CameraLiveService()