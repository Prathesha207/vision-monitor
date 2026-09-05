import { useState, useEffect } from 'react';
import type { StreamSourceType, LogEntry, DuckEntity } from '../types';
import { getApiBaseUrl } from '../lib/api';
import { useInferenceStore } from '../store/inferenceStore';
import { mapDetectionsToDucks, resetBBoxCache } from '../utils/mlDataMapper';
import { DEFAULT_VIDEO_WIDTH, DEFAULT_VIDEO_HEIGHT } from '../utils/constants';
import { playWaterDropSound } from '../utils/audio';

export function useInferenceLoop({
  sourceType,
  videoSessionId,
  expectedDucks,
  showToast,
  addLog,
  startCameraPipeline,
  setDucks,
  setVideoDimensions,
  cameraService,
  isRunning,
  setIsRunning,
  isStarting,
  setIsStarting,
  fps,
  setFps,
  framesProcessed,
  setFramesProcessed,
  uptimeSeconds,
  setUptimeSeconds
}: {
  sourceType: StreamSourceType;
  videoSessionId: string | null;
  expectedDucks: number;
  showToast: (type: 'error' | 'success' | 'info', message: string) => void;
  addLog: (message: string, level?: LogEntry['level']) => void;
  startCameraPipeline: () => Promise<boolean>;
  setDucks: (ducks: DuckEntity[]) => void;
  setVideoDimensions: (dim: { width: number; height: number }) => void;
  cameraService: any;
  isRunning: boolean;
  setIsRunning: (val: boolean) => void;
  isStarting: boolean;
  setIsStarting: (val: boolean) => void;
  fps: number;
  setFps: (val: number) => void;
  framesProcessed: number;
  setFramesProcessed: (val: number) => void;
  uptimeSeconds: number;
  setUptimeSeconds: (val: number) => void;
}) {
  // FPS is preserved on stop so users can review the achieved performance.
  // It is only reset upon explicit session reset / clearing video or starting a new run.

  // Effect: Core dual-transport loop - WebSocket for camera, polling for video
  useEffect(() => {
    if (!isRunning) return;
    
    const isLive = sourceType === 'oak-camera' || sourceType === 'webcam';
    if (!isLive && !videoSessionId) return;
    
    if (isLive) {
      let ws: WebSocket | null = null;
      let isMounted = true;
      const connectWs = () => {
        const wsUrl = getApiBaseUrl().replace('http', 'ws') + '/oak/inference/ws/live';
        ws = new WebSocket(wsUrl);
        ws.onmessage = (event) => {
          try {
            const data = JSON.parse(event.data);
            if (!isMounted) return;
            useInferenceStore.getState().setStats(data);
            if (data.status !== 'queued' && data.status !== 'error' && data.status !== 'idle') {
              setFps(data.metrics?.fps || data.fps || 0);
              setFramesProcessed(data.frames_processed || 0);
              setUptimeSeconds(Math.floor((data.frames_processed || 0) / (data.metrics?.fps || data.fps || 30)));
              
              if (data.video_width && data.video_height) {
                setVideoDimensions({ width: data.video_width, height: data.video_height });
              }

              const vw = data.video_width || DEFAULT_VIDEO_WIDTH;
              const vh = data.video_height || DEFAULT_VIDEO_HEIGHT;
              const incomingDucks = mapDetectionsToDucks(data, vw, vh);
              if (data.status !== "HAND" && !data.hand_detected) {
                setDucks(incomingDucks);
              }
            }
          } catch(e) {}
        };
        ws.onclose = () => {
          if (isMounted) setTimeout(connectWs, 2000);
        };
      };
      connectWs();
      return () => { isMounted = false; if (ws) ws.close(); };
    }
    
    if (!videoSessionId) return;

    const sessionId = videoSessionId;
    
    let isMounted = true;
    let timeoutId: ReturnType<typeof setTimeout>;
    let consecutive404s = 0;
    
    const pollBackend = async () => {
      try {
        const res = await fetch(`${getApiBaseUrl()}/video/status/${sessionId}`);
        if (res.status === 404) {
          consecutive404s += 1;
          if (consecutive404s >= 3) {
            console.warn(`[VisionAI] Session ${sessionId} is no longer active on the backend. Polling stopped.`);
            return;
          }
          if (isMounted) timeoutId = setTimeout(pollBackend, 500);
          return;
        }

        consecutive404s = 0;
        if (!res.ok) {
          if (isMounted) timeoutId = setTimeout(pollBackend, 500);
          return;
        }
        
        const data = await res.json();
        if (!isMounted) return;

        // Update the global Zustand store with pure ML statistics
        useInferenceStore.getState().setStats(data);
        
        if (data.status !== 'queued' && data.status !== 'error' && data.status !== 'idle') {
          setFps(data.fps || 0);
          setFramesProcessed(data.frames_processed || 0);
          setUptimeSeconds(Math.floor((data.frames_processed || 0) / (data.fps || 30)));
          
          if (data.video_width && data.video_height) {
            setVideoDimensions({ width: data.video_width, height: data.video_height });
          }

          const vw = data.video_width || DEFAULT_VIDEO_WIDTH;
          const vh = data.video_height || DEFAULT_VIDEO_HEIGHT;
          const incomingDucks = mapDetectionsToDucks(data, vw, vh);

          // Render exactly what the ML model sends — 1:1 mapping
          if (data.status !== "HAND" && !data.hand_detected) {
            setDucks(incomingDucks);
          }

          if (data.status === 'completed' || (data.progress >= 100 && data.status !== 'WARMING' && (data.frames_processed || 0) > 0)) {
            setIsRunning(false);
            showToast('success', 'Video inference completed.');
            addLog('Video inference processing completed.', 'success');
            return;
          }
        } else if (data.status === 'error') {
          console.error('[VisionAI] Inference error:', data.reasons);
          setIsRunning(false);
          showToast('error', `Inference failed: ${data.reasons?.join(', ') || 'Unknown error'}`);
          addLog(`Inference failed: ${data.reasons?.join(', ') || 'Unknown error'}`, 'anomaly');
          return;
        }
        
        const pollInterval = Math.max(120, Math.min(250, Math.floor(1000 / (data?.fps || 15))));
        if (isMounted) {
          timeoutId = setTimeout(pollBackend, pollInterval);
        }
        return;
        
      } catch(err) {
      } finally {
        if (isMounted && !timeoutId && isRunning) {
          timeoutId = setTimeout(pollBackend, 200);
        }
      }
    };
    
    pollBackend();
    
    return () => {
      isMounted = false;
      clearTimeout(timeoutId);
    };
  }, [isRunning, videoSessionId, sourceType]);

  const handleToggleRunning = async (startVideoInference: () => Promise<void>) => {
    playWaterDropSound();
    if (isRunning) {
      setIsRunning(false);
      showToast('info', 'Inference paused. Click Resume or Start.');
      addLog('Inference paused • Model evaluation temporarily suspended.', 'info');
      if (sourceType === 'oak-camera' || sourceType === 'webcam') {
        try { await cameraService.stopLiveInference(); } catch (e) { }
      }
      if (videoSessionId) {
        fetch(`${getApiBaseUrl()}/video/stop/${videoSessionId}`, { method: 'POST' })
          .catch(() => {});
      }
    } else {
      const isCameraMode = sourceType === 'oak-camera' || sourceType === 'webcam';
      if (isCameraMode) {
        setIsStarting(true);
        await startCameraPipeline();
        setIsStarting(false);
      } else {
        await startVideoInference();
      }
    }
  };

  const handleStopInference = async () => {
    playWaterDropSound();
    setIsRunning(false);
    
    if (videoSessionId) {
      try {
        await fetch(`${getApiBaseUrl()}/video/stop/${videoSessionId}`, { method: 'POST' });
        const res = await fetch(`${getApiBaseUrl()}/video/status/${videoSessionId}`);
        if (res.ok) {
          const data = await res.json();
          useInferenceStore.getState().setStats(data);
          if (typeof data.fps === 'number' && data.fps > 0) {
            setFps(data.fps);
          }
          const vw = data.video_width || DEFAULT_VIDEO_WIDTH;
          const vh = data.video_height || DEFAULT_VIDEO_HEIGHT;
          const incomingDucks = mapDetectionsToDucks(data, vw, vh);
          if (data.status !== "HAND" && !data.hand_detected) {
            setDucks(incomingDucks);
          }
        }
      } catch (err) {
        console.error("Failed to stop backend inference", err);
      }
    }

    if (sourceType === 'oak-camera' || sourceType === 'webcam') {
      cameraService.stopLiveInference().catch(() => {});
    }
    
    showToast('info', 'Inference paused. Last state retained.');
    addLog('Inference stopped • Detections and side cards preserved.', 'info');
  };

  const handleResumeInference = (startVideoInference: () => Promise<void>) => {
    if (sourceType === 'uploaded-video' || sourceType === 'sample-pond') {
      playWaterDropSound();
      void startVideoInference();
      return;
    }
    playWaterDropSound();
    setFramesProcessed(0);
    setFps(0);
    setDucks([]);
    useInferenceStore.getState().resetStats();
    resetBBoxCache();
    setIsStarting(true);
    if (sourceType === 'oak-camera' || sourceType === 'webcam') {
      cameraService.startLiveInference('live')
        .then((result: any) => {
          setIsStarting(false);
          if (result?.status === 'error') throw new Error(result.message || 'Inference start failed');
          setIsRunning(true);
          showToast('success', 'Inference started');
          addLog('AI inference started on the live camera stream.', 'success');
        })
        .catch((error: any) => {
          setIsStarting(false);
          setIsRunning(false);
          showToast('error', error instanceof Error ? error.message : 'Unable to start inference');
        });
    }
  };

  return {
    isRunning,
    setIsRunning,
    isStarting,
    setIsStarting,
    fps,
    setFps,
    framesProcessed,
    setFramesProcessed,
    uptimeSeconds,
    setUptimeSeconds,
    handleToggleRunning,
    handleStopInference,
    handleResumeInference,
  };
}
