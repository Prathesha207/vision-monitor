
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


# ---------------------------------------------------------------------- #
#  Richer appearance signature for occlusion re-identification.          #
#  A single HS histogram is weak between similarly-coloured rubber       #
#  ducks, so a reappearing duck often matches several slots almost       #
#  equally and position becomes the (unreliable) tiebreaker. We add two  #
#  cheap extra cues -- mean Lab colour and aspect ratio -- and combine   #
#  them into one similarity score. Purely internal; no schema change.    #
# ---------------------------------------------------------------------- #
def appearance_signature(crop):
    """Return a dict signature {hist, lab, ar} for a crop, or None."""
    if crop is None or crop.size == 0:
        return None
    hist = color_hist(crop)
    try:
        lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).reshape(-1, 3).mean(axis=0)
    except Exception:
        lab = None
    h, w = crop.shape[:2]
    ar = (w / h) if h > 0 else 0.0
    return {"hist": hist, "lab": lab, "ar": float(ar)}


def signature_sim(s1, s2):
    """Blended appearance similarity in [0,1] from two signatures.
    Weights: histogram 0.6, Lab colour 0.3, aspect ratio 0.1."""
    if s1 is None or s2 is None:
        return 0.0
    # histogram correlation -> map [-1,1] to [0,1]
    hs = appearance_sim(s1.get("hist"), s2.get("hist"))
    hs = max(0.0, hs)
    # Lab distance -> similarity (Lab spans ~0..255; 60 is a soft scale)
    if s1.get("lab") is not None and s2.get("lab") is not None:
        d = float(np.linalg.norm(s1["lab"] - s2["lab"]))
        ls = max(0.0, 1.0 - d / 60.0)
    else:
        ls = 0.0
    # aspect-ratio similarity
    a1, a2 = s1.get("ar", 0.0), s2.get("ar", 0.0)
    if a1 > 0 and a2 > 0:
        rs = max(0.0, 1.0 - abs(a1 - a2) / max(a1, a2))
    else:
        rs = 0.0
    return 0.6 * hs + 0.3 * ls + 0.1 * rs


def _hungarian(cost):
    """Optimal assignment minimizing total cost for a rectangular matrix.
    Uses scipy if available, otherwise a self-contained O(n^3) solver so we
    add NO hard dependency. Returns (row_idx, col_idx) like scipy."""
    cost = np.asarray(cost, dtype=float)
    if cost.size == 0:
        return np.array([], dtype=int), np.array([], dtype=int)
    try:
        from scipy.optimize import linear_sum_assignment
        return linear_sum_assignment(cost)
    except Exception:
        pass
    # --- pure-numpy Hungarian (square-padded) ---
    n_r, n_c = cost.shape
    n = max(n_r, n_c)
    big = cost.max() + 1.0 if cost.size else 1.0
    C = np.full((n, n), big, dtype=float)
    C[:n_r, :n_c] = cost
    u = np.zeros(n + 1); v = np.zeros(n + 1)
    p = np.zeros(n + 1, dtype=int); way = np.zeros(n + 1, dtype=int)
    for i in range(1, n + 1):
        p[0] = i; j0 = 0
        minv = np.full(n + 1, np.inf); used = np.zeros(n + 1, dtype=bool)
        while True:
            used[j0] = True
            i0 = p[j0]; delta = np.inf; j1 = -1
            for j in range(1, n + 1):
                if not used[j]:
                    cur = C[i0 - 1, j - 1] - u[i0] - v[j]
                    if cur < minv[j]:
                        minv[j] = cur; way[j] = j0
                    if minv[j] < delta:
                        delta = minv[j]; j1 = j
            for j in range(n + 1):
                if used[j]:
                    u[p[j]] += delta; v[j] -= delta
                else:
                    minv[j] -= delta
            j0 = j1
            if p[j0] == 0:
                break
        while True:
            j1 = way[j0]; p[j0] = p[j1]; j0 = j1
            if j0 == 0:
                break
    rows, cols = [], []
    for j in range(1, n + 1):
        i = p[j]
        if i <= n_r and j <= n_c:
            rows.append(i - 1); cols.append(j - 1)
    order = np.argsort(rows)
    return np.array(rows)[order], np.array(cols)[order]


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
        # When ducks are CLOSE together, position can't tell them apart, so IDs
        # swap. In that case lean on appearance (colour) instead. This is the
        # appearance weight used specifically for a crowded match; the normal
        # weights above are used when the candidate slot is well separated.
        # crowd_dist_frac: how close (as a fraction of the frame diagonal) two
        # candidates must be to count as "crowded".
        self.rebind_app_weight_crowded = float(
            cfg.get("rebind_app_weight_crowded", 0.8))
        self.crowd_dist_frac = float(cfg.get("crowd_dist_frac", 0.06))
        self.debug = bool(cfg.get("debug", False))
        self.rebind_threshold = float(cfg.get("rebind_threshold", 0.2))
        self.reappear_patience = int(cfg.get("reappear_patience", 5))
        self.strict_count = bool(cfg.get("strict_count", True))
        # A rebind must ALSO clear a minimum APPEARANCE similarity, not just the
        # blended score. Without this a wrong-colour duck that merely drifts
        # over a missing duck's position reclaims the slot on position alone
        # (0.5*pos easily beats rebind_threshold), masking the loss and forcing
        # a false NORMAL. Set 0 to disable. ~0.35 rejects clearly-different ducks.
        self.rebind_min_app = float(cfg.get("rebind_min_app", 0.35))

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

        # -------- hand ROI (draw once, then fixed) --------
        # If a ROI is set, the hand short-circuit fires ONLY when a detected
        # hand overlaps this rectangle -- a hand elsewhere in the frame is
        # ignored. The ROI is stored as [x, y, w, h] in a small JSON file
        # (roi_path) so it persists across runs: draw it one time (see
        # calibrate_roi / draw_roi.py) and every later run reuses it. With no
        # ROI file present, behaviour is unchanged (whole frame counts).
        self.roi_path = cfg.get("roi_path", "hand_roi.json")
        self.roi = None  # (x, y, w, h) or None
        self._load_roi()

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
                raise ImportError(
                    "hand_backend is 'mediapipe' but the mediapipe package is "
                    "not installed. Run: pip install mediapipe  (or set "
                    "hand_backend: yolo in config.yaml to use a YOLO model).")
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
    #  Hand ROI helpers                                                   #
    # ------------------------------------------------------------------ #
    def _load_roi(self):
        """Load the fixed hand ROI from roi_path if it exists."""
        try:
            import json
            if self.roi_path and os.path.exists(self.roi_path):
                with open(self.roi_path, "r") as f:
                    d = json.load(f)
                r = d.get("roi", d)  # accept {"roi":[...]} or a bare [...]
                if r and len(r) == 4:
                    self.roi = tuple(int(v) for v in r)
                    print(f"[ROI] loaded hand ROI {self.roi} from {self.roi_path}")
        except Exception as e:
            print(f"[ROI] [WARN] could not load ROI from {self.roi_path}: {e}")
            self.roi = None

    def set_roi(self, x, y, w, h, save=True):
        """Set the ROI programmatically and (optionally) persist it."""
        self.roi = (int(x), int(y), int(w), int(h))
        if save and self.roi_path:
            try:
                import json
                with open(self.roi_path, "w") as f:
                    json.dump({"roi": list(self.roi)}, f)
                print(f"[ROI] saved hand ROI {self.roi} to {self.roi_path}")
            except Exception as e:
                print(f"[ROI] [WARN] could not save ROI: {e}")

    def calibrate_roi(self, frame, window="Draw hand ROI - drag, ENTER to accept"):
        """Open an interactive window on `frame`, let the user drag ONE
        rectangle, save it to roi_path, and set it. Blocking; call once at
        setup. Returns the (x,y,w,h) chosen, or None if cancelled."""
        try:
            box = cv2.selectROI(window, frame, showCrosshair=True, fromCenter=False)
            cv2.destroyWindow(window)
        except Exception as e:
            print(f"[ROI] [WARN] interactive selectROI failed: {e}")
            return None
        x, y, w, h = [int(v) for v in box]
        if w <= 0 or h <= 0:
            print("[ROI] selection cancelled / empty -> ROI unchanged.")
            return None
        self.set_roi(x, y, w, h, save=True)
        return self.roi

    @staticmethod
    def _rects_overlap(a, b):
        """True if rectangles a,b (each x,y,w,h) share any area."""
        ax, ay, aw, ah = a; bx, by, bw, bh = b
        return not (ax + aw <= bx or bx + bw <= ax or
                    ay + ah <= by or by + bh <= ay)

    # ------------------------------------------------------------------ #
    #  Hand detection                                                    #
    # ------------------------------------------------------------------ #
    def _hand_boxes(self, frame):
        """Return a list of hand bounding boxes [(x,y,w,h), ...] for this
        frame (empty if none). Works for both mediapipe and yolo backends."""
        boxes = []
        h, w = frame.shape[:2]

        if self._mp_hands is not None:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            res = self._mp_hands.process(rgb)
            if res.multi_hand_landmarks:
                for hand_lms in res.multi_hand_landmarks:
                    xs = [lm.x for lm in hand_lms.landmark]
                    ys = [lm.y for lm in hand_lms.landmark]
                    x1 = max(0, int(min(xs) * w)); y1 = max(0, int(min(ys) * h))
                    x2 = min(w, int(max(xs) * w)); y2 = min(h, int(max(ys) * h))
                    if x2 > x1 and y2 > y1:
                        boxes.append((x1, y1, x2 - x1, y2 - y1))
            return boxes

        if self.hand_model is not None:
            r = self.hand_model.predict(source=frame, conf=self.hand_conf,
                                        device=self._device_str,
                                        verbose=False)[0]
            if getattr(r, "boxes", None) is not None and len(r.boxes) > 0:
                for xyxy in r.boxes.xyxy.cpu().numpy():
                    x1, y1, x2, y2 = [int(v) for v in xyxy]
                    boxes.append((x1, y1, x2 - x1, y2 - y1))
            else:
                kpts = getattr(r, "keypoints", None)
                if kpts is not None and getattr(kpts, "data", None) is not None \
                        and len(kpts.data) > 0:
                    for kp in kpts.data.cpu().numpy():
                        pts = kp[kp[:, 2] > 0.1] if kp.shape[-1] >= 3 else kp
                        if len(pts) == 0:
                            continue
                        x1 = max(0, int(pts[:, 0].min())); y1 = max(0, int(pts[:, 1].min()))
                        x2 = min(w, int(pts[:, 0].max())); y2 = min(h, int(pts[:, 1].max()))
                        if x2 > x1 and y2 > y1:
                            boxes.append((x1, y1, x2 - x1, y2 - y1))
            return boxes

        return boxes

    def _hand_present(self, frame):
        """True if a hand should short-circuit this frame. If a ROI is set, only
        a hand OVERLAPPING the ROI counts; otherwise any detected hand counts."""
        boxes = self._hand_boxes(frame)
        if not boxes:
            return False
        if self.roi is None:
            return True
        return any(self._rects_overlap(b, self.roi) for b in boxes)

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
        # Maintain a rolling appearance template (EMA of the signature) so
        # re-identification after occlusion matches a STABLE signature rather
        # than a single possibly-blurred last frame. Improves wrong-id cases.
        sig = appearance_signature(crop)
        self.display_info[did]["last_sig"] = sig
        prev = self.display_info[did].get("sig_ema")
        if sig is not None:
            if prev is None:
                self.display_info[did]["sig_ema"] = sig
            else:
                a = 0.7  # weight on the existing template
                ema = dict(prev)
                if prev.get("hist") is not None and sig.get("hist") is not None:
                    ema["hist"] = a * prev["hist"] + (1 - a) * sig["hist"]
                if prev.get("lab") is not None and sig.get("lab") is not None:
                    ema["lab"] = a * prev["lab"] + (1 - a) * sig["lab"]
                ema["ar"] = a * prev.get("ar", sig["ar"]) + (1 - a) * sig["ar"]
                self.display_info[did]["sig_ema"] = ema

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
            sig = appearance_signature(crop)
            self.display_info[did] = {"last_crop": crop,
                                      "last_hist": color_hist(crop),
                                      "last_sig": sig,
                                      "sig_ema": sig,
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
            if self.roi is not None:
                rx, ry, rw, rh = self.roi
                cv2.rectangle(annotated, (rx, ry), (rx + rw, ry + rh), ORANGE, 2)
            draw_banner(annotated, "HAND DETECTED", ORANGE)
            return self._finish(
                annotated, status="HAND", detected=0, fps=fps,
                hand_detected=True,
                missing_ids=[], added_ids=[], other_ids=[],
                detections=[], thumbnails=[], reasons=[])

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
            # FIX: seed the smoothing buffer DURING warmup too, so by the time
            # we lock the anomaly decision already has a full window to vote on.
            # Without this the buffer only starts filling after lock, forcing a
            # spurious NORMAL for `anomaly_smoothing_frames` frames before the
            # real too_many/too_few verdict can appear.
            self.count_history.append(detected)
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

            # FIX (Issue 2): populate detections during WARMING so the frontend
            # can render provisional boxes on its own canvas instead of only
            # having them burned into annotated_frame. id=-1 + provisional flag.
            warmup_detections = [
                {"bbox": [int(v) for v in xyxy],
                 "id": -1,
                 "species": "duck",
                 "confidence": round(float(duck_conf.get(tid, 0.0)), 4),
                 "provisional": True}
                for xyxy, tid in ducks
            ]

            for xyxy, tid in ducks:
                draw_box(annotated, xyxy, "duck ?", ORANGE)
            warm_status = "WARMING"
            if others:
                warm_status = "ANOMALY"
                for xyxy, tid in others:
                    draw_box(annotated, xyxy, "other", RED)
            draw_banner(annotated, warm_status,
                        RED if warm_status == "ANOMALY" else ORANGE)
            # If we just locked on this same frame, fall through so the locked
            # pipeline (below) runs on the next frame; here we still return the
            # warmup payload but with anchor_locked now reflecting reality.
            return self._finish(
                annotated, status=warm_status, detected=detected, fps=fps,
                hand_detected=False,
                missing_ids=[], added_ids=[], other_ids=[],
                detections=warmup_detections, thumbnails=thumbnails,
                reasons=(["other_species_present"] if others else []))

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

        # ---- GLOBAL rebind assignment (fixes wrong-id-after-occlusion) ----
        # Instead of letting each pending duck greedily grab its own best free
        # slot (order-dependent -> one duck steals a slot another duck matched
        # far better), we score EVERY pending duck against EVERY free slot and
        # solve one optimal assignment that maximizes total similarity. Score
        # uses the richer signature (hist+Lab+aspect) EMA template + position.
        free_slots = [d for d in range(1, self.num_anchor + 1) if d not in used_slots]
        # precompute per-pending cues
        pend_meta = []
        for xyxy, tid, crop in pending:
            pend_meta.append((xyxy, tid, crop, center(xyxy), appearance_signature(crop)))

        sim_matrix = np.zeros((len(pend_meta), len(free_slots)), dtype=float)
        app_matrix = np.zeros((len(pend_meta), len(free_slots)), dtype=float)

        # Precompute each free slot's last-known center so we can measure how
        # crowded the neighbourhood of a candidate match is.
        slot_centers = {}
        for d in free_slots:
            lx = self.display_info[d].get("last_xyxy")
            if lx:
                slot_centers[d] = np.array([lx[0] + lx[2] / 2.0,
                                            lx[1] + lx[3] / 2.0])
        crowd_dist = self.crowd_dist_frac * self.diag

        for pi, (xyxy, tid, crop, dc, dsig) in enumerate(pend_meta):
            for sj, d in enumerate(free_slots):
                info = self.display_info[d]
                # appearance against the rolling template (fallback to last)
                ap = signature_sim(dsig, info.get("sig_ema") or info.get("last_sig"))
                app_matrix[pi, sj] = ap
                lx = info.get("last_xyxy")
                if lx:
                    lc = np.array([lx[0] + lx[2] / 2.0, lx[1] + lx[3] / 2.0])
                    ps = position_sim(dc, lc, self.diag)
                else:
                    lc = None
                    ps = 0.0

                # crowded? -> another free slot sits within crowd_dist of THIS
                # slot, so position alone can't disambiguate. Lean on appearance.
                crowded = False
                if lc is not None:
                    for d2, c2 in slot_centers.items():
                        if d2 != d and np.linalg.norm(lc - c2) < crowd_dist:
                            crowded = True
                            break
                if crowded:
                    aw = self.rebind_app_weight_crowded
                    pw = 1.0 - aw
                else:
                    aw = self.rebind_app_weight
                    pw = self.rebind_pos_weight
                sim_matrix[pi, sj] = aw * ap + pw * ps

        # solve: Hungarian minimizes cost, so cost = -similarity
        assigned_slot = {}   # pending index -> slot did (best global match)
        assigned_score = {}  # pending index -> that match's similarity
        assigned_app = {}    # pending index -> that match's raw appearance sim
        if len(pend_meta) and len(free_slots):
            rows, cols = _hungarian(-sim_matrix)
            for pi, sj in zip(rows, cols):
                assigned_slot[int(pi)] = free_slots[int(sj)]
                assigned_score[int(pi)] = float(sim_matrix[int(pi), int(sj)])
                assigned_app[int(pi)] = float(app_matrix[int(pi), int(sj)])

        for pi, (xyxy, tid, crop, dc, dsig) in enumerate(pend_meta):
            best_did = assigned_slot.get(pi)
            best_score = assigned_score.get(pi, -1.0)
            best_app = assigned_app.get(pi, 0.0)
            if self.debug:
                _crowded = False
                if best_did is not None and best_did in slot_centers:
                    _lc = slot_centers[best_did]
                    for _d2, _c2 in slot_centers.items():
                        if _d2 != best_did and np.linalg.norm(_lc - _c2) < crowd_dist:
                            _crowded = True
                            break
                print(f"[DBG f{self.frame_idx}] pending tid={tid} -> slot #{best_did} "
                      f"score={best_score:.3f} app={best_app:.3f} crowded={_crowded} "
                      f"missing_active={best_did in self.missing_active}")

            # Two different situations need different strictness:
            #  * Reclaiming a slot whose duck is in a MISSING episode -> be
            #    strict: also require appearance agreement, so a different duck
            #    drifting over the gap can't silently steal the slot and mask
            #    the loss (that caused the false NORMAL).
            #  * An ordinary free slot (its duck merely MOVED, not missing) ->
            #    the blended score threshold alone is enough. Do NOT also demand
            #    the appearance gate here, or a duck that changed pose/lighting
            #    gets wrongly orphaned to id:-1 while its slot goes MISSING --
            #    which shows up as a "duck" box with no id AND a false ANOMALY
            #    even though every duck is present.
            reject = (best_did is None or best_score < self.rebind_threshold)
            if not reject and best_did in self.missing_active:
                if best_app < self.rebind_min_app:
                    reject = True

            if reject:
                if self.strict_count:
                    unbound.append((xyxy, duck_conf.get(tid, 0.0)))
                else:
                    self.prov_new[tid] = self.prov_new.get(tid, 0) + 1
                    if self.prov_new[tid] >= self.new_id_patience:
                        did = self.next_display_id; self.next_display_id += 1
                        self.tid_to_display[tid] = did
                        _sig = appearance_signature(crop)
                        self.display_info[did] = {"last_crop": crop,
                                                  "last_hist": color_hist(crop),
                                                  "last_sig": _sig,
                                                  "sig_ema": _sig,
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
        # NOTE: the buffer is already seeded during warmup, so at lock it holds
        # a full window. We no longer wait for buffer_ready before deciding --
        # we vote on whatever is in the window (min a couple of frames) so a
        # genuine count mismatch is flagged immediately after lock instead of
        # showing a spurious NORMAL for `anomaly_smoothing_frames` frames.
        self.count_history.append(detected)
        smoothed = Counter(self.count_history).most_common(1)[0][0]
        reasons = []
        if len(self.count_history) >= min(3, self.count_history.maxlen):
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

        # ---- missing: anchor id not present this frame ----
        # FIX (Issue 3): report the id in missing_ids AS SOON AS it is absent
        # (gone_frames >= 1), not only after missing_patience. missing_patience
        # now only gates the missing_active episode / frozen box, so the id no
        # longer vanishes from the payload during the patience window.
        missing_ids = []
        for did in range(1, self.num_anchor + 1):
            if did in present_display:
                self.display_info[did]["gone_frames"] = 0
                self.missing_active.discard(did)
                self.display_info[did].pop("frozen_xyxy", None)
            else:
                self.display_info[did]["gone_frames"] = \
                    self.display_info[did].get("gone_frames", 0) + 1
                # report immediately so the UI card never disappears
                missing_ids.append(did)
                if self.display_info[did]["gone_frames"] >= self.missing_patience:
                    if did not in self.missing_active:
                        self.missing_active.add(did)
                        self.display_info[did]["frozen_xyxy"] = \
                            self.display_info[did].get("last_xyxy")

        # A KNOWN anchor duck being absent is itself an anomaly, regardless of
        # what the shake-smoothed total count says. The count check above can
        # miss this: if one duck vanishes but a spurious box (reflection, tray
        # edge, mis-detection) keeps the TOTAL at `expected`, the count matches
        # and status would wrongly read NORMAL. For a QC pipeline a missing
        # duck must never be NORMAL, so drive the verdict off missing state too.
        #
        # We gate on missing_active (duck gone for >= missing_patience frames)
        # rather than the raw missing_ids, so a duck that is only BRIEFLY hidden
        # (behind a hand / another duck) and comes right back does not flash a
        # false ANOMALY. The id is still reported in missing_ids immediately;
        # only the anomaly VERDICT waits for the patience window.
        if self.missing_active and "missing_duck" not in reasons:
            reasons.append("missing_duck")

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
                               "species": "duck", "confidence": round(float(cf), 4),
                               "status": "present"})
            draw_box(annotated, xyxy, f"#{did} {int(cf * 100)}%", box_color)

        for xyxy, cf in unbound:
            x1, y1, x2, y2 = [int(v) for v in xyxy]
            detections.append({"bbox": [x1, y1, x2, y2], "id": -1,
                               "species": "duck", "confidence": round(float(cf), 4),
                               "status": "unbound"})
            draw_box(annotated, xyxy, "duck", box_color)

        for oid, (xyxy, cf) in present_other.items():
            x1, y1, x2, y2 = [int(v) for v in xyxy]
            detections.append({"bbox": [x1, y1, x2, y2], "id": int(oid),
                               "species": "other_toys", "confidence": round(float(cf), 4),
                               "status": "present"})
            draw_box(annotated, xyxy, f"other #{oid} {int(cf * 100)}%", box_color)

        # FIX (Issue 1): include missing ducks in detections with their last
        # known box + status="missing", so the frontend keeps the card in its
        # correct ID position and can show the frozen crop, instead of the id
        # only appearing as a bare number in missing_ids.
        for did in missing_ids:
            lx = self.display_info[did].get("frozen_xyxy") \
                or self.display_info[did].get("last_xyxy")
            if lx:
                x, y, w, hh = lx
                detections.append({"bbox": [int(x), int(y), int(x + w), int(y + hh)],
                                   "id": int(did), "species": "duck",
                                   "confidence": 0.0, "status": "missing"})
                # only draw the frozen dashed box once the episode is confirmed
                if did in self.missing_active:
                    draw_box(annotated, [x, y, x + w, y + hh],
                             f"#{did} MISSING", box_color, dashed=True)

        draw_banner(annotated, status, box_color)

        if self.debug:
            print(f"[DBG f{self.frame_idx}] status={status} detected={detected} "
                  f"present={sorted(present_display.keys())} pending={len(pend_meta)} "
                  f"unbound={len(unbound)} missing={missing_ids} reasons={reasons}")
        return self._finish(
            annotated, status=status, detected=detected, fps=fps,
            hand_detected=False,
            missing_ids=missing_ids, added_ids=added_ids_this_frame,
            other_ids=other_ids, detections=detections, thumbnails=thumbnails,
            reasons=reasons)

    def _finish(self, annotated, status, detected, fps, hand_detected,
                missing_ids, added_ids, other_ids, detections, thumbnails,
                reasons=None):
        if reasons is None:
            reasons = []
        if self.save_local and self.annotated_dir:
            cv2.imwrite(os.path.join(self.annotated_dir,
                        f"frame_{self.frame_idx:05d}.jpg"), annotated)
        return {
            "frame": self.frame_idx,
            "fps": fps,
            "status": status,
            "anchor_locked": self.anchor_locked,   # FIX (Issue 4): expose state
            "reasons": reasons,                    # FIX (Issue 4): expose reasons
            "hand_detected": hand_detected,
            "detected_duck_count": detected,
            "expected_duck_count": self.expected,
            "missing_count": len(missing_ids),
            "missing_ids": missing_ids,
            "added_count": len(added_ids),
            "added_ids": added_ids,
            "other_count": len(other_ids),
            "detected_other_toy_count": len(other_ids),  # compatibility alias
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