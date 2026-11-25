"""
traycount/_runtime.py - the decision logic of the TrayCount runtime (ducks / non-ducks on a turning tray, an ID per toy,
pause while a hand is in the tray, missing toys after a hand). Copied unchanged from the project's scripts/21_run_v2.py
(2026-11-18); left out: the label check, CSV / cache / video files, command line. Only additions: runtime.quick_margin
(rule (a), off when not set), runtime.hand.arm_inside_rim_frac (arm counted only inside the tray, off when not
set) and Track.conf (this frame's confidence, for the results only). Use it through traycount.TrayCounter.
"""
import json, os, sys
from collections import Counter, deque

import cv2
import numpy as np
from scipy.linalg import expm, logm
from scipy.optimize import linear_sum_assignment

from . import _helpers as hp      # toy_cleanup, on_tray, tray_zone, descriptor, Embedder, match_ids
from . import _hands as pv        # MediaPipe hand helpers


# ------------------------------------------------------------------ one toy mask of one frame
class Det:
    """box (x0, y0, x1, y1) and mask crop m at 1/mask_scale size; poly / c / area at full size"""
    __slots__ = ("box", "m", "poly", "c", "area", "cls", "conf", "weak", "hist")

    def __init__(self, small, s, cls, conf, weak):
        ys, xs = np.nonzero(small)
        self.box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        self.m = small[self.box[1]:self.box[3], self.box[0]:self.box[2]].copy()
        cs, _ = cv2.findContours(self.m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        c = max(cs, key=cv2.contourArea).reshape(-1, 2)
        self.poly = ((c + np.array(self.box[:2])) * s + s / 2.0).astype(np.float32)
        self.c = (float((xs.mean() + 0.5) * s), float((ys.mean() + 0.5) * s))
        self.area = float(len(xs) * s * s)
        self.cls, self.conf, self.weak, self.hist = int(cls), float(conf), weak, None

    def __getstate__(self):                                # --cache: masks stored packed (8x smaller)
        return dict(box=self.box, m=(self.m.shape, np.packbits(self.m)), poly=self.poly, c=self.c, area=self.area,
                    cls=self.cls, conf=self.conf, weak=self.weak)

    def __setstate__(self, st):
        sh, pk = st.pop("m")
        self.m = np.unpackbits(pk)[:sh[0] * sh[1]].reshape(sh).astype(bool)
        for k, v in st.items():
            setattr(self, k, v)
        self.hist = None


def inter(a, b):
    """overlapping pixels of two masks (Det / Track: box + m)"""
    x0, y0, x1, y1 = max(a.box[0], b.box[0]), max(a.box[1], b.box[1]), min(a.box[2], b.box[2]), min(a.box[3], b.box[3])
    if x1 <= x0 or y1 <= y0:
        return 0
    return int(np.count_nonzero(a.m[y0 - a.box[1]:y1 - a.box[1], x0 - a.box[0]:x1 - a.box[0]]
                                & b.m[y0 - b.box[1]:y1 - b.box[1], x0 - b.box[0]:x1 - b.box[0]]))


def covered(d, others):
    """share of d's mask lying on the union of the other masks"""
    u = np.zeros_like(d.m)
    for o in others:
        x0, y0, x1, y1 = max(d.box[0], o.box[0]), max(d.box[1], o.box[1]), min(d.box[2], o.box[2]), min(d.box[3], o.box[3])
        if x1 > x0 and y1 > y0:
            u[y0 - d.box[1]:y1 - d.box[1], x0 - d.box[0]:x1 - d.box[0]] |= o.m[y0 - o.box[1]:y1 - o.box[1], x0 - o.box[0]:x1 - o.box[0]]
    return float(np.count_nonzero(u & d.m)) / max(1, int(np.count_nonzero(d.m)))


def biggest(b):
    n, lab, st, _ = cv2.connectedComponentsWithStats(b.astype(np.uint8), connectivity=8)
    return b if n <= 1 else lab == 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))


def disk(r):
    return cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))


def clean_tray(tray, toys, L, T):
    """The model's tray mask can run onto a white box under the tray (b4be1c48 start). 1) big BRIGHT pieces at its
    edge that are no toy = not the tray (the box face) -> cut off; 2) parts joined to the tray by a narrow neck (strips
    of floor / wall) -> cut off by an opening, then the tray's own corners are given back (pixels close to what is
    left). Tested on all labelled frames of the 3 Friday videos (2026-11-12): b4be start tray 0.78 -> 0.90 IoU with
    your tray labels; Farhan_1790 and 12-45 unchanged."""
    area = float(tray.sum())
    if area == 0 or not T["use"]:
        return tray
    sq, t2 = np.sqrt(area), tray.copy()
    U = cv2.dilate(toys.astype(np.uint8), disk(T["toy_margin_px"])).astype(bool)
    off = (tray & ~U & (L > T["bright_L"])).astype(np.uint8)
    off = cv2.morphologyEx(off, cv2.MORPH_OPEN, disk(T["bright_open_px"]))
    n, lm, st, _ = cv2.connectedComponentsWithStats(off, connectivity=8)
    if n > 1:
        outside = cv2.dilate((~tray).astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        for i in range(1, n):
            if st[i, cv2.CC_STAT_AREA] >= T["bright_min_frac"] * area:
                pc = lm == i
                if (pc & outside).any():
                    t2 &= ~pc
    r, q = int(T["neck_frac"] * sq), T["neck_scale"]
    if r >= q:
        h, w = t2.shape
        small = cv2.resize(t2.astype(np.uint8), (w // q, h // q), interpolation=cv2.INTER_AREA)
        op = biggest(cv2.morphologyEx(small, cv2.MORPH_OPEN, disk(r // q)).astype(bool))
        back = cv2.dilate(op.astype(np.uint8), disk(max(1, int(T["restore_frac"] * sq) // q)))
        t2 = biggest(t2 & cv2.resize(back, (w, h), interpolation=cv2.INTER_NEAREST).astype(bool))
    return t2 if t2.sum() >= T["min_keep"] * area else tray


def tray_outline(small, s):
    """tray mask (1/s size) -> convex hull at full size (the labels' tray is hull-shaped)"""
    cs, _ = cv2.findContours(small.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cs:
        return None
    return (cv2.convexHull(np.vstack(cs)).reshape(-1, 2) * s + s / 2.0).astype(np.float32)


def detect(model, frame, cfg, R):
    """-> tray outline (or None), strong toy masks (one per toy, on the tray), weak toy masks (conf below the normal
    one, not lying on a strong mask: only used to see that a KNOWN toy is still there)"""
    args = dict(cfg["model_predict_args"])
    conf_min = args["conf"]
    args["conf"] = min(conf_min, R["weak_conf"])
    r = model.predict(frame, verbose=False, **args)[0]
    if r.masks is None:
        return None, [], [], None
    s, tray_c = R["mask_scale"], cfg["classes"]["tray"]
    md = r.masks.data
    if md.shape[1:] != frame.shape[:2]:
        sys.exit("model_predict_args.retina_masks must be true (masks at full video size)")
    cls, conf = r.boxes.cls.cpu().numpy().astype(int), r.boxes.conf.cpu().numpy()
    trays = [i for i in range(len(cls)) if cls[i] == tray_c and conf[i] >= conf_min]
    strong = [i for i in range(len(cls)) if cls[i] != tray_c and conf[i] >= conf_min]
    weak = [i for i in range(len(cls)) if cls[i] != tray_c and conf[i] < conf_min]
    tray, concave = None, None
    if trays:
        i = max(trays, key=lambda k: conf[k])
        tm = biggest(md[i, ::s, ::s].cpu().numpy() > 0.5)
        toys = (md[strong, ::s, ::s].amax(0).cpu().numpy() > 0.5) if strong else np.zeros_like(tm)
        L = cv2.cvtColor(np.ascontiguousarray(frame[::s, ::s]), cv2.COLOR_BGR2LAB)[:, :, 0]
        cm = clean_tray(tm, toys, L, R["tray_clean"])
        tray = tray_outline(cm, s)
        if tray is not None:                               # how far the tray mask is from convex (a tray is convex)
            hull = np.zeros(cm.shape, np.uint8)
            cs, _ = cv2.findContours(cm.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.fillPoly(hull, [cv2.convexHull(np.vstack(cs))], 1)
            concave = int(np.count_nonzero(hull)) / max(1, int(np.count_nonzero(cm))) - 1.0
    min_px = R["min_toy_px"]
    dets = []
    if strong:
        km, kc, kl = hp.toy_cleanup(md[strong], conf[strong], cls[strong], cfg)
        small = km[:, ::s, ::s].cpu().numpy()
        for j in range(len(small)):
            if np.count_nonzero(small[j]) * s * s >= min_px:
                d = Det(small[j], s, kl[j], kc[j], False)
                if hp.on_tray(d.poly, tray, cfg):
                    dets.append(d)
    weaks = []
    for i in weak:
        sm = md[i, ::s, ::s].cpu().numpy() > 0.5
        if np.count_nonzero(sm) * s * s >= min_px:
            d = Det(sm, s, cls[i], conf[i], True)
            if hp.on_tray(d.poly, tray, cfg) and covered(d, dets) < R["double_on_toy_frac"]:
                weaks.append(d)
    return tray, dets, weaks, concave


class Observer:
    """the models on one frame - tray outline (the last one seen), toy masks, hand signs; no decisions"""

    def __init__(self, cfg, R, models):
        self.cfg, self.R, self.tray = cfg, R, None
        self.model, self.hands, self.person = models[:3]

    def __call__(self, frame):
        cfg = self.cfg
        tray, dets, weaks, concave = detect(self.model, frame, cfg, self.R)
        if tray is not None:
            self.tray = tray
        pts = pv.hands_in_frame(self.hands, frame)
        mp_hit = bool(pv.hand_in_tray(pts, self.tray, cfg))
        pf, reach = arm(self.person, frame, self.tray, cfg, self.R["reach"]["scale"])
        if mp_hit:
            q = self.R["reach"]["scale"]
            h, w = frame.shape[0] // q, frame.shape[1] // q
            reach = np.zeros((h, w), bool) if reach is None else reach
            for hp_ in pts:
                for x, y in hp_ / q:
                    if 0 <= int(y) < h and 0 <= int(x) < w:
                        reach[int(y), int(x)] = True
        return dict(tray=self.tray, tray_concave=concave, dets=dets, weaks=weaks, mp_hit=mp_hit, pf=pf,
                    reach=None if reach is None or not reach.any() else (reach.shape, np.packbits(reach)),
                    mp_pts=[np.round(p, 1).astype(np.float32) for p in pts])   # every MediaPipe hand (21 points each)


def arm(person, bgr, tray_poly, cfg, q):
    """share of the tray zone covered by the biggest person mask - exactly v2_helpers.person_frac (the labels' arm
    check) - plus every person pixel inside the tray zone at 1/q size (where the hand / arm reached)"""
    if tray_poly is None:
        return 0.0, None
    res = person.predict(bgr, classes=[0], conf=cfg["hand_person_conf"], imgsz=cfg["hand_person_imgsz"],
                         retina_masks=True, verbose=False)[0]
    if res.masks is None:
        return 0.0, None
    zone = hp.tray_zone(tray_poly, bgr.shape, cfg)
    rim = (cfg["runtime"]["hand"].get("arm_inside_rim_frac") if "runtime" in cfg else None)
    inner = zone                                           # package: runtime.hand.arm_inside_rim_frac (null = as before)
    if rim:                                                # only arm pixels INSIDE the tray, its rim band left out
        tm = np.zeros(zone.shape, np.uint8)
        cv2.fillPoly(tm, [np.asarray(tray_poly).astype(np.int32)], 1)
        r = int(rim * np.sqrt(max(1.0, cv2.contourArea(np.asarray(tray_poly).astype(np.int32).reshape(-1, 1, 2)))))
        inner = cv2.erode(tm, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))).astype(bool) if r > 0 else tm.astype(bool)
    best, union = 0, np.zeros(zone.shape, bool)
    for mk in res.masks.data.cpu().numpy():
        mk = mk > 0.5
        if mk.shape != zone.shape:
            mk = cv2.resize(mk.astype(np.uint8), zone.shape[::-1], interpolation=cv2.INTER_NEAREST).astype(bool)
        inter = mk & zone
        best = max(best, int((mk & inner).sum()))
        union |= inter
    small = cv2.resize(union.astype(np.uint8), (zone.shape[1] // q, zone.shape[0] // q), interpolation=cv2.INTER_AREA) > 0
    return round(best / max(1, int(zone.sum())), 5), (small if small.any() else None)


def mp_inside(hands, tray):
    """the biggest share of one MediaPipe hand's 21 points lying inside the tray outline itself (no rim margin)"""
    t = tray.reshape(-1, 1, 2).astype(np.float32)
    return max((float(np.mean([cv2.pointPolygonTest(t, (float(x), float(y)), False) >= 0 for x, y in p])) for p in hands),
               default=0.0)


# ------------------------------------------------------------------ tracks (one per toy)
class Track:
    def __init__(self, key, d, t):
        self.key, self.votes, self.hists, self.embs = key, Counter(), [], []
        self.view_t, self.seen, self.first_t, self.weak_only, self.missing = -10 ** 9, 0, t, True, False
        self.past = deque(maxlen=PAST)                   # (t, box, mask) of its last frames - drawing only (display.smooth)
        self.update(d, t)

    def update(self, d, t):
        self.box, self.m, self.poly, self.c, self.area = d.box, d.m, d.poly, d.c, d.area
        self.past.append((t, d.box, d.m))
        self.votes[d.cls] += d.conf
        self.conf = d.conf                                 # package: this frame's model confidence (results only, no decision)
        self.last_t, self.seen, self.visible = t, self.seen + 1, True
        self.weak_only = self.weak_only and d.weak

    @property
    def cls(self):
        return self.votes.most_common(1)[0][0]

    def take(self, o):
        """this (known) toy takes over a track found later (same toy): its place, mask, votes and views"""
        self.box, self.m, self.poly, self.c, self.area = o.box, o.m, o.poly, o.c, o.area
        self.votes.update(o.votes)
        self.conf = getattr(o, "conf", None)               # package: results only
        self.last_t, self.visible, self.missing = o.last_t, o.visible, False
        self.seen += o.seen
        self.hists, self.embs = (self.hists + o.hists)[-VIEWS:], (self.embs + o.embs)[-VIEWS:]
        self.past = deque(o.past, maxlen=PAST)


VIEWS = 12                                   # replaced by reid_gallery_views in main()
PAST = 3                                     # replaced by display.smooth.frames in main()


def add_view(tr, frame, cfg, emb, t, every):
    if t - tr.view_t >= every:
        ds = hp.descriptor(frame, tr.poly, cfg, emb)
        tr.hists, tr.embs = (tr.hists + [ds["hist"]])[-VIEWS:], (tr.embs + [ds["emb"]])[-VIEWS:]
        tr.view_t = t


def hist_of(d, frame, cfg):
    if d.hist is None:
        d.hist = hp.descriptor(frame, d.poly, cfg)["hist"]
    return d.hist


def step(tracks, dets, frame, cfg, R, t):
    """one frame of tracking for `tracks` (all of them, seen or hidden): seen toys by mask overlap, then hidden toys by
    place (moved with the tray's turn) + colour. Returns the masks left over (no toy of these tracks) and the tray's turn
    since the previous frame (2x3 matrix, full size; None when fewer than 3 toys were seen in both frames)."""
    vis = [x for x in tracks if x.visible and not x.missing]
    pairs, left = [], list(range(len(dets)))
    if vis and dets:
        C = np.zeros((len(vis), len(dets)))
        for a, x in enumerate(vis):
            for b, d in enumerate(dets):
                i = inter(x, d)
                if i:
                    C[a, b] = i / (np.count_nonzero(x.m) + np.count_nonzero(d.m) - i)
        rows, cols = linear_sum_assignment(-C)
        for a, b in zip(rows, cols):
            if C[a, b] >= R["track_min_iou"]:
                pairs.append((vis[a], dets[b]))
        used = {id(d) for _, d in pairs}
        left = [b for b in range(len(dets)) if id(dets[b]) not in used]
    # the tray's turn from the toys seen in both frames -> hidden toys move with it
    M = None
    if len(pairs) >= 3:
        A = np.float32([x.c for x, _ in pairs])
        B = np.float32([d.c for _, d in pairs])
        M, _ = cv2.estimateAffinePartial2D(A, B, method=cv2.RANSAC, ransacReprojThreshold=R["motion_ransac_px"])
    matched = {id(x) for x, _ in pairs}
    P0 = np.float32([x.c for x, _ in pairs]).reshape(-1, 2)  # places of the toys seen in both frames: before ...
    P1 = np.float32([d.c for _, d in pairs]).reshape(-1, 2)  # ... and now
    for x, d in pairs:
        x.update(d, t)
    hidden = [x for x in tracks if not x.missing and id(x) not in matched]
    k = int(R.get("hidden_local_k", 0))
    for x in hidden:
        x.visible = False
        if k > 0 and len(P0):                              # moves like its nearest seen neighbours (the whole-tray turn
            c = np.float32(x.c)                            # is off for single toys when the camera moves)
            dist = np.hypot(*(P0 - c).T)
            idx = np.argsort(dist)[:k]
            w = 1.0 / (dist[idx] + np.sqrt(max(1.0, x.area)))
            x.c = tuple(float(v) for v in c + (w[:, None] * (P1[idx] - P0[idx])).sum(0) / w.sum())
        elif M is not None:
            x.c = tuple(float(v) for v in (M[:, :2] @ np.float32(x.c) + M[:, 2]))
    # masks left over -> a hidden toy of these tracks? (place + colour + kind)
    rest = [dets[b] for b in left]
    if hidden and rest:
        C = np.full((len(rest), len(hidden)), np.inf)
        for a, d in enumerate(rest):
            h = hist_of(d, frame, cfg)
            for b, x in enumerate(hidden):
                pos = np.hypot(d.c[0] - x.c[0], d.c[1] - x.c[1]) / (R["hidden_pos_scale"] * np.sqrt(max(1.0, x.area)))
                app = min((cv2.compareHist(v, h, cv2.HISTCMP_BHATTACHARYYA) for v in x.hists), default=1.0)
                C[a, b] = (R["hidden_w_pos"] * min(1.0, pos) + R["hidden_w_app"] * app
                           + (0.0 if x.cls == d.cls else cfg["reid_cls_penalty"]) + (1e6 if pos > 1.0 else 0.0))
        rows, cols = linear_sum_assignment(np.where(np.isfinite(C), C, 1e9))
        took = set()
        for a, b in zip(rows, cols):
            if C[a, b] <= R["hidden_max_cost"]:
                hidden[b].update(rest[a], t)
                took.add(a)
        rest = [d for a, d in enumerate(rest) if a not in took]
    seen = [d for _, d in pairs]
    return [d for d in rest if covered(d, seen) < R["double_on_toy_frac"]], M


def best_shift(r, tie):
    """the shift with the best overlap (matchTemplate result r); among shifts within `tie` of the best (a bigger shape
    can contain a smaller one in several places) the one closest to the expected place (the centre of r)"""
    cy, cx = (r.shape[0] - 1) / 2.0, (r.shape[1] - 1) / 2.0
    ys, xs = np.nonzero(r >= r.max() * (1.0 - tie))
    i = int(np.argmin((ys - cy) ** 2 + (xs - cx) ** 2))
    return int(xs[i]), int(ys[i])


def pieces(m, min_frac):
    """the parts of mask m that are drawn: EVERY connected piece of at least min_frac of the whole mask (a head or beak
    the model joined to the body by a thin or broken strip stays - only the biggest piece was drawn before 2026-11-13);
    smaller specks go"""
    m = m.astype(bool)
    n, lab, st, _ = cv2.connectedComponentsWithStats(m.astype(np.uint8), connectivity=8)
    if n <= 2:
        return m
    tot = st[1:, cv2.CC_STAT_AREA].sum()
    return np.isin(lab, [i for i in range(1, n) if st[i, cv2.CC_STAT_AREA] >= min_frac * tot])


def own_shape(x, mp):
    """toy x as drawn without smoothing: its mask pieces (half size) and where they sit (half size)"""
    return pieces(x.m, mp), x.box[0], x.box[1]


def steady_outline(x, t, motion, S, s, mp):
    """DRAWING ONLY (display.smooth): toy x's shape from its last S['frames'] masks - a pixel is kept when it is in at
    least S['min_votes'] of them, the older masks first moved with the tray's turn (motion: {frame: turn since the frame
    before}). Too few frames / turn unknown / less than S['min_keep'] of this frame's mask left -> this frame's mask.
    Returns (mask pieces, x0, y0) at half size."""
    past = [p for p in x.past if t - p[0] < S["frames"]]
    if not S["use"] or len(past) < S["frames"] or past[-1][0] != t:
        return own_shape(x, mp)
    Ssm = np.array([[s, 0, s / 2.0], [0, s, s / 2.0], [0, 0, 1]])   # half size -> full size (as Det.poly)
    mats, corners = [], []
    for tp, box, m in past:
        A = np.eye(3)
        for k in range(tp + 1, t + 1):
            if motion.get(k) is None:
                return own_shape(x, mp)
            A = np.vstack([motion[k], [0, 0, 1]]) @ A
        T = np.linalg.inv(Ssm) @ A @ Ssm @ np.array([[1, 0, box[0]], [0, 1, box[1]], [0, 0, 1]])
        h, w = m.shape
        corners.append((T @ np.array([[0, w, 0, w], [0, 0, h, h], [1, 1, 1, 1]]))[:2].T)
        mats.append(T)
    a = S.get("align_frac", 0)                             # older masks: lined up with this frame's mask (best overlap)
    R = max(3, int(a * np.sqrt(np.count_nonzero(x.m)))) if a > 0 else 0
    allc = np.vstack(corners)
    ox, oy = int(np.floor(allc[:, 0].min())) - 1 - R, int(np.floor(allc[:, 1].min())) - 1 - R
    W, H = int(np.ceil(allc[:, 0].max())) - ox + 2 + R, int(np.ceil(allc[:, 1].max())) - oy + 2 + R
    To = np.array([[1, 0, -ox], [0, 1, -oy], [0, 0, 1]])
    cur = cv2.warpAffine(past[-1][2].astype(np.uint8), (To @ mats[-1])[:2], (W, H), flags=cv2.INTER_NEAREST, borderValue=0)
    votes = cur.copy()
    curp = cv2.copyMakeBorder(cur, R, R, R, R, cv2.BORDER_CONSTANT, value=0).astype(np.float32)
    for (_, _, m), T in zip(past[:-1], mats[:-1]):
        T2 = To @ T                                        # moved with the tray's turn ...
        if R:                                              # ... then the shift (up to R) that overlaps this frame's mask most
            o = cv2.warpAffine(m.astype(np.uint8), T2[:2], (W, H), flags=cv2.INTER_NEAREST, borderValue=0)
            _, _, _, (dx, dy) = cv2.minMaxLoc(cv2.matchTemplate(curp, o.astype(np.float32), cv2.TM_CCORR))
            T2 = np.array([[1, 0, dx - R], [0, 1, dy - R], [0, 0, 1]]) @ T2
        votes += cv2.warpAffine(m.astype(np.uint8), T2[:2], (W, H), flags=cv2.INTER_NEAREST, borderValue=0)
    keep = pieces(votes >= S["min_votes"], mp)
    if np.count_nonzero(keep) < S["min_keep"] * np.count_nonzero(x.m):
        return own_shape(x, mp)
    return keep, ox, oy


def known_of(tracks, positions=None):
    return {x.key: dict(cls=x.cls, hists=x.hists, embs=[e for e in x.embs if e is not None], area=x.area,
                        c=None if positions is None else positions.get(x.key)) for x in tracks}


def cur_of(tracks):
    return {x.key: dict(cls=x.cls, hist=x.hists[-1], emb=x.embs[-1], area=x.area, c=x.c) for x in tracks}


# ------------------------------------------------------------------ one video
class Runtime:
    def __init__(self, cfg, R, fps, models):
        self.cfg, self.R, self.fps = cfg, R, fps
        self.model, self.hands, self.person, self.emb = models
        sec = lambda v: max(1, int(round(v * fps)))
        self.hold, self.settle, self.confirm, self.start = (sec(R["hand_hold_s"]), sec(R["settle_s"]), sec(R["confirm_s"]),
                                                            sec(R["start_s"]))
        self.new_min, self.every, self.pend_gap = sec(R["new_min_s"]), sec(R["view_every_s"]), sec(R["pending_gap_s"])
        self.hid_min = sec(R["hidden_min_s"])
        self.roster, self.next_id = [], 1
        self.state, self.state_until, self.last_sign, self.win_t0 = "start", self.start, -10 ** 9, 0
        self.window, self.wkey, self.pending, self.pkey = [], 0, [], 0
        self.snap, self.snap_t, self.G = None, None, None  # before the hand: (frame, polys, toy places), its time, turn
        self.prev = None                                   # previous frame: (frame, polys of the toys seen, tray)
        self.trail = deque(maxlen=sec(R["turn"]["fit_s"]) + 1)   # (t, {id: place}) of the toys seen, last fit_s
        self.reach, self._P = [], {}                       # [(t, where the arm / hand was)] during a hand moment
        self.hand_t0, self.mp_alone = None, deque()        # start of the hand moment; recent MediaPipe hits without arm
        self.arm_alone = deque()                           # recent frames with an arm but no MediaPipe hand
        self.shown, self.tray, self.events, self.log = None, None, [], []
        self.motion = {}                                   # {frame: tray's turn since the frame before} - drawing only

    def outlines(self, vis, t):
        """the outline drawn for each toy seen on this frame (display.smooth; no section in the config = off)"""
        D, s = self.R["display"], self.R["mask_scale"]
        S, mp = D.get("smooth"), D.get("min_piece", 0.0)
        if not S or not S["use"]:
            return {x.key: own_shape(x, mp) for x in vis}
        if S.get("half_life_s", 0) > 0:
            return self.memory_outlines(vis, t, S, s, mp)
        return {x.key: steady_outline(x, t, self.motion, S, s, mp) for x in vis}

    def memory_outlines(self, vis, t, S, s, mp):
        """DRAWING ONLY (display.smooth.half_life_s > 0): shapes as the frames vote (steady_outline), plus a SHAPE MEMORY
        per toy = a running average of its masks (half-life half_life_s; moved with the tray's turn and lined up with this
        frame's mask) that only settles OVERLAPS: a piece claimed by two toys goes to the toy holding it longer - but
        only if it is attached to the rest of that toy's shape, else to the toy it is attached to (a beak the
        neighbour's mask held for a while goes back to its own toy at once). Returns {id: (mask pieces, x0, y0)}."""
        a = 1.0 - 0.5 ** (1.0 / max(1e-6, S["half_life_s"] * self.fps))
        Ssm = np.array([[s, 0, s / 2.0], [0, s, s / 2.0], [0, 0, 1]])
        got = {}
        for x in vis:
            (bx0, by0, bx1, by1), m = x.box, x.m
            R_ = max(3, int(S["align_frac"] * np.sqrt(max(1, np.count_nonzero(m)))))
            ox, oy, W, H = bx0 - R_, by0 - R_, bx1 - bx0 + 2 * R_, by1 - by0 + 2 * R_
            cur = np.zeros((H, W), np.float32)
            cur[R_:R_ + m.shape[0], R_:R_ + m.shape[1]] = m
            mem = getattr(x, "mem", None)
            if mem is not None and mem[3] == t - 1:
                A, aox, aoy, _ = mem
                M = self.motion.get(t)
                T = np.linalg.inv(Ssm) @ (np.eye(3) if M is None else np.vstack([M, [0, 0, 1]])) @ Ssm
                T = np.array([[1, 0, -ox], [0, 1, -oy], [0, 0, 1]]) @ T @ np.array([[1, 0, aox], [0, 1, aoy], [0, 0, 1]])
                old = cv2.warpAffine(A, T[:2], (W, H), flags=cv2.INTER_LINEAR, borderValue=0)
                curp = cv2.copyMakeBorder(cur, R_, R_, R_, R_, cv2.BORDER_CONSTANT, value=0)
                dx, dy = best_shift(cv2.matchTemplate(curp, old, cv2.TM_CCORR), S["align_tie"])
                old = cv2.warpAffine(old, np.float32([[1, 0, dx - R_], [0, 1, dy - R_]]), (W, H),
                                     flags=cv2.INTER_LINEAR, borderValue=0)
                A = (1.0 - a) * old + a * cur
            else:
                A = cur
            x.mem = (A, ox, oy, t)
            wm, wx, wy = steady_outline(x, t, self.motion, S, s, mp)
            got[x.key] = [wm.copy(), wx, wy, A, ox, oy]

        def mem_at(k, x0, y0, x1, y1):                     # toy k's memory on the half-size window x0..x1, y0..y1
            _, _, _, A, ax, ay = got[k]
            outw = np.zeros((y1 - y0, x1 - x0), np.float32)
            u0, v0, u1, v1 = max(x0, ax), max(y0, ay), min(x1, ax + A.shape[1]), min(y1, ay + A.shape[0])
            if u1 > u0 and v1 > v0:
                outw[v0 - y0:v1 - y0, u0 - x0:u1 - x0] = A[v0 - ay:v1 - ay, u0 - ax:u1 - ax]
            return outw

        def attached(k, reg, x0, y0):                      # does region reg (window at x0, y0) touch the rest of toy k?
            m, wx, wy = got[k][:3]
            R2 = np.zeros(m.shape, np.uint8)
            R2[y0 - wy:y0 - wy + reg.shape[0], x0 - wx:x0 - wx + reg.shape[1]] = reg
            ring = cv2.dilate(R2, np.ones((3, 3), np.uint8)).astype(bool) & ~R2.astype(bool)
            return bool((ring & m).any())

        keys = list(got)
        for i, ka in enumerate(keys):
            for kb in keys[i + 1:]:
                ma, xa, ya = got[ka][:3]
                mb, xb, yb = got[kb][:3]
                x0, y0 = max(xa, xb), max(ya, yb)
                x1, y1 = min(xa + ma.shape[1], xb + mb.shape[1]), min(ya + ma.shape[0], yb + mb.shape[0])
                if x1 <= x0 or y1 <= y0:
                    continue
                sa = (slice(y0 - ya, y1 - ya), slice(x0 - xa, x1 - xa))
                sb = (slice(y0 - yb, y1 - yb), slice(x0 - xb, x1 - xb))
                both = ma[sa] & mb[sb]
                if not both.any():
                    continue
                va, vb = mem_at(ka, x0, y0, x1, y1), mem_at(kb, x0, y0, x1, y1)
                n, lab = cv2.connectedComponents(both.astype(np.uint8), connectivity=8)
                for j in range(1, n):
                    reg = lab == j
                    a_wins = va[reg].mean() >= vb[reg].mean()
                    ma[sa][reg], mb[sb][reg] = False, False   # each toy without the region, to test what it touches
                    ta, tb = attached(ka, reg.astype(np.uint8), x0, y0), attached(kb, reg.astype(np.uint8), x0, y0)
                    win_attached, lose_attached = (ta, tb) if a_wins else (tb, ta)
                    if not win_attached and lose_attached:    # it would be a loose island of the winner
                        a_wins = not a_wins
                    if a_wins:
                        ma[sa][reg] = True
                    else:
                        mb[sb][reg] = True
        return {k: (pieces(m, mp), wx, wy) for k, (m, wx, wy, _, _, _) in got.items()}

    # ---- counts
    def counts(self):
        cl = self.cfg["classes"]
        on = [x for x in self.roster if not x.missing]
        mis = [x for x in self.roster if x.missing]
        return dict(ducks=sum(x.cls == cl["duck"] for x in on), non_ducks=sum(x.cls == cl["non_duck"] for x in on),
                    missing_ducks=sum(x.cls == cl["duck"] for x in mis),
                    missing_non_ducks=sum(x.cls == cl["non_duck"] for x in mis))

    # ---- the tray's turn
    def turn_model(self):
        """the tray's turn per frame, from the toys' places over the last turn.fit_s seconds: a homography (a turn of
        the tray plane seen in perspective) -> its matrix log / frames, so it can be run forward any number of frames"""
        T = self.R["turn"]
        if len(self.trail) < 2:
            return None
        (ta, A), (tb, B) = self.trail[0], self.trail[-1]
        ks = [k for k in A if k in B]
        if tb <= ta or len(ks) < T["min_toys"]:
            return None
        size = float(np.median([np.sqrt(max(1.0, x.area)) for x in self.roster if not x.missing]))
        H, inl = cv2.findHomography(np.float32([A[k] for k in ks]), np.float32([B[k] for k in ks]), cv2.RANSAC,
                                    T["ransac_toys"] * size)
        if H is None or inl is None or int(inl.sum()) < T["min_toys"] or np.linalg.det(H) <= 0:
            return None
        L = logm(H / np.cbrt(np.linalg.det(H)))
        if np.iscomplexobj(L):
            if np.abs(L.imag).max() > 1e-6:
                return None
            L = L.real
        return L / (tb - ta)

    def predict(self, c, k):
        """place c, k frames later on the turning tray"""
        if self.G is None or c is None or k == 0:
            return c
        if k not in self._P:
            self._P[k] = expm(self.G * k)
        v = self._P[k] @ np.array([c[0], c[1], 1.0])
        return (float(v[0] / v[2]), float(v[1] / v[2]))

    # ---- toys found during a window (start / after a hand)
    def window_step(self, frame, dets, weaks, t):
        left, _ = step(self.window, dets + weaks, frame, self.cfg, self.R, t)
        for d in left:
            self.wkey += 1
            self.window.append(Track(f"w{self.wkey}", d, t))
        for x in self.window:
            if x.visible and x.last_t == t:
                add_view(x, frame, self.cfg, self.emb, t, self.every)

    def match(self, known, cur, pos, max_cost=None):
        """IDs for the window's toys: by look first; the best-matching half of them (by look) then give the tray's turn
        since the hand (a homography, RANSAC); only if most of them agree on one turn, again with look + place.
        Otherwise (toys moved around by the hand) look only."""
        cfg, T = self.cfg, self.R["turn"]
        c2 = cfg if max_cost is None else dict(cfg, reid_max_cost=max_cost)
        mp, cost = hp.match_ids(known_of(known, pos), cur_of(cur), None, c2)
        if not pos:
            return mp, cost, "look only"
        pairs = sorted(((cost[x.key], pos[i], x.c) for x in cur for i in [mp[x.key]] if i is not None
                        and pos.get(i) is not None), key=lambda v: v[0])
        pairs = pairs[:max(T["min_toys"], int(np.ceil(T["fit_best_share"] * len(pairs))))]
        if len(pairs) >= T["min_toys"]:
            size = float(np.median([np.sqrt(max(1.0, x.area)) for x in cur]))
            Hc, inl = cv2.findHomography(np.float32([a for _, a, _ in pairs]), np.float32([b for _, _, b in pairs]),
                                         cv2.RANSAC, T["ransac_toys"] * size)
            n = int(inl.sum()) if Hc is not None and inl is not None else 0
            if n >= max(T["min_toys"], T["fit_min_share"] * len(pairs)):
                mp, cost = hp.match_ids(known_of(known, pos), cur_of(cur), Hc, c2)
                return mp, cost, f"look + place (turn from {n} of {len(pairs)} best toys)"
        return mp, cost, "look only (toys moved)"

    def resolve(self, frame, t, after_hand):
        """toys of the window -> IDs (see the rules at the top)"""
        R = self.R
        need = max(R["confirm_min_frames"], int(np.ceil(R["confirm_min_share"] * (t - self.win_t0 + 1))))
        strong = [x for x in self.window if not x.weak_only and x.seen >= need and x.hists]
        weak = [x for x in self.window if x.weak_only and x.seen >= need and x.hists]
        before = [x for x in self.roster if not x.missing]
        k = (t - self.snap_t) if self.snap_t is not None else 0
        pos = {i: self.predict(c, k) for i, c in self.snap[2].items()} if self.snap is not None else None
        mp, cost, how = {x.key: None for x in strong}, {}, {}
        if self.roster and strong:
            mp, cost, way = self.match(self.roster, strong, pos)
            how = {kk: way for kk, i in mp.items() if i is not None}
            # the same toys come back: left over toys take the closest toy of their kind that was on the tray before
            used = {i for i in mp.values() if i is not None}
            free = [x for x in before if x.key not in used]
            lft = [x for x in strong if mp[x.key] is None]
            if free and lft:
                sub, sc, way2 = self.match(free, lft, pos, max_cost=float("inf"))
                for kk, i in sub.items():
                    if i is not None:
                        mp[kk], cost[kk], how[kk] = i, sc[kk], "same toys, " + way2
            self.log.append((t, f"window {len(self.window)} tracks, {len(strong)} toys (seen >= {need} frames), "
                                f"{len(weak)} weak; turn {'measured' if self.G is not None else 'NOT measured'} before the hand"))
        by_key = {x.key: x for x in self.roster}
        taken = set()
        strong.sort(key=lambda x: (x.c[1], x.c[0]))      # new IDs numbered top to bottom, left to right
        for x in strong:
            i = mp.get(x.key)
            info = f"{x.key} cls {x.cls} seen {x.seen} area {x.area:.0f} c ({x.c[0]:.0f},{x.c[1]:.0f})"
            if i is not None and i not in taken:
                by_key[i].take(x)
                taken.add(i)
                self.log.append((t, f"  {info} -> #{i} ({how.get(x.key)}, cost {cost.get(x.key)})"))
            else:
                x.key, x.missing = self.next_id, False
                self.next_id += 1
                self.roster.append(x)
                taken.add(x.key)
                self.events.append((t, "new toy", x.key))
                self.log.append((t, f"  {info} -> NEW #{x.key}"))
        # toys on the tray before the hand and not found: a weak mask shows it is still there? else MISSING
        lost = [x for x in before if x.key not in taken]
        if lost and weak:
            sub, sc, _ = self.match(lost, weak, pos)
            for kk, i in sub.items():
                if i is not None and i not in taken:
                    w = next(w for w in weak if w.key == kk)
                    by_key[i].take(w)
                    taken.add(i)
                    self.log.append((t, f"  weak {kk} seen {w.seen} -> #{i} kept (cost {sc[kk]})"))
        span = (self.last_sign - self.hand_t0) / self.fps if self.hand_t0 is not None else 1e9
        for x in before:
            if x.key not in taken:
                if after_hand and span < R["reach"]["min_take_s"]:
                    x.visible = False                      # a hand there this briefly cannot lift a toy out
                    if pos and pos.get(x.key) is not None:
                        x.c = pos[x.key]
                    self.log.append((t, f"  #{x.key} cls {x.cls} not found, but the hand was in only {span:.2f} s -> hidden"))
                    continue
                if after_hand and R["reach"]["use"] and not self.reached(x):
                    x.visible = False                      # the hand never came near it: hidden behind a toy
                    if pos and pos.get(x.key) is not None:
                        x.c = pos[x.key]
                    self.log.append((t, f"  #{x.key} cls {x.cls} not found, but the hand never reached it -> hidden"))
                    continue
                x.missing, x.visible = True, False
                self.events.append((t, "missing", x.key))
                self.log.append((t, f"  #{x.key} cls {x.cls} MISSING"))
        self.window, self._P = [], {}
        self.trail.clear()

    def reached(self, x):
        """could the hand have taken toy x? its place (moved with the tray's turn) on each frame of the hand moment lies
        within reach.margin_toys toy sizes of where the arm / hand was on that frame"""
        Rr = self.R["reach"]
        c0 = self.snap[2].get(x.key) if self.snap is not None else None
        if not self.reach or c0 is None:
            return True                                    # no picture of where the hand was: cannot rule it out
        if self.G is None and self.reach[-1][0] - self.snap_t > Rr["max_unknown_turn_s"] * self.fps:
            return True                                    # turn unknown and a long moment: places unknown
        q = Rr["scale"]
        r = max(1, int(Rr["margin_toys"] * np.sqrt(max(1.0, x.area)) / q))
        for tk, mk in self.reach:
            cx, cy = self.predict(c0, tk - self.snap_t)
            h, w = mk.shape
            y, xx = int(cy / q), int(cx / q)
            win = mk[max(0, y - r):min(h, y + r + 1), max(0, xx - r):min(w, xx + r + 1)]
            if win.any():
                return True
        return False

    # ---- pending masks between hands (no known toy): after new_min frames -> hidden / missing / new toy
    def pending_step(self, frame, left, t):
        R, cfg = self.R, self.cfg
        left, _ = step(self.pending, left, frame, cfg, R, t)
        for d in left:
            self.pkey += 1
            self.pending.append(Track(f"p{self.pkey}", d, t))
        keep = []
        for p in self.pending:
            if t - p.last_t > self.pend_gap:
                continue                                   # gone again: a passing double mask
            if p.visible:
                add_view(p, frame, cfg, self.emb, t, self.every)
            done = False
            if p.hists and p.seen >= min(self.hid_min, self.new_min):
                done = self.adopt(p, t, quick=p.seen < self.new_min)
            if not done:
                keep.append(p)
        self.pending = keep

    def best_look(self, toys, p):
        """the toy of `toys` that looks most like pending mask p (colour + DINOv2 + size) and its cost, or (None, None)"""
        if not toys:
            return None, None
        mp, cost = hp.match_ids(known_of(toys), cur_of([p]), None, dict(self.cfg, reid_max_cost=float("inf")))
        return next(x for x in toys if x.key == mp[p.key]), cost[p.key]

    def adopt(self, p, t, quick=False):
        """pending mask p -> a hidden toy (it shows again), a missing toy (back without the hand being seen) or NEW.
        quick (seen >= hidden_min_s only): only a hidden toy it clearly looks like (counts cannot change). Returns True
        when p was given an ID."""
        R = self.R
        hidden = [x for x in self.roster if not x.missing and not x.visible and x.cls == p.cls]
        miss = [x for x in self.roster if x.missing and x.cls == p.cls]
        xh, ch = self.best_look(hidden, p)
        xm, cm = self.best_look(miss, p)
        costs = (f"look cost: hidden {'-' if xh is None else f'#{xh.key} {ch:.2f}'}, "
                 f"missing {'-' if xm is None else f'#{xm.key} {cm:.2f}'}")
        back = xm is not None and cm <= self.cfg["reid_max_cost"] and (xh is None or cm + R["back_margin"] < ch)
        if quick:
            if xh is None or back or ch > R["hidden_quick_max_cost"]:
                return False                               # not clear yet: wait for new_min_s
            qm = R.get("quick_margin")                     # rule (a): a missing toy looks about as good -> wait too
            if qm is not None and xm is not None and cm < ch + qm:
                return False
            xh.take(p)
            self.log.append((t, f"mask seen {p.seen} frames at ({p.c[0]:.0f},{p.c[1]:.0f}) cls {p.cls} -> hidden "
                                f"#{xh.key} (quick; {costs})"))
            return True
        if xh is not None and not back:                    # closest hidden toy of its kind (the same toys come back)
            xh.take(p)
            self.log.append((t, f"mask seen {p.seen} frames at ({p.c[0]:.0f},{p.c[1]:.0f}) cls {p.cls} -> hidden "
                                f"#{xh.key} ({costs})"))
            return True
        if back:                                           # a missing toy back without the hand being seen
            xm.take(p)
            self.events.append((t, "back", xm.key))
            self.log.append((t, f"mask seen {p.seen} frames cls {p.cls} -> missing #{xm.key} is BACK ({costs})"))
            return True
        p.key, p.missing = self.next_id, False
        self.next_id += 1
        self.roster.append(p)
        self.events.append((t, "new toy (no hand)", p.key))
        self.log.append((t, f"mask seen {p.seen} frames at ({p.c[0]:.0f},{p.c[1]:.0f}) cls {p.cls} -> NEW #{p.key} (no hand; "
                            f"{costs})"))
        return True

    # ---- one frame
    def frame(self, frame, t, obs):
        cfg, R = self.cfg, self.R
        dets, weaks, mp_hit, pf = obs["dets"], obs["weaks"], obs["mp_hit"], obs["pf"]
        self.tray = obs["tray"]
        H = R["hand"]
        if mp_hit and H.get("mp_inside_min", 0) > 0 and obs.get("mp_pts") is not None and self.tray is not None:
            mp_hit = mp_inside(obs["mp_pts"], self.tray) >= H["mp_inside_min"]   # not only fingertips at the rim
        alone = mp_hit and pf <= H["mp_alone_max_arm"]     # MediaPipe sees a hand but no arm at all in the tray
        if alone:
            self.mp_alone.append(t)
        while self.mp_alone and t - self.mp_alone[0] >= max(1, int(round(H["mp_alone_window_s"] * self.fps))):
            self.mp_alone.popleft()
        mp_ok = mp_hit and (not alone or len(self.mp_alone) >= H["mp_alone_min_frames"])
        arm = pf >= cfg["hand_person_strong_frac"]
        amf = H.get("arm_alone_min_frames") or 1           # an arm WITHOUT a MediaPipe hand counts only when seen on at least
        if amf > 1:                                        # this many frames within arm_alone_window_s (2026-11-18 team clip:
            if arm and not mp_ok:                          # a resting arm's mask jumped for ONE frame -> 6 s false pause)
                self.arm_alone.append(t)
            while self.arm_alone and t - self.arm_alone[0] >= max(1, int(round(H["arm_alone_window_s"] * self.fps))):
                self.arm_alone.popleft()
            arm = arm and (mp_ok or len(self.arm_alone) >= amf)
        strong = mp_ok or arm
        weak = pf >= cfg["hand_person_min_frac"]
        st = self.state
        if strong and st in ("start", "run"):
            self.reach, self.hand_t0 = [], t               # a new hand moment: where it reaches starts empty
        if obs.get("reach") is not None and (strong or st in ("hand", "settle")):
            sh, pk = obs["reach"]
            self.reach.append((t, np.unpackbits(pk)[:sh[0] * sh[1]].reshape(sh).astype(bool)))
        if strong and st in ("start", "run", "confirm"):
            if st == "start":
                self.resolve(frame, t, False)              # what was learned so far
                self.shown = self.counts()
            if st in ("start", "run"):
                pf_, polys, _ = self.prev if self.prev else (frame, [], None)
                self.snap = (pf_, polys + ([self.tray] if self.tray is not None else []),
                             {x.key: x.c for x in self.roster if not x.missing})
                self.snap_t, self.G, self._P = t - 1, self.turn_model(), {}
            self.window, self.pending = [], []
            self.state, self.last_sign = "hand", t
        elif st == "hand":
            if strong or weak:
                self.last_sign = t
            elif t - self.last_sign > self.hold:
                self.state, self.state_until = "settle", t + self.settle
        elif st == "settle":
            if strong or weak:
                self.state, self.last_sign = "hand", t
            elif t >= self.state_until:
                self.state, self.state_until, self.win_t0 = "confirm", t + self.confirm, t
        if self.state in ("start", "confirm"):
            self.window_step(frame, dets, weaks, t)
            if t >= self.state_until:
                self.resolve(frame, t, self.state == "confirm")
                self.state = "run"
        elif self.state == "run":
            left, self.motion[t] = step(self.roster, dets, frame, cfg, R, t)
            self.motion = {k: v for k, v in self.motion.items() if k > t - PAST}
            for x in self.roster:
                if x.visible and x.last_t == t:
                    add_view(x, frame, cfg, self.emb, t, self.every)
            self.pending_step(frame, left, t)
            self.trail.append((t, {x.key: x.c for x in self.roster if not x.missing and x.visible and x.last_t == t}))
        if self.state == "run":
            self.shown = self.counts()
        self.prev = (frame, [x.poly for x in self.roster if x.visible and not x.missing], self.tray)
        vis = [x for x in self.roster if x.visible and not x.missing and x.last_t == t] if self.state == "run" else []
        return dict(state=self.state, shown=self.shown, vis=vis, tray=self.tray, tray_concave=obs.get("tray_concave", 0.0),
                    mp_hit=mp_hit, person=round(pf, 4),
                    seen=len(dets), weak=len(weaks),
                    hidden=sum(1 for x in self.roster if not x.missing and not (x.visible and x.last_t == t)))


# ------------------------------------------------------------------ drawing
def missing_text(sh, D):
    n = sh["missing_ducks"] + sh["missing_non_ducks"]
    if n == 0:
        return f"{D['missing']}: 0"
    a, b = sh["missing_ducks"], sh["missing_non_ducks"]
    word = lambda k, one, many: one if k == 1 else many
    if a and b:                                            # Missing: 2 (1 duck, 1 non-duck)
        kinds = f"{a} {word(a, D['duck_word'], D['ducks_word'])}, {b} {word(b, D['non_duck_word'], D['non_ducks_word'])}"
    elif a:                                                # Missing: 1 (duck)
        kinds = word(a, D["duck_word"], D["ducks_word"])
    else:
        kinds = word(b, D["non_duck_word"], D["non_ducks_word"])
    return f"{D['missing']}: {n} ({kinds})"


def overlap_to_deeper(shapes, P):
    """DRAWING ONLY (display.deep_overlap): where two toys' masks overlap by at least min_frac of the smaller toy and the
    overlap lies clearly DEEPER inside one toy (mean distance to its mask edge >= ratio x the other's), it is drawn only
    with that toy - for the other it is a strip sticking out (12-45: a rectangle of the pink Among Us' mask lying on
    the blue one f505-518 / f794-800). Tested 2026-11-13: touches 1 frame in Farhan, 2 in b4be."""
    if not P or not P.get("use") or len(shapes) < 2:
        return shapes
    keys = list(shapes)
    x0 = min(ox for _, ox, _ in shapes.values()); y0 = min(oy for _, _, oy in shapes.values())
    x1 = max(ox + m.shape[1] for m, ox, _ in shapes.values()); y1 = max(oy + m.shape[0] for m, _, oy in shapes.values())
    full = {}
    for k in keys:
        m, ox, oy = shapes[k]
        o = np.zeros((y1 - y0, x1 - x0), bool)
        o[oy - y0:oy - y0 + m.shape[0], ox - x0:ox - x0 + m.shape[1]] = m
        full[k] = o
    changed = set()
    for i, ka in enumerate(keys):
        for kb in keys[i + 1:]:
            both = full[ka] & full[kb]
            if not both.any() or both.sum() < P["min_frac"] * min(full[ka].sum(), full[kb].sum()):
                continue
            da = cv2.distanceTransform(full[ka].astype(np.uint8), cv2.DIST_L2, 3)[both].mean()
            db = cv2.distanceTransform(full[kb].astype(np.uint8), cv2.DIST_L2, 3)[both].mean()
            if da >= P["ratio"] * max(0.5, db):
                full[kb] &= ~both; changed.add(kb)
            elif db >= P["ratio"] * max(0.5, da):
                full[ka] &= ~both; changed.add(ka)
    if not changed:
        return shapes
    out = dict(shapes)
    for k in changed:
        ys, xs = np.nonzero(full[k])
        if len(ys):
            out[k] = (full[k][ys.min():ys.max() + 1, xs.min():xs.max() + 1], int(xs.min()) + x0, int(ys.min()) + y0)
    return out


def drop_pieces_on_others(shapes, max_on, neck=0):
    """DRAWING ONLY (display.piece_on_other_max): a DETACHED piece of a toy's mask (not its biggest piece) lying for at
    least max_on of its pixels on another toy's mask is not drawn - it is the other toy's area (12-45: a rectangle of
    the pink Among Us' mask lying on the blue one, f499-518 / f783-802). Detached heads on the tray stay."""
    if not max_on or len(shapes) < 2:
        return shapes
    out = dict(shapes)
    k_ = disk(neck) if neck > 0 else None
    for k, (m, ox, oy) in shapes.items():
        mo = cv2.morphologyEx(m.astype(np.uint8), cv2.MORPH_OPEN, k_) if neck > 0 else m.astype(np.uint8)
        n, lab, st, _ = cv2.connectedComponentsWithStats(mo, connectivity=8)   # pieces joined by a neck thinner than
        if n <= 2:                                                             # 2 x neck px count as detached
            continue
        big = 1 + int(np.argmax(st[1:, cv2.CC_STAT_AREA]))
        others = np.zeros(m.shape, bool)
        for k2, (m2, ox2, oy2) in shapes.items():
            if k2 == k:
                continue
            x0, y0 = max(ox, ox2), max(oy, oy2)
            x1, y1 = min(ox + m.shape[1], ox2 + m2.shape[1]), min(oy + m.shape[0], oy2 + m2.shape[0])
            if x1 > x0 and y1 > y0:
                others[y0 - oy:y1 - oy, x0 - ox:x1 - ox] |= m2[y0 - oy2:y1 - oy2, x0 - ox2:x1 - ox2]
        keep = m.copy()
        body = cv2.dilate((lab == big).astype(np.uint8), k_).astype(bool) if neck > 0 else (lab == big)
        for j in range(1, n):
            if j != big:
                piece = lab == j
                if (piece & others).sum() >= max_on * piece.sum():
                    grown = cv2.dilate(piece.astype(np.uint8), k_).astype(bool) if neck > 0 else piece
                    keep &= ~(grown & ~body)
        if keep.sum() != m.sum():
            out[k] = (keep, ox, oy)
    return out


def add_parts(frame, shapes, tray, s, P):
    """DRAWING ONLY (display.parts): a COLOURFUL piece on the tray that is in no toy's mask and touches mainly one toy
    is drawn as part of that toy - beaks, heads and tails the model leaves out (its training outlines left them out
    too, e.g. Farhan #17's beak). Colourful = Lab chroma > min_chroma and far from the tray's own colour, or white =
    lightness >= the tray's + bright_over (grey tray and shadows never count); size between min_frac and max_frac of
    that toy; 'mainly one toy' = the toy touching it most touches it at least dominant_ratio x as much as the next.
    shapes: {id: (mask, x0, y0)} at half size -> same, with the pieces added."""
    if not P or not P.get("use") or tray is None or not shapes:
        return shapes
    h, w = frame.shape[0] // s, frame.shape[1] // s
    lab = cv2.cvtColor(np.ascontiguousarray(frame[::s, ::s][:h, :w]), cv2.COLOR_BGR2LAB).astype(np.float32)
    keys = list(shapes)
    owner = np.zeros((h, w), np.int32)
    for i, k in enumerate(keys):
        m, ox, oy = shapes[k]
        u0, v0, u1, v1 = max(0, ox), max(0, oy), min(w, ox + m.shape[1]), min(h, oy + m.shape[0])
        if u1 > u0 and v1 > v0:
            sub = owner[v0:v1, u0:u1]
            sub[m[v0 - oy:v1 - oy, u0 - ox:u1 - ox]] = i + 1
    tm = np.zeros((h, w), np.uint8)
    cv2.fillPoly(tm, [np.round(tray / s).astype(np.int32).reshape(-1, 1, 2)], 1)
    free = tm.astype(bool) & (owner == 0)
    far = free & ~cv2.dilate((owner > 0).astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    if far.sum() < 200:
        return shapes
    a0, b0 = np.median(lab[..., 1][far]), np.median(lab[..., 2][far])
    chroma = np.hypot(lab[..., 1] - 128, lab[..., 2] - 128)
    cand = free & (chroma > P["min_chroma"]) & (np.hypot(lab[..., 1] - a0, lab[..., 2] - b0) > P["min_chroma"])
    if P.get("bright_over", 0) > 0:                        # white parts (a unicorn's tail / mane)
        cand |= free & (chroma <= P["min_chroma"]) & (lab[..., 0] >= np.median(lab[..., 0][far]) + P["bright_over"])
    cand = cv2.morphologyEx(cand.astype(np.uint8), cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    n, cl, st, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    area = np.bincount(owner.ravel(), minlength=len(keys) + 1)
    changed = set()
    for j in range(1, n):
        x, y, bw, bh, ar = st[j]
        y0, y1, x0, x1 = max(0, y - 2), min(h, y + bh + 2), max(0, x - 2), min(w, x + bw + 2)
        comp = cl[y0:y1, x0:x1] == j
        ring = cv2.dilate(comp.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool) & ~comp
        if (~tm[y0:y1, x0:x1].astype(bool) & ring).sum() > P["max_outside"] * max(1, ring.sum()):
            continue                                       # at the tray's edge: the wall / floor beyond the rim
        tch = np.bincount(owner[y0:y1, x0:x1][ring], minlength=len(keys) + 1)
        tch[0] = 0
        order = np.argsort(tch)[::-1]
        i, second = int(order[0]), int(tch[order[1]]) if len(order) > 1 else 0
        if (tch[i] == 0 or tch[i] < P["dominant_ratio"] * second
                or not (P["min_frac"] * area[i] <= ar <= P["max_frac"] * area[i])):
            continue
        owner[y0:y1, x0:x1][comp] = i
        changed.add(i)
    if not changed:
        return shapes
    out = dict(shapes)
    for i in changed:
        ys, xs = np.nonzero(owner == i)
        out[keys[i - 1]] = (owner[ys.min():ys.max() + 1, xs.min():xs.max() + 1] == i, int(xs.min()), int(ys.min()))
    return out


def draw(frame, out, cfg, D):
    vis = frame.copy()
    H, W = vis.shape[:2]
    k = W / D["ref_width"]                                 # sizes in the config are for a 1920 px wide frame
    lw = max(1, int(round(D["line"] * k)))
    cl, sc = cfg["classes"], cfg["runtime"]["mask_scale"]
    shapes = out.get("outline") or {}                      # display.smooth (else this frame's mask pieces)
    colour = lambda x: tuple(D["colours"]["duck" if x.cls == cl["duck"] else "non_duck"])
    full = {}                                              # each toy's mask pieces at FULL size (edges not shaved)
    for x in out["vis"]:
        m, ox, oy = shapes.get(x.key) or own_shape(x, D.get("min_piece", 0.0))
        up = cv2.resize(m.astype(np.uint8), (m.shape[1] * sc, m.shape[0] * sc), interpolation=cv2.INTER_NEAREST)
        X0, Y0 = ox * sc, oy * sc
        u0, v0, u1, v1 = max(0, X0), max(0, Y0), min(W, X0 + up.shape[1]), min(H, Y0 + up.shape[0])
        if u1 > u0 and v1 > v0:
            full[x.key] = (up[v0 - Y0:v1 - Y0, u0 - X0:u1 - X0].astype(bool), u0, v0)
    a = D.get("fill_alpha", 0)
    if a > 0 and full:                                     # see-through fill in the class colour
        over = vis.copy()
        for x in out["vis"]:
            if x.key in full:
                m, u0, v0 = full[x.key]
                over[v0:v0 + m.shape[0], u0:u0 + m.shape[1]][m] = colour(x)
        vis = cv2.addWeighted(over, a, vis, 1 - a, 0)
    tc = out.get("tray_concave")                           # None: no tray found on this frame (the last outline is kept)
    if (out["tray"] is not None and D["show_tray"] and out["state"] == "run"   # an arm in the tray bends the outline
            and tc is not None and tc <= D["tray_max_concave"]):
        cv2.polylines(vis, [out["tray"].astype(np.int32)], True, tuple(D["colours"]["tray"]), lw)
    for x in out["vis"]:
        if x.key in full:
            m, u0, v0 = full[x.key]
            cs, _ = cv2.findContours(m.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.polylines(vis, [c + np.array([[[u0, v0]]]) for c in cs], True, colour(x), lw)
    for x in out["vis"]:
        txt = str(x.key)
        sc = D["id_scale"] * k
        (tw, th), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, sc, max(1, int(2 * k)))
        p = (int(x.c[0] - tw / 2), int(x.c[1] + th / 2))
        cv2.putText(vis, txt, p, cv2.FONT_HERSHEY_SIMPLEX, sc, (0, 0, 0), max(2, int(5 * k)), cv2.LINE_AA)
        cv2.putText(vis, txt, p, cv2.FONT_HERSHEY_SIMPLEX, sc, (255, 255, 255), max(1, int(2 * k)), cv2.LINE_AA)
    sh = out["shown"]
    lines = [D["counting"]] if sh is None else [f"{D['ducks']}: {sh['ducks']}", f"{D['non_ducks']}: {sh['non_ducks']}",
                                                 missing_text(sh, D)]
    sc, th = D["box_scale"] * k, max(1, int(2 * k))
    pad, gap = int(D["box_pad"] * k), int(D["box_gap"] * k)
    sizes = [cv2.getTextSize(s, cv2.FONT_HERSHEY_SIMPLEX, sc, th)[0] for s in lines]
    bw = max(w for w, _ in sizes) + 2 * pad
    bh = sum(h for _, h in sizes) + gap * (len(lines) - 1) + 2 * pad
    x0, y0 = int(D["box_margin"] * k), int(D["box_margin"] * k)
    roi = vis[y0:y0 + bh, x0:x0 + bw]
    vis[y0:y0 + bh, x0:x0 + bw] = cv2.addWeighted(roi, 1 - D["box_alpha"], np.zeros_like(roi), D["box_alpha"], 0)
    y = y0 + pad
    for s, (w, h) in zip(lines, sizes):
        y += h
        cv2.putText(vis, s, (x0 + pad, y), cv2.FONT_HERSHEY_SIMPLEX, sc, tuple(D["colours"]["text"]), th, cv2.LINE_AA)
        y += gap
    if out["state"] in ("hand", "settle", "confirm"):
        txt, sc2, th2 = D["paused"], D["banner_scale"] * k, max(2, int(3 * k))
        (tw, tht), _ = cv2.getTextSize(txt, cv2.FONT_HERSHEY_SIMPLEX, sc2, th2)
        bx0, by0 = (W - tw) // 2 - pad, H - int(D["banner_margin"] * k) - tht - 2 * pad
        cv2.rectangle(vis, (bx0, by0), (bx0 + tw + 2 * pad, by0 + tht + 2 * pad), tuple(D["colours"]["banner"]), -1)
        cv2.putText(vis, txt, (bx0 + pad, by0 + pad + tht), cv2.FONT_HERSHEY_SIMPLEX, sc2,
                    tuple(D["colours"]["banner_text"]), th2, cv2.LINE_AA)
    return vis
