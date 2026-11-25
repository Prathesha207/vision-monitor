# TrayCount changes

## 1.3.0 (2026-11-25)
New model + faster: `weights/best_seg_v4_ep20.pt` (v4: v3 trained further on more and corrected frames) replaces
`best_seg_v3.pt`, and the toy clean-up (one mask per toy) is ~5x faster (config `toy_cleanup.fast: true`; same rule,
identical output on 1086 of 1086 frames tested, 112 -> 22 ms per frame). Model scores on our held-out test frames
(mask mAP50-95): 0.929 -> 0.950, tray outline 0.936 -> 0.991.
Tested (RTX 4050 Laptop, 1920x1080): Farhan_1790 3258 frames, counts identical frame by frame to our runtime with the
same model (1636 of 1636 frames compared), 6.4 fps (median latency 141 ms, 90% under 187 ms), no overlay.
Our runtime on our 3 test videos (overlay video written): 3.6 - 6.2 fps (before, model v2_ft + old clean-up: 2.9 - 4.6), counts 100 %.
Known: two toy-ID swaps not seen with our earlier model v2_ft (not compared with v3) (12-45: two toys swap IDs after a hand moment; b4be: two toys swap
for 43 labelled frames). Counts are not affected. Being fixed for the next version.

## 1.2.2 (2026-11-19)
Fix: `false_mask` was still reported up to 1 s after one of the two masks had gone (team feedback, duck_new_video3 f178:
"9 on 10" with no double mask). Now only while both masks are there and overlap.
New: ANOMALY reason `mask_missing` - a toy counted on the tray has no mask and lies inside another toy's mask for >= 0.5 s
(one mask over two toys): reported with the toy's ID, the mask it is in, a picture of that area and its template.
Config `output.mask_anomalies.missing_min_s`. Tested: team clip 4 alarms (#10 inside #9, incl. f178), Farhan_1790 / 12-45
none, b4be 1 (a duck fully behind the tall die). Counts, IDs and pauses unchanged from 1.2.1.

## 1.2.1 (2026-11-18)
Fix: an arm resting beside the tray no longer pauses counting because of a single-frame jump of its mask. An arm without
a MediaPipe hand now counts as a hand only when seen on >= 2 frames within 0.2 s (`runtime.hand.arm_alone_min_frames`,
`arm_alone_window_s`; MediaPipe hands already had this rule). Tested: team clip duck_new_video3 6 s false pause -> none;
every real hand pause in our 3 test videos kept (counts 100 %), b4be IDs 94.98 -> 97.98 %. Nothing else changed.

## 1.2.0 (2026-11-18)
New: `mask_anomalies` + ANOMALY reason `false_mask` - two masks on one toy (two toy masks overlapping >= 30 % for >= 0.5 s;
the less confident one is reported with a picture of that part only). Config `output.mask_anomalies`.
`expected_duck_count` can be set per run: `reset(fps, expected_duck_count=17)` (or config `output.expected_duck_count`);
with 18 ducks on the tray and 17 expected, the newest duck ID is `excess_ids` and the status ANOMALY `too_many_ducks`.
New setting `runtime.hand.arm_inside_rim_frac` (off). Counts, IDs and pauses unchanged from 1.1.0.

## 1.1.0 (2026-11-18)
New in the results: `status` (WARMING / HAND / NORMAL / ANOMALY) + `reasons`, `expected_duck_count` (learned at warm-up),
`missing_ids` / `missing_other_ids`, `added_ids` / `added_count`, `excess_ids` / `excess_count`, `other_ids`, `thumbnails`,
per-toy `confidence` + `min_confidence`, `latency_ms`, `fps`. Templates of every toy (`tc.templates()` + files in `templates/`).
New config section `output`. Counts, IDs and pauses unchanged from 1.0.0 (checked frame by frame).

## 1.0.0 (2026-11-18)
First version. Model `weights/best_seg_v3.pt`. Rule (a) on (`runtime.quick_margin: 0.3`).
