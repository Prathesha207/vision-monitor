"""
traycount/pipeline.py - TrayCounter, the single entry point of TrayCount.

    from traycount import TrayCounter
    tc = TrayCounter("config.yaml")      # paths in the config are relative to the config file
    tc.load_model()                      # all models onto the GPU (stops if there is no CUDA GPU)
    tc.reset(fps=30)                     # a new video / camera session; fps = frames per second you will send
    for frame in frames:                 # BGR images (OpenCV), every frame in order
        result = tc.process_frame(frame) # status, counts, IDs, thumbnails, toys, tray, timing (see process_frame)
        picture = tc.draw(frame)         # optional: the same frame with our overlay drawn on it
    tc.templates()                       # every toy's template picture (made once: warm-up end / when a toy is added)
    tc.unload_model()                    # frees the GPU

The decisions (IDs, pause, missing toys) are made in traycount/_runtime.py, copied unchanged from the project's
runtime; this file loads the models and turns the runtime's state into the results (status, IDs, templates).
"""
import base64
import gc
import os
import time
from collections import deque
from datetime import datetime

import cv2
import numpy as np
import yaml

from . import _runtime as rt
from ._anomaly import MaskAnomalies
from ._helpers import Embedder

PAUSED_STATES = ("hand", "settle", "confirm")              # hand in the tray / toys settling / toys being found again


class TrayCounter:
    def __init__(self, config_path):
        self.config_path = os.path.abspath(config_path)
        self.base = os.path.dirname(self.config_path)
        with open(self.config_path) as fh:
            cfg = yaml.safe_load(fh)
        W = cfg.pop("weights")
        path = lambda k: os.path.normpath(os.path.join(self.base, W[k]))
        cfg["runtime"]["model"] = path("toy_model")
        cfg["hand_person_model"] = path("person_model")
        cfg["hand_landmarker"] = path("hand_model")
        cfg["reid_embed_weights"] = path("embed_model")
        missing = [p for p in (cfg["runtime"]["model"], cfg["hand_person_model"], cfg["hand_landmarker"],
                               cfg["reid_embed_weights"]) if not os.path.isfile(p)]
        if missing:
            raise FileNotFoundError("TrayCount weights not found (paths in the config are relative to the config "
                                    "file): " + ", ".join(missing))
        self.out_cfg = cfg.pop("output")
        self.cfg, self.R, self.D = cfg, cfg["runtime"], cfg["runtime"]["display"]
        self.models, self._rt, self._obs, self._out, self._t = None, None, None, None, 0

    # ------------------------------------------------------------------ models
    def load_model(self):
        """Load the 4 models onto the GPU (model_predict_args.device) and run them once on a blank frame."""
        import torch
        if not torch.cuda.is_available():
            raise RuntimeError("TrayCount needs an NVIDIA GPU with CUDA; torch.cuda.is_available() is False")
        if self.models is not None:
            return
        from ultralytics import YOLO
        from mediapipe.tasks import python as mp_python
        from mediapipe.tasks.python import vision as mp_vision
        cfg, R = self.cfg, self.R
        dev = cfg["model_predict_args"].get("device", 0)
        model = YOLO(R["model"])
        person = YOLO(cfg["hand_person_model"])
        hands = mp_vision.HandLandmarker.create_from_options(mp_vision.HandLandmarkerOptions(
            base_options=mp_python.BaseOptions(model_asset_path=cfg["hand_landmarker"]),
            running_mode=mp_vision.RunningMode.IMAGE, num_hands=2, min_hand_detection_confidence=cfg["hand_min_conf"]))
        emb = Embedder(cfg, device=f"cuda:{dev}" if isinstance(dev, int) or str(dev).isdigit() else str(dev))
        blank = np.zeros((1080, 1920, 3), np.uint8)        # first run puts the YOLO models on the GPU now, not later
        model.predict(blank, verbose=False, **cfg["model_predict_args"])
        person.predict(blank, classes=[0], conf=cfg["hand_person_conf"], imgsz=cfg["hand_person_imgsz"],
                       retina_masks=True, verbose=False)
        rt.VIEWS = cfg["reid_gallery_views"]               # (set in the project runtime's main())
        rt.PAST = max(1, int((self.D.get("smooth") or {}).get("frames", 1)))
        self.models = (model, hands, person, emb)
        self._rt = None

    def unload_model(self):
        """Free the GPU. load_model() again before the next frame."""
        if self.models is not None:
            try:
                self.models[1].close()                     # MediaPipe hand landmarker
            except Exception:
                pass
        self.models, self._rt, self._obs, self._out = None, None, None, None
        gc.collect()
        try:
            import torch
            torch.cuda.empty_cache()
        except Exception:
            pass

    # ------------------------------------------------------------------ one session
    def reset(self, fps, expected_duck_count=None, session=None):
        """Start a new video / camera session (forgets all toys, IDs and templates).
        fps                  frames per second that will be passed to process_frame (time settings are in seconds)
        expected_duck_count  None (default) = config output.expected_duck_count (null = the ducks counted at the end
                             of warm-up); or a number to use instead
        session              name of this session's template folder (default: date_time)"""
        if self.models is None:
            raise RuntimeError("call load_model() first")
        self._rt = rt.Runtime(self.cfg, self.R, float(fps), self.models)
        self._obs = rt.Observer(self.cfg, self.R, self.models)
        self._out, self._t = None, 0
        if expected_duck_count is None:
            expected_duck_count = self.out_cfg.get("expected_duck_count")
        self._expected_fixed = expected_duck_count
        self.expected = expected_duck_count
        self._warm_ids = None                              # IDs known at the end of warm-up (later ones = added)
        self._tpl = {}                                     # id -> dict(kind, jpeg bytes, b64, path, frame_index)
        self._last_info = None                             # status fields of the last running frame (frozen while HAND)
        self._times = deque(maxlen=max(1, int(self.out_cfg["fps_window"])))
        self._anom = MaskAnomalies(self.out_cfg["mask_anomalies"], float(fps), self.R["mask_scale"])
        self.session = session or datetime.now().strftime("%Y%m%d_%H%M%S")
        td = self.out_cfg.get("templates_dir")
        self.templates_dir = None if not td else os.path.join(self.base, td, self.session)

    def process_frame(self, frame):
        """One BGR frame (every frame, in order) -> dict:
          frame_index          0, 1, 2 ... since reset()
          status               'WARMING' (learning the toys, first runtime.start_s) | 'HAND' (hand in the tray, until
                               the toys are found again; the IDs / counts below stay as they were before the hand) |
                               'NORMAL' | 'ANOMALY'
          reasons              ANOMALY: any of 'missing_duck', 'too_few_ducks', 'too_many_ducks', 'other_species_present',
                               'false_mask', 'mask_missing'
          expected_duck_count  ducks counted at the end of warm-up (or the number given to reset); None while WARMING
          counts               None while WARMING, else dict(ducks, non_ducks, missing, missing_ducks, missing_non_ducks)
          missing_ids          duck IDs taken away            missing_other_ids  non-duck IDs taken away
          added_ids            IDs that appeared after warm-up and are on the tray (ducks and non-ducks), added_count
          excess_ids           the newest duck IDs above expected_duck_count, excess_count
          other_ids            non-duck IDs on the tray
          mask_anomalies       [dict(type, ids, other_id, box [x0, y0, x1, y1], image base64 JPEG)]:
                               'false_mask'   two masks on one toy: ids [the false mask's ID], other_id the toy it lies on,
                                              image = that part only
                               'mask_missing' a toy with NO mask inside another toy's mask (one mask over two toys): ids [the
                                              toy without a mask], other_id the mask it is in, image = that mask's area
          thumbnails           {id: base64 JPEG} template picture of every ID named in the lists above
          toys                 toys seen on this frame: dict(id, kind 'duck' | 'non_duck', confidence 0..1,
                               center [x, y], outline [[[x, y], ...], ...] full-frame pixels); empty while WARMING / HAND
          min_confidence       the lowest toy confidence on this frame (None if no toy)
          tray, tray_clean     tray outline [[x, y], ...] or None; True when clean enough to draw
          latency_ms           dict(models, total) time for this frame;  fps  frames per second over the last
                               output.fps_window frames
          state                the runtime's own state: start | run | hand | settle | confirm"""
        if self._rt is None:
            raise RuntimeError("call reset(fps) first (after load_model())")
        t0 = time.perf_counter()
        t = self._t
        obs = self._obs(frame)
        t1 = time.perf_counter()
        out = self._rt.frame(frame, t, obs)
        D, s = self.D, self.R["mask_scale"]
        shapes = rt.drop_pieces_on_others(self._rt.outlines(out["vis"], t), D.get("piece_on_other_max", 0),
                                          D.get("piece_neck_px", 0))
        shapes = rt.overlap_to_deeper(shapes, D.get("deep_overlap"))
        out["outline"] = rt.add_parts(frame, shapes, out["tray"], s, D.get("parts"))
        self._out, self._t = out, t + 1
        cl = self.cfg["classes"]
        kind = lambda x: "duck" if x.cls == cl["duck"] else "non_duck"
        toys = []
        for x in out["vis"]:
            m, ox, oy = out["outline"].get(x.key) or rt.own_shape(x, D.get("min_piece", 0.0))
            cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            polys = [((c.reshape(-1, 2) + np.array([ox, oy])) * s + s / 2.0).round(1).tolist() for c in cs if len(c) >= 3]
            conf = getattr(x, "conf", None)
            toys.append(dict(id=int(x.key), kind=kind(x), confidence=None if conf is None else round(float(conf), 3),
                             center=[round(float(x.c[0]), 1), round(float(x.c[1]), 1)], outline=polys))
        sh = out["shown"]
        counts = None if sh is None else dict(ducks=sh["ducks"], non_ducks=sh["non_ducks"],
                                              missing=sh["missing_ducks"] + sh["missing_non_ducks"],
                                              missing_ducks=sh["missing_ducks"], missing_non_ducks=sh["missing_non_ducks"])
        state = out["state"]
        if state == "run":
            roster = self._rt.roster
            if self._warm_ids is None:                     # end of warm-up: the toy set and the expected ducks
                self._warm_ids = {x.key for x in roster}
                if self._expected_fixed is None:
                    self.expected = counts["ducks"]
            for x in out["vis"]:                           # a template once per ID: first frame it is seen running
                if x.key not in self._tpl:
                    self._make_template(frame, x, kind(x), t)
            on = [x for x in roster if not x.missing]
            ducks_on = sorted(x.key for x in on if x.cls == cl["duck"])
            info = dict(
                missing_ids=sorted(x.key for x in roster if x.missing and x.cls == cl["duck"]),
                missing_other_ids=sorted(x.key for x in roster if x.missing and x.cls != cl["duck"]),
                added_ids=sorted(x.key for x in on if x.key not in self._warm_ids),
                excess_ids=ducks_on[len(ducks_on) - max(0, len(ducks_on) - self.expected):] if self.expected is not None
                else [],
                other_ids=sorted(x.key for x in on if x.cls != cl["duck"]),
                mask_anomalies=[dict(a, image=self._crop_b64(frame, a["box"])) for a in self._anom.step(roster, t)])
            reasons = []
            if info["missing_ids"]:
                reasons.append("missing_duck")
            if counts["ducks"] < self.expected:
                reasons.append("too_few_ducks")
            if counts["ducks"] > self.expected:
                reasons.append("too_many_ducks")
            if counts["non_ducks"] > 0:
                reasons.append("other_species_present")
            for kind in ("false_mask", "mask_missing"):
                if any(m["type"] == kind for m in info["mask_anomalies"]):
                    reasons.append(kind)
            info.update(status="ANOMALY" if reasons else "NORMAL", reasons=reasons)
            self._last_info = info
        elif state == "start":
            info = dict(status="WARMING", reasons=[], missing_ids=[], missing_other_ids=[], added_ids=[], excess_ids=[],
                        other_ids=[], mask_anomalies=[])
        else:                                              # hand / settle / confirm
            info = dict(self._last_info or dict(reasons=[], missing_ids=[], missing_other_ids=[], added_ids=[],
                                                excess_ids=[], other_ids=[], mask_anomalies=[]), status="HAND")
        named = sorted(set(info["missing_ids"] + info["missing_other_ids"] + info["added_ids"] + info["excess_ids"]
                           + info["other_ids"] + [i for m in info["mask_anomalies"] for i in m["ids"]]))
        thumbs = {i: self._tpl[i]["b64"] for i in named if i in self._tpl}
        tc = out.get("tray_concave")
        t2 = time.perf_counter()
        self._times.append(t2 - t0)
        confs = [x["confidence"] for x in toys if x["confidence"] is not None]
        return dict(frame_index=t, status=info["status"], reasons=list(info["reasons"]),
                    expected_duck_count=None if state == "start" else self.expected, counts=counts,
                    missing_ids=info["missing_ids"], missing_other_ids=info["missing_other_ids"],
                    added_ids=info["added_ids"], added_count=len(info["added_ids"]),
                    excess_ids=info["excess_ids"], excess_count=len(info["excess_ids"]), other_ids=info["other_ids"],
                    mask_anomalies=info["mask_anomalies"],
                    thumbnails=thumbs, toys=toys, min_confidence=min(confs) if confs else None,
                    tray=None if out["tray"] is None else out["tray"].round(1).tolist(),
                    tray_clean=bool(out["tray"] is not None and tc is not None and tc <= D["tray_max_concave"]),
                    latency_ms=dict(models=round(1000 * (t1 - t0), 1), total=round(1000 * (t2 - t0), 1)),
                    fps=round(len(self._times) / max(1e-9, sum(self._times)), 2), state=state,
                    paused=state in PAUSED_STATES)

    def _make_template(self, frame, x, kind, t):
        """crop around the toy (output.template_margin of its size on each side) -> JPEG in memory (+ file)"""
        O = self.out_cfg
        x0, y0 = np.floor(x.poly.min(0)).astype(int)
        x1, y1 = np.ceil(x.poly.max(0)).astype(int)
        mx, my = int(O["template_margin"] * (x1 - x0)), int(O["template_margin"] * (y1 - y0))
        H, W = frame.shape[:2]
        crop = frame[max(0, y0 - my):min(H, y1 + my), max(0, x0 - mx):min(W, x1 + mx)]
        if crop.size == 0:
            return
        ok, buf = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, int(O["template_jpeg_quality"])])
        if not ok:
            return
        data = buf.tobytes()
        path = None
        if self.templates_dir:
            os.makedirs(self.templates_dir, exist_ok=True)
            path = os.path.join(self.templates_dir, f"id_{int(x.key):02d}_{kind}.jpg")
            with open(path, "wb") as fh:
                fh.write(data)
        self._tpl[x.key] = dict(id=int(x.key), kind=kind, jpeg=data, b64=base64.b64encode(data).decode("ascii"),
                                path=path, frame_index=t)

    def _crop_b64(self, frame, box):
        x0, y0, x1, y1 = box
        H, W = frame.shape[:2]
        c = frame[max(0, y0):min(H, y1), max(0, x0):min(W, x1)]
        if c.size == 0:
            return None
        ok, buf = cv2.imencode(".jpg", c, [cv2.IMWRITE_JPEG_QUALITY, int(self.out_cfg["template_jpeg_quality"])])
        return base64.b64encode(buf.tobytes()).decode("ascii") if ok else None

    def templates(self):
        """{id: dict(id, kind, frame_index, path (file, or None when output.templates_dir is null), b64 JPEG)}"""
        return {i: {k: v for k, v in d.items() if k != "jpeg"} for i, d in sorted(self._tpl.items())}

    def draw(self, frame):
        """Our overlay (counts box, toy outlines + IDs, tray outline, pause banner) on the frame LAST passed to
        process_frame -> a new BGR image of the same size."""
        if self._out is None:
            raise RuntimeError("call process_frame(frame) first")
        return rt.draw(frame, self._out, self.cfg, self.D)

    # ------------------------------------------------------------------ convenience
    def process_video(self, path, out_path=None, expected_duck_count=None, session=None):
        """A whole video file -> list of process_frame results (reset() is done here with the video's fps).
        out_path: also write the overlay video there (OpenCV, mp4v)."""
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise FileNotFoundError(path)
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        self.reset(fps, expected_duck_count=expected_duck_count, session=session)
        results, vw = [], None
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            results.append(self.process_frame(frame))
            if out_path:
                vis = self.draw(frame)
                if vw is None:
                    vw = cv2.VideoWriter(out_path, cv2.VideoWriter_fourcc(*"mp4v"), fps, vis.shape[1::-1])
                vw.write(vis)
        cap.release()
        if vw is not None:
            vw.release()
        return results
