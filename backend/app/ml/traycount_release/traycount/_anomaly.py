"""
traycount/_anomaly.py - mask anomalies, read from the runtime's toys after each frame (no decision is changed).

  false_mask   two toy masks overlap by >= overlap_frac of the smaller one, on every frame for >= overlap_min_s:
               two masks on one toy (e.g. head and body as two "toys"). The less confident mask is the false one:
               ids [false id], other_id [the toy it lies on], box = the false mask's box (full-size pixels).

Tested 2026-11-18 (overlap 30 %, 0.5 s): 0 alarms on Farhan_1790 / 12-45, 1 (3 frames) on b4be; the team's clip
duck_new_video3 (one duck split into 3 overlapping masks) 8 alarms. 
  mask_missing a toy counted on the tray has NO mask and its last place lies inside another toy's mask on every frame for
               >= missing_min_s (one mask covering two toys): ids [the toy without a mask], other_id [the mask it is in],
               box = that mask's box.
"""
import numpy as np


def _overlap(a, b):
    """overlapping mask pixels of two toys (box + mask crop at the runtime's mask scale)"""
    x0, y0 = max(a.box[0], b.box[0]), max(a.box[1], b.box[1])
    x1, y1 = min(a.box[2], b.box[2]), min(a.box[3], b.box[3])
    if x1 <= x0 or y1 <= y0:
        return 0
    return int(np.count_nonzero(a.m[y0 - a.box[1]:y1 - a.box[1], x0 - a.box[0]:x1 - a.box[0]]
                                & b.m[y0 - b.box[1]:y1 - b.box[1], x0 - b.box[0]:x1 - b.box[0]]))


def _inside(x, p, s):
    """is full-size point p inside toy x's mask (box + crop at 1/s size)?"""
    u, v = int(p[0] / s) - x.box[0], int(p[1] / s) - x.box[1]
    return 0 <= v < x.m.shape[0] and 0 <= u < x.m.shape[1] and bool(x.m[v, u])


class MaskAnomalies:
    def __init__(self, P, fps, s):
        self.frac, self.s = P["overlap_frac"], s
        self.need = max(1, int(round(P["overlap_min_s"] * fps)))
        self.run = {}                                      # (id, id) -> frames in a row the two masks overlap
        self.hold = max(0, int(round(P.get("hold_s", 0) * fps)))
        ms = P.get("missing_min_s")                        # mask_missing: None = off
        self.need_missing = None if ms is None else max(1, int(round(ms * fps)))
        self.inside_run = {}                               # hidden toy id -> frames in a row its place lies inside another mask
        self.kept = {}                                     # false id -> (last anomaly, frames left to keep reporting it)

    def step(self, roster, t):
        """-> [dict(type 'false_mask', ids [false id], other_id, box [x0, y0, x1, y1] full-size)] for this frame"""
        vis = [x for x in roster if not x.missing and x.visible and x.last_t == t]
        px = {x.key: int(np.count_nonzero(x.m)) for x in vis}
        run, found = {}, []
        for i, a in enumerate(vis):
            for b in vis[i + 1:]:
                ov = _overlap(a, b)
                if not ov or ov < self.frac * max(1, min(px[a.key], px[b.key])):
                    continue
                k = tuple(sorted((a.key, b.key)))
                run[k] = self.run.get(k, 0) + 1
                if run[k] >= self.need:
                    ca, cb = getattr(a, "conf", None) or 0.0, getattr(b, "conf", None) or 0.0
                    false, true = (a, b) if (ca, -px[a.key]) < (cb, -px[b.key]) else (b, a)
                    s = self.s
                    found.append(dict(type="false_mask", ids=[int(false.key)], other_id=int(true.key),
                                      box=[int(false.box[0] * s), int(false.box[1] * s), int(false.box[2] * s),
                                           int(false.box[3] * s)]))
        self.run = run
        live = {int(x.key) for x in vis}
        for a in found:
            self.kept[a["ids"][0]] = (a, self.hold)
        out = list(found)
        byk = {int(x.key): x for x in vis}
        for k, (a, left) in list(self.kept.items()):       # hold_s: bridge a flicker of the overlap, ONLY while both masks
            if any(f["ids"][0] == k for f in found):        # are still there and still overlap (2026-11-19 team: the hold
                continue                                   # kept reporting 9-on-10 after #10's mask had gone)
            o = byk.get(a["other_id"])
            if left <= 0 or k not in live or o is None or not _overlap(byk[k], o):
                del self.kept[k]
                continue
            x = byk[k]                                     # its box where the toy is now
            s = self.s
            a = dict(a, box=[int(x.box[0] * s), int(x.box[1] * s), int(x.box[2] * s), int(x.box[3] * s)])
            self.kept[k] = (a, left - 1)
            out.append(a)
        if self.need_missing is not None:                  # mask_missing: a toy counted on the tray has NO mask and its place
            hid = [x for x in roster if not x.missing and not (x.visible and x.last_t == t)]   # lies inside another toy's
            runs = {}                                      # mask on every frame for >= missing_min_s (swallowed by that mask)
            for x in hid:
                y = next((y for y in vis if _inside(y, x.c, self.s)), None)
                if y is None:
                    continue
                runs[x.key] = self.inside_run.get(x.key, 0) + 1
                if runs[x.key] >= self.need_missing:
                    s = self.s
                    out.append(dict(type="mask_missing", ids=[int(x.key)], other_id=int(y.key),
                                    box=[int(y.box[0] * s), int(y.box[1] * s), int(y.box[2] * s), int(y.box[3] * s)]))
            self.inside_run = runs
        return out
