# app/services/camera.py
from __future__ import annotations
import cv2
import asyncio
import queue
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional
import av
from fractions import Fraction
import depthai as dai

from app.logger import log_info, log_warning, log_error, log_critical, log_debug
from app.services.card_opertions.data_Models import OperationResult
from app.services.appStateStore import AppStateStore
from app.services.settings_service import APP_CONFIG
from app.services.card_opertions import recording_writter
from app.services.card_opertions.streaming_socket_emitters import emit_streaming_started, emit_streaming_stopped
if TYPE_CHECKING:
    from app.services.card_instance import CardInstance

# ---------------- Video writer abstractions ----------------

class _BaseWriter:
    path: Path
    def write(self, frame): ...
    def close(self): ...
    def is_open(self) -> bool: ...


class _OpenCVMJPGWriter(_BaseWriter):
    """Existing MJPG path via OpenCV (unchanged behavior)."""
    def __init__(self, path: Path, fps: float, size: tuple[int, int]):
        self.path = path
        fourcc = cv2.VideoWriter_fourcc(*"MJPG")
        self._vw = cv2.VideoWriter(str(path), fourcc, float(fps), size, True)

    def write(self, frame):
        self._vw.write(frame)

    def close(self):
        try:
            self._vw.release()
        except Exception:
            pass

    def is_open(self) -> bool:
        return bool(self._vw and self._vw.isOpened())


class _PyAVFFV1Writer(_BaseWriter):
    """
    Mathematically lossless, efficient compression.
    Container: MKV; Codec: FFV1; PixFmt: bgr0 (lossless).
    """
    def __init__(self, path: Path, fps: float, size: tuple[int, int]):
        if av is None:
            raise RuntimeError("PyAV not installed; cannot use FFV1")
        self.path = path
        w, h = size
        self._container = av.open(str(path), mode="w")
        fr = Fraction.from_float(float(fps)).limit_denominator()
        time_base = Fraction(fr.denominator, fr.numerator)  # 1/FPS
        stream = self._container.add_stream("ffv1", rate=fr)
        stream.time_base = time_base
        stream.pix_fmt = "bgr0"
        stream.width = int(w)
        stream.height = int(h)
        # Good defaults for robust lossless
        stream.options = {
            "level": "3",
            "context": "1",
            "slicecrc": "1",
        }
        self._stream = stream
        self._open = True

    def write(self, frame):
        # frame: numpy BGR
        frame_av = av.VideoFrame.from_ndarray(frame, format="bgr24").reformat(format="bgr0")
        for packet in self._stream.encode(frame_av):
            self._container.mux(packet)

    def close(self):
        if not getattr(self, "_open", False):
            return
        try:
            for packet in self._stream.encode():  # flush
                self._container.mux(packet)
            self._container.close()
        finally:
            self._open = False

    def is_open(self) -> bool:
        return bool(getattr(self, "_open", False))


class _PyAVRawWriter(_BaseWriter):
    """
    Raw uncompressed frames.
    Container: AVI; Codec: rawvideo; PixFmt: bgr24.
    """
    def __init__(self, path: Path, fps: float, size: tuple[int, int]):
        if av is None:
            raise RuntimeError("PyAV not installed; cannot use rawvideo")
        self.path = path
        w, h = size
        self._container = av.open(str(path), mode="w")
        fr = Fraction.from_float(float(fps)).limit_denominator()
        time_base = Fraction(fr.denominator, fr.numerator)
        stream = self._container.add_stream("rawvideo", rate=fr)
        stream.time_base = time_base
        stream.pix_fmt = "bgr24"
        stream.width = int(w)
        stream.height = int(h)
        self._stream = stream
        self._open = True

    def write(self, frame):
        frame_av = av.VideoFrame.from_ndarray(frame, format="bgr24")
        for packet in self._stream.encode(frame_av):
            self._container.mux(packet)

    def close(self):
        if not getattr(self, "_open", False):
            return
        try:
            for packet in self._stream.encode():  # flush
                self._container.mux(packet)
            self._container.close()
        finally:
            self._open = False

    def is_open(self) -> bool:
        return bool(getattr(self, "_open", False))


class Camera:
    """
    Handles camera operations: streaming, recording, preview.
    Each CardInstance owns one Camera object (if camera has valid IP).

    Simple function responsibilities:
      - connect_device()    : Connect to DepthAI device over network
      - close_connection()  : Close device connection
      - build_pipeline()    : Build pipeline nodes (internal, called by start_pipeline)
      - start_pipeline()    : Create pipeline context, build nodes, start pipeline
      - cleanup_pipeline()  : Stop pipeline and reset queue references
      - start_capture()     : Start capture threads (MJPEG + hi-res + converter)
      - stop_capture()      : Stop capture threads
    """

    STREAMING_MODE = "streaming"
    HIGH_RES_MODE = "high_res"

    @staticmethod
    def _normalize_resolution(value: object, fallback: str = "1920x1080") -> str:
        """Convert resolution enum/string to canonical 'WxH' string."""
        if value is None:
            return fallback
        if hasattr(value, "value"):
            value = getattr(value, "value")
        text = str(value).strip()
        if "x" not in text:
            return fallback
        return text

    def __init__(
        self,
        card: CardInstance,
        ip: str,
        name: str,
        resolution: str = "1920x1080",
        rotate_angle: int = 0,
        fps: int = 30,
    ) -> None:

        # Reference to parent card
        self.card = card
        self.slot_number = card.slot_number
        self.id = card.card_id

        # Camera configuration
        cam_dict = AppStateStore.get_camera(self.slot_number) or {}
        self.ip_address = ip
        self.name = name
        self.resolution = self._normalize_resolution(
            cam_dict.get("resolution"),
            fallback=self._normalize_resolution(resolution),
        )
        self.rotate_angle = int(cam_dict.get("rotation_deg", rotate_angle))
        self.target_fps = int(cam_dict.get("target_fps", fps))

        try:
            self.width, self.height = map(int, self.resolution.split("x"))
        except Exception:
            self.resolution = "1920x1080"
            self.width, self.height = 1920, 1080

        # ==================== DepthAI Device Connection ====================
        self.is_device_connected: bool = False
        self.device_info: Optional[dai.DeviceInfo] = None
        self.device: Optional[dai.Device] = None

        # ===================== Pipeline Management =========================
        self._pipeline_context: Optional[dai.Pipeline] = None
        self._is_pipeline_running: bool = False
        self._pipeline_lock: threading.Lock = threading.Lock()
        self._lifecycle_lock: asyncio.Lock = asyncio.Lock()

        # Pipeline output queues
        self._cam_raw_queue = None
        self._cam_mjpeg_queue = None
        self._cam_script_control_queue = None

        # Tracks last sent hires forwarding state to avoid redundant sends
        self._hires_forwarding_state: Optional[bool] = None

        # ====================== Capture Threads ============================
        self._mjpeg_thread: Optional[threading.Thread] = None
        self._hires_capture_thread: Optional[threading.Thread] = None
        self._convert_thread: Optional[threading.Thread] = None

        self._mjpeg_stop_event: threading.Event = threading.Event()
        self._hires_stop_event: threading.Event = threading.Event()
        self._convert_stop_event: threading.Event = threading.Event()

        self._mjpeg_count: int = 0

        # FPS tracking
        self._modal_stream_fps_counter: int = 0
        self._modal_stream_fps_last_log: float = 0.0
        self._hires_fps_counter: int = 0
        self._hires_fps_last_log: float = 0.0

        # Camera state flags
        self._is_modal_active: bool = False
        self._cam_modal_queue: Optional[asyncio.Queue] = None
        self._server_loop: Optional[asyncio.AbstractEventLoop] = None
        self._is_streaming_active: bool = False
        self._preview_latest_frame: Optional[bytes] = None

        # Hi-res frame queues (unbounded — 512GB RAM, no frame drops)
        self._cam_hires_packet_queue: queue.Queue = queue.Queue(maxsize=0)

        self.PREVIEW_INTERVAL_FRAMES = 5

        # ====================== Recording/Writing ==========================
        self._writer_thread: Optional[threading.Thread] = None
        self._writer_stop_event = threading.Event()
        self._writer_type: Optional[str] = None
        self._writer_mode_lock = threading.Lock()
        self._writer_path: Optional[Path] = None
        self._writer_vw: Optional[cv2.VideoWriter] = None
        self._writer_started_at: Optional[datetime] = None

        self.skip_hires_frame_count = 6 # Skip first N hi-res frames after starting pipeline to allow camera to settle (avoid bad auto-exposure frames)

        log_info(self.slot_number, "camera", f"Camera initialized: {self.name} ({self.ip_address})", name=self.name)

    @property
    def is_pipeline_running(self) -> bool:
        return self._is_pipeline_running

    # ==================== Device Connection ====================

    def _on_device_log(self, message: dai.LogMessage) -> None:
        """Pipe on-device Script node logs into the application log."""
        log_info(self.slot_number, "camera_script", message.payload, name=self.name)

    async def connect_device(self) -> bool:
        """Connect to DepthAI device over network."""
        try:
            log_info(self.slot_number, "camera", f"Connecting to device at {self.ip_address}", name=self.name)
            self.device_info = dai.DeviceInfo(self.ip_address)
            loop = asyncio.get_running_loop()
            self.device = await loop.run_in_executor(None, dai.Device, self.device_info)
            self.device.setLogLevel(dai.LogLevel.WARN)
            self.device.setLogOutputLevel(dai.LogLevel.WARN)
            self.device.addLogCallback(self._on_device_log)
            self.is_device_connected = True
            log_info(self.slot_number, "camera", f"Connected to device at {self.ip_address}", name=self.name)
            return True
        except Exception as exc:
            log_error(self.slot_number, "camera", f"Failed to connect device at {self.ip_address}: {exc}", name=self.name)
            self.is_device_connected = False
            return False

    async def close_connection(self) -> None:
        """Close the device connection. Does NOT touch pipeline or capture threads."""
        try:
            if self.device is not None:
                log_info(self.slot_number, "camera", f"Closing device connection at {self.ip_address}", name=self.name)
                self.device.close()
                log_info(self.slot_number, "camera", f"Device connection closed at {self.ip_address}", name=self.name)
        except Exception as exc:
            log_error(self.slot_number, "camera", f"Error closing device at {self.ip_address}: {exc}", name=self.name)
        finally:
            self.device = None
            self.is_device_connected = False

    # ==================== Pipeline Management ====================

    @staticmethod
    def nv12_bytes(w: int, h: int) -> int:
        return int(w) * int(h) * 3

    def choose_preview_size(self) -> tuple[int, int]:
        """Choose optimal preview size based on resolution."""
        log_info(self.slot_number, "depthai", f"Preview size: {self.width}x{self.height}", name=self.name)
        return self.width, self.height

    def _set_hires_forwarding(self, is_enabled: bool) -> None:
        """Tell the on-device Script to enable/disable forwarding of full-res frames."""
        if self._hires_forwarding_state is is_enabled:
            log_info(self.slot_number, "depthai", f"Hires forwarding already {'ON' if is_enabled else 'OFF'} for {self.ip_address}, skipping", name=self.name)
            return
        try:
            if self._cam_script_control_queue is None:
                log_warning(self.slot_number, "camera", f"Cannot set hires forwarding to {is_enabled} for {self.name} - script control queue not available", name=self.name)
                return
            control_buffer = dai.Buffer(1)
            control_buffer.setData([1 if is_enabled else 0])
            self._cam_script_control_queue.send(control_buffer)
            self._hires_forwarding_state = is_enabled
            log_info(self.slot_number, "depthai", f"Hires forwarding={'ON' if is_enabled else 'OFF'} for {self.ip_address}", name=self.name)
        except Exception as exc:
            log_warning(self.slot_number, "depthai", f"Failed to set hires forwarding={is_enabled}: {exc}", name=self.name)

    def _simulate_queue_closed(self) -> None:
        """TEST ONLY: Simulate a dead device by stopping pipeline but keeping queue references.
        This causes tryGet() to throw 'MessageQueue was closed' — the real error.
        Call this, then start inference/recording to test the recovery fallback.
        Remove this method after testing."""
        log_warning(
            self.slot_number, "camera",
            f"[TEST] Simulating dead queue for {self.name} — stopping pipeline but keeping queue refs",
            name=self.name,
        )
        self.stop_capture()
        if self._pipeline_context is not None:
            self._pipeline_context.stop()
            self._pipeline_context.wait()
        self._pipeline_context = None
        # NOTE: intentionally NOT setting queues to None and NOT setting _is_pipeline_running = False
        # Queue refs still exist but are dead — tryGet() will throw "MessageQueue was closed"

    def _verify_queue_health(self) -> bool:
        """Check if DAI queues are still alive (not closed by device disconnect).
        Returns True if queues are healthy, False if closed."""
        try:
            if self._cam_mjpeg_queue is None:
                log_warning(
                    self.slot_number, "camera",
                    f"Queue health check: MJPEG queue is None for {self.name} (pipeline not built or already cleaned up)",
                    name=self.name,
                )
                return False
            if self._cam_raw_queue is None:
                log_warning(
                    self.slot_number, "camera",
                    f"Queue health check: High-res raw queue is None for {self.name} (pipeline not built or already cleaned up)",
                    name=self.name,
                )
                return False
            self._cam_mjpeg_queue.tryGet()  # returns None if empty, throws if closed
            self._cam_raw_queue.tryGet()  # returns None if empty, throws if closed
            log_info(
                self.slot_number, "camera",
                f"Queue health check passed for {self.name}",
                name=self.name,
            )
            return True
        except Exception as exc:
            err_msg = str(exc).lower()
            if "messagequeue" in err_msg and "closed" in err_msg:
                log_critical(
                    self.slot_number, "camera",
                    f"Queue health check: MessageQueue closed for {self.name}: {exc}",
                    name=self.name,
                )
                return False
            # Some other unexpected error — log it but don't trigger restart
            log_warning(
                self.slot_number, "camera",
                f"Queue health check: unexpected error for {self.name}: {exc}",
                name=self.name,
            )
            return True

    def _refresh_hires_forwarding(self) -> None:
        """Re-evaluate whether hi-res forwarding should be on based on current card state."""
        if self.card._is_training_active:
            self._set_hires_forwarding(True)
            return
        want = (self.card._is_recording_active or self.card._is_inference_active)
        self._set_hires_forwarding(want)

    async def start_training_capture_with_reset(self, settle_seconds: float = 1.0) -> bool:
        """Hard-reset camera runtime and start a fresh hi-res training capture path."""
        async with self._lifecycle_lock:
            try:
                log_info(
                    self.slot_number,
                    "camera",
                    f"Starting training hi-res capture (full reset) for {self.name} ({self.ip_address})",
                    name=self.name,
                )

                self._set_hires_forwarding(False)
                self.stop_capture()
                self.clear_hi_res_queues()
                # self.drain_dai_queue(self._cam_raw_queue)
                # self.drain_dai_queue(self._cam_mjpeg_queue)
                self.cleanup_pipeline()

                await self.close_connection()
                if settle_seconds > 0:
                    await asyncio.sleep(settle_seconds)

                if not await self.connect_device():
                    log_error(
                        self.slot_number,
                        "camera",
                        f"Training capture reset failed - device connect failed for {self.name} ({self.ip_address})",
                        name=self.name,
                    )
                    return False

                

                if not await self.start_pipeline():
                    log_error(
                        self.slot_number,
                        "camera",
                        f"Training capture reset failed - pipeline failed for {self.name} ({self.ip_address})",
                        name=self.name,
                    )
                    return False

               

                if not self.start_capture():
                    log_error(
                        self.slot_number,
                        "camera",
                        f"Training capture reset failed - capture threads failed for {self.name} ({self.ip_address})",
                        name=self.name,
                    )
                    return False
                await asyncio.sleep(0.2)

                self._set_hires_forwarding(True)
                log_info(
                    self.slot_number,
                    "camera",
                    f"Training hi-res capture started for {self.name} ({self.ip_address})",
                    name=self.name,
                )
                return True
            except Exception as exc:
                log_error(
                    self.slot_number,
                    "camera",
                    f"Training capture reset failed for {self.name} ({self.ip_address}): {exc}",
                    name=self.name,
                )
                return False

    def freeze_training_input(self) -> None:
        """Stop feeding new training frames while allowing saver threads to drain queued data."""
        try:
            self._set_hires_forwarding(False)
        except Exception as exc:
            log_warning(
                self.slot_number,
                "camera",
                f"Failed to freeze training input for {self.name}: {exc}",
                name=self.name,
            )

    async def finalize_training_capture(self, settle_seconds: float = 1.0) -> bool:
        """Finalize training camera state with full reset and fresh pipeline bring-up."""
        async with self._lifecycle_lock:
            try:
                self._set_hires_forwarding(False)
                # self.drain_dai_queue(self._cam_raw_queue)
                self.clear_hi_res_queues()
                self.stop_capture()
                self.cleanup_pipeline()

                await self.close_connection()
                if settle_seconds > 0:
                    await asyncio.sleep(settle_seconds)
                if not await self.connect_device():
                    log_error(
                        self.slot_number,
                        "camera",
                        f"Finalize training capture failed - reconnect failed for {self.name}",
                        name=self.name,
                    )
                    return False
                if not await self.start_pipeline():
                    log_error(
                        self.slot_number,
                        "camera",
                        f"Finalize training capture failed - pipeline start failed for {self.name}",
                        name=self.name,
                    )
                    return False

                self._preview_latest_frame = None
                return True
            except Exception as exc:
                log_warning(
                    self.slot_number,
                    "camera",
                    f"Error during finalize_training_capture for {self.name}: {exc}",
                    name=self.name,
                )
                return False

    async def restart_runtime(self, settle_seconds: float = 1.0) -> OperationResult:
        """Hard restart camera runtime: stop capture -> cleanup -> reconnect -> rebuild pipeline."""
        async with self._lifecycle_lock:
            try:
                log_info(
                    self.slot_number,
                    "camera",
                    f"Camera runtime restart started for {self.name}",
                    name=self.name,
                )

                self._is_streaming_active = False
                self._is_modal_active = False
                self._cam_modal_queue = None
                self.stop_capture()
                self.clear_hi_res_queues()
                # self.drain_dai_queue(self._cam_raw_queue)
                # self.drain_dai_queue(self._cam_mjpeg_queue)
                self.cleanup_pipeline()
                await self.close_connection()

                if settle_seconds > 0:
                    await asyncio.sleep(settle_seconds)

                if not await self.connect_device():
                    msg = "Camera reconnect failed during restart"
                    log_error(self.slot_number, "camera", f"{msg} for {self.name}", name=self.name)
                    return OperationResult(success=False, message=msg)

                if not await self.start_pipeline():
                    msg = "Camera pipeline start failed during restart"
                    log_error(self.slot_number, "camera", f"{msg} for {self.name}", name=self.name)
                    return OperationResult(success=False, message=msg)

                log_info(
                    self.slot_number,
                    "camera",
                    f"Camera runtime restart completed for {self.name}",
                    name=self.name,
                )
                return OperationResult(success=True, message="Camera restarted successfully")
            except Exception as exc:
                log_error(self.slot_number, "camera", f"Camera restart failed for {self.name}: {exc}", name=self.name)
                return OperationResult(success=False, message=f"Camera restart failed: {exc}")

    def build_pipeline(self, pipeline: dai.Pipeline) -> bool:
        """Build DepthAI pipeline nodes. Only creates nodes, does NOT start the pipeline."""
        try:
            log_info(self.slot_number, "camera", f"Building pipeline for {self.ip_address}", name=self.name)

            self._cam_mjpeg_queue = None
            self._cam_raw_queue = None
            self._cam_script_control_queue = None
            self.card._cam_roi_output_queues = []

            camera = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)

            # High-res output (record/infer/training)
            camera_out = camera.requestOutput(
                (int(self.width), int(self.height)),
                type=dai.ImgFrame.Type.NV12,
                fps=float(self.target_fps),
            )

            # Preview output (UI preview/stream)
            int_preview_width, int_preview_height = self.choose_preview_size()
            preview_out = camera.requestOutput(
                (int(int_preview_width), int(int_preview_height)),
                type=dai.ImgFrame.Type.NV12,
                fps=float(self.target_fps),
            )

            log_info(
                self.slot_number, "camera",
                f"Camera node: hires {self.width}x{self.height}, preview {int_preview_width}x{int_preview_height} @ {self.target_fps}fps",
                name=self.name,
            )

            # Rotation nodes for both streams
            if self.rotate_angle % 360 != 0:
                # High-res rotate
                rotate_hi = pipeline.create(dai.node.ImageManip)
                rotate_hi.initialConfig.addRotateDeg(float(self.rotate_angle))
                rotate_hi.initialConfig.setOutputSize(int(self.width), int(self.height))
                try:
                    rotate_hi.setMaxOutputFrameSize(self.nv12_bytes(int(self.width), int(self.height)))
                except AttributeError:
                    rotate_hi.setMaxOutputFrameSize(int(self.width) * int(self.height) * 3 // 2)
                camera_out.link(rotate_hi.inputImage)
                camera_out = rotate_hi.out

                # Preview rotate
                rotate_prev = pipeline.create(dai.node.ImageManip)
                rotate_prev.initialConfig.addRotateDeg(float(self.rotate_angle))
                rotate_prev.initialConfig.setOutputSize(int(int_preview_width), int(int_preview_height))
                try:
                    rotate_prev.setMaxOutputFrameSize(self.nv12_bytes(int(int_preview_width), int(int_preview_height)))
                except AttributeError:
                    rotate_prev.setMaxOutputFrameSize(int(int_preview_width) * int(int_preview_height) * 3 // 2)
                preview_out.link(rotate_prev.inputImage)
                preview_out = rotate_prev.out

                log_info(self.slot_number, "camera", f"Rotation nodes added: {self.rotate_angle}deg", name=self.name)

            # Preview MJPEG encoder
            encode = pipeline.create(dai.node.VideoEncoder)
            encode.setDefaultProfilePreset(self.target_fps, dai.VideoEncoderProperties.Profile.MJPEG)
            preview_out.link(encode.input)
            self._cam_mjpeg_queue = encode.bitstream.createOutputQueue(maxSize=12, blocking=False)

            # High-res forwarding via Script node
            script = pipeline.create(dai.node.Script)
            camera_out.link(script.inputs["in"])
            script.inputs["ctl"].setBlocking(False)
            script.setScript(
                """
import time
enabled = False
frame_count = 0
sent_count = 0
last_sent = 0
last_log = time.time()
LOG_INTERVAL = 10

while True:
    ctrl = node.io['ctl'].tryGet()
    if ctrl is not None:
        data = ctrl.getData()
        new_enabled = (data[0] != 0)
        if new_enabled != enabled:
            node.warn(f"[hires] forwarding changed: {'ON' if new_enabled else 'OFF'} at frame {frame_count}")
        enabled = new_enabled

    now = time.time()
    if now - last_log >= LOG_INTERVAL:
        sent_this_interval = sent_count - last_sent
        node.warn(f"[hires] alive | frames={frame_count} | sent_total={sent_count} | sent_last_10s={sent_this_interval} | forwarding={'ON' if enabled else 'OFF'}")
        last_sent = sent_count
        last_log = now

    msg = node.io['in'].tryGet()
    if msg is None:
        continue

    frame_count += 1
    if enabled:
        node.io['hires'].send(msg)
        sent_count += 1
                """
            )

            # ROI crops (training pipeline)
            if self.card.is_roi_enabled and self.card.roi_specifications:
                hires_out = script.outputs["hires"]
                for x, y, w, h in self.card.roi_specifications:
                    crop = pipeline.create(dai.node.ImageManip)
                    crop.initialConfig.addCrop(int(x), int(y), int(w), int(h))
                    crop.setMaxOutputFrameSize(self.nv12_bytes(int(w), int(h)))
                    hires_out.link(crop.inputImage)
                    cam_roi_queue = crop.out.createOutputQueue(maxSize=2, blocking=False)
                    self.card._cam_roi_output_queues.append(cam_roi_queue)
                log_info(self.slot_number, "camera", f"Created {len(self.card.roi_specifications)} ROI crop nodes", name=self.name)

            # High-res output queue + script control queue
            self._cam_raw_queue = script.outputs["hires"].createOutputQueue(maxSize=6, blocking=False)
            self._cam_script_control_queue = script.inputs["ctl"].createInputQueue(maxSize=4, blocking=False)

            log_info(self.slot_number, "camera", f"Pipeline built for {self.ip_address}", name=self.name)
            return True

        except Exception as exc:
            log_critical(self.slot_number, "camera", f"Failed to build pipeline for {self.ip_address}: {exc}", name=self.name)
            return False

    async def start_pipeline(self) -> bool:
        """Create pipeline context, build nodes, and start the pipeline.
        Caller must ensure device is connected and no pipeline is already running."""
        

        with self._pipeline_lock:
            try:
                self._pipeline_context = dai.Pipeline(self.device)
                if not self.build_pipeline(self._pipeline_context):
                    log_error(self.slot_number, "camera", f"Pipeline build failed for {self.name}", name=self.name)
                    self._pipeline_context = None
                    return False

                self._pipeline_context.start()
                self._set_hires_forwarding(False)
                self._is_pipeline_running = True
                log_info(self.slot_number, "camera", f"Pipeline started for {self.name}", name=self.name)
                return True

            except Exception as exc:
                log_error(self.slot_number, "camera", f"Pipeline start failed for {self.name}: {exc}", name=self.name)
                self._is_pipeline_running = False
                self._pipeline_context = None
                return False

    def cleanup_pipeline(self) -> None:
        """Stop the DepthAI pipeline and reset queue references. Does NOT touch capture threads or device."""
        with self._pipeline_lock:
            if not self._is_pipeline_running and self._pipeline_context is None:
                return

            log_info(self.slot_number, "camera", f"Cleaning up pipeline for {self.name}", name=self.name)
            try:
                if self._pipeline_context is not None:
                    self._pipeline_context.stop()
                    self._pipeline_context.wait()
            except Exception as exc:
                log_error(self.slot_number, "camera", f"Error stopping pipeline for {self.name}: {exc}", name=self.name)
            finally:
                self._is_pipeline_running = False
                self._pipeline_context = None
                self._cam_mjpeg_queue = None
                self._cam_raw_queue = None
                self._cam_script_control_queue = None
                self.card._cam_roi_output_queues = []

    # ==================== Capture Threads ====================

    def start_capture(self) -> bool:
        """Start MJPEG, hi-res capture, and converter threads."""
        # Check ALL 3 threads — if all alive, already running
        mjpeg_alive = self._mjpeg_thread and self._mjpeg_thread.is_alive()
        hires_alive = self._hires_capture_thread and self._hires_capture_thread.is_alive()
        convert_alive = self._convert_thread and self._convert_thread.is_alive()

        if mjpeg_alive and hires_alive and convert_alive:
            log_info(self.slot_number, "depthai", f"All capture threads already running for {self.name}", name=self.name)
            return True

        if not self._is_pipeline_running:
            log_error(self.slot_number, "depthai", f"Cannot start capture - pipeline not running for {self.name}", name=self.name)
            return False

        # Start each thread only if not already alive
        if not mjpeg_alive:
            self._mjpeg_stop_event.clear()
            self._mjpeg_thread = threading.Thread(
                target=self._mjpeg_loop_thread,
                name=f"mjpeg-{self.slot_number}-{self.ip_address}",
                daemon=True,
            )
            self._mjpeg_thread.start()

        if not hires_alive:
            self._hires_stop_event.clear()
            self._hires_capture_thread = threading.Thread(
                target=self._hires_capture_loop_thread,
                name=f"hires-{self.slot_number}-{self.ip_address}",
                daemon=True,
            )
            self._hires_capture_thread.start()

        if not convert_alive:
            self._convert_stop_event.clear()
            self._convert_thread = threading.Thread(
                target=self._convert_loop_thread,
                name=f"convert-{self.slot_number}-{self.ip_address}",
                daemon=True,
            )
            self._convert_thread.start()

        log_info(self.slot_number, "depthai", f"Capture threads started for {self.name} (mjpeg={not mjpeg_alive}, hires={not hires_alive}, convert={not convert_alive})", name=self.name)
        return True

    def stop_capture(self) -> None:
        """Signal and join all capture threads.
        Order: signal all -> join converter FIRST -> then producers (MJPEG, Hi-res).
        Converter stops first because producers block waiting for frame consumption.
        """
        log_info(self.slot_number, "depthai", f"Stopping capture for {self.name}", name=self.name)

        # 1. Signal ALL threads to stop
        self._mjpeg_stop_event.set()
        self._hires_stop_event.set()
        self._convert_stop_event.set()

        # 2. Join converter FIRST (consumer — if stopped after producers, producers block looking for frame consumption)
        if self._convert_thread and self._convert_thread.is_alive():
            self._convert_thread.join(timeout=5.0)
        self._convert_thread = None

        # 3. Join producers after consumer is gone
        if self._mjpeg_thread and self._mjpeg_thread.is_alive():
            self._mjpeg_thread.join(timeout=3.0)
        self._mjpeg_thread = None

        if self._hires_capture_thread and self._hires_capture_thread.is_alive():
            self._hires_capture_thread.join(timeout=3.0)
        self._hires_capture_thread = None

        # 4. Clear stale preview frame
        self._preview_latest_frame = None

    # ==================== Capture Thread Loops ====================

    def _mjpeg_loop_thread(self) -> None:
        """MJPEG consumer: reads device-encoded MJPEG frames for preview + modal stream."""
        idle_sleep = 0.001
        frames_processed = 0
        _last_log_no_pipeline = 0.0
        _last_log_no_packet = 0.0
        _last_log_preview = 0.0
        _LOG_INTERVAL = 5.0
        try:
            while not self._mjpeg_stop_event.is_set():
                if not self._is_pipeline_running or self._cam_mjpeg_queue is None:
                    now = time.time()
                    if now - _last_log_no_pipeline >= _LOG_INTERVAL:
                        log_warning(self.slot_number, "depthai", f"MJPEG loop: pipeline not running or queue missing for {self.name} - queue size - {self._cam_mjpeg_queue.getSize() if self._cam_mjpeg_queue else 'N/A'}", name=self.name)
                        _last_log_no_pipeline = now
                    time.sleep(idle_sleep)
                    continue

                pkt = self._cam_mjpeg_queue.tryGet()
                if pkt is None:
                    now = time.time()
                    if now - _last_log_no_packet >= _LOG_INTERVAL:
                        log_warning(self.slot_number, "depthai", f"MJPEG loop: no packet received for {self.name} - queue size - {self._cam_mjpeg_queue.getSize() if self._cam_mjpeg_queue else 'N/A'}", name=self.name)
                        _last_log_no_packet = now
                    time.sleep(idle_sleep)
                    continue

                jpeg_bytes = bytes(pkt.getData())
                frames_processed += 1

                # Preview update (every N frames)
                self._mjpeg_count += 1
                if self._mjpeg_count % self.PREVIEW_INTERVAL_FRAMES == 0:
                    self._preview_latest_frame = jpeg_bytes
                    now = time.time()
                    if now - _last_log_preview >= _LOG_INTERVAL:
                        log_info(self.slot_number, "camera", f"Updating preview frame for {self.name} (frame {frames_processed})", name=self.name)
                        _last_log_preview = now

                # Modal streaming push
                if self._is_modal_active and self._cam_modal_queue is not None and self._server_loop is not None:
                    try:
                        asyncio.run_coroutine_threadsafe(
                            self._cam_modal_queue_put_nowait(jpeg_bytes),
                            self._server_loop,
                        )
                        self._modal_stream_fps_counter += 1
                        current_time = time.time()
                        if current_time - self._modal_stream_fps_last_log >= 5.0:
                            log_info(self.slot_number, "camera", f"Modal stream FPS: {self._modal_stream_fps_counter // 5}/sec", name=self.name)
                            self._modal_stream_fps_counter = 0
                            self._modal_stream_fps_last_log = current_time
                    except Exception:
                        pass

        except Exception as exc:
            log_error(self.slot_number, "depthai", f"MJPEG loop error for {self.name}: {exc}", name=self.name)
        finally:
            log_info(self.slot_number, "depthai", f"MJPEG loop ended for {self.name} (processed {frames_processed} frames)", name=self.name)

    def _hires_capture_loop_thread(self) -> None:
        """Hi-res producer: reads NV12 packets from device and queues them for converter."""
        idle_sleep = 0.001
        frames_queued = 0
        frames_dropped = 0
        _last_log_no_pipeline = 0.0
        _last_log_no_consumer = 0.0
        _last_log_no_packet = 0.0
        _LOG_INTERVAL = 5.0
        self.skip_hires_frame_count = 0  # Reset skip count on thread start to allow camera to settle
        try:
            while not self._hires_stop_event.is_set():
                if not self._is_pipeline_running or self._cam_raw_queue is None:
                    now = time.time()
                    if now - _last_log_no_pipeline >= _LOG_INTERVAL:
                        log_warning(
                                self.slot_number,
                                "depthai",
                                f"Hi-res loop: pipeline not running or queue missing for {self.name} - queue size - {self._cam_raw_queue.getSize() if self._cam_raw_queue else 'N/A'}",
                                name=self.name
                            )
                        _last_log_no_pipeline = now
                    time.sleep(idle_sleep)
                    continue

                need_hires = (
                    self.card._is_recording_active
                    or self.card._is_training_active
                    or self.card._is_inference_active
                )
                if not need_hires:
                    now = time.time()
                    if now - _last_log_no_consumer >= _LOG_INTERVAL:
                        log_warning(self.slot_number, "depthai", f"Hi-res loop: no active consumer for hi-res frames for {self.name} - skipping capture", name=self.name)
                        _last_log_no_consumer = now
                    time.sleep(idle_sleep)
                    continue
                
                pkt = self._cam_raw_queue.tryGet()
                if pkt is None:
                    now = time.time()
                    if now - _last_log_no_packet >= _LOG_INTERVAL:
                        log_warning(self.slot_number, "depthai", f"Hi-res loop: no packet received for {self.name} - queue size - {self._cam_raw_queue.getSize() if self._cam_raw_queue else 'N/A'}", name=self.name)
                        _last_log_no_packet = now
                    time.sleep(idle_sleep)
                    continue
                if self.skip_hires_frame_count < 6:
                    self.skip_hires_frame_count += 1
                    continue

                try:
                    self._cam_hires_packet_queue.put_nowait(pkt)
                    frames_queued += 1
                    self._hires_fps_counter += 1
                    current_time = time.time()
                    if current_time - self._hires_fps_last_log >= 5.0:
                        log_info(self.slot_number, "camera", f"Hi-res capture FPS: {self._hires_fps_counter // 5}/sec", name=self.name)
                        self._hires_fps_counter = 0
                        self._hires_fps_last_log = current_time
                except Exception:
                    frames_dropped += 1
                    if frames_dropped % 30 == 1:
                        log_warning(self.slot_number, "camera", f"Hi-res packet queue full, dropping frames for {self.name} (total dropped: {frames_dropped})", name=self.name)

        except Exception as exc:
            log_error(self.slot_number, "depthai", f"Hi-res capture loop error for {self.name}: {exc}", name=self.name)
        finally:
            log_info(self.slot_number, "depthai", f"Hi-res capture loop ended for {self.name} (queued={frames_queued}, dropped={frames_dropped})", name=self.name)

    def _convert_loop_thread(self) -> None:
        """Converter consumer: converts NV12 -> BGR. Drains remaining packets after stop signal."""
        frames_converted = 0
        try:
            while (not self._convert_stop_event.is_set()) or (not self._cam_hires_packet_queue.empty()):
                try:
                    nv12_packet = self._cam_hires_packet_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                if self._convert_stop_event.is_set() and not (
                    self.card._is_recording_active or self.card._is_training_active or self.card._is_inference_active
                ):
                    continue

                try:
                    bgr_frame = nv12_packet.getCvFrame()
                except Exception:
                    continue

                frames_converted += 1

                if self.card._is_recording_active or self.card._is_training_active:
                    try:
                        self.card._cam_hires_frame_queue.put_nowait(bgr_frame)
                    except Exception:
                        pass

                if self.card._is_inference_active:
                    self.card.cam_latest_inference_frame = bgr_frame

        except Exception as exc:
            log_error(self.slot_number, "depthai", f"Converter loop error for {self.name}: {exc}", name=self.name)
        finally:
            log_info(self.slot_number, "depthai", f"Converter loop ended for {self.name} (converted {frames_converted} frames)", name=self.name)

    async def _cam_modal_queue_put_nowait(self, data: bytes) -> None:
        """Async helper so capture thread can push without blocking."""
        if self._cam_modal_queue is None:
            return
        try:
            self._cam_modal_queue.put_nowait(data)
        except asyncio.QueueFull:
            try:
                _ = self._cam_modal_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                self._cam_modal_queue.put_nowait(data)
            except asyncio.QueueFull:
                pass

    # ==================== Queue Management ====================

    def clear_hi_res_queues(self) -> None:
        """Clear any pending hi-res packets and frames from both queues."""
        packet_count = 0
        frame_count = 0
        try:
            while not self._cam_hires_packet_queue.empty():
                _ = self._cam_hires_packet_queue.get_nowait()
                packet_count += 1
        except Exception:
            pass
        try:
            while not self.card._cam_hires_frame_queue.empty():
                _ = self.card._cam_hires_frame_queue.get_nowait()
                frame_count += 1
        except Exception:
            pass
        if packet_count or frame_count:
            log_info(self.slot_number, "camera",
                     f"Cleared hi-res queues for {self.name}: {packet_count} packets, {frame_count} frames",
                     name=self.name)

    def drain_dai_queue(self, q) -> int:
        """Drain a DepthAI output queue completely without blocking. Returns packets discarded."""
        if q is None:
            return 0
        drained = 0
        while True:
            try:
                pkt = q.tryGet()
            except Exception:
                break
            if pkt is None:
                break
            drained += 1
        if drained > 0:
            log_info(self.slot_number, "camera", f"Drained {drained} packets from DAI queue for {self.name}", name=self.name)
        return drained

    # ==================== Streaming Operations ====================

    async def start_streaming(self) -> OperationResult:
        """Start card preview stream.
        Flow: guard checks -> connect -> start pipeline (hires OFF) -> start capture -> emit event.
        """
        try:
            # Guard: already streaming
            if self._is_streaming_active:
                log_info(self.slot_number, "camera", f"Streaming already active for {self.name}", name=self.name)
                return OperationResult(success=True, message="Streaming already active")

            # Guard: conflicting operations
            if self.card._is_recording_active or self.card._is_training_active or self.card._is_inference_active:
                log_warning(
                    self.slot_number, "camera",
                    f"Cannot start streaming - active: recording={self.card._is_recording_active}, "
                    f"training={self.card._is_training_active}, inference={self.card._is_inference_active}",
                    name=self.name,
                )
                return OperationResult(
                    success=False,
                    message="Cannot start streaming while recording/training/inference is active",
                )

            # Step 1: Connect device if needed
            if not self.is_device_connected:
                if not await self.connect_device():
                    return OperationResult(success=False, message="Device not connected")
            else:
                log_info(self.slot_number, "camera", f"Device already connected for {self.name}", name=self.name)

            # Step 2: Start pipeline (hires forwarding OFF - streaming only needs MJPEG preview)
            self._server_loop = asyncio.get_running_loop()
            if not self._is_pipeline_running:
                if not await self.start_pipeline():
                    return OperationResult(success=False, message="Failed to start pipeline")
            else:
                log_info(self.slot_number, "camera", f"Pipeline already running for {self.name}", name=self.name)

            # simulate dead queue to test recovery
            # self._simulate_queue_closed()

            # Verify queues are alive — recover if device disconnected silently
            if not self._verify_queue_health():
                log_warning(self.slot_number, "camera",
                            f"Queue dead before streaming for {self.name}, restarting runtime",
                            name=self.name)
                restart_result = await self.restart_runtime()
                if not restart_result.success:
                    return OperationResult(success=False, message=f"Runtime restart failed: {restart_result.message}")

            # Drain stale frames before starting capture
            # self.drain_dai_queue(self._cam_mjpeg_queue)
            # self.drain_dai_queue(self._cam_raw_queue)

            # Step 3: Start capture threads (MJPEG + Hi-res + Converter)
            if not self.start_capture():
                return OperationResult(success=False, message="Failed to start capture")
            await asyncio.sleep(0.2)

            self._is_streaming_active = True
            log_info(self.slot_number, "camera", f"Streaming started for {self.name}", name=self.name)
            emit_streaming_started(self)
            return OperationResult(success=True, message="Streaming started")

        except Exception as exc:
            log_error(self.slot_number, "camera", f"Failed to start streaming for {self.name}: {exc}", name=self.name)
            return OperationResult(success=False, message=f"Failed to start streaming: {exc}")

    async def stop_streaming(self) -> OperationResult:
        """Stop card preview stream.
        Streaming is exclusive — no other operations run during streaming.
        Flow: clear flag -> stop capture -> clean ALL queues -> emit event.
        """
        try:
            if not self._is_streaming_active:
                log_info(self.slot_number, "camera", f"Streaming already stopped for {self.name}", name=self.name)
                return OperationResult(success=True, message="Streaming already stopped")

            self._is_streaming_active = False

            # Stop all capture threads
            self.stop_capture()

            # Clean ALL queues to prevent memory leaks
            self.clear_hi_res_queues()
            # self.drain_dai_queue(self._cam_raw_queue)
            # self.drain_dai_queue(self._cam_mjpeg_queue)
            self._preview_latest_frame = None

            log_info(self.slot_number, "camera", f"Streaming stopped and all resources cleaned up for {self.name}", name=self.name)

            emit_streaming_stopped(self, "stopped")
            return OperationResult(success=True, message="Streaming stopped")
        except Exception as exc:
            log_error(self.slot_number, "camera", f"Failed to stop streaming for {self.name}: {exc}", name=self.name)
            emit_streaming_stopped(self, "failed")
            return OperationResult(success=False, message=f"Failed to stop streaming: {exc}")

    async def get_preview_frame(self) -> Optional[bytes]:
        """Get the latest preview frame as MJPEG bytes."""
        return self._preview_latest_frame

    async def start_model_streaming(self) -> bool:
        """Start modal/double-click live stream."""
        try:
            if not self.is_device_connected:
                log_error(self.slot_number, "camera", f"Cannot start model stream - device not connected for {self.name}", name=self.name)
                return False
            self._server_loop = asyncio.get_running_loop()
            queue_size = 3 if self.width >= 7000 else 6 if self.width >= 3840 else 12 if self.width >= 1920 else 16
            self._is_modal_active = True
            self._cam_modal_queue = asyncio.Queue(maxsize=queue_size)
            log_info(self.slot_number, "camera", f"Model stream started for {self.name} (queue={queue_size})", name=self.name)
            return True
        except Exception as exc:
            log_error(self.slot_number, "camera", f"Failed to start model stream for {self.name}: {exc}", name=self.name)
            return False

    async def stop_model_streaming(self) -> bool:
        """Stop modal/double-click live stream."""
        try:
            self._is_modal_active = False
            self._cam_modal_queue = None
            log_info(self.slot_number, "camera", f"Model stream stopped for {self.name}", name=self.name)
            return True
        except Exception as exc:
            log_error(self.slot_number, "camera", f"Failed to stop model stream for {self.name}: {exc}", name=self.name)
            return False

    async def get_model_stream_frame(self, timeout: float = 0.1) -> Optional[bytes]:
        """Get the next model stream frame as MJPEG bytes."""
        try:
            if self._cam_modal_queue is None:
                return None
            return await asyncio.wait_for(self._cam_modal_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    # ==================== Inference Operations ====================

    async def start_inference_capture(self) -> OperationResult:
        """Start hi-res capture for inference."""
        try:
            if self.card._is_inference_active:
                return OperationResult(success=True, message="Inference capture already running")

            if self.card._is_recording_active or self.card._is_training_active:
                return OperationResult(
                    success=False,
                    message="Cannot start inference while recording or training is active",
                )
            
            if not self.is_device_connected:
                if not await self.connect_device():
                    return OperationResult(success=False, message="Device not connected")

            

            if not self._is_pipeline_running:
                if not await self.start_pipeline():
                    return OperationResult(success=False, message="Failed to start pipeline")

            # Verify queues are alive — recover if device disconnected silently
            if not self._verify_queue_health():
                log_warning(
                    self.slot_number, "camera",
                    f"Queue dead before inference for {self.name}, restarting runtime",
                    name=self.name,
                )
                restart_result = await self.restart_runtime()
                if not restart_result.success:
                    return OperationResult(success=False, message=f"Runtime restart failed: {restart_result.message}")
                
            self.clear_hi_res_queues()
            
            if not self.start_capture():
                return OperationResult(success=False, message="Failed to start capture")
            await asyncio.sleep(0.2)

            
            
            self._set_hires_forwarding(True)

            self.card._is_inference_active = True
            self.card.cam_latest_inference_frame = None
            log_info(self.slot_number, "camera", f"Inference capture started for {self.name}", name=self.name)
            return OperationResult(success=True, message="Inference capture started")

        except Exception as exc:
            log_error(self.slot_number, "camera", f"Failed to start inference capture for {self.name}: {exc}", name=self.name)
            return OperationResult(success=False, message=f"Failed to start inference capture: {exc}")

    async def stop_inference_capture(self) -> OperationResult:
        """Stop hi-res capture for inference."""
        try:
            if not self.card._is_inference_active:
                return OperationResult(success=True, message="Inference capture already stopped")

            self.card._is_inference_active = False
            self._set_hires_forwarding(False)
            # self.drain_dai_queue(self._cam_raw_queue)
            self.card.cam_latest_inference_frame = None
            self.clear_hi_res_queues()

            other_activity = (
                self.card._is_recording_active
                or self.card._is_training_active
                or self._is_streaming_active
                
            )
            if not other_activity:
                self.stop_capture()

            log_info(self.slot_number, "camera", f"Inference capture stopped for {self.name}", name=self.name)
            return OperationResult(success=True, message="Inference capture stopped")

        except Exception as exc:
            log_error(self.slot_number, "camera", f"Failed to stop inference capture for {self.name}: {exc}", name=self.name)
            return OperationResult(success=False, message=f"Failed to stop inference capture: {exc}")

    # ==================== Recording Operations ====================

    def _basename_with_codec(self, basename_no_ext: Path) -> Path:
        codec = self._selected_codec()
        tag = {"MJPG": "MJPG", "FFV1": "FFV1", "rawvideo": "RAW"}.get(codec, "MJPG")
        if basename_no_ext.name.endswith(("_MJPG", "_FFV1", "_RAW")):
            return basename_no_ext
        return basename_no_ext.with_name(f"{basename_no_ext.name}_{tag}")

    def _selected_codec(self) -> str:
        try:
            val = getattr(APP_CONFIG, "recording_codec", "MJPG")
            if isinstance(val, str):
                v = val.strip()
                if v.upper() == "MJPG":
                    return "MJPG"
                if v.upper() == "FFV1":
                    return "FFV1"
                if v.lower() == "rawvideo":
                    return "rawvideo"
        except Exception:
            pass
        return "MJPG"

    def _open_record_writer(self, basename_no_ext: Path, fps: float, size: tuple[int, int]) -> tuple[_BaseWriter, Path]:
        codec = self._selected_codec()
        if codec == "FFV1":
            out_path = basename_no_ext.with_suffix(".mkv")
            writer = _PyAVFFV1Writer(out_path, fps, size)
            return writer, out_path
        elif codec == "rawvideo":
            out_path = basename_no_ext.with_suffix(".avi")
            writer = _PyAVRawWriter(out_path, fps, size)
            return writer, out_path
        else:
            out_path = basename_no_ext.with_suffix(".avi")
            writer = _OpenCVMJPGWriter(out_path, fps, size)
            return writer, out_path

    async def start_recording(self) -> OperationResult:
        """Start recording on this camera's card."""
        return await recording_writter.start_recording_writter(self)

    async def stop_recording(self) -> OperationResult:
        """Stop recording on this camera's card."""
        return await recording_writter.stop_recording_writter(self)

    def __repr__(self) -> str:
        return f"Camera(slot={self.slot_number}, name='{self.name}', ip='{self.ip_address}')"
