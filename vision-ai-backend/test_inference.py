import asyncio
from app.ml.ml_inference import ml_inference_service
import app.ml.app_state as app_state

async def main():
    session_id = ml_inference_service.create_session(expected_ducks=18)
    session = ml_inference_service.sessions[session_id]
    session['video_save_path'] = 'storage/duck_camera_feed.mp4'
    session['status'] = 'processing'
    
    print(f'Running inference for session {session_id}')
    await ml_inference_service.process_video_task(session_id, session['video_save_path'])
    print(session['stats'])

if __name__ == '__main__':
    asyncio.run(main())
