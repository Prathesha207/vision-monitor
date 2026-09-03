"""
duck_analyzer.py
Streaming, frame-by-frame duck ID + anomaly detection (HYBRID: tracker + anchors).

Frontend pushes ONE frame at a time; each call returns THAT frame's result now.

    from duck_analyzer import DuckAnalyzer
    analyzer = DuckAnalyzer("config.yaml", expected_duck_count=18)
    result = analyzer.process_frame(frame)

IMPORTANT -- load the model ONCE, reuse for every frame
-------------------------------------------------------
The YOLO model + MediaPipe are created a single time in __init__ and then
reused by every process_frame() call -- the model is NOT reloaded per frame.
So for a stream of 50 frames through ONE analyzer, the weights load once.
If your backend instead constructs a NEW DuckAnalyzer() for every request,
that reloads the model each time; construct ONE analyzer at startup and reuse
it (see get_shared_analyzer() at the bottom for a ready-made singleton).

Design (handles MOVING ducks)
-----------------------------
- A real tracker (BoT-SORT via ultralytics model.track) follows each duck as it
  MOVES, giving each a persistent tracker-id that survives motion.
- At anchor-lock (a clean expected-count frame), each tracker-id is bound to a
  stable DISPLAY id 1..N, numbered by position. We then always follow the
  tracker-id but SHOW your 1..N number.
- "Missing" = the tracker lost that duck (truly gone/occluded), NOT "it moved".
- New tracker-ids after lock -> "added" ducks (display ids N+1, ...), gated by
  persistence so brief detector glitches don't create IDs.
- Shake stabilization: anomaly decided on the majority count over
  `anomaly_smoothing_frames`.

GPU acceleration (NEW)
----------------------
- Model is explicitly moved onto the configured device (.to(device)).
- One dummy warmup inference runs at load, so the FIRST real frame isn't slow
  (CUDA kernels/memory get initialized on the dummy frame instead).
- Optional FP16 (half precision) via `use_half: true` -- ~2x faster on GPU,
  applied to both detection and model.track(). Slightly changes confidences.
- An optional one-time GPU sanity benchmark prints FP16 TFLOP/s at startup
  (enable with `gpu_benchmark: true`).

Hand short-circuit
------------------
- MediaPipe Hands (default) runs FIRST on every frame. If a hand is detected,
  the whole duck pipeline is SKIPPED for that frame -> {"status":"HAND", ...}.

Thumbnail logic
---------------
- confirmed  : INITIAL anchor ducks, once each at lock.
- added      : a genuinely-new duck beyond anchor count, once, new id.
- other_toys : non-duck object, once when first detected, separate id space.
- missing    : NO thumbnail; only the id is reported (missing_ids).

Per-frame result: see _finish() at the bottom.
- Color coding: whole frame's boxes GREEN if NORMAL, RED if ANOMALY.
- Optional local saving via `save_local` (off by default).
"""

import os
# --- silence noisy native (C++) logs from MediaPipe / TFLite / absl ---
# These MUST be set before mediapipe / tensorflow-lite get imported, so they
# live at the very top, above the other imports. 3 = errors only.
os.environ.setdefault("GLOG_minloglevel", "3")
os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "3")

import time
import base64
from collections import deque, Counter

import warnings
import logging
# --- silence Python-level deprecation chatter (protobuf, ultralytics) ---
warnings.filterwarnings("ignore", category=UserWarning, module="google.protobuf.*")
warnings.filterwarnings("ignore", message=".*GetPrototype.*")
warnings.filterwarnings("ignore", message=".*deprecated.*")
logging.getLogger("ultralytics").setLevel(logging.ERROR)

import cv2
import numpy as np
import yaml
import torch

GREEN = (0, 200, 0)
RED = (0, 0, 255)
ORANGE = (0, 165, 255)
WHITE = (255, 255, 255)


def _gpu_sanity_benchmark(device):
    """One-time FP16 matmul benchmark to verify GPU compute capability.
    Purely diagnostic -- prints TFLOP/s. Non-fatal on any error."""
    try:
        torch.cuda.synchronize()
        n = 4096
        a = torch.randn(n, n, device=device, dtype=torch.float16)
        b = torch.randn(n, n, device=device, dtype=torch.float16)
        for _ in range(3):
            _ = a @ b
        torch.cuda.synchronize()
        iters = 10
        t0 = time.perf_counter()
        for _ in range(iters):
            _ = a @ b
        torch.cuda.synchronize()
        dt_per_iter = (time.perf_counter() - t0) / iters
        tflops = (2 * n ** 3) / dt_per_iter / 1e12
        print(f"  [GPU] FP16 matmul sanity check: {n}x{n} in "
              f"{dt_per_iter * 1000:.2f} ms/iter (~{tflops:.1f} TFLOP/s)")
        del a, b
        torch.cuda.empty_cache()
    except Exception as e:
        print(f"  [GPU] [WARN] sanity benchmark failed (non-fatal): {e}")


def get_crop(frame, xyxy, pad_frac=0.05):
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = xyxy
    bw, bh = x2 - x1, y2 - y1
    x1 = max(0, int(x1 - bw * pad_frac)); y1 = max(0, int(y1 - bh * pad_frac))
    x2 = min(w, int(x2 + bw * pad_frac)); y2 = min(h, int(y2 + bh * pad_frac))
    if x2 <= x1 or y2 <= y1:
        return None
    return frame[y1:y2, x1:x2].copy()


def color_hist(crop):
    if crop is None or crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
    cv2.normalize(hist, hist)
    return hist.flatten()


def appearance_sim(h1, h2):
    if h1 is None or h2 is None:
        return 0.0
    return float(cv2.compareHist(h1.astype("float32"), h2.astype("float32"),
                                 cv2.HISTCMP_CORREL))


def position_sim(c1, c2, diag):
    return max(0.0, 1.0 - np.linalg.norm(c1 - c2) / diag)


def center(xyxy):
    x1, y1, x2, y2 = xyxy
    return np.array([(x1 + x2) / 2.0, (y1 + y2) / 2.0])


def xyxy_to_xywh(xyxy):
    x1, y1, x2, y2 = xyxy
    return [int(x1), int(y1), int(x2 - x1), int(y2 - y1)]


def crop_to_base64(crop, quality):
    if crop is None or crop.size == 0:
        return None
    ok, buf = cv2.imencode(".jpg", crop, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        return None
    return "data:image/jpeg;base64," + base64.b64encode(buf).decode("utf-8")


def _rounded_rect(frame, p1, p2, color, radius=6):
    x1, y1 = p1; x2, y2 = p2
    radius = int(max(1, min(radius, (x2 - x1) // 2, (y2 - y1) // 2)))
    cv2.rectangle(frame, (x1 + radius, y1), (x2 - radius, y2), color, -1)
    cv2.rectangle(frame, (x1, y1 + radius), (x2, y2 - radius), color, -1)
    for cx, cy in [(x1 + radius, y1 + radius), (x2 - radius, y1 + radius),
                   (x1 + radius, y2 - radius), (x2 - radius, y2 - radius)]:
        cv2.circle(frame, (cx, cy), radius, color, -1)


def draw_box(frame, xyxy, label, color, dashed=False):
    x1, y1, x2, y2 = map(int, xyxy)
    bw, bh = x2 - x1, y2 - y1
    cl = int(max(8, min(bw, bh) * 0.25))
    t = 2
    if dashed:
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 1, cv2.LINE_AA)
    else:
        for (ax, ay, bx, by) in [
            (x1, y1, x1 + cl, y1), (x1, y1, x1, y1 + cl),
            (x2, y1, x2 - cl, y1), (x2, y1, x2, y1 + cl),
            (x1, y2, x1 + cl, y2), (x1, y2, x1, y2 - cl),
            (x2, y2, x2 - cl, y2), (x2, y2, x2, y2 - cl)]:
            cv2.line(frame, (ax, ay), (bx, by), color, t, cv2.LINE_AA)
    scale = max(0.4, frame.shape[1] / 1800.0)
    (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    pad = 5
    ty2 = y1; ty1 = y1 - th - 2 * pad; tx1 = x1; tx2 = x1 + tw + 2 * pad
    if ty1 < 0:
        ty1, ty2 = y1, y1 + th + 2 * pad
    _rounded_rect(frame, (tx1, ty1), (tx2, ty2), color, radius=6)
    cv2.putText(frame, label, (tx1 + pad, ty2 - pad - 1),
                cv2.FONT_HERSHEY_SIMPLEX, scale, WHITE, 1, cv2.LINE_AA)


def draw_banner(frame, text, color):
    w = frame.shape[1]
    scale = max(0.7, w / 1280.0)
    thick = max(2, int(w / 640))
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thick)
    pad = int(10 * scale)
    cv2.rectangle(frame, (0, 0), (tw + 2 * pad, th + 2 * pad), color, -1)
    cv2.putText(frame, text, (pad, th + pad),
                cv2.FONT_HERSHEY_SIMPLEX, scale, WHITE, thick, cv2.LINE_AA)


class DuckAnalyzer:
    def __init__(self, config_path, expected_duck_count=None):
        with open(config_path, "r") as f:
            cfg = yaml.safe_load(f)
        self.cfg = cfg
        self.model_path = cfg["model_path"]
        self.thumbnail_dir = cfg.get("thumbnail_dir")
        self.annotated_dir = cfg.get("annotated_dir")

        if expected_duck_count is not None:
            self.expected = int(expected_duck_count)
        elif cfg.get("expected_duck_count") is not None:
            self.expected = int(cfg["expected_duck_count"])
        else:
            raise ValueError("expected_duck_count not provided")

        self.duck_cls = int(cfg["duck_class_id"])
        self.other_cls = int(cfg["other_class_id"])
        self.conf = float(cfg["conf"])
        self.iou = float(cfg.get("iou", 0.7))
        self.imgsz = int(cfg.get("imgsz", 640))
        self.device = cfg.get("device", 0)
        self.warmup_frames = int(cfg.get("warmup_frames", 10))
        self.warmup_require_exact = bool(cfg.get("warmup_require_exact", True))
        self.warmup_max_frames = int(cfg.get("warmup_max_frames", 300))
        self.row_tol_frac = float(cfg["row_tolerance_frac"])
        self.jpeg_quality = int(cfg.get("jpeg_quality", 80))
        self.smooth_n = int(cfg.get("anomaly_smoothing_frames", 5))
        self.save_local = bool(cfg.get("save_local", False))
        self.tracker = cfg.get("tracker", "botsort.yaml")
        self.new_id_patience = int(cfg.get("new_id_patience", 20))
        self.missing_patience = int(cfg.get("missing_patience", 5))
        self.rebind_pos_weight = float(cfg.get("rebind_pos_weight", 0.5))
        self.rebind_app_weight = float(cfg.get("rebind_app_weight", 0.5))
        self.rebind_threshold = float(cfg.get("rebind_threshold", 0.2))
        self.reappear_patience = int(cfg.get("reappear_patience", 5))
        self.strict_count = bool(cfg.get("strict_count", True))

        # -------- GPU / precision --------
        # use_half -> FP16 inference (faster on GPU; only applies when running
        # on a CUDA device, ignored on CPU). gpu_benchmark -> one-time TFLOP/s
        # sanity print at startup.
        self.use_half = bool(cfg.get("use_half", False))
        self.gpu_benchmark = bool(cfg.get("gpu_benchmark", False))
        # resolve a device string for torch/ultralytics (e.g. "cuda:0" / "cpu")
        self._device_str = self._resolve_device(self.device)
        # FP16 only makes sense on CUDA -- force it off on CPU.
        if self.use_half and not self._device_str.startswith("cuda"):
            print("[GPU] use_half requested but device is CPU -> disabling half.")
            self.use_half = False

        # -------- hand detection (short-circuits a frame) --------
        self.hand_backend = str(cfg.get("hand_backend", "mediapipe")).lower()
        self.hand_model_path = cfg.get("hand_model_path")   # only used by "yolo"
        self.hand_conf = float(cfg.get("hand_conf", 0.5))
        self.hand_track_conf = float(cfg.get("hand_track_conf", 0.5))
        self.hand_device = cfg.get("hand_device", self.device)
        self.hand_model = None
        self._mp_hands = None
        self.hand_hold_frames = int(cfg.get("hand_hold_frames", 5))
        self._hand_hold = 0

        # other_toys persistence gate
        self.other_id_patience = int(cfg.get("other_id_patience", 8))
        self._prov_other = {}

        if self.save_local:
            if self.thumbnail_dir:
                os.makedirs(self.thumbnail_dir, exist_ok=True)
            if self.annotated_dir:
                os.makedirs(self.annotated_dir, exist_ok=True)

        # ---- load models ONCE, on device, with a warmup pass ----
        from ultralytics import YOLO

        if self.gpu_benchmark and self._device_str.startswith("cuda"):
            _gpu_sanity_benchmark(self._device_str)

        self.model = YOLO(self.model_path)
        try:
            self.model.to(self._device_str)
        except Exception as e:
            print(f"[GPU] [WARN] could not move duck model to "
                  f"{self._device_str}: {e}")
        # FP16 is applied ONCE here by half-ing the model weights, instead of
        # passing the (now-deprecated) half=/quantize= argument on every
        # predict/track call -- which floods the console with deprecation
        # warnings on newer ultralytics. Half weights on a CUDA device give the
        # same FP16 speedup with no per-call argument.
        if self.use_half and self._device_str.startswith("cuda"):
            try:
                self.model.model.half()
            except Exception as e:
                print(f"[GPU] [WARN] could not set duck model to half: {e}")
        self._log_model_device("YOLO-duck")
        # dummy warmup so the first REAL frame isn't slow (CUDA init happens here)
        self._warmup_model()

        # init the chosen hand backend
        if self.hand_backend == "mediapipe":
            try:
                import mediapipe as mp
                self._mp_hands = mp.solutions.hands.Hands(
                    static_image_mode=False,
                    max_num_hands=2,
                    min_detection_confidence=self.hand_conf,
                    min_tracking_confidence=self.hand_track_conf,
                )
            except ImportError:
                print("[WARN] mediapipe is not installed -> hand detection disabled. (Install mediapipe with 'pip install mediapipe' to enable hand detection).")
                self._mp_hands = None
        elif self.hand_backend == "yolo":
            if self.hand_model_path:
                self.hand_model = YOLO(self.hand_model_path)
                try:
                    self.hand_model.to(self._device_str)
                    if self.use_half and self._device_str.startswith("cuda"):
                        self.hand_model.model.half()
                except Exception as e:
                    print(f"[GPU] [WARN] could not move hand model to "
                          f"{self._device_str}: {e}")

        # state
        self.frame_idx = 0
        self.diag = None
        self.anchor_locked = False
        self.warmup_best = None
        self.warmup_count = 0

        self.tid_to_display = {}
        self.display_info = {}
        self.next_display_id = 1
        self.num_anchor = 0

        self.otid_to_display = {}
        self.other_info = {}
        self.next_other_display = 1
        self.num_other_anchor = 0

        self.prov_new = {}
        self.reclaim_candidates = {}

        self.confirmed_sent = set()
        self.missing_active = set()
        self.other_sent = set()

        self.count_history = deque(maxlen=self.smooth_n)
        self._last_time = None

    # ------------------------------------------------------------------ #
    #  GPU helpers                                                        #
    # ------------------------------------------------------------------ #
    @staticmethod
    def _resolve_device(device):
        """Turn a config device value (0, "0", "cuda:0", "cpu", None) into a
        torch/ultralytics device string, falling back to CPU if CUDA is
        unavailable."""
        if device is None:
            return "cuda:0" if torch.cuda.is_available() else "cpu"
        s = str(device).strip().lower()
        if s in ("cpu",):
            return "cpu"
        if s.startswith("cuda"):
            return s if torch.cuda.is_available() else "cpu"
        # bare integer like 0 / "0" -> cuda:0
        if s.isdigit():
            return f"cuda:{s}" if torch.cuda.is_available() else "cpu"
        return "cuda:0" if torch.cuda.is_available() else "cpu"

    def _log_model_device(self, label):
        try:
            actual = next(self.model.model.parameters()).device
        except Exception:
            actual = "unknown"
        print(f"[GPU-CHECK] {label:<12} -> device={actual}  half={self.use_half}")

    def _warmup_model(self):
        """One dummy inference so the first real frame doesn't pay CUDA init."""
        try:
            dummy = np.zeros((self.imgsz, self.imgsz, 3), dtype=np.uint8)
            self.model.predict(dummy, device=self._device_str,
                               imgsz=self.imgsz, verbose=False)
            print("[GPU] duck model warmup inference OK")
        except Exception as e:
            print(f"[GPU] [WARN] duck model warmup failed (non-fatal): {e}")

    def set_expected_duck_count(self, n):
        self.expected = int(n)

    # ------------------------------------------------------------------ #
    #  Hand detection                                                    #
    # ------------------------------------------------------------------ #
    def _hand_present(self, frame):
        if self._mp_hands is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = self._mp_hands.process(rgb)
            return bool(res.multi_hand_landmarks)

        if self.hand_model is not None:
            r = self.hand_model.predict(source=frame, conf=self.hand_conf,
                                        device=self._device_str,
                                        verbose=False)[0]
            if getattr(r, "boxes", None) is not None and len(r.boxes) > 0:
                return True
            kpts = getattr(r, "keypoints", None)
            if kpts is not None and getattr(kpts, "data", None) is not None \
                    and len(kpts.data) > 0:
                return True
            return False

        return False

    def _emit_thumbnail(self, event, sid, species, crop):
        b64 = crop_to_base64(crop, self.jpeg_quality)
        if self.save_local and self.thumbnail_dir and crop is not None and crop.size > 0:
            fname = f"{species}_{sid}_{event}_frame{self.frame_idx:06d}.jpg"
            cv2.imwrite(os.path.join(self.thumbnail_dir, fname), crop,
                        [int(cv2.IMWRITE_JPEG_QUALITY), self.jpeg_quality])
        return {"id": str(sid), "species": species, "event": event, "thumbnail": b64}

    def _update_slot(self, did, crop, xyxy):
        self.display_info[did]["last_crop"] = crop
        self.display_info[did]["last_hist"] = color_hist(crop)
        self.display_info[did]["last_xyxy"] = xyxy_to_xywh(xyxy)
        self.display_info[did]["gone_frames"] = 0
        self.display_info[did]["present_now"] = True

    def _collect_tracked(self, r):
        out = []
        if r.boxes is not None and len(r.boxes) > 0:
            xyxys = r.boxes.xyxy.cpu().numpy()
            clss = r.boxes.cls.cpu().numpy().astype(int)
            confs = r.boxes.conf.cpu().numpy()
            ids = (r.boxes.id.cpu().numpy().astype(int)
                   if r.boxes.id is not None else [-1] * len(clss))
            for xyxy, c, cf, tid in zip(xyxys, clss, confs, ids):
                out.append((xyxy, int(c), float(cf), int(tid)))
        return out

    def _order_by_position(self, items, frame_h):
        tol = frame_h * self.row_tol_frac
        arr = [(center(xyxy), tid, xyxy) for tid, xyxy in items]
        arr.sort(key=lambda it: it[0][1])
        rows = []
        for c, tid, xyxy in arr:
            placed = False
            for row in rows:
                if abs(row[0] - c[1]) <= tol:
                    row[1].append((c, tid, xyxy)); placed = True; break
            if not placed:
                rows.append([c[1], [(c, tid, xyxy)]])
        ordered = []
        for _, ritems in rows:
            ritems.sort(key=lambda it: it[0][0])
            ordered.extend([(tid, xyxy) for _, tid, xyxy in ritems])
        return ordered

    def _lock_anchor(self):
        _, _, frame, ducks, others = self.warmup_best
        h = frame.shape[0]
        duck_items = [(tid, xyxy) for xyxy, tid in ducks]
        for did, (tid, xyxy) in enumerate(self._order_by_position(duck_items, h), start=1):
            self.tid_to_display[tid] = did
            crop = get_crop(frame, xyxy)
            self.display_info[did] = {"last_crop": crop,
                                      "last_hist": color_hist(crop),
                                      "last_xyxy": xyxy_to_xywh(xyxy),
                                      "gone_frames": 0,
                                      "present": True}
        self.num_anchor = len(self.tid_to_display)
        self.next_display_id = self.num_anchor + 1

        other_items = [(tid, xyxy) for xyxy, tid in others]
        for oid, (tid, xyxy) in enumerate(self._order_by_position(other_items, h), start=1):
            self.otid_to_display[tid] = oid
            crop = get_crop(frame, xyxy)
            self.other_info[oid] = {"last_crop": crop,
                                    "last_hist": color_hist(crop),
                                    "last_xyxy": xyxy_to_xywh(xyxy),
                                    "gone_frames": 0}
        self.num_other_anchor = len(self.otid_to_display)
        self.next_other_display = self.num_other_anchor + 1
        self.anchor_locked = True

    def process_frame(self, frame):
        self.frame_idx += 1
        if self.diag is None:
            self.diag = float(np.hypot(frame.shape[1], frame.shape[0]))

        now = time.time()
        fps = 0.0
        if self._last_time is not None:
            dt = now - self._last_time
            fps = round(1.0 / dt, 1) if dt > 0 else 0.0
        self._last_time = now

        # ============================================================== #
        #  HAND SHORT-CIRCUIT (with debounce hold)                       #
        # ============================================================== #
        hand_now = self._hand_present(frame)
        if hand_now:
            self._hand_hold = self.hand_hold_frames
        elif self._hand_hold > 0:
            self._hand_hold -= 1

        if hand_now or self._hand_hold > 0:
            annotated = frame.copy()
            draw_banner(annotated, "HAND DETECTED", ORANGE)
            return self._finish(
                annotated, status="HAND", detected=0, fps=fps,
                hand_detected=True,
                missing_ids=[], added_ids=[], other_ids=[],
                detections=[], thumbnails=[])

        # TRACK so ducks keep ids as they move. FP16 comes from the model
        # weights being half-ed at load, so no per-call half=/quantize= arg is
        # needed (avoids the deprecation warning spam).
        r = self.model.track(source=frame, conf=self.conf, iou=self.iou,
                             imgsz=self.imgsz, persist=True,
                             tracker=self.tracker, device=self._device_str,
                             verbose=False)[0]
        dets = self._collect_tracked(r)
        ducks = [(xyxy, tid) for xyxy, c, cf, tid in dets if c == self.duck_cls and tid >= 0]
        others = [(xyxy, tid) for xyxy, c, cf, tid in dets if c == self.other_cls and tid >= 0]
        duck_conf = {tid: cf for xyxy, c, cf, tid in dets if c == self.duck_cls}
        other_conf = {tid: cf for xyxy, c, cf, tid in dets if c == self.other_cls}
        detected = len(ducks)

        annotated = frame.copy()
        thumbnails = []

        # ---- warmup ----
        if not self.anchor_locked:
            score = -abs(detected - self.expected)
            if self.warmup_best is None or score > self.warmup_best[0] \
               or (score == self.warmup_best[0] and detected > self.warmup_best[1]):
                self.warmup_best = (score, detected, frame.copy(), list(ducks), list(others))
            self.warmup_count += 1
            lock_now = False
            if self.warmup_require_exact:
                if detected == self.expected and self.warmup_count >= self.warmup_frames:
                    lock_now = True
                elif self.warmup_count >= self.warmup_max_frames:
                    lock_now = True
            else:
                if self.warmup_count >= self.warmup_frames:
                    lock_now = True
            if lock_now:
                self._lock_anchor()
                for did in range(1, self.num_anchor + 1):
                    if did not in self.confirmed_sent:
                        self.confirmed_sent.add(did)
                        thumbnails.append(self._emit_thumbnail(
                            "confirmed", did, "duck",
                            self.display_info[did]["last_crop"]))

            for xyxy, tid in ducks:
                draw_box(annotated, xyxy, "duck ?", ORANGE)
            warm_status = "WARMING"
            if others:
                warm_status = "ANOMALY"
                for xyxy, tid in others:
                    draw_box(annotated, xyxy, "other", RED)
            draw_banner(annotated, warm_status,
                        RED if warm_status == "ANOMALY" else ORANGE)
            return self._finish(
                annotated, status=warm_status, detected=detected, fps=fps,
                hand_detected=False,
                missing_ids=[], added_ids=[], other_ids=[],
                detections=[], thumbnails=thumbnails)

        # ---- map tracker ids -> display ids ----
        present_display = {}
        for d in self.display_info:
            self.display_info[d]["present_now"] = False

        used_slots = set()
        det_list = [(xyxy, tid, get_crop(frame, xyxy)) for xyxy, tid in ducks]

        pending = []
        for xyxy, tid, crop in det_list:
            did = self.tid_to_display.get(tid)
            if did is not None and 1 <= did <= self.num_anchor and did not in used_slots:
                used_slots.add(did)
                present_display[did] = (xyxy, duck_conf.get(tid, 0.0))
                self._update_slot(did, crop, xyxy)
            else:
                pending.append((xyxy, tid, crop))

        for did in used_slots:
            self.reclaim_candidates.pop(did, None)

        added_ids_this_frame = []
        unbound = []
        matched_this_frame = set()

        for xyxy, tid, crop in pending:
            dc = center(xyxy); dh = color_hist(crop)
            best_did, best_score = None, -1.0
            for d in range(1, self.num_anchor + 1):
                if d in used_slots:
                    continue
                info = self.display_info[d]
                ap = appearance_sim(dh, info.get("last_hist"))
                lx = info.get("last_xyxy")
                if lx:
                    lc = np.array([lx[0] + lx[2] / 2.0, lx[1] + lx[3] / 2.0])
                    ps = position_sim(dc, lc, self.diag)
                else:
                    ps = 0.0
                sc = self.rebind_app_weight * max(0.0, ap) + self.rebind_pos_weight * ps
                if sc > best_score:
                    best_score, best_did = sc, d

            if best_did is None or best_score < self.rebind_threshold:
                if self.strict_count:
                    unbound.append((xyxy, duck_conf.get(tid, 0.0)))
                else:
                    self.prov_new[tid] = self.prov_new.get(tid, 0) + 1
                    if self.prov_new[tid] >= self.new_id_patience:
                        did = self.next_display_id; self.next_display_id += 1
                        self.tid_to_display[tid] = did
                        self.display_info[did] = {"last_crop": crop,
                                                  "last_hist": color_hist(crop),
                                                  "last_xyxy": xyxy_to_xywh(xyxy),
                                                  "gone_frames": 0, "present": True}
                        del self.prov_new[tid]
                        present_display[did] = (xyxy, duck_conf.get(tid, 0.0))
                        added_ids_this_frame.append(did)
                continue

            if best_did in self.missing_active:
                cand = self.reclaim_candidates.get(best_did)
                if cand is not None and cand["tid"] == tid:
                    cand["count"] += 1
                else:
                    cand = {"tid": tid, "count": 1}
                    self.reclaim_candidates[best_did] = cand
                matched_this_frame.add(best_did)
                if cand["count"] >= self.reappear_patience:
                    used_slots.add(best_did)
                    self.tid_to_display[tid] = best_did
                    present_display[best_did] = (xyxy, duck_conf.get(tid, 0.0))
                    self._update_slot(best_did, crop, xyxy)
                    del self.reclaim_candidates[best_did]
            else:
                used_slots.add(best_did)
                self.tid_to_display[tid] = best_did
                present_display[best_did] = (xyxy, duck_conf.get(tid, 0.0))
                self._update_slot(best_did, crop, xyxy)

        for did in list(self.reclaim_candidates.keys()):
            if did not in matched_this_frame:
                del self.reclaim_candidates[did]

        # ---- shake-smoothed anomaly ----
        self.count_history.append(detected)
        smoothed = Counter(self.count_history).most_common(1)[0][0]
        buffer_ready = len(self.count_history) >= self.count_history.maxlen
        reasons = []
        if buffer_ready:
            if smoothed < self.expected:
                reasons.append("too_few_ducks")
            if smoothed > self.expected:
                reasons.append("too_many_ducks")

        # ---- added thumbnails (once each) ----
        for did in added_ids_this_frame:
            if did not in self.confirmed_sent:
                self.confirmed_sent.add(did)
                thumbnails.append(self._emit_thumbnail(
                    "added", did, "duck", self.display_info[did]["last_crop"]))

        # ---- missing: anchor id not present this frame (NO thumbnail) ----
        missing_ids = []
        for did in range(1, self.num_anchor + 1):
            if did in present_display:
                self.display_info[did]["gone_frames"] = 0
                self.missing_active.discard(did)
                self.display_info[did].pop("frozen_xyxy", None)
            else:
                self.display_info[did]["gone_frames"] = \
                    self.display_info[did].get("gone_frames", 0) + 1
                if self.display_info[did]["gone_frames"] >= self.missing_patience:
                    if did not in self.missing_active:
                        self.missing_active.add(did)
                        self.display_info[did]["frozen_xyxy"] = \
                            self.display_info[did].get("last_xyxy")
                    missing_ids.append(did)

        # ---- other species (separate id space, thumbnail once) ----
        present_other = {}
        other_ids = []
        used_oslots = set()
        matched_prov_keys = set()
        for xyxy, tid in others:
            crop = get_crop(frame, xyxy)
            dc = center(xyxy); dh = color_hist(crop)
            best_oid, best_score = None, -1.0
            for o in range(1, self.num_other_anchor + 1):
                if o in used_oslots or o not in self.other_info:
                    continue
                info = self.other_info[o]
                ap = appearance_sim(dh, info.get("last_hist"))
                lx = info.get("last_xyxy")
                if lx:
                    lc = np.array([lx[0] + lx[2] / 2.0, lx[1] + lx[3] / 2.0])
                    ps = position_sim(dc, lc, self.diag)
                else:
                    ps = 0.0
                sc = self.rebind_app_weight * max(0.0, ap) + self.rebind_pos_weight * ps
                if sc > best_score:
                    best_score, best_oid = sc, o

            if best_oid is not None and best_score >= self.rebind_threshold:
                oid = best_oid
                self.other_info[oid]["last_crop"] = crop
                self.other_info[oid]["last_hist"] = color_hist(crop)
                self.other_info[oid]["last_xyxy"] = xyxy_to_xywh(xyxy)
                used_oslots.add(oid)
                present_other[oid] = (xyxy, other_conf.get(tid, 0.0))
                other_ids.append(oid)
                if oid not in self.other_sent:
                    self.other_sent.add(oid)
                    thumbnails.append(self._emit_thumbnail(
                        "other_present", oid, "other_toys", crop))
            else:
                key = tid if tid >= 0 else (int(dc[0] // 40), int(dc[1] // 40))
                prov = self._prov_other.get(key)
                if prov is not None and prov.get("tid") == tid:
                    prov["count"] += 1
                else:
                    prov = {"tid": tid, "count": 1}
                    self._prov_other[key] = prov
                prov["last"] = (xyxy, crop, other_conf.get(tid, 0.0))
                matched_prov_keys.add(key)

                if prov["count"] >= self.other_id_patience:
                    oid = self.next_other_display
                    self.next_other_display += 1
                    self.other_info[oid] = {"last_crop": crop,
                                            "last_hist": color_hist(crop),
                                            "last_xyxy": xyxy_to_xywh(xyxy),
                                            "gone_frames": 0}
                    if oid > self.num_other_anchor:
                        self.num_other_anchor = oid
                    del self._prov_other[key]
                    used_oslots.add(oid)
                    present_other[oid] = (xyxy, other_conf.get(tid, 0.0))
                    other_ids.append(oid)
                    if oid not in self.other_sent:
                        self.other_sent.add(oid)
                        thumbnails.append(self._emit_thumbnail(
                            "other_present", oid, "other_toys", crop))

        for key in list(self._prov_other.keys()):
            if key not in matched_prov_keys:
                del self._prov_other[key]

        if len(present_other) > 0:
            reasons.append("other_species_present")

        is_anomaly = len(reasons) > 0
        status = "ANOMALY" if is_anomaly else "NORMAL"

        box_color = RED if is_anomaly else GREEN

        detections = []
        for did, (xyxy, cf) in present_display.items():
            x1, y1, x2, y2 = [int(v) for v in xyxy]
            detections.append({"bbox": [x1, y1, x2, y2], "id": int(did),
                               "species": "duck", "confidence": round(float(cf), 4)})
            draw_box(annotated, xyxy, f"#{did} {int(cf * 100)}%", box_color)

        for xyxy, cf in unbound:
            x1, y1, x2, y2 = [int(v) for v in xyxy]
            detections.append({"bbox": [x1, y1, x2, y2], "id": -1,
                               "species": "duck", "confidence": round(float(cf), 4)})
            draw_box(annotated, xyxy, "duck", box_color)

        for oid, (xyxy, cf) in present_other.items():
            x1, y1, x2, y2 = [int(v) for v in xyxy]
            detections.append({"bbox": [x1, y1, x2, y2], "id": int(oid),
                               "species": "other_toys", "confidence": round(float(cf), 4)})
            draw_box(annotated, xyxy, f"other #{oid} {int(cf * 100)}%", box_color)

        for did in missing_ids:
            lx = self.display_info[did].get("frozen_xyxy") \
                or self.display_info[did].get("last_xyxy")
            if lx:
                x, y, w, hh = lx
                draw_box(annotated, [x, y, x + w, y + hh],
                         f"#{did} MISSING", box_color, dashed=True)

        draw_banner(annotated, status, box_color)

        return self._finish(
            annotated, status=status, detected=detected, fps=fps,
            hand_detected=False,
            missing_ids=missing_ids, added_ids=added_ids_this_frame,
            other_ids=other_ids, detections=detections, thumbnails=thumbnails)

    def _finish(self, annotated, status, detected, fps, hand_detected,
                missing_ids, added_ids, other_ids, detections, thumbnails):
        if self.save_local and self.annotated_dir:
            cv2.imwrite(os.path.join(self.annotated_dir,
                        f"frame_{self.frame_idx:05d}.jpg"), annotated)
        return {
            "frame": self.frame_idx,
            "fps": fps,
            "status": status,
            "hand_detected": hand_detected,
            "detected_duck_count": detected,
            "expected_duck_count": self.expected,
            "missing_count": len(missing_ids),
            "missing_ids": missing_ids,
            "added_count": len(added_ids),
            "added_ids": added_ids,
            "other_count": len(other_ids),
            "other_ids": other_ids,
            "detections": detections,
            "thumbnails": thumbnails,
            "annotated_frame": annotated,
        }

    def close(self):
        if self._mp_hands is not None:
            try:
                self._mp_hands.close()
            except Exception:
                pass


# ---------------------------------------------------------------------- #
#  Optional singleton: load the model ONCE for the whole process.        #
#  If your backend calls this instead of constructing DuckAnalyzer()     #
#  per request, the weights load a single time and every request reuses  #
#  the same in-memory model.                                             #
# ---------------------------------------------------------------------- #
_SHARED_ANALYZER = None


def get_shared_analyzer(config_path="config.yaml", expected_duck_count=None):
    global _SHARED_ANALYZER
    if _SHARED_ANALYZER is None:
        _SHARED_ANALYZER = DuckAnalyzer(config_path, expected_duck_count)
    elif expected_duck_count is not None:
        _SHARED_ANALYZER.set_expected_duck_count(expected_duck_count)
    return _SHARED_ANALYZER


if __name__ == "__main__":
    import sys
    cfg_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    video = sys.argv[2] if len(sys.argv) > 2 else None
    expected = int(sys.argv[3]) if len(sys.argv) > 3 else None
    analyzer = DuckAnalyzer(cfg_path, expected_duck_count=expected)
    if video:
        cap = cv2.VideoCapture(video)
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            res = analyzer.process_frame(frame)
            if res["thumbnails"]:
                print(f"frame {res['frame']} events:",
                      [(t["event"], t["id"]) for t in res["thumbnails"]])
            if res["frame"] % 30 == 0:
                print(f"frame {res['frame']}: {res['status']}, "
                      f"ducks={res['detected_duck_count']}, "
                      f"missing={res['missing_ids']}, added={res['added_ids']}, "
                      f"other={res['other_ids']}, hand={res['hand_detected']}, "
                      f"fps={res['fps']}")
        cap.release()
        print("done")
    else:
        print("Loaded. Feed frames via analyzer.process_frame(frame).")
