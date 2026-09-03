import requests

base = 'http://127.0.0.1:8000'
session_id = 'b473c69d-ed87-4f31-9156-4520bc7602b0'
status = requests.get(f'{base}/video/status/{session_id}').json()
print(status.get('reasons'))
