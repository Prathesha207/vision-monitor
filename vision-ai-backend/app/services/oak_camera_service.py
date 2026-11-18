import asyncio
import os
import threading
import time
from queue import Queue, Empty, Full
from typing import Any

import cv2
import depthai as dai
import numpy as np
import logging
from app.services.realtime_log_service import realtime_log_service

logger = logging.getLogger("oak-camera")

# Sentinel placed between videos in folder mode (not the final EOF None)
_VIDEO_BOUNDARY = object()



class OakCameraService:
    """Single OAK-D camera service for DHTX.

    Pipeline (2 DAI output nodes):
      Camera -> VideoEncoder (MJPEG) -> _mjpeg_dai_queue
      Camera -> raw NV12             -> _raw_dai_queue

    Capture threads (3):
      _mjpeg_thread   : drains MJPEG DAI queue -> pushes JPEG into asyncio stream_queue
      _hires_thread   : drains raw NV12 DAI queue -> puts NV12 packets into _hires_packet_queue
      _convert_thread : pulls NV12 packets -> getCvFrame() -> stores _latest_bgr

    Streaming:
      HTTP StreamingResponse reads from stream_queue (asyncio.Queue)
      MJPEG thread pushes via run_coroutine_threadsafe (drop-oldest on full)
    """

    def __init__(self):
        # ---- Device ----
        self.device: dai.Device | None = None
        self.is_connected: bool = False
        self._last_connection_error: str | None = None

        # ---- Pipeline ----
        self._pipeline: dai.Pipeline | None = None
        self._is_running: bool = False
        self._pipeline_lock = threading.Lock()
        self._lifecycle_lock = asyncio.Lock()
        self._capture_lock = threading.Lock()

        # ---- DAI queues ----
        self._mjpeg_dai_queue: Any = None
        self._raw_dai_queue: Any = None
        self._control_queue: Any = None

        # ---- Inter-thread queue (hires capture -> preview/inference converter) ----
        self._hires_packet_queue: Queue = Queue(maxsize=16)
        # Dedicated lossless recording queue so slow encoding/disk work never
        # adds latency to live preview/inference, and preview never drops recording frames.
        self._record_packet_queue: Queue = Queue()

        # ---- Offline frame queue (synchronized, every frame processed once) ----
        self._offline_frame_queue: Queue = Queue(maxsize=32) 
        self._offline_frame_index: int = 0
        self._offline_total_frames: int = 1

        # ---- Stream queue (MJPEG thread -> HTTP streaming endpoint) ----
        self._stream_queue: asyncio.Queue | None = None
        self._stream_subscribers: set[asyncio.Queue] = set()
        self._server_loop: asyncio.AbstractEventLoop | None = None
        self._is_streaming: bool = False

        # ---- Capture threads ----
        self._mjpeg_thread: threading.Thread | None = None
        self._hires_thread: threading.Thread | None = None
        self._convert_thread: threading.Thread | None = None
        self._record_thread: threading.Thread | None = None

        self._mjpeg_stop = threading.Event()
        self._hires_stop = threading.Event()
        self._convert_stop = threading.Event()
        self._record_stop = threading.Event()

        # ---- Latest BGR & JPEG frame caches (GIL-safe single ref assign) ----
        self._latest_bgr: np.ndarray | None = None
        self._latest_jpeg: bytes | None = None
        self._latest_bgr_seq: int = 0
        self._pipeline_width: int = 1920
        self._pipeline_height: int = 1080

        # ---- Camera settings ----
        self._active_camera_id: int | None = None
        self.control_mode: str = "auto"
        self.current_brightness: int = 0
        self.current_contrast: int = 50
        self.current_fps: float = 0.0
        self._configured_fps: int = 30
        self._ae_limit_us: int | None = None  # None = manual/steady mode
        self.current_exposure_us: int = 16000  # Default 16ms steady shutter
        self.current_gain: int = 400           # Default ISO 400 steady gain
        self.current_focus: int = 120          # Default lens position (0-255)
        self.auto_exposure_enabled: bool = False
        self.auto_focus_enabled: bool = False

        # ---- Recording ----
        self._active_recording: Any = None  # RecordingSession instance when active
        self._completed_recordings: dict[str, dict] = {}
        self._recording_stop_lock = threading.Lock()

        # ---- Inference ----
        self._inference_thread: threading.Thread | None = None
        self._inference_watchdog_thread: threading.Thread | None = None
        self._inference_stop = threading.Event()
        self._inference_result_queue: asyncio.Queue | None = None
        self._inference_loop: asyncio.AbstractEventLoop | None = None
        self._inference_session_id: str | None = None

        # ---- Offline inference ----
        self._offline_thread: threading.Thread | None = None
        self._offline_stop = threading.Event()
        self._inference_offline: bool = False

        # ---- Offline multi-video tracking ----
        self._total_videos: int = 1
        self._current_video_idx: int = 0
        self._current_video_name: str = ""


        # GPU sampler — runs independently, never blocks inference
        self._gpu_usage: float = -1.0
        self._gpu_sampler_thread: threading.Thread | None = None
        self._gpu_sampler_stop = threading.Event()

    # ==================== Device ====================

    async def connect(self, ip: str) -> bool:
        loop = asyncio.get_running_loop()
        self._last_connection_error = None
        for attempt in range(2):
            try:
                logger.info(f"[DEVICE] Connecting to {ip} (attempt {attempt + 1}/2)")
                device_info = self._resolve_device_info(ip)
                logger.info(f"[DEVICE] Resolved connection target: {device_info}")
                self.device = await loop.run_in_executor(None, dai.Device, device_info)
                self.device.setLogLevel(dai.LogLevel.WARN)
                self.device.setLogOutputLevel(dai.LogLevel.WARN)
                self.is_connected = True

                try:
                    usb_speed = self.device.getUsbSpeed()
                    logger.info(f"[DEVICE] Connected link speed: {usb_speed}")
                    if usb_speed == dai.UsbSpeed.HIGH:
                        logger.warning(
                            "[DEVICE] ⚠️ Camera connected via USB 2.0 (HIGH speed: ~35-40 MB/s). "
                            "Uncompressed 1080p stream exceeds USB 2.0 bandwidth and will cause X_LINK_ERROR. "
                            "Please connect camera to a blue USB 3.0 port using a USB 3.0 SuperSpeed cable."
                        )
                        realtime_log_service.add_log(
                            "camera",
                            "WARN",
                            "OAK connected at USB 2.0 speed! Use a USB 3.0 port and cable for stability.",
                            "warning"
                        )
                except Exception as speed_err:
                    logger.debug(f"[DEVICE] Could not determine USB speed: {speed_err}")

                logger.info(f"[DEVICE] Connected to {ip}")
                realtime_log_service.add_log(
                    "camera",
                    "CAMERA",
                    f"Camera connected ({ip})",
                    "success"
                )
                self._last_connection_error = None
                return True
            except Exception as e:
                err_str = str(e)
                if "already used" in err_str.lower() or "in use" in err_str.lower():
                    clean_msg = "Device is already used by another application/process. Make sure to close all applications/processes using the device before starting a new one."
                    self._last_connection_error = clean_msg
                    if attempt == 0:
                        logger.warning(f"[DEVICE] Device is reported in use, waiting 1.5s to retry: {clean_msg}")
                        await asyncio.sleep(1.5)
                        continue
                    else:
                        logger.warning(f"[DEVICE] Connection failed: {clean_msg}")
                        realtime_log_service.add_log(
                            "camera",
                            "CAMERA",
                            clean_msg,
                            "error"
                        )
                        self.is_connected = False
                        return False

                logger.error(f"[DEVICE] Connection failed: {e}")
                realtime_log_service.add_log(
                    "camera",
                    "CAMERA",
                    f"DEVICE Connection failed ({ip}): {e}",
                    "error"
                )
                self.is_connected = False
                self._last_connection_error = err_str
                return False
        self._last_connection_error = self._last_connection_error or "Failed after retries"
        return False

    @staticmethod
    def _resolve_device_info(identifier: str):
        """Resolve a database value to a current USB or network DeviceInfo."""
        value = (identifier or "").strip()
        available = dai.Device.getAllAvailableDevices()

        if not available:
            raise RuntimeError("No OAK device found over USB or network")

        # 1. Match against MXID, deviceId, name, or description
        if value and value.lower() not in {"", "usb", "auto", "127.0.0.1", "localhost"}:
            for device_info in available:
                device_id = str(getattr(device_info, "deviceId", "") or "")
                mxid = str(getattr(device_info, "mxid", "") or "")
                name = str(getattr(device_info, "name", "") or "")
                desc_name = ""
                try:
                    desc_name = str(getattr(device_info.getXLinkDeviceDesc(), "name", ""))
                except Exception:
                    pass
                if value in {device_id, mxid, name, desc_name}:
                    return device_info
            logger.warning(
                f"[DEVICE] Identifier '{value}' not matched in visible devices. Trying available devices..."
            )

        # 2. Prefer USB devices
        for device_info in available:
            name = str(getattr(device_info, "name", "")).lower()
            state = str(getattr(device_info, "state", "")).lower()
            if "usb" in name or "usb" in state or "tcp" not in state:
                return device_info

        # 3. Fallback to first discovered device
        return available[0]

    async def disconnect(self) -> None:
        try:
            if self.device is not None:
                await asyncio.to_thread(self.device.close)
                logger.info("[DEVICE] Disconnected")
                realtime_log_service.add_log(
                    "camera",
                    "CAMERA",
                    "Camera disconnected",
                    "success"
                )
        except Exception as e:
            logger.error(f"[DEVICE] Disconnect error: {e}")
            realtime_log_service.add_log(
                "camera",
                "CAMERA",
                "DEVICE Disconnect error",
                "error"
            )
        finally:
            self.device = None
            self.is_connected = False

    # ==================== Pipeline ====================

    def _build_pipeline(self, pipeline: dai.Pipeline, config) -> bool:
        try:
            logger.info("[PIPELINE] Building...")

            # --- Resolution / FPS (parsed first — needed by AE limit below) ---
            resolution = config.resolution or "1920x1080"
            try:
                sep = "x" if "x" in resolution else "*"
                width, height = map(int, resolution.split(sep))
            except Exception:
                logger.warning("[PIPELINE] Bad resolution — fallback 1920x1080")
                width, height = 1920, 1080
            self._pipeline_width = width
            self._pipeline_height = height
            fps = int(config.fps or 30)
            self._configured_fps = fps
            logger.info(f"[PIPELINE] {width}x{height} @ {fps}fps")

            # --- Control mode ---
            self._active_camera_id = getattr(config, "id", None)
            control_mode = (config.control_mode or "auto").lower()
            self.control_mode = control_mode

            cam: Any = None
            is_color_camera = False

            # Try DepthAI unified Camera node first
            try:
                cam = pipeline.create(dai.node.Camera).build(dai.CameraBoardSocket.CAM_A)
                init_ctrl = cam.initialControl
            except Exception as cam_err:
                err_str = str(cam_err)
                logger.warning(f"[PIPELINE] dai.node.Camera build failed: {err_str}")
                if "OAK4 has not been setup yet" in err_str or "setup.luxonis.com" in err_str:
                    logger.error(
                        "[PIPELINE] Connected camera is an OAK4 device that requires setup! "
                        "Complete setup via https://setup.luxonis.com/ or run 'oakctl device setup apply'."
                    )
                    realtime_log_service.add_log(
                        "camera",
                        "CAMERA",
                        "OAK4 camera requires setup: visit https://setup.luxonis.com or run oakctl",
                        "error"
                    )

                # Attempt ColorCamera fallback (for RVC2 / classic OAK-D / OAK-1 devices)
                try:
                    logger.info("[PIPELINE] Attempting dai.node.ColorCamera fallback...")
                    cam = pipeline.create(dai.node.ColorCamera)
                    cam.setBoardSocket(dai.CameraBoardSocket.CAM_A)
                    if hasattr(dai.ColorCameraProperties, "SensorResolution"):
                        cam.setResolution(dai.ColorCameraProperties.SensorResolution.THE_1080_P)
                    cam.setFps(fps)
                    cam.setInterleaved(False)
                    cam.setVideoSize(width, height)
                    init_ctrl = cam.initialControl
                    is_color_camera = True
                    logger.info("[PIPELINE] ColorCamera node created successfully as fallback")
                except Exception as color_err:
                    logger.error(f"[PIPELINE] ColorCamera fallback also failed: {color_err}")
                    raise cam_err

            if control_mode == "auto":
                # For standard inspection stream: Do NOT run CONTINUOUS_VIDEO autofocus (causes focus hunting on moving objects).
                # Keep autofocus OFF and lock to calibrated standard focus (120) and standard exposure (16ms, 400 ISO)
                # so the image remains perfectly steady and never hunts or flickers when objects move.
                self._ae_limit_us = None
                self.current_exposure_us = 16000
                self.current_gain = 400
                self.current_focus = 120
                self.current_brightness = 0
                self.current_contrast = 50
                self.auto_exposure_enabled = False
                self.auto_focus_enabled = False
                init_ctrl.setAutoFocusMode(dai.CameraControl.AutoFocusMode.OFF)
                init_ctrl.setManualFocus(self.current_focus)
                init_ctrl.setManualExposure(self.current_exposure_us, self.current_gain)
                init_ctrl.setBrightness(0)
                init_ctrl.setContrast(0)
                logger.info("[PIPELINE] Auto mode: calibrated steady stream (Focus=120, Exp=16ms, Gain=400 ISO, continuous AF hunting disabled)")
            else:
                self._ae_limit_us = None
                raw_exp = config.exposure if config.exposure is not None else 16
                raw_gain = config.gain if config.gain is not None else 400
                raw_focus = config.focus if config.focus is not None else 120
                raw_brightness = config.brightness if config.brightness is not None else 0
                raw_contrast = config.contrast if config.contrast is not None else 50
                try:
                    exposure_us = self._normalize_exposure_us(raw_exp)
                    gain = max(100, min(1600, int(round(float(raw_gain)))))
                    focus = max(0, min(255, int(round(float(raw_focus)))))
                    brightness = max(-50, min(50, int(round(float(raw_brightness)))))
                    contrast = max(0, min(100, int(round(float(raw_contrast)))))
                except Exception:
                    logger.warning("[PIPELINE] Invalid manual values — using calibrated defaults")
                    exposure_us, gain, focus, brightness, contrast = 16000, 400, 120, 0, 50

                self.current_exposure_us = exposure_us
                self.current_gain = gain
                self.current_focus = focus
                self.current_brightness = brightness
                self.current_contrast = contrast
                self.auto_exposure_enabled = False
                self.auto_focus_enabled = False

                init_ctrl.setAutoFocusMode(dai.CameraControl.AutoFocusMode.OFF)
                init_ctrl.setManualExposure(self.current_exposure_us, self.current_gain)
                init_ctrl.setManualFocus(self.current_focus)
                hw_b = max(-10, min(10, round(self.current_brightness / 5.0)))
                hw_c = max(-10, min(10, round((self.current_contrast - 50) / 5.0)))
                init_ctrl.setBrightness(hw_b)
                init_ctrl.setContrast(hw_c)
                logger.info(
                    f"[PIPELINE] Manual (from DB): exp={self.current_exposure_us}µs ({self.current_exposure_us / 1000:.1f}ms), "
                    f"gain={self.current_gain}, focus={self.current_focus}, "
                    f"brightness={self.current_brightness}, contrast={self.current_contrast}"
                )

            is_usb2 = False
            if self.device is not None:
                try:
                    if self.device.getUsbSpeed() == dai.UsbSpeed.HIGH:
                        is_usb2 = True
                        logger.warning(
                            "[PIPELINE] ⚠️ Detected USB 2.0 connection. "
                            "Uncompressed 1080p @ 30fps requires ~93 MB/s which exceeds USB 2.0 bandwidth (~35 MB/s). "
                            "Throttling raw frame output rate to 10 FPS to prevent XLink communication buffer overflows. "
                            "Please connect camera to a blue USB 3.0 port with a SuperSpeed cable for full 30 FPS."
                        )
                except Exception:
                    pass

            raw_fps = min(fps, 10) if is_usb2 else fps

            encoder = pipeline.create(dai.node.VideoEncoder)
            encoder.setDefaultProfilePreset(fps, dai.VideoEncoderProperties.Profile.MJPEG)
            encoder.setQuality(95)

            if not is_color_camera:
                # --- Node 1: MJPEG encoder → stream preview ---
                mjpeg_out = cam.requestOutput(
                    (width, height), type=dai.ImgFrame.Type.NV12, fps=fps
                )
                mjpeg_out.link(encoder.input)
                self._mjpeg_dai_queue = encoder.bitstream.createOutputQueue(maxSize=4, blocking=False)
                logger.info("[PIPELINE] Node 1: MJPEG encoder ready")

                # --- Node 2: Raw NV12 → inference / recording ---
                # maxSize=30 (~1s buffer at 30fps) prevents DepthAI from silently dropping
                # raw frames during transient Python/GIL/OS pauses.
                raw_out = cam.requestOutput(
                    (width, height), type=dai.ImgFrame.Type.NV12, fps=raw_fps
                )
                self._raw_dai_queue = raw_out.createOutputQueue(maxSize=30, blocking=False)
                logger.info(f"[PIPELINE] Node 2: Raw NV12 ready (fps={raw_fps}, queue=30)")
            else:
                cam.video.link(encoder.input)
                self._mjpeg_dai_queue = encoder.bitstream.createOutputQueue(maxSize=4, blocking=False)
                self._raw_dai_queue = cam.video.createOutputQueue(maxSize=30, blocking=False)
                logger.info("[PIPELINE] ColorCamera outputs wired successfully")

            # --- Camera control input ---
            self._control_queue = cam.inputControl.createInputQueue(maxSize=4, blocking=False)

            logger.info("[PIPELINE] Build success")
            return True

        except Exception as e:
            logger.error(f"[PIPELINE] Build failed: {e}", exc_info=True)
            return False

    def _cleanup_pipeline(self) -> None:
        with self._pipeline_lock:
            if self._pipeline is None:
                return
            logger.info("[PIPELINE] Stopping...")
            try:
                self._pipeline.stop()
                self._pipeline.wait()
            except Exception as e:
                logger.error(f"[PIPELINE] Stop error: {e}")
            finally:
                self._pipeline = None
                self._is_running = False
                self._mjpeg_dai_queue = None
                self._raw_dai_queue = None
                self._control_queue = None
                self._latest_jpeg = None
                self._latest_bgr = None
            logger.info("[PIPELINE] Cleaned up")

    def _on_device_lost(self, reason: str = "Device disconnected") -> None:
        """Central teardown when DepthAI device disconnects, XLink crashes, or queues close."""
        with self._pipeline_lock:
            if not self._is_running and not self.is_connected:
                return
            logger.warning(f"[DEVICE] Teardown triggered: {reason}")
            self._is_running = False
            self.is_connected = False
            self._is_streaming = False

            # Signal capture & inference loops to stop
            self._mjpeg_stop.set()
            self._hires_stop.set()
            self._convert_stop.set()
            self._record_stop.set()
            self._inference_stop.set()

            # Invalidate queues so callers know device is unavailable
            self._mjpeg_dai_queue = None
            self._raw_dai_queue = None
            self._control_queue = None
            self._pipeline = None
            self._latest_jpeg = None
            self._latest_bgr = None

            # Wake up any streaming client queues with None sentinel so generators exit cleanly (thread-safe)
            loop = self._server_loop
            for q in list(self._stream_subscribers):
                if loop is not None and not loop.is_closed():
                    try:
                        loop.call_soon_threadsafe(q.put_nowait, None)
                    except Exception:
                        pass
                else:
                    try:
                        q.put_nowait(None)
                    except Exception:
                        pass
            self._stream_subscribers.clear()
            self._stream_queue = None

            # Close device handle safely if not already closed
            if self.device is not None:
                try:
                    self.device.close()
                except Exception:
                    pass
                self.device = None

            realtime_log_service.add_log(
                "camera",
                "CAMERA",
                f"Camera device disconnected: {reason}",
                "error"
            )

    

    # ── New method: background GPU sampler ───────────────────────────────────────
    def _start_gpu_sampler(self) -> None:
        """Samples GPU every 2s in background — never blocks inference."""
        def _loop():
            import subprocess
            while not self._gpu_sampler_stop.is_set():
                try:
                    out = subprocess.check_output(
                        ["nvidia-smi", "--query-gpu=utilization.gpu",
                        "--format=csv,noheader,nounits"],
                        timeout=1,
                    )
                    self._gpu_usage = float(out.decode().strip().split("\n")[0])
                except Exception:
                    self._gpu_usage = -1.0
                self._gpu_sampler_stop.wait(timeout=2.0)   # sleep 2s between polls

        self._gpu_sampler_stop.clear()
        self._gpu_sampler_thread = threading.Thread(
            target=_loop, name="gpu-sampler", daemon=True
        )
        self._gpu_sampler_thread.start()

    def _stop_gpu_sampler(self) -> None:
        self._gpu_sampler_stop.set()
        if self._gpu_sampler_thread and self._gpu_sampler_thread.is_alive():
            self._gpu_sampler_thread.join(timeout=3.0)
        self._gpu_sampler_thread = None


    # ==================== Capture Threads ====================

    def _start_capture_threads(self) -> bool:
        with self._capture_lock:
            if not self._is_running or not self.is_connected or self._mjpeg_dai_queue is None:
                logger.warning("[CAPTURE] Cannot start — pipeline or device queue not running")
                return False

            mjpeg_alive = self._mjpeg_thread and self._mjpeg_thread.is_alive()
            hires_alive = self._hires_thread and self._hires_thread.is_alive()
            convert_alive = self._convert_thread and self._convert_thread.is_alive()
            record_alive = self._record_thread and self._record_thread.is_alive()

            if not mjpeg_alive:
                self._mjpeg_stop.clear()
                self._mjpeg_thread = threading.Thread(
                    target=self._mjpeg_loop, name="oak-mjpeg", daemon=True
                )
                self._mjpeg_thread.start()
                logger.info("[CAPTURE] MJPEG thread started")

            if not hires_alive:
                self._hires_stop.clear()
                self._hires_thread = threading.Thread(
                    target=self._hires_loop, name="oak-hires", daemon=True
                )
                self._hires_thread.start()
                logger.info("[CAPTURE] Hi-res thread started")

            if not convert_alive:
                self._convert_stop.clear()
                self._convert_thread = threading.Thread(
                    target=self._convert_loop, name="oak-convert", daemon=True
                )
                self._convert_thread.start()
                logger.info("[CAPTURE] Converter thread started")

            if not record_alive:
                self._record_stop.clear()
                self._record_thread = threading.Thread(
                    target=self._record_convert_loop, name="oak-record-convert", daemon=True
                )
                self._record_thread.start()
                logger.info("[CAPTURE] Recording converter thread started")

            return True

    def _stop_capture_threads(self) -> None:
        with self._capture_lock:
            logger.info("[CAPTURE] Stopping threads...")

            # Signal all
            self._mjpeg_stop.set()
            self._hires_stop.set()
            self._convert_stop.set()
            self._record_stop.set()

            # Join consumers first so producers never block on full queues
            if self._convert_thread and self._convert_thread.is_alive():
                self._convert_thread.join(timeout=5.0)
            self._convert_thread = None

            if self._record_thread and self._record_thread.is_alive():
                self._record_thread.join(timeout=5.0)
            self._record_thread = None

            if self._mjpeg_thread and self._mjpeg_thread.is_alive():
                self._mjpeg_thread.join(timeout=3.0)
            self._mjpeg_thread = None

            if self._hires_thread and self._hires_thread.is_alive():
                self._hires_thread.join(timeout=3.0)
            self._hires_thread = None

            self._latest_bgr = None
            self._latest_jpeg = None
            self._drain_hires_packet_queue()
            while not self._record_packet_queue.empty():
                try:
                    self._record_packet_queue.get_nowait()
                except Empty:
                    break

            logger.info("[CAPTURE] Threads stopped")

    def _drain_hires_packet_queue(self) -> None:
        drained = 0
        while not self._hires_packet_queue.empty():
            try:
                self._hires_packet_queue.get_nowait()
                drained += 1
            except Empty:
                break
        if drained:
            logger.info(f"[CAPTURE] Drained {drained} stale hi-res packets")

    # ---- Thread 1: MJPEG consumer ----

    def _mjpeg_loop(self) -> None:
        """Drains MJPEG DAI queue and pushes JPEG bytes into the asyncio stream_queue."""
        logger.info("[MJPEG] Thread running")
        frames = 0
        frames_pushed = 0
        frames_dropped = 0
        last_log = time.time()
        last_frame_ts = time.time()

        try:
            while not self._mjpeg_stop.is_set():
                if self._mjpeg_dai_queue is None:
                    time.sleep(0.001)
                    continue

                pkt = self._mjpeg_dai_queue.tryGet()
                if pkt is None:
                    # Watchdog: only trigger if actively streaming to clients and stalled for 15.0s
                    has_subscribers = bool(self._stream_subscribers) or (self._stream_queue is not None)
                    if (
                        self._is_running
                        and self.is_connected
                        and self._is_streaming
                        and has_subscribers
                        and time.time() - last_frame_ts > 15.0
                    ):
                        logger.error("[MJPEG] Watchdog: No MJPEG frames from camera for 15.0s during active streaming — device connection frozen")
                        self._on_device_lost("No MJPEG frames from camera for 15.0s")
                        break
                    time.sleep(0.001)
                    continue

                last_frame_ts = time.time()
                try:
                    jpeg = pkt.getData().tobytes()
                except Exception as e:
                    logger.warning(f"[MJPEG] Frame extraction failed: {e}")
                    continue
                self._latest_jpeg = jpeg
                frames += 1

                # Push to all subscribed client queues thread-safely
                # Keep stream/event loop errors completely isolated from camera hardware errors!
                has_subscribers = bool(self._stream_subscribers) or (self._stream_queue is not None)
                loop = self._server_loop
                if has_subscribers and loop is not None and not loop.is_closed():
                    try:
                        loop.call_soon_threadsafe(self._push_sync, jpeg)
                        frames_pushed += 1
                    except RuntimeError:
                        # Event loop closed / server reload — do NOT treat as camera device failure!
                        pass
                    except Exception as push_err:
                        logger.debug(f"[MJPEG] Stream push error: {push_err}")
                else:
                    frames_dropped += 1

                now = time.time()
                if now - last_log >= 5.0:
                    elapsed = now - last_log
                    sub_count = len(self._stream_subscribers)
                    fps_val = frames / max(elapsed, 0.001)
                    bitrate_mbps = (frames_pushed * len(jpeg) * 8) / (max(elapsed, 0.001) * 1_000_000)
                    logger.info(
                        f"[MJPEG] FPS: {fps_val:.1f} | "
                        f"bitrate: {bitrate_mbps:.1f} Mbps | "
                        f"pushed: {frames_pushed} | "
                        f"dropped (no client): {frames_dropped} | "
                        f"subscribers: {sub_count}"
                    )
                    realtime_log_service.add_log(
                        "stream",
                        "NETWORK",
                        f"Live Stream: {fps_val:.1f} FPS, {bitrate_mbps:.1f} Mbps",
                        "info"
                    )
                    frames = 0
                    frames_pushed = 0
                    frames_dropped = 0
                    last_log = now

        except Exception as e:
            err_msg = str(e)
            if "closed" in err_msg.lower() or "xlink" in err_msg.lower():
                logger.warning(f"[MJPEG] Device connection lost: {err_msg}")
            else:
                logger.error(f"[MJPEG] Thread error: {e}", exc_info=True)
            self._on_device_lost(f"MJPEG queue closed: {err_msg}")
            realtime_log_service.add_log(
                "stream",
                "NETWORK",
                "MJPEG Thread disconnected",
                "error"
            )
        finally:
            logger.info("[MJPEG] Thread ended")

    # ---- Thread 2: Hi-res capture ----

    @staticmethod
    def _extract_nv12_frame(pkt) -> tuple[np.ndarray, np.ndarray] | None:
        """Unpack NV12 Y and UV planes safely, honoring DepthAI stride/plane alignment."""
        try:
            if hasattr(pkt, "getType") and pkt.getType() != dai.ImgFrame.Type.NV12:
                return None
            w = int(pkt.getWidth())
            h = int(pkt.getHeight())
        except Exception:
            return None

        if w <= 0 or h <= 0 or (w & 1) or (h & 1):
            return None

        data = np.asarray(pkt.getData(), dtype=np.uint8).ravel()
        uv_h = h // 2

        stride = w
        plane_h = h
        uv_offset = 0
        try:
            s = int(pkt.getStride())
            if s >= w:
                stride = s
        except Exception:
            pass
        try:
            ph = int(pkt.getPlaneHeight())
            if ph >= h:
                plane_h = ph
        except Exception:
            pass
        try:
            uv_offset = int(pkt.getPlaneStride(0))
        except Exception:
            uv_offset = 0

        if uv_offset >= h * stride and uv_offset + uv_h * stride <= data.size:
            y_plane = data[:plane_h * stride].reshape(plane_h, stride)[:h, :w].copy()
            uv_plane = data[uv_offset:uv_offset + uv_h * stride].reshape(uv_h, stride)[:, :w].copy()
            return y_plane, uv_plane

        total_rows = h + uv_h
        if data.size < total_rows * w:
            return None

        eff_stride = data.size // total_rows if (data.size % total_rows == 0) else w
        if eff_stride < w or total_rows * eff_stride > data.size:
            return None

        buf2d = data[:total_rows * eff_stride].reshape(total_rows, eff_stride)
        return buf2d[:h, :w].copy(), buf2d[h:total_rows, :w].copy()

    def _feed_lossless_recording(self, rec, pkt, cam_ts_sec: float | None, seq_num: int | None) -> bool:
        """Feed raw NV12 planes directly into FFV1 recording. Returns False to fall back to BGR."""
        extracted = self._extract_nv12_frame(pkt)
        if extracted is None:
            return False
        y_plane, uv_plane = extracted
        if y_plane.shape != (rec.height, rec.width):
            return False
        rec.add_nv12_frame(y_plane, uv_plane, cam_ts_sec=cam_ts_sec, seq_num=seq_num)
        return True

    def _hires_loop(self) -> None:
        """Drains raw NV12 DAI queue.
        - Routes EVERY captured packet to _record_packet_queue while recording is active (zero drops).
        - Keeps only the newest packet in _hires_packet_queue for low-latency preview/inference.
        """
        logger.info("[HIRES] Thread running")

        try:
            while not self._hires_stop.is_set():
                if self._raw_dai_queue is None:
                    time.sleep(0.001)
                    continue

                pkt = self._raw_dai_queue.tryGet()
                if pkt is None:
                    time.sleep(0.002)
                    continue

                # Preserve every raw packet for active recording on its dedicated worker
                rec = self._active_recording
                if rec is not None and getattr(rec, "is_running", False):
                    try:
                        rec._capture_packets_pending = getattr(rec, "_capture_packets_pending", 0) + 1
                        self._record_packet_queue.put_nowait((rec, pkt))
                    except Exception:
                        rec._capture_packets_pending = max(0, getattr(rec, "_capture_packets_pending", 1) - 1)
                        logger.exception("[RECORD] Could not queue captured camera packet")

                # Preview / inference queue always keeps only the newest packet for minimal latency
                while not self._hires_packet_queue.empty():
                    try:
                        self._hires_packet_queue.get_nowait()
                    except Empty:
                        break
                try:
                    self._hires_packet_queue.put_nowait(pkt)
                except Full:
                    pass

        except Exception as e:
            err_msg = str(e)
            if "closed" in err_msg.lower() or "xlink" in err_msg.lower():
                logger.warning(f"[HIRES] Device connection lost: {err_msg}")
            else:
                logger.error(f"[HIRES] Thread error: {e}", exc_info=True)
            self._on_device_lost(f"Hi-res queue closed: {err_msg}")
        finally:
            logger.info("[HIRES] Thread ended")

    # ---- Thread 3: Converter (preview / inference — always newest frame) ----

    def _convert_loop(self) -> None:
        """Pulls the newest NV12 packet, converts to BGR (getCvFrame), stores latest for inference/snapshots."""
        logger.info("[CONVERT] Thread running")
        frames = 0
        last_log = time.time()
        _first_frame_logged = False

        try:
            while not self._convert_stop.is_set() or not self._hires_packet_queue.empty():
                # Drain to the newest available packet so preview/inference never lags behind
                pkt = None
                while not self._hires_packet_queue.empty():
                    try:
                        pkt = self._hires_packet_queue.get_nowait()
                    except Empty:
                        break
                if pkt is None:
                    try:
                        pkt = self._hires_packet_queue.get(timeout=0.05)
                    except Empty:
                        continue

                try:
                    raw_bgr = pkt.getCvFrame()
                except Exception as e:
                    logger.warning(f"[CONVERT] getCvFrame failed: {e}")
                    continue

                if not _first_frame_logged:
                    logger.info(f"[CONVERT] First frame received — shape={raw_bgr.shape} | ae_limit_us={self._ae_limit_us} | configured_fps={self._configured_fps}")
                    _first_frame_logged = True

                # Apply software adjustments ONLY when explicitly non-neutral and in manual mode.
                # Default contrast (50 or 0) and brightness 0 is neutral.
                is_neutral = (self.current_brightness == 0) and (self.current_contrast in (0, 50))
                if not is_neutral and self.control_mode == "manual":
                    display_bgr = self._apply_adjustments(raw_bgr)
                else:
                    display_bgr = raw_bgr

                self._latest_bgr = display_bgr  # GIL-safe single reference assignment
                self._latest_bgr_seq += 1

                frames += 1
                now = time.time()
                if now - last_log >= 2.0:
                    elapsed = now - last_log
                    self.current_fps = frames / elapsed
                    expected = self._configured_fps
                    fps_status = "OK" if self.current_fps >= expected * 0.8 else f"LOW (expected ~{expected})"
                    logger.info(
                        f"[CONVERT] FPS: {self.current_fps:.1f} [{fps_status}] | "
                        f"recording: {self._active_recording is not None} | "
                        f"ae_limit_us: {self._ae_limit_us}"
                    )
                    frames = 0
                    last_log = now

        except Exception as e:
            logger.error(f"[CONVERT] Thread error: {e}", exc_info=True)
        finally:
            logger.info("[CONVERT] Thread ended")

    # ---- Thread 4: Dedicated Recording Converter (lossless, isolated from preview) ----

    def _record_convert_loop(self) -> None:
        """Converts queued recording packets independently without blocking live preview/inference."""
        logger.info("[RECORD-CONVERT] Thread running")
        try:
            while not self._record_stop.is_set() or not self._record_packet_queue.empty():
                try:
                    rec, pkt = self._record_packet_queue.get(timeout=0.05)
                except Empty:
                    continue

                try:
                    if not getattr(rec, "is_running", False):
                        continue

                    cam_ts_sec = None
                    seq_num = None
                    try:
                        ts = pkt.getTimestamp()
                        cam_ts_sec = ts.total_seconds() if hasattr(ts, "total_seconds") else float(ts)
                    except Exception:
                        pass
                    try:
                        seq_num = int(pkt.getSequenceNum())
                    except Exception:
                        pass

                    is_neutral = (self.current_brightness == 0) and (self.current_contrast in (0, 50))
                    # Fast path: feed raw sensor NV12 planes directly into FFV1 (no BGR conversion)
                    if getattr(rec, "is_raw_nv12", False) and (is_neutral or self.control_mode != "manual"):
                        if self._feed_lossless_recording(rec, pkt, cam_ts_sec=cam_ts_sec, seq_num=seq_num):
                            continue

                    frame = pkt.getCvFrame()
                    if not is_neutral and self.control_mode == "manual":
                        frame = self._apply_adjustments(frame)

                    rec.add_frame(frame, cam_ts_sec=cam_ts_sec, seq_num=seq_num, copy_frame=False)
                except Exception as e:
                    logger.warning(f"[RECORD-CONVERT] Packet conversion failed: {e}")
                finally:
                    rec._capture_packets_pending = max(0, getattr(rec, "_capture_packets_pending", 1) - 1)
        except Exception as e:
            logger.error(f"[RECORD-CONVERT] Thread error: {e}", exc_info=True)
        finally:
            logger.info("[RECORD-CONVERT] Thread ended")

    # ==================== Stream Queue ====================

    def _push_sync(self, jpeg: bytes) -> None:
        """Runs on the asyncio event loop thread — drop-oldest push into all client queues.
        Invoked via loop.call_soon_threadsafe so no Futures/Tasks are created.
        """
        for q in list(self._stream_subscribers):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                q.put_nowait(jpeg)
            except asyncio.QueueFull:
                pass
        if self._stream_queue is not None and self._stream_queue not in self._stream_subscribers:
            if self._stream_queue.full():
                try:
                    self._stream_queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            try:
                self._stream_queue.put_nowait(jpeg)
            except asyncio.QueueFull:
                pass

    async def _async_stream_push(self, jpeg: bytes) -> None:
        """Async fallback for pushing into client queues."""
        self._push_sync(jpeg)

    def subscribe_stream(self) -> asyncio.Queue:
        try:
            self._server_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        q: asyncio.Queue = asyncio.Queue(maxsize=2)
        self._stream_subscribers.add(q)
        self._stream_queue = q
        logger.info(f"[STREAM] Client subscribed — active subscribers: {len(self._stream_subscribers)}")
        return q

    def unsubscribe_stream(self, q: asyncio.Queue | None = None) -> None:
        if q is not None:
            self._stream_subscribers.discard(q)
            while not q.empty():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    break
        else:
            for sub in list(self._stream_subscribers):
                while not sub.empty():
                    try:
                        sub.get_nowait()
                    except asyncio.QueueEmpty:
                        break
            self._stream_subscribers.clear()

        # Bugfix: Point _stream_queue to an existing active subscriber, not a dead queue!
        if self._stream_subscribers:
            self._stream_queue = next(iter(self._stream_subscribers))
        else:
            self._stream_queue = None
        logger.info(f"[STREAM] Client unsubscribed — remaining subscribers: {len(self._stream_subscribers)}")

    def _open_stream_queue(self) -> asyncio.Queue:
        return self.subscribe_stream()

    def _close_stream_queue(self, q: asyncio.Queue | None = None) -> None:
        self.unsubscribe_stream(q)

    async def get_stream_frame(self, timeout: float = 1.0) -> bytes | None:
        if self._latest_jpeg is not None:
            return self._latest_jpeg
        if self._stream_queue is not None:
            try:
                return await asyncio.wait_for(self._stream_queue.get(), timeout=timeout)
            except asyncio.TimeoutError:
                pass
        if self._latest_bgr is not None:
            try:
                frame_copy = self._latest_bgr.copy()
                loop = asyncio.get_running_loop()
                def _encode():
                    ret, buf = cv2.imencode(".jpg", frame_copy, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    return buf.tobytes() if ret else None
                return await loop.run_in_executor(None, _encode)
            except Exception:
                pass
        return None

    # ==================== Frame Access ====================

    def get_bgr_frame(self) -> np.ndarray | None:
        """Latest BGR frame for inference / recording."""
        frame = self._latest_bgr
        return frame.copy() if frame is not None else None

    # ==================== Image Adjustments ====================

    def _apply_adjustments(self, frame: np.ndarray) -> np.ndarray:
        c = self.current_contrast
        b = self.current_brightness

        # If neutral, return frame directly without copying or modifying
        if (c == 0 or c == 50) and b == 0:
            return frame

        # Contrast: 50 is neutral (1.0x). 0..100 maps to 0.0x..2.0x
        alpha = (float(c) / 50.0) if c > 0 else 1.0
        beta = float(b)

        if abs(alpha - 1.0) < 0.01 and abs(beta) < 0.01:
            return frame

        return cv2.convertScaleAbs(frame, alpha=alpha, beta=beta)

    # ==================== Camera Controls ====================

    @property
    def is_running(self) -> bool:
        return self._is_running

    def is_recording_active(self) -> bool:
        return bool(self._active_recording is not None and getattr(self._active_recording, "is_running", False))

    def _normalize_exposure_us(self, exposure_val: float | int) -> int:
        """Normalize exposure value: <= 1000 is treated as milliseconds, > 1000 as microseconds."""
        val = float(exposure_val)
        exp_us = int(round(val * 1000.0)) if val <= 1000.0 else int(round(val))
        max_period_us = max(1000, int(1_000_000 / max(self._configured_fps, 1)))
        return max(100, min(max_period_us, exp_us))

    def get_current_controls(self) -> dict:
        return {
            "camera_id": self._active_camera_id,
            "control_mode": self.control_mode,
            "exposure": round(self.current_exposure_us / 1000.0, 2),
            "exposure_us": int(self.current_exposure_us),
            "gain": int(self.current_gain),
            "focus": int(self.current_focus),
            "brightness": int(self.current_brightness),
            "contrast": int(self.current_contrast),
            "auto_exposure": bool(self.auto_exposure_enabled),
            "auto_focus": bool(self.auto_focus_enabled),
            "locked_for_recording": self.is_recording_active(),
        }

    def _apply_current_controls_to_hardware(self, lock_awb: bool | None = None) -> None:
        """Re-apply the active saved/runtime controls via _control_queue so firmware never overrides them."""
        if not self._is_running or not self.is_connected or self._control_queue is None:
            return
        try:
            ctrl = dai.CameraControl()
            ctrl.setAutoFocusMode(dai.CameraControl.AutoFocusMode.OFF)
            ctrl.setManualFocus(self.current_focus)
            try:
                ctrl.setAutoExposureLock(False)
            except Exception:
                pass
            ctrl.setManualExposure(self.current_exposure_us, self.current_gain)

            hw_b = max(-10, min(10, round(self.current_brightness / 5.0)))
            hw_c = max(-10, min(10, round((self.current_contrast - 50) / 5.0)))
            try:
                ctrl.setBrightness(hw_b)
                ctrl.setContrast(hw_c)
            except Exception:
                pass

            if lock_awb is not None:
                try:
                    ctrl.setAutoWhiteBalanceLock(bool(lock_awb))
                except Exception:
                    pass

            self._control_queue.send(ctrl)
            logger.info(
                f"[CONTROL] Applied runtime controls -> mode={self.control_mode}, "
                f"exp={self.current_exposure_us}µs ({self.current_exposure_us / 1000:.1f}ms), "
                f"gain={self.current_gain} ISO, focus={self.current_focus}, "
                f"brightness={self.current_brightness}, contrast={self.current_contrast}"
                + (f", awb_lock={lock_awb}" if lock_awb is not None else "")
            )
        except Exception as e:
            if "closed" in str(e).lower() or "xlink" in str(e).lower():
                self._on_device_lost("Control queue closed during control sync")
            else:
                logger.warning(f"[CONTROL] Runtime control apply failed: {e}")

    def reset_controls(self) -> dict:
        """Resets physical camera controls to calibrated standard stream defaults (Focus=120, Exp=16ms, Gain=400).
        Disables continuous AF hunting and AE fluctuations so object movement does not cause blur or flicker.
        """
        if self.is_recording_active():
            logger.warning("[CONTROL] Rejected reset_controls — recording is active")
            return {
                "status": "error",
                "locked": True,
                "message": "Camera controls are locked while recording is active",
                "settings": self.get_current_controls(),
            }

        self.current_brightness = 0
        self.current_contrast = 50
        self.current_gain = 400
        self.current_focus = 120
        self.current_exposure_us = 16000
        self.auto_exposure_enabled = False
        self.auto_focus_enabled = False
        self.control_mode = "auto"

        if not self._is_running or not self.is_connected or self._control_queue is None:
            return {
                "status": "ok",
                "message": "Controls reset to default steady stream",
                "settings": self.get_current_controls(),
            }

        ctrl = dai.CameraControl()
        try:
            ctrl.setAutoFocusMode(dai.CameraControl.AutoFocusMode.OFF)
            ctrl.setManualFocus(120)
            ctrl.setManualExposure(16000, 400)
            try:
                ctrl.setAutoWhiteBalanceMode(dai.CameraControl.AutoWhiteBalanceMode.AUTO)
                ctrl.setAutoWhiteBalanceLock(False)
            except Exception:
                pass
            ctrl.setBrightness(0)
            ctrl.setContrast(0)

            self._control_queue.send(ctrl)
            logger.info("[CONTROL] 🔄 Camera reset to standard steady stream (Focus=120, Exp=16ms, Gain=400, AF hunting OFF)")
            return {
                "status": "ok",
                "message": "Camera hardware reset to steady stream defaults",
                "settings": self.get_current_controls(),
            }
        except Exception as e:
            logger.warning(f"[CONTROL] Factory reset send failed: {e}")
            return {"status": "error", "message": str(e)}

    def update_controls(
        self,
        exposure: float | int | None = None,
        gain: float | int | None = None,
        focus: int | None = None,
        brightness: int | None = None,
        contrast: int | None = None,
        auto_focus: bool | None = None,
        auto_exposure: bool | None = None,
        reset: bool | None = None,
        control_mode: str | None = None,
    ) -> dict:
        """Update hardware camera controls (Exposure, Gain, Focus, Brightness, Contrast)."""
        if self.is_recording_active():
            logger.warning("[CONTROL] Rejected update_controls — recording is active")
            return {
                "status": "error",
                "locked": True,
                "message": "Camera controls are locked while recording is active",
                "settings": self.get_current_controls(),
            }

        if reset:
            return self.reset_controls()

        # 1. Update internal state
        if control_mode in ("auto", "manual"):
            self.control_mode = control_mode

        if brightness is not None:
            self.current_brightness = max(-50, min(50, int(round(brightness))))

        if contrast is not None:
            self.current_contrast = max(0, min(100, int(round(contrast))))

        if gain is not None:
            self.current_gain = max(100, min(1600, int(round(gain))))

        if focus is not None:
            self.current_focus = max(0, min(255, int(round(focus))))

        if exposure is not None:
            self.current_exposure_us = self._normalize_exposure_us(exposure)

        if auto_exposure is True and control_mode != "manual":
            self.auto_exposure_enabled = True
            self.control_mode = "auto"
        elif auto_exposure is False or (auto_exposure is None and (exposure is not None or gain is not None)):
            self.auto_exposure_enabled = False
            if control_mode != "auto":
                self.control_mode = "manual"

        if auto_focus is True:
            self.auto_focus_enabled = True
        elif auto_focus is False or (auto_focus is None and focus is not None):
            self.auto_focus_enabled = False

        if not self._is_running or not self.is_connected or self._control_queue is None:
            return {"status": "ok", "settings": self.get_current_controls()}

        ctrl = dai.CameraControl()
        should_send = False

        # 2. Exposure & Gain Logic
        if auto_exposure is True and self.control_mode == "auto":
            try:
                ctrl.setAutoExposureEnable()
                if self._ae_limit_us is not None:
                    try:
                        ctrl.setAutoExposureLimit(self._ae_limit_us)
                    except Exception:
                        pass
                should_send = True
            except Exception as e:
                logger.warning(f"[CONTROL] setAutoExposureEnable failed: {e}")
        elif auto_exposure is False or (auto_exposure is None and (exposure is not None or gain is not None)):
            try:
                ctrl.setAutoExposureLock(False)
                ctrl.setManualExposure(self.current_exposure_us, self.current_gain)
                should_send = True
            except Exception as e:
                logger.warning(f"[CONTROL] setManualExposure failed: {e}")

        # 3. Focus Logic (Both auto_focus toggle and manual focus position)
        if auto_focus is True:
            try:
                ctrl.setAutoFocusMode(dai.CameraControl.AutoFocusMode.CONTINUOUS_VIDEO)
                try:
                    ctrl.setAutoFocusTrigger()
                except Exception:
                    pass
                should_send = True
            except Exception as e:
                logger.warning(f"[CONTROL] setAutoFocusMode failed: {e}")
        elif auto_focus is False or (auto_focus is None and focus is not None):
            try:
                ctrl.setAutoFocusMode(dai.CameraControl.AutoFocusMode.OFF)
                ctrl.setManualFocus(self.current_focus)
                should_send = True
            except Exception as e:
                logger.warning(f"[CONTROL] setManualFocus failed: {e}")

        # 4. Hardware Brightness (-10 to 10)
        if brightness is not None:
            try:
                hw_b = max(-10, min(10, round(self.current_brightness / 5.0)))
                ctrl.setBrightness(hw_b)
                should_send = True
            except Exception as e:
                logger.debug(f"[CONTROL] setBrightness failed: {e}")

        # 5. Hardware Contrast (-10 to 10)
        if contrast is not None:
            try:
                hw_c = max(-10, min(10, round((self.current_contrast - 50) / 5.0)))
                ctrl.setContrast(hw_c)
                should_send = True
            except Exception as e:
                logger.debug(f"[CONTROL] setContrast failed: {e}")

        # 6. Send to device
        if should_send:
            try:
                self._control_queue.send(ctrl)
                logger.info(
                    f"[CONTROL] Dispatched -> mode={self.control_mode}, exp={self.current_exposure_us}µs, "
                    f"gain={self.current_gain} ISO, focus={self.current_focus}, "
                    f"brightness={self.current_brightness}, contrast={self.current_contrast}, "
                    f"auto_exp={self.auto_exposure_enabled}, auto_focus={self.auto_focus_enabled}"
                )
            except Exception as e:
                if "closed" in str(e).lower() or "xlink" in str(e).lower():
                    self._on_device_lost("Control queue closed")
                else:
                    logger.warning(f"[CONTROL] Device send failed: {e}")

        return {"status": "ok", "settings": self.get_current_controls()}

    def save_current_controls_to_db(
        self,
        camera_id: int | None = None,
        control_mode: str | None = None,
        exposure: float | int | None = None,
        gain: float | int | None = None,
        focus: int | None = None,
        brightness: int | None = None,
        contrast: int | None = None,
        auto_exposure: bool | None = None,
        auto_focus: bool | None = None,
    ) -> dict:
        """Apply (if provided) and persist camera control settings to the SQLite cameras table."""
        if self.is_recording_active():
            logger.warning("[CONTROL] Rejected save_current_controls_to_db — recording is active")
            return {
                "status": "error",
                "locked": True,
                "message": "Camera controls are locked while recording is active",
                "settings": self.get_current_controls(),
            }

        target_mode = control_mode.lower() if isinstance(control_mode, str) and control_mode.lower() in ("auto", "manual") else None

        # Sync runtime & hardware state with any explicit values passed by the Save button
        if target_mode == "auto":
            self.reset_controls()
        elif any(v is not None for v in (target_mode, exposure, gain, focus, brightness, contrast, auto_exposure, auto_focus)):
            self.update_controls(
                exposure=exposure,
                gain=gain,
                focus=focus,
                brightness=brightness,
                contrast=contrast,
                auto_focus=False if target_mode == "manual" else auto_focus,
                auto_exposure=False if target_mode == "manual" else auto_exposure,
                control_mode=target_mode or "manual",
            )

        from app.core.database import SessionLocal
        from app.models.camera_model import Camera
        from app.services import camera_service

        db = SessionLocal()
        try:
            cam = None
            resolved_id = camera_id or self._active_camera_id
            if resolved_id is not None:
                cam = db.query(Camera).filter(Camera.id == resolved_id).first()
            if cam is None:
                cam = camera_service.get_camera_config(db)
            if cam is None:
                cam = db.query(Camera).order_by(Camera.id.desc()).first()
            if cam is None:
                return {"status": "error", "message": "No camera record found in database"}

            self._active_camera_id = cam.id
            cam.control_mode = self.control_mode
            if self.control_mode == "auto":
                cam.exposure = 16.0
                cam.gain = 400.0
                cam.focus = 120
                cam.brightness = 0
                cam.contrast = 50
                cam.auto_exposure = False
                cam.auto_focus = False
            else:
                cam.exposure = round(self.current_exposure_us / 1000.0, 2)
                cam.gain = float(self.current_gain)
                cam.focus = int(self.current_focus)
                cam.brightness = int(self.current_brightness)
                cam.contrast = int(self.current_contrast)
                cam.auto_exposure = False
                cam.auto_focus = False

            db.commit()
            db.refresh(cam)

            saved_settings = self.get_current_controls()
            logger.info(
                f"[CONTROL] Saved settings to DB (camera_id={cam.id}): "
                f"mode={cam.control_mode}, exp={cam.exposure}ms, gain={cam.gain}, "
                f"focus={cam.focus}, brightness={cam.brightness}, contrast={cam.contrast}"
            )
            realtime_log_service.add_log(
                "camera",
                "CAMERA",
                f"Saved camera controls ({cam.control_mode}: exp={cam.exposure}ms, ISO {int(cam.gain or 400)}, focus={cam.focus})",
                "success",
            )
            return {
                "status": "ok",
                "message": "Camera settings saved to database",
                "camera_id": cam.id,
                "settings": saved_settings,
            }
        except Exception as e:
            db.rollback()
            logger.error(f"[CONTROL] Failed to save controls to DB: {e}", exc_info=True)
            return {"status": "error", "message": f"Failed to save camera settings: {e}"}
        finally:
            db.close()

    # ==================== Inference ====================
    # Duck Analyzer Worker — runs continuous DuckAnalyzer inference.

    def _inference_worker(self, session_id: str) -> None:
        from app.ml.camera_inference_service import run_inference
        from app.services.inference_recording_service import InferenceRecorder

        logger.info(f"[INFERENCE] Thread running — session: {session_id}")

        # ── Metrics state ──
        fps_counter     = 0
        fps_window_start = time.time()
        current_fps     = 0.0

        recorder: InferenceRecorder | None = None
        recording_active = False
        last_seen_seq = -1

        try:
            while not self._inference_stop.is_set():

                # ── Frame acquisition ──────────────────────────────────────────
                if self._inference_offline:
                    frame = None
                    _got_boundary = False
                    _got_eof = False

                    while not self._inference_stop.is_set():
                        try:
                            item = self._offline_frame_queue.get(timeout=0.1)
                        except Empty:
                            continue

                        if item is None:
                            logger.info("[INFERENCE] All videos processed — inference complete")
                            self._inference_stop.set()
                            _got_eof = True
                            break
                        elif item is _VIDEO_BOUNDARY:
                            from app.ml.camera_inference_service import reset_session_for_next_video
                            reset_session_for_next_video(session_id)
                            logger.info("[INFERENCE] Video boundary — session reset, cycle counter carried forward")
                            _got_boundary = True
                            break
                        else:
                            frame = item
                            break

                    if _got_boundary:
                        continue

                    if frame is None:
                        if _got_eof:
                            done_result = {
                                "status": "INFERENCE_COMPLETE",
                                "done": True,
                                "metrics": {
                                    "fps": round(current_fps, 1),
                                    "gpu_pct": round(self._gpu_usage, 1),
                                    "latency_ms": 0,
                                },
                            }
                            if self._inference_result_queue is not None and self._inference_loop is not None:
                                asyncio.run_coroutine_threadsafe(
                                    self._async_inference_push(done_result),
                                    self._inference_loop,
                                )
                        break
                    self._latest_bgr = frame
                else:
                    if self._latest_bgr is None or self._latest_bgr_seq == last_seen_seq:
                        time.sleep(0.005)
                        continue
                    last_seen_seq = self._latest_bgr_seq
                    frame = self._latest_bgr

                # ── Online frame normalization ────────────────────────────────
                if not self._inference_offline:
                    _fmt = "FFV1"
                    if _fmt == "MJPEG":
                        _ok, _buf = cv2.imencode(
                            ".jpg", frame,
                            [cv2.IMWRITE_JPEG_QUALITY, 95],
                        )
                        if _ok:
                            frame = cv2.imdecode(_buf, cv2.IMREAD_COLOR)

                # ── Run inference + measure latency ───────────────────────────
                t_start = time.perf_counter()
                infer_res = run_inference(
                    frame.copy(),
                    session_id,
                    video_name=self._current_video_name,
                )
                latency_ms = (time.perf_counter() - t_start) * 1000

                if infer_res is None:
                    logger.warning("❌ [INFERENCE] run_inference returned None")
                    continue

                if isinstance(infer_res, tuple):
                    result, annotated_frame = infer_res
                else:
                    result, annotated_frame = infer_res, frame

                if not isinstance(result, dict):
                    logger.warning("❌ [INFERENCE] run_inference result is not a dict")
                    continue

                # ── FPS calculation (rolling 2-second window) ─────────────────
                fps_counter += 1
                now = time.time()
                elapsed = now - fps_window_start
                if elapsed >= 0.5:
                    current_fps      = fps_counter / elapsed
                    fps_counter      = 0
                    fps_window_start = now

                # ── GPU usage (sampled every result, cheap if nvidia-smi absent) ─
                gpu_pct = self._gpu_usage

                # ── Attach metrics to result ──────────────────────────────────
                result["metrics"] = {
                    "fps":        round(current_fps, 1),
                    "gpu_pct":    round(gpu_pct, 1),
                    "latency_ms": round(latency_ms, 1),
                }

                logger.debug(
                    f"[INFERENCE] status={result.get('status')} "
                    f"record={result.get('record')} "
                    f"fps={current_fps:.1f} gpu={gpu_pct:.0f}% "
                    f"latency={latency_ms:.1f}ms"
                )

                # ── Frame progress (offline) ──────────────────────────────────
                if self._inference_offline:
                    result["frame_index"]   = self._offline_frame_index
                    result["total_frames"]  = self._offline_total_frames
                    result["video_index"]   = self._current_video_idx
                    result["total_videos"]  = self._total_videos
                    result["video_name"]    = self._current_video_name

                if self._inference_offline:
                    result["_raw_frame"] = frame

                # ── Push result to WebSocket ──────────────────────────────────
                if self._inference_result_queue is not None and self._inference_loop is not None:
                    asyncio.run_coroutine_threadsafe(
                        self._async_inference_push(result),
                        self._inference_loop,
                    )

                # ── Feed frames to duck recorder ─────────────────────────────
                record_flag = result.get("record", False)
                should_record = record_flag

                if should_record and not recording_active:
                    h, w = frame.shape[:2]
                    rec_fmt = self._resolve_recording_format()
                    recorder = InferenceRecorder(
                        session_id=f"{session_id}_{int(time.time())}",
                        width=w,
                        height=h,
                        fps=self._configured_fps,
                        recording_format=rec_fmt,
                    )
                    recording_active = True
                    logger.info(f"[INFERENCE] Duck recording started (format={rec_fmt})")
                elif not should_record and recording_active:
                    if recorder:
                        recorder.stop()
                    recorder = None
                    recording_active = False
                    logger.info("[INFERENCE] Duck recording stopped")

                if recording_active and recorder:
                    recorder.write(frame, result)

        except Exception as e:
            logger.error(f"[INFERENCE] Thread error: {e}", exc_info=True)
        finally:
            if recorder:
                logger.info("[INFERENCE] Stopping recorder on thread exit")
                recorder.stop()
            logger.info(f"[INFERENCE] Thread ended — session: {session_id}")

    def _inference_watchdog(self, session_id: str) -> None:
        """Restarts the online inference worker thread if it dies unexpectedly
        (uncaught exception) without an explicit stop_inference() call.

        Without this, an uncaught error in the worker (e.g. a locked SQLite
        read) silently ends the thread while capture threads and the FastAPI
        process keep running — the dashboard then stops receiving updates
        with no visible crash. Offline (video file) runs are not restarted
        since replaying from the correct frame position isn't safe to do
        automatically.
        """
        max_restarts = 5
        restarts = 0

        while not self._inference_stop.wait(timeout=5.0):
            if self._inference_offline:
                continue

            thread = self._inference_thread
            if thread is None or thread.is_alive():
                continue

            if restarts >= max_restarts:
                logger.critical(
                    f"[INFERENCE] Worker died {restarts} times — giving up "
                    f"auto-restart for session: {session_id}"
                )
                break

            restarts += 1
            logger.error(
                f"[INFERENCE] Worker thread died unexpectedly — "
                f"restarting (attempt {restarts}/{max_restarts}) for session: {session_id}"
            )

            new_thread = threading.Thread(
                target=self._inference_worker,
                args=(session_id,),
                name="oak-inference",
                daemon=True,
            )
            self._inference_thread = new_thread
            new_thread.start()

        logger.info(f"[INFERENCE] Watchdog stopped — session: {session_id}")

    # ==================== Offline Frame Feed ====================

    def _offline_loop(self, video_paths: list[str]) -> None:
        """Reads frames from one or more video files sequentially and pushes each into
        _offline_frame_queue.  Blocks if inference can't keep up — guarantees every frame
        is processed exactly once.  A _VIDEO_BOUNDARY sentinel is placed between videos so
        the inference worker can reset session state.  A final None sentinel signals EOF.
        """
        total = len(video_paths)
        self._total_videos = total
        logger.info(f"[OFFLINE] Thread running — {total} video(s)")

        try:
            for idx, video_path in enumerate(video_paths):
                if self._offline_stop.is_set():
                    break

                self._current_video_idx = idx + 1
                self._current_video_name = os.path.basename(video_path)

                logger.info(f"[OFFLINE] Video {idx + 1}/{total}: {video_path}")

                cap = cv2.VideoCapture(video_path)
                if not cap.isOpened():
                    logger.error(f"[OFFLINE] Failed to open video: {video_path}")
                    continue

                total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
                fps    = cap.get(cv2.CAP_PROP_FPS) or 25.0
                width  = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
                height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))

                self._offline_frame_index  = 0
                self._offline_total_frames = total_frames

                logger.info(f"[OFFLINE] {width}x{height} | fps={fps:.1f} | total_frames={total_frames}")

                frames_read = 0
                try:
                    while not self._offline_stop.is_set():
                        ret, frame = cap.read()
                        if not ret:
                            logger.info(f"[OFFLINE] EOF — frames_read={frames_read}")
                            break

                        frames_read += 1
                        self._offline_frame_index = frames_read

                        # Block here if inference is behind — no frames are skipped
                        while not self._offline_stop.is_set():
                            try:
                                self._offline_frame_queue.put(frame, timeout=0.1)
                                break
                            except Exception:
                                continue

                        if frames_read % 100 == 0:
                            logger.info(
                                f"[OFFLINE] frames_read={frames_read} | "
                                f"queue_size={self._offline_frame_queue.qsize()}"
                            )
                finally:
                    cap.release()

                # Between videos: boundary sentinel so inference worker resets state
                if idx < total - 1 and not self._offline_stop.is_set():
                    self._offline_frame_queue.put(_VIDEO_BOUNDARY)
                    logger.info(f"[OFFLINE] Video {idx + 1} done — boundary sentinel sent")

        except Exception as e:
            logger.error(f"[OFFLINE] Thread error: {e}", exc_info=True)
        finally:
            # Final EOF sentinel — inference worker will push INFERENCE_COMPLETE to WS
            self._offline_frame_queue.put(None)
            self._offline_stop.set()
            logger.info(f"[OFFLINE] Thread ended — processed {total} video(s)")

    def _start_offline_thread(self, video_paths: list[str]) -> bool:
        # If thread exists but is dead (finished naturally), clean it up first
        if self._offline_thread is not None and not self._offline_thread.is_alive():
            self._offline_thread = None
            self._offline_stop.clear()

        if self._offline_thread and self._offline_thread.is_alive():
            logger.warning("[OFFLINE] Thread already running")
            return True

        if not video_paths:
            logger.error("[OFFLINE] No video paths provided")
            return False

        # ALWAYS drain the queue before starting — catches stale sentinels
        # from threads that finished after stop_inference returned
        drained = 0
        while not self._offline_frame_queue.empty():
            try:
                self._offline_frame_queue.get_nowait()
                drained += 1
            except Exception:
                break
        if drained:
            logger.info(f"[OFFLINE] Drained {drained} stale items from previous run")

        self._offline_stop.clear()
        self._inference_stop.clear()  # also clear this — late EOF may have set it

        # Quick readability check on the first video only
        cap_check = cv2.VideoCapture(video_paths[0])
        if not cap_check.isOpened():
            cap_check.release()
            logger.error(f"[OFFLINE] First video not readable: {video_paths[0]}")
            return False
        cap_check.release()

        self._offline_thread = threading.Thread(
            target=self._offline_loop,
            args=(video_paths,),
            name="oak-offline",
            daemon=True,
        )
        self._offline_thread.start()
        logger.info(f"[OFFLINE] Thread started — {len(video_paths)} video(s), first: {video_paths[0]}")
        return True

    # def _stop_offline_thread(self) -> None:
    #     if not (self._offline_thread and self._offline_thread.is_alive()):
    #         return

    #     logger.info("[OFFLINE] Stopping thread...")
    #     self._offline_stop.set()
    #     self._offline_thread.join(timeout=5.0)
    #     if self._offline_thread.is_alive():
    #         logger.warning("[OFFLINE] Thread did not stop in time")
    #     self._offline_thread = None
    #     self._latest_bgr = None
    #     logger.info("[OFFLINE] Thread stopped")
    
    def _stop_offline_thread(self) -> None:
        if self._offline_thread is None:
            return

        logger.info("[OFFLINE] Stopping thread...")
        self._offline_stop.set()

        if self._offline_thread.is_alive():
            self._offline_thread.join(timeout=5.0)
            if self._offline_thread.is_alive():
                logger.warning("[OFFLINE] Thread did not stop in time")

        # Drain queue so inference worker unblocks
        while not self._offline_frame_queue.empty():
            try:
                self._offline_frame_queue.get_nowait()
            except Exception:
                break

        self._offline_thread = None
        self._latest_bgr = None
        self._offline_stop.clear()
        logger.info("[OFFLINE] Thread stopped")

    async def _async_inference_push(self, result: dict) -> None:
        """Drop-oldest push into inference result queue."""
        if self._inference_result_queue is None:
            return
        if self._inference_result_queue.full():
            try:
                self._inference_result_queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            self._inference_result_queue.put_nowait(result)
        except asyncio.QueueFull:
            pass

    def _collect_folder_videos(self, folder_path: str) -> list[str]:
        """Return sorted list of video file paths found directly inside folder_path."""
        VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.wmv', '.m4v'}
        if not os.path.isdir(folder_path):
            logger.error(f"[OFFLINE] Not a directory: {folder_path}")
            return []
        videos = [
            os.path.join(folder_path, name)
            for name in sorted(os.listdir(folder_path))
            if os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS
        ]
        logger.info(f"[OFFLINE] Found {len(videos)} video(s) in: {folder_path}")
        return videos

    def start_inference(
        self,
        session_id: str,
        loop: asyncio.AbstractEventLoop,
        offline: bool = False,
        video_path: str | None = None,
        folder_path: str | None = None,
    ) -> dict:
        if self._inference_thread and self._inference_thread.is_alive():
            logger.warning("[INFERENCE] Already running")
            return {"status": "already_running"}

        logger.info(f"[INFERENCE] Mode: {'OFFLINE' if offline else 'ONLINE'} — session: {session_id}")

        # Set up queue and loop FIRST so the WebSocket can connect even if validation fails.
        # Validation errors are pushed as a result so the WS receives them before closing.
        self._inference_session_id = session_id
        self._inference_loop = loop
        self._inference_offline = offline
        self._inference_result_queue = asyncio.Queue(maxsize=4)

        def _push_error(msg: str) -> None:
            asyncio.run_coroutine_threadsafe(
                self._async_inference_push({"status": "ERROR", "error": msg, "done": True}),
                loop,
            )

        if offline:
            if folder_path:
                video_paths = self._collect_folder_videos(folder_path)
                if not video_paths:
                    msg = f"No video files found in folder: {folder_path}"
                    logger.error(f"[INFERENCE] {msg}")
                    _push_error(msg)
                    return {"status": "error", "message": msg}
                logger.info(f"[INFERENCE] Folder mode — {len(video_paths)} video(s): {folder_path}")
            elif video_path:
                if not os.path.isfile(video_path):
                    msg = f"Video file not found: {video_path}"
                    logger.error(f"[INFERENCE] {msg}")
                    _push_error(msg)
                    return {"status": "error", "message": msg}
                video_paths = [video_path]
                logger.info(f"[INFERENCE] Single video mode: {video_path}")
            else:
                msg = "video_path or folder_path is required for offline mode"
                logger.error(f"[INFERENCE] {msg}")
                _push_error(msg)
                return {"status": "error", "message": msg}

            if not self._start_offline_thread(video_paths):
                msg = "Could not open video(s)"
                logger.error(f"[INFERENCE] {msg}")
                _push_error(msg)
                return {"status": "error", "message": msg}
        else:
            # Online mode: feed _latest_bgr from camera capture threads (unchanged)
            if not self._is_running:
                msg = "Pipeline not started"
                _push_error(msg)
                return {"status": "error", "message": msg}
            if not (self._hires_thread and self._hires_thread.is_alive()):
                logger.info("[INFERENCE] Capture threads not running — starting now")
                self._start_capture_threads()

        self._inference_stop.clear()
        self._inference_thread = threading.Thread(
            target=self._inference_worker,
            args=(session_id,),
            name="oak-inference",
            daemon=True,
        )
        self._start_gpu_sampler()
        self._inference_thread.start()

        if not offline:
            self._inference_watchdog_thread = threading.Thread(
                target=self._inference_watchdog,
                args=(session_id,),
                name="oak-inference-watchdog",
                daemon=True,
            )
            self._inference_watchdog_thread.start()

        logger.info(f"[INFERENCE] Started — session: {session_id} | offline: {offline}")
        return {
            "status": "started",
            "mode": "offline" if offline else "online",
            "total_videos": self._total_videos if offline else 1,
        }

    def stop_inference(self) -> dict:
        from app.ml.camera_inference_service import clear_session

        logger.info("[INFERENCE] Stopping...")
        self._inference_stop.set()
        
        # Stop offline feed thread immediately so it stops queueing frames
        if self._offline_thread and self._offline_thread.is_alive():
            self._stop_offline_thread()

        # BUGFIX: this used to join(timeout=2.0), log a warning if the thread
        # was still alive, and then fall through anyway to unconditionally
        # call clear_session() -> app_state.exit_inference("camera") and null
        # out self._inference_thread. That releases the cross-kind GPU claim
        # (and defeats start_inference()'s "already_running" guard) while the
        # worker thread can still be mid-call inside analyzer.process_frame()
        # on the GPU. The very next /video/start can then pass
        # try_enter_inference("video") and load a second DuckAnalyzer onto
        # the same GPU while the camera thread is still using it -- two
        # threads hitting the same CUDA context concurrently, which is
        # exactly the kind of thing that can wedge the whole process badly
        # enough to look like "backend OFFLINE". This is the backend half of
        # the race useInferenceLoop.ts's "GPU-contention fix" comment was
        # working around from the frontend side.
        #
        # Fix: only release the claim / clear the session once the thread is
        # actually confirmed dead. If it's still alive after the timeout, we
        # leave self._inference_thread pointing at it (so start_inference()'s
        # existing "already_running" guard keeps correctly refusing a new
        # start) and hand off to a background watcher that does an unbounded
        # join and releases the claim only when the thread genuinely exits.
        thread_confirmed_dead = True
        if self._inference_thread and self._inference_thread.is_alive():
            self._inference_thread.join(timeout=2.0)
            if self._inference_thread.is_alive():
                thread_confirmed_dead = False
                stuck_thread = self._inference_thread
                stuck_session_id = self._inference_session_id
                logger.warning(
                    "[INFERENCE] Worker thread did not stop within 2s -- it is "
                    "still using the GPU. NOT releasing the camera's GPU claim "
                    "yet; a background watcher will release it once the thread "
                    "actually exits."
                )

                def _wait_and_release(thread=stuck_thread, session_id=stuck_session_id):
                    thread.join()  # unbounded -- the native call WILL return eventually
                    logger.info(
                        f"[INFERENCE] Delayed-stopped worker thread for session "
                        f"{session_id} has now actually exited -- releasing GPU claim."
                    )
                    try:
                        if session_id:
                            clear_session(session_id)
                    except Exception as e:
                        logger.error(f"[INFERENCE] clear_session during delayed release failed: {e}")

                threading.Thread(
                    target=_wait_and_release,
                    name="oak-inference-delayed-release",
                    daemon=True,
                ).start()

        # ── Clean up inference session (only if the thread is actually gone) ──
        if thread_confirmed_dead and self._inference_session_id:
            logger.info(f"[INFERENCE] Clearing session: {self._inference_session_id}")
            clear_session(self._inference_session_id)

        # Only forget the thread reference once it's confirmed dead -- otherwise
        # start_inference()'s already_running guard must keep seeing it so a
        # second worker can't start on top of the still-running one.
        if thread_confirmed_dead:
            self._inference_thread = None
        self._inference_watchdog_thread = None
        self._inference_result_queue = None
        self._inference_session_id = None
        self._inference_offline = False

        streaming_active = bool(self._is_streaming or self._stream_subscribers or (self._stream_queue is not None))
        recording_active = self._active_recording is not None
        # Keep capture threads alive if streaming or camera pipeline is running
        if not streaming_active and not recording_active and not self._is_running:
            logger.info("[INFERENCE] Camera not streaming or running — stopping capture threads")
            self._stop_capture_threads()
        else:
            logger.info(f"[INFERENCE] Camera active (streaming={streaming_active}, recording={recording_active}, running={self._is_running}) — keeping capture threads running")

        logger.info("[INFERENCE] Stopped")
        self._stop_gpu_sampler()
        # ── Reset offline thread reference if it finished naturally ──
        if self._offline_thread is not None and not self._offline_thread.is_alive():
            self._offline_thread = None
            self._offline_stop.clear()
            # Drain any leftover sentinel from previous EOF
            while not self._offline_frame_queue.empty():
                try:
                    self._offline_frame_queue.get_nowait()
                except Exception:
                    break
        return {"status": "stopped"}

    async def get_inference_result(self, timeout: float = 1.0) -> dict | None:
        if self._inference_result_queue is None:
            return None
        try:
            return await asyncio.wait_for(self._inference_result_queue.get(), timeout=timeout)
        except asyncio.TimeoutError:
            return None

    # ==================== Recording ====================

    def _resolve_recording_path(self) -> str | None:
        return None

    def _resolve_recording_format(self, override: str | None = None) -> str:
        if override and override.strip():
            return override.strip().upper()
        try:
            from app.core.database import SessionLocal
            from app.models.camera_model import Camera
            db = SessionLocal()
            try:
                cam = db.query(Camera).first()
                fmt = getattr(cam, "recording_format", None)
                if fmt and isinstance(fmt, str) and fmt.strip():
                    return fmt.strip().upper()
            finally:
                db.close()
        except Exception:
            pass
        return "MP4"

    def start_recording(
        self,
        session_id: str,
        width: int,
        height: int,
        fps: float,
        root_path: str | None = None,
        recording_format: str | None = None,
    ) -> str:
        from app.services.recording_service import start_recording as _start, active_recordings

        resolved_root = root_path or self._resolve_recording_path()
        rec_fmt = self._resolve_recording_format(recording_format)

        # Ensure recording resolution matches the actual camera pipeline to prevent PyAV rescaling
        if hasattr(self, "_pipeline_width") and self._pipeline_width and hasattr(self, "_pipeline_height") and self._pipeline_height:
            if (width, height) != (self._pipeline_width, self._pipeline_height):
                logger.warning(
                    f"[RECORD] Requested resolution {width}x{height} differs from camera pipeline "
                    f"{self._pipeline_width}x{self._pipeline_height} — overriding to match camera."
                )
                width = self._pipeline_width
                height = self._pipeline_height

        if self._latest_bgr is not None:
            actual_h, actual_w = self._latest_bgr.shape[:2]
            if (width, height) != (actual_w, actual_h):
                logger.warning(f"[RECORD] Resolution adjusted to actual frame size: {actual_w}x{actual_h}")
                width, height = actual_w, actual_h

        logger.info(f"[RECORD] Starting recording — session: {session_id}, {width}x{height} @ {fps}fps | format={rec_fmt}")
        logger.info(f"[RECORD] Pipeline running: {self._is_running}")
        logger.info(
            f"[RECORD] Threads — mjpeg: {bool(self._mjpeg_thread and self._mjpeg_thread.is_alive())} | "
            f"hires: {bool(self._hires_thread and self._hires_thread.is_alive())} | "
            f"convert: {bool(self._convert_thread and self._convert_thread.is_alive())} | "
            f"record: {bool(self._record_thread and self._record_thread.is_alive())}"
        )
        realtime_log_service.add_log(
            "record",
            "RECORD",
            f"Recording started: Session {session_id} ({rec_fmt})",
            "success"
        )

        # Start capture threads if not already running (e.g. recording without streaming)
        if not (self._hires_thread and self._hires_thread.is_alive()) or not (self._record_thread and self._record_thread.is_alive()):
            logger.info("[RECORD] Step 1 — capture/recording threads not running, starting now")
            self._start_capture_threads()
            self._enforce_ae_limit()
            logger.info(f"[RECORD] Step 2 — AE limit enforced | configured_fps={self._configured_fps} | ae_limit_us={self._ae_limit_us}")
        else:
            logger.info("[RECORD] Capture & recording threads already running (AE limit already applied at stream start)")

        # Re-apply and lock active camera controls (exposure, gain, focus, brightness, contrast, AWB)
        # so sensor parameters cannot vary or drift during recording.
        self._apply_current_controls_to_hardware(lock_awb=True)

        path = _start(session_id, width, height, fps, resolved_root, rec_fmt)
        self._active_recording = active_recordings.get(session_id)

        if self._active_recording is None:
            logger.error("[RECORD] Failed to get recording session from active_recordings!")
        else:
            logger.info(f"[RECORD] Session active — packets will be fed via dedicated _record_convert_loop → path: {path}")

        return path

    def stop_recording(self, session_id: str) -> dict:
        from app.services.recording_service import stop_recording as _stop

        logger.info(f"[RECORD] Stopping recording — session: {session_id}")
        realtime_log_service.add_log(
            "record",
            "RECORD",
            f"Recording stopped: Session {session_id}",
            "success"
        )

        with self._recording_stop_lock:
            rec = self._active_recording
            if rec is None:
                cached = self._completed_recordings.get(session_id)
                if cached:
                    return cached

            # Clear _active_recording first so _hires_loop stops enqueueing new packets
            # for this session, then wait for all already-captured packets to finish
            # converting before stopping and flushing the encoder worker.
            if rec is not None and getattr(rec, "session_id", session_id) == session_id:
                self._active_recording = None
                logger.info(
                    f"[RECORD] Draining pending capture packets "
                    f"(capture_pending={getattr(rec, '_capture_packets_pending', 0)}, "
                    f"encoder_queue={rec.frame_queue.qsize()})"
                )
                drain_deadline = time.monotonic() + 5.0
                while (
                    getattr(rec, "_capture_packets_pending", 0) > 0
                    and self._record_thread is not None
                    and self._record_thread.is_alive()
                    and time.monotonic() < drain_deadline
                ):
                    time.sleep(0.005)
            else:
                self._active_recording = None

            result = _stop(session_id)
            if not result:
                # Handle harmless duplicate stop requests cleanly
                result = self._completed_recordings.get(session_id, {})
            if result:
                self._completed_recordings[session_id] = result

        logger.info(f"[RECORD] Recording stopped and session cleared → {result}")

        # Release AWB lock while keeping user's active manual/steady exposure & focus intact
        self._apply_current_controls_to_hardware(lock_awb=False)

        # Stop capture threads only if streaming and pipeline are also not active
        streaming_active = bool(self._is_streaming or self._stream_subscribers or (self._stream_queue is not None))
        if not streaming_active and not self._is_running:
            logger.info("[RECORD] No active streaming or running pipeline — stopping capture threads")
            self._stop_capture_threads()
        else:
            logger.info("[RECORD] Streaming/pipeline still active — keeping capture threads running")

        return result

    # ==================== App Lifecycle (connect / disconnect) ====================

    async def start(self, config) -> dict:
        """App startup — connect device and build pipeline only. Does NOT start capture threads."""
        async with self._lifecycle_lock:
            if self._is_running:
                logger.info("[START] Pipeline already running")
                return {
                    "status": "already_running",
                    "camera_id": self._active_camera_id,
                    "camera_settings": self.get_current_controls(),
                }

            try:
                if not self.is_connected:
                    if not await self.connect(config.ip_address):
                        # If device is already in use by another process, do NOT attempt USB fallback
                        # as it will only repeat the exact same failure and spam logs.
                        if "already used" in (self._last_connection_error or "").lower() or "in use" in (self._last_connection_error or "").lower():
                            return {
                                "status": "error",
                                "message": self._last_connection_error or "Device is already used by another application/process. Make sure to close all applications/processes using the device before starting a new one.",
                                "error_code": "DEVICE_IN_USE",
                            }

                        # Older databases may select a locked/unavailable
                        # network camera as the newest row. Prefer a local USB
                        # OAK when one is available so startup still works.
                        selected = str(config.ip_address or "").strip().lower()
                        if selected not in {"", "usb", "auto", "127.0.0.1", "localhost"}:
                            logger.warning(
                                f"[START] Could not connect to {config.ip_address}; trying USB OAK"
                            )
                            if not await self.connect("usb"):
                                return {"status": "error", "message": self._last_connection_error or "Device connection failed"}
                        else:
                            return {"status": "error", "message": self._last_connection_error or "Device connection failed"}

                self.current_brightness = int(config.brightness or 0)
                self.current_contrast = int(config.contrast if config.contrast is not None and config.contrast != 0 else 50)

                if self.device is None:
                    return {"status": "error", "message": "Device not connected"}

                with self._pipeline_lock:
                    pipeline = dai.Pipeline(self.device)
                    self._pipeline = pipeline

                if not self._build_pipeline(pipeline, config):
                    return {"status": "error", "message": "Pipeline build failed"}

                pipeline.start()
                self._is_running = True

                # Enforce saved DB controls via runtime control queue right after pipeline starts
                # so camera firmware cannot override initialControl settings.
                if self.control_mode == "auto" and self._ae_limit_us is not None:
                    self._enforce_ae_limit()
                else:
                    self._apply_current_controls_to_hardware()

                logger.info("[START] Pipeline ready — waiting for stream/start")
                realtime_log_service.add_log(
                    "system",
                    "SYSTEM",
                    "Application initialized successfully",
                    "success"
                )
                return {
                    "status": "started",
                    "camera_id": self._active_camera_id,
                    "camera_settings": self.get_current_controls(),
                }

            except Exception as e:
                logger.error(f"[START] Failed: {e}", exc_info=True)
                try:
                    self._cleanup_pipeline()
                    await self.disconnect()
                except Exception:
                    pass
                return {"status": "error", "message": str(e)}

    async def stop(self) -> dict:
        """App shutdown — stop capture threads if running, cleanup pipeline, disconnect device."""
        async with self._lifecycle_lock:
            try:
                await asyncio.to_thread(self.stop_inference)
                
                # If a recording is active, we should attempt to stop it gracefully
                if self._active_recording and getattr(self._active_recording, "session_id", None):
                    await asyncio.to_thread(self.stop_recording, self._active_recording.session_id)
                elif self._inference_session_id:
                    await asyncio.to_thread(self.stop_recording, self._inference_session_id)
                    
                self._close_stream_queue()
                self._latest_jpeg = None
                await asyncio.to_thread(self._stop_capture_threads)
                await asyncio.to_thread(self._cleanup_pipeline)
                await self.disconnect()
                logger.info("[STOP] Camera stopped")
                realtime_log_service.add_log(
                    "system",
                    "SYSTEM",
                    "Application stopped",
                    "success"
                )
                return {"status": "stopped"}
            except Exception as e:
                logger.error(f"[STOP] Failed: {e}")
                return {"status": "error", "message": str(e)}

    # ==================== AE Runtime Enforcement ====================

    def _enforce_ae_limit(self) -> None:
        """Send AE shutter limit via runtime control queue.

        initialControl settings are applied at pipeline build time but are often
        overridden by the camera firmware during AE convergence.  Sending the same
        limit again as a runtime CameraControl message after the pipeline is running
        is the authoritative way to cap the shutter and guarantee the configured fps.
        """
        if self._ae_limit_us is None:
            logger.info("[AE] Control mode is manual — no AE limit to enforce")
            return
        if not self._is_running or not self.is_connected or self._control_queue is None:
            return
        try:
            ctrl = dai.CameraControl()
            ctrl.setAutoExposureEnable()
            ctrl.setAutoExposureLimit(self._ae_limit_us)
            self._control_queue.send(ctrl)
            logger.info(
                f"[AE] Runtime AE limit sent: {self._ae_limit_us} µs "
                f"(max shutter = {self._ae_limit_us / 1000:.1f} ms → "
                f"guarantees {self._configured_fps} fps)"
            )
        except Exception as e:
            if "closed" in str(e).lower() or "xlink" in str(e).lower():
                self._on_device_lost("Control queue closed during AE enforcement")
            else:
                logger.warning(f"[AE] Runtime AE limit failed: {e}")

    # ==================== Stream Lifecycle (capture threads) ====================

    async def start_streaming(self) -> dict:
        """User clicks Start Streaming — start the 3 capture threads."""
        if not self._is_running:
            return {"status": "error", "message": "Pipeline not started — call /oak/start first"}

        if not self._start_capture_threads():
            return {"status": "error", "message": "Capture threads failed to start"}

        # Enforce active saved controls via runtime control now that the pipeline is live.
        # This is more reliable than initialControl alone.
        logger.info("[STREAMING] Step 1 — capture threads started")
        if self.control_mode == "auto" and self._ae_limit_us is not None:
            self._enforce_ae_limit()
        else:
            self._apply_current_controls_to_hardware()
        logger.info(f"[STREAMING] Step 2 — Controls enforced | configured_fps={self._configured_fps} | ae_limit_us={self._ae_limit_us}")

        self._is_streaming = True
        logger.info("[STREAMING] Started")
        realtime_log_service.add_log(
            "stream",
            "STREAM",
            "Video stream initialized successfully",
            "success"
        )
        return {"status": "streaming"}

    async def stop_streaming(self) -> dict:
        """User clicks Stop Streaming — close stream queue, stop threads if recording and inference also inactive."""
        self._is_streaming = False
        self._latest_jpeg = None
        for q in list(self._stream_subscribers):
            try:
                q.put_nowait(None)
            except Exception:
                pass
        self._stream_subscribers.clear()
        self._stream_queue = None

        recording_active = self._active_recording is not None
        inference_active = bool(self._inference_thread and self._inference_thread.is_alive())
        if not recording_active and not inference_active:
            logger.info("[STREAMING] No active recording or inference — stopping capture threads")
            await asyncio.to_thread(self._stop_capture_threads)
        else:
            logger.info(f"[STREAMING] Keeping capture threads running (recording={recording_active}, inference={inference_active})")

        logger.info("[STREAMING] Stopped")
        return {"status": "stopped"}

    # ==================== Health ====================

    def health(self) -> dict:
        return {
            "running": self._is_running,
            "connected": self.is_connected,
            "camera_running": self._is_running,
            "is_running": self._is_running,
            "is_connected": self.is_connected,
            "mjpeg_thread": bool(self._mjpeg_thread and self._mjpeg_thread.is_alive()),
            "hires_thread": bool(self._hires_thread and self._hires_thread.is_alive()),
            "convert_thread": bool(self._convert_thread and self._convert_thread.is_alive()),
            "record_thread": bool(self._record_thread and self._record_thread.is_alive()),
            "recording_active": bool(self._active_recording and getattr(self._active_recording, "is_running", False)),
            "inference_thread": bool(self._inference_thread and self._inference_thread.is_alive()),
            "inference_watchdog_thread": bool(self._inference_watchdog_thread and self._inference_watchdog_thread.is_alive()),
            "streaming": self._is_streaming,
            "fps": round(self.current_fps, 1),
            "last_error": getattr(self, "_last_connection_error", None),
            "camera_id": self._active_camera_id,
            "camera_settings": self.get_current_controls(),
        }


oak_camera_service = OakCameraService()