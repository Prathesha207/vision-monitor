"""traycount/_helpers.py - helper functions copied unchanged from the project's scripts/v2_helpers.py (2026-11-18);
only change: Embedder loads the DINOv2 weights from a local file (config reid_embed_weights)."""
import cv2
import numpy as np
from scipy.optimize import linear_sum_assignment


def pts(poly):
    return np.asarray(poly, np.float32).reshape(-1, 2)


# ------------------------------------------------------------------ on the tray?


def base_point(poly, cfg):
    """where the toy stands: bottom part of its outline (the top of a toy can stick out past the far rim)."""
    q = pts(poly)
    y0, y1 = q[:, 1].min(), q[:, 1].max()
    h = max(1.0, y1 - y0)
    low = q[q[:, 1] >= y1 - cfg["on_tray_base_frac"] * h]
    return float(low[:, 0].mean()), float(y1 - 0.5 * cfg["on_tray_base_frac"] * h)


def on_tray(poly, tray_poly, cfg):
    if poly is None:
        return False
    if tray_poly is None:                                   # no tray outline for this frame: keep everything
        return True
    t = pts(tray_poly).reshape(-1, 1, 2)
    tol = cfg["on_tray_tol_frac"] * np.sqrt(max(1.0, cv2.contourArea(t)))
    return cv2.pointPolygonTest(t, base_point(poly, cfg), True) >= -tol


# ------------------------------------------------------------------ tray outline: cable spikes


def tray_zone(tray_poly, shape, cfg):
    """filled tray outline grown by hand_tray_margin_frac x sqrt(tray area) (same margin as the MediaPipe check)."""
    m = np.zeros(shape[:2], np.uint8)
    t = pts(tray_poly).astype(np.int32)
    cv2.fillPoly(m, [t], 1)
    r = int(cfg["hand_tray_margin_frac"] * np.sqrt(max(1.0, cv2.contourArea(t.reshape(-1, 1, 2)))))
    if r > 0:
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    return m.astype(bool)


class Embedder:
    """DINOv2 embedding of one toy (crop around it, everything outside its mask grey). Tells apart toys with the
    same colours (dalmatian vs cow duck, yellow cube vs yellow toucan) that a colour histogram mixes up."""

    def __init__(self, cfg, device="cuda:0"):
        import timm
        import torch
        self.torch, self.device, self.size = torch, device, cfg["reid_embed_size"]
        local = cfg.get("reid_embed_weights")             # package: the weights file shipped next to the config (no internet)
        extra = dict(pretrained_cfg_overlay=dict(file=local)) if local else {}
        self.model = timm.create_model(cfg["reid_embed_model"], pretrained=True, num_classes=0,
                                       img_size=self.size, **extra).eval().to(device)
        self.mean = np.array([0.485, 0.456, 0.406], np.float32)
        self.std = np.array([0.229, 0.224, 0.225], np.float32)
        self.fill = np.array(cfg["reid_embed_fill_bgr"], np.uint8)

    def __call__(self, bgr, poly):
        q = pts(poly).astype(np.int32)
        x, y, w, h = cv2.boundingRect(q)
        s, cx, cy = max(w, h) // 2 + 4, x + w // 2, y + h // 2
        x0, y0, x1, y1 = max(0, cx - s), max(0, cy - s), min(bgr.shape[1], cx + s), min(bgr.shape[0], cy + s)
        m = np.zeros((y1 - y0, x1 - x0), np.uint8)
        cv2.fillPoly(m, [q - np.array([x0, y0], np.int32)], 1)
        c = bgr[y0:y1, x0:x1].copy()
        c[m == 0] = self.fill
        c = (cv2.resize(c, (self.size, self.size))[:, :, ::-1].astype(np.float32) / 255.0 - self.mean) / self.std
        with self.torch.no_grad():
            v = self.model(self.torch.from_numpy(c.transpose(2, 0, 1).copy())[None].to(self.device))[0].float().cpu().numpy()
        return v / max(1e-6, float(np.linalg.norm(v)))


def descriptor(bgr, poly, cfg, embedder=None):
    """{hist (HSV colour histogram inside the toy), emb (DINOv2, if embedder), area, c (centre)}"""
    H, W = bgr.shape[:2]
    q = pts(poly).astype(np.int32)
    x, y, w, h = cv2.boundingRect(q)
    x0, y0, x1, y1 = max(0, x), max(0, y), min(W, x + w), min(H, y + h)
    m = np.zeros((max(1, y1 - y0), max(1, x1 - x0)), np.uint8)
    cv2.fillPoly(m, [q - np.array([x0, y0], np.int32)], 255)
    k = cfg["reid_erode_px"]
    if k > 0:
        m2 = cv2.erode(m, np.ones((2 * k + 1, 2 * k + 1), np.uint8))
        if m2.any():
            m = m2                                          # leave out the edge (tray / neighbour pixels)
    hsv = cv2.cvtColor(bgr[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    hist = cv2.calcHist([hsv], [0, 1, 2], m, list(cfg["reid_hist_bins"]), [0, 180, 0, 256, 0, 256])
    hist = (hist / max(1e-6, float(hist.sum()))).astype(np.float32)
    mo = cv2.moments(q.reshape(-1, 1, 2))
    c = (mo["m10"] / mo["m00"], mo["m01"] / mo["m00"]) if mo["m00"] else tuple(q.mean(0))
    return dict(hist=hist, emb=None if embedder is None else embedder(bgr, poly),
                area=float(max(1.0, cv2.contourArea(q.reshape(-1, 1, 2)))), c=(float(c[0]), float(c[1])))


def match_ids(known, cur, Hm, cfg):
    """known: {id: dict(cls, hists=[...], embs=[...], area, c or None)}  toys seen before, several views each
                                                         (c = position on the reference frame)
    cur:   {key: dict(cls, hist, area, c)}                   toys on the new key frame
    -> ({key: id or None (new toy)}, {key: cost})"""
    kids, ckeys = list(known), list(cur)
    out = {k: None for k in ckeys}
    if not kids or not ckeys:
        return out, {}
    scale = np.sqrt(np.median([cur[k]["area"] for k in ckeys]))   # a normal toy's size on this frame
    C = np.zeros((len(ckeys), len(kids)))
    for a, k in enumerate(ckeys):
        X = cur[k]
        for b, i in enumerate(kids):
            K = known[i]
            app = cfg["reid_w_app"] * min(cv2.compareHist(h, X["hist"], cv2.HISTCMP_BHATTACHARYYA) for h in K["hists"])
            if X.get("emb") is not None and K.get("embs"):
                app += cfg["reid_w_emb"] * min(1.0 - float(e @ X["emb"]) for e in K["embs"])
            if K.get("c") is not None and Hm is not None:
                p = cv2.perspectiveTransform(np.float32([[K["c"]]]), Hm)[0, 0]
                pos = min(1.0, float(np.hypot(p[0] - X["c"][0], p[1] - X["c"][1])) / (cfg["reid_pos_scale"] * scale))
            else:
                pos = cfg["reid_pos_unknown"]
            size = min(1.0, abs(float(np.log(X["area"] / max(1.0, K["area"])))))
            C[a, b] = (app + cfg["reid_w_pos"] * pos + cfg["reid_w_size"] * size
                       + (0.0 if K["cls"] == X["cls"] else cfg["reid_cls_penalty"]))
    cost = {}
    for a, b in zip(*linear_sum_assignment(C)):
        if C[a, b] <= cfg["reid_max_cost"]:
            out[ckeys[a]] = kids[b]
            cost[ckeys[a]] = round(float(C[a, b]), 3)
    return out, cost


def toy_cleanup(masks, conf, cls, cfg):
    """One mask per toy - the SAME rule in the runtime and the measuring tool (settings: toy_cleanup in the config).
    1) a mask lying >= merge_inside_frac inside another toy mask is the same toy (YOLO's own duplicate filter compares
       boxes, so e.g. a unicorn's body box next to the whole-unicorn box survives it) -> merged into it: the union of
       both, the higher score and that score's class;
    2) then a weaker mask lying >= weak_covered_frac on the stronger kept masks is not a toy of its own -> dropped.
    masks: torch (n, H, W) 0/1 on any device; conf, cls: (n,). Returns masks (bool), conf, cls of the kept toys."""
    import torch
    t = cfg["toy_cleanup"]
    conf, cls = np.array(conf, float).copy(), np.array(cls).copy()
    if len(masks) == 0:
        return masks.bool(), conf, cls
    M = masks.float().clone()
    s = int(t["compute_scale"])
    flat = lambda X: X[:, ::s, ::s].reshape(len(X), -1)
    alive = list(range(len(M)))
    while len(alive) > 1:                                  # 1) merge masks lying inside another (repeat until none)
        A = flat(M[alive]); area = A.sum(1); inter = A @ A.T
        order = torch.argsort(area, descending=True).tolist()
        if t.get("fast", False):                           # config toy_cleanup.fast (1.3.0): copy the numbers to the CPU once
            area, inter = area.cpu().numpy(), inter.cpu().numpy()   # per round instead of reading them one by one from the GPU
        hit = None                                         # (same result; 1086/1086 frames identical, 112 -> 22 ms/frame)
        for x in order:
            for y in range(len(alive)):
                if y != x and 0 < area[y] <= area[x] and inter[x, y] >= t["merge_inside_frac"] * area[y]:
                    hit = (alive[x], alive[y])
                    break
            if hit:
                break
        if hit is None:
            break
        a, b = hit
        M[a] = torch.maximum(M[a], M[b])
        if conf[b] > conf[a]:
            cls[a] = cls[b]
        conf[a] = max(conf[a], conf[b])
        alive.remove(b)
    keep = []
    for j in sorted(alive, key=lambda k: -conf[k]):        # 2) strongest first: drop weak masks lying on kept ones
        if keep:
            mj = flat(M[j:j + 1])[0]
            if mj.sum() > 0 and float((flat(M[keep]).max(0).values * mj).sum() / mj.sum()) >= t["weak_covered_frac"]:
                continue
        keep.append(j)
    keep.sort()
    return M[keep] > 0.5, conf[keep], cls[keep]
