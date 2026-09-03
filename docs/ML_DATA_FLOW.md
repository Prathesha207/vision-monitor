# ML data flow

This document describes how inference data moves from the backend ML pipeline to the frontend.

## There is no Ray payload here

The active code does not send a Ray-specific object or transport. The ML pipeline returns a normal Python dictionary. FastAPI serializes that dictionary as JSON, and the frontend receives it through either a WebSocket or an HTTP response.

## Live OAK camera or webcam

```text
Camera frames
  -> oak_camera_service.py
  -> duck_inference_service.run_inference()
  -> inference result dictionary
  -> asyncio result queue
  -> WS /oak/inference/ws/live
  -> App.tsx WebSocket onmessage
  -> inferenceStore.setStats(data)
  -> mapDetectionsToDucks(data, videoWidth, videoHeight)
  -> DetectionCanvas overlay
```

The WebSocket sends one JSON message for each processed frame. The frontend does not call the ML model directly.

## Uploaded video

```text
POST /video/upload
  -> backend creates a session
  -> background video inference starts

GET /video/status/{session_id}  (repeated by App.tsx)
  -> current JSON stats and detections
  -> inferenceStore.setStats(data)
  -> mapDetectionsToDucks(data, videoWidth, videoHeight)
  -> DetectionCanvas overlay
```

The polling interval is adjusted from the `fps` value returned by the backend. A session can also expose its annotated video through `GET /video/stream/{session_id}` as an MJPEG stream.

## Result shape

A typical inference update looks like this:

```json
{
  "session_id": "live",
  "status": "NORMAL",
  "frames_processed": 42,
  "fps": 12.5,
  "detected_duck_count": 18,
  "expected_duck_count": 18,
  "detected_other_toy_count": 0,
  "anchor_locked": true,
  "hand_detected": false,
  "missing_ids": [],
  "added_ids": [],
  "other_ids": [],
  "detections": [
    {
      "id": 3,
      "species": "duck",
      "confidence": 0.94,
      "isAnomaly": false,
      "provisional": false,
      "bbox": [100, 80, 240, 210]
    }
  ],
  "metrics": {
    "fps": 12.5,
    "gpu_pct": 48.0,
    "latency_ms": 72.4
  }
}
```

### Meaning of `detections[].bbox`

The four numbers are pixel corner coordinates:

```text
[x1, y1, x2, y2]
```

They are not `[x, y, width, height]`. `mapDetectionsToDucks()` converts these pixels into percentages using `video_width` and `video_height`, because the canvas overlay is responsive.

For example:

```text
x      = x1 / video_width  * 100
 y     = y1 / video_height * 100
width  = (x2 - x1) / video_width  * 100
height = (y2 - y1) / video_height * 100
```

The frontend trusts the backend's per-detection `isAnomaly` and `provisional` values when they are present. It does not run a second ML model in the browser.

## Frames versus JSON

These are separate data channels:

- JSON contains status, counts, IDs, detections, metrics, and thumbnail events.
- The OAK WebSocket may also include a base64 JPEG in the `frame` field for offline inference.
- Uploaded-video playback uses the separate MJPEG endpoint `/video/stream/{session_id}`.
- The detection boxes are drawn by the frontend over the displayed video/frame.

## Recording paths

There are two recording mechanisms:

1. Frontend recording: `useRecording.ts` uses browser `MediaRecorder`, creates a local WebM, and can upload it for inference.
2. Backend recording: `recording_api.py` starts or stops a backend recording session, and `recording_service.py` writes incoming camera frames.

These mechanisms are separate from the JSON inference update. The automatic anomaly recorder in the OAK worker only starts when an inference result contains `record: true`.

## Main files

- Backend inference worker: `vision-ai-backend/app/services/oak_camera_service.py`
- Backend live WebSocket: `vision-ai-backend/app/routers/oak_camera_router.py`
- Backend uploaded-video status: `vision-ai-backend/app/routers/video_router.py`
- Frontend transport handling: `vision-ai-frontend/src/App.tsx`
- Frontend payload mapper: `vision-ai-frontend/src/utils/mlDataMapper.ts`
- Frontend stats store: `vision-ai-frontend/src/store/inferenceStore.ts`
- Frontend detection overlay: `vision-ai-frontend/src/components/DetectionCanvas.tsx`
