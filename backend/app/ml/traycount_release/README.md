# TrayCount 1.3.0

Ducks / non-ducks on a turning tray: counts, an ID per toy, pause while a hand is in the tray, missing toys (and their
kind) after a hand. One entry point: `TrayCounter`.

## Install
```
pip install torch==2.15.0 torchvision==0.29.2 --index-url https://download.pytorch.org/whl/cu132   # CUDA build for your driver
pip install -r requirements.txt
pip install traycount-1.3.0-py3-none-any.whl
```
Keep `config.yaml` and the `weights/` folder together (paths in the config are relative to the config file).

## Use
```python
from traycount import TrayCounter
tc = TrayCounter("config.yaml")
tc.load_model()                 # all 4 models onto the GPU (error if no CUDA GPU)
tc.reset(fps=30)                # new video / camera session; fps = how many frames per second you pass in
for frame in frames:            # BGR frames (OpenCV), every frame, in order
    r = tc.process_frame(frame) # results (below)
    pic = tc.draw(frame)        # optional: the frame with our overlay (counts box, outlines + IDs, tray, pause banner)
tc.unload_model()               # frees the GPU
```
`tc.process_video("video.mp4", out_path="overlay.mp4")` does a whole file (reset with the video's fps included).
See `example.py`.

## Results of `process_frame`
| key | meaning |
|---|---|
| `frame_index` | 0, 1, 2 ... since `reset` |
| `status` | `WARMING` (learning the toys, first 1 s), `HAND` (hand in the tray, until the toys are found again), `NORMAL`, `ANOMALY` |
| `reasons` | for ANOMALY, any of `missing_duck`, `too_few_ducks`, `too_many_ducks`, `other_species_present`, `false_mask`, `mask_missing` |
| `expected_duck_count` | ducks counted at the end of warm-up (per video / session); or the number given in the config / `reset(expected_duck_count=...)` |
| `counts` | `None` while warming, else `{ducks, non_ducks, missing, missing_ducks, missing_non_ducks}` |
| `missing_ids` / `missing_other_ids` | IDs of ducks / non-ducks taken away |
| `added_ids`, `added_count` | IDs that appeared after warm-up and are on the tray (a new ID and template are made when they appear) |
| `excess_ids`, `excess_count` | the newest duck IDs above `expected_duck_count` |
| `other_ids` | IDs of the non-ducks on the tray |
| `mask_anomalies` | `[{type, ids, other_id, box: [x0, y0, x1, y1], image: base64 JPEG}]` - `false_mask`: two masks on one toy (ids = the false mask, other_id = the toy it lies on, image = that part only, only while both masks are there); `mask_missing`: a toy with NO mask inside another toy's mask (ids = the toy without a mask, other_id = the mask it is in, image = that area; its template is in `thumbnails`) |
| `thumbnails` | `{id: base64 JPEG}` the template picture of every ID named in the lists above |
| `toys` | toys seen on this frame: `{id, kind: "duck"/"non_duck", confidence: 0..1, center: [x, y], outline: [[[x, y], ...], ...]}` (empty while warming / HAND) |
| `min_confidence` | the lowest toy confidence on this frame |
| `tray`, `tray_clean` | tray outline `[[x, y], ...]` (found automatically on every frame - no ROI file needed) or `None`; True when clean enough to draw |
| `latency_ms` | `{models, total}` time for this frame (ms) |
| `fps` | frames processed per second over the last 30 frames (`output.fps_window`) |
| `state`, `paused` | the runtime's own state (`start`, `run`, `hand`, `settle`, `confirm`) and whether counting is paused |

NORMAL = ducks == expected_duck_count, non_ducks == 0, missing_ducks == 0, excess_ids == [].
During HAND the IDs, counts and reasons stay as they were before the hand.

## Templates (a picture of every toy)
Made **once**: for every toy at the end of warm-up (the first frame it is seen after warm-up), and for a toy added later when
it gets its ID. Used for the whole session.
- `tc.templates()` -> `{id: {id, kind, frame_index, path, b64}}`
- saved as files: `templates/<session>/id_04_duck.jpg` next to `config.yaml` (`output.templates_dir`; `null` = memory only).
  `<session>` = `reset(session=...)` or the date and time of `reset`.
- `thumbnails` in each result = the templates of the IDs that result names (missing / added / excess / other).

## Known limits
If a hand takes out a duck and puts a **different** duck in during the same hand moment, the app takes it for the same duck
coming back (it keeps the old ID): no missing, no added.
- One mask covering **two** toys is reported only through `mask_missing` - when the covered toy has its own ID. If that toy
  never got an ID (merged from the start), nothing tells the mask apart from one toy. Fixed by the next model training.
- `mask_missing` is also reported when a duck is fully hidden behind a tall toy for more than 0.5 s (looks the same).
- An arm resting **beside** the tray: fixed in 1.2.1 for the case seen (the arm's mask jumping into the tray zone for a
  single frame). An arm that really lies inside the tray zone for several frames still pauses counting.

## Files
- `traycount-1.3.0-py3-none-any.whl` - the code
- `config.yaml` - thresholds / parameters only (with comments); paths relative to the file
- `weights/` - `best_seg_v4_ep20.pt` (toys + tray), `yolo11m-seg.pt` (arms), `hand_landmarker.task` (MediaPipe hands),
  `dinov2_vits14.safetensors` (DINOv2 ViT-S/14, tells same-coloured toys apart; no internet needed)
- `requirements.txt` - exact versions it was tested with
- `example.py`

## Notes
- Speed (1.3.0): 6.4 frames per second on Farhan_1790 (median latency 141 ms, 90% under 187 ms, no overlay) on an RTX 4050
  Laptop GPU (6 GB) with 1920x1080 frames; slower on busier videos (our runtime with overlay: 3.6 - 6.2 fps). 1.2.2: ~3 fps.
- Frame rate: give it at least 5 frames per second. Tested on a 15 fps video given every 3rd frame (5 fps): counts the same as
  with every frame on 97% of frames; every 5th frame (3 fps): only 20% the same - toys move too far between frames.
- Camera: pass the frame rate you actually call `process_frame` at to `reset(fps=...)` (all time settings are in seconds).
- Linux without a desktop: MediaPipe may need `libGLESv2` (`apt install libgles2`).
- Tested with: Python 3.14.4, Ubuntu (WSL2), torch 2.15.0 + CUDA 13.2.
