import time, cv2
start = time.time()
cap = cv2.VideoCapture(r'd:\DHTX\vision-ai-backend\app\ml\output\e70fff47-a6eb-4104-8f0c-9367ce54a9b0\source.mp4')
fps = cap.get(cv2.CAP_PROP_FPS)
w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
out = cv2.VideoWriter('test.mp4', cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
count = 0
while True:
    ret, frame = cap.read()
    if not ret: break
    out.write(frame)
    count += 1
out.release()
cap.release()
print('Transcoded', count, 'frames in', time.time()-start, 'seconds')
