import requests, time

base = 'http://127.0.0.1:8000'
print('Uploading video...')
with open('storage/duck_camera_feed.mp4', 'rb') as f:
    res = requests.post(f'{base}/video/upload', files={'file': f})
session_id = res.json()['session_id']
print(f'Uploaded. Session ID: {session_id}')

print('Starting inference...')
res = requests.post(f'{base}/video/start/{session_id}')
print(res.json())

while True:
    time.sleep(1)
    status = requests.get(f'{base}/video/status/{session_id}').json()
    fps = status.get('fps')
    frames = status.get('frames_processed', 0)
    st = status.get('status')
    dets = status.get('detections', [])
    print(f"Frames: {frames}, Status: {st}, FPS: {fps}, Detections: {len(dets)}")
    if st in ('completed', 'error'):
        break
