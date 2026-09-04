import { useState, useEffect } from 'react';
import type { StreamSourceType, LogEntry } from '../types';
import { getApiBaseUrl } from '../lib/api';
import { useInferenceStore } from '../store/inferenceStore';
import { resetBBoxCache } from '../utils/mlDataMapper';

export function useVideoPipeline({
  showToast,
  addLog,
  expectedDucks,
  sourceType,
  isRunning,
  setIsRunning,
  setDucks,
  setFramesProcessed,
  setFps,
  setCameraStartingState,
  setSourceType,
  setIsStarting
}: {
  showToast: (type: 'error' | 'success' | 'info', message: string) => void;
  addLog: (message: string, level?: LogEntry['level']) => void;
  expectedDucks: number;
  sourceType: StreamSourceType;
  isRunning: boolean;
  setIsRunning: (running: boolean) => void;
  setDucks: (ducks: any[]) => void;
  setFramesProcessed: (count: number) => void;
  setFps: (fps: number) => void;
  setCameraStartingState: (state: any) => void;
  setSourceType: (type: StreamSourceType) => void;
  setIsStarting: (starting: boolean) => void;
}) {
  const [customVideoUrl, setCustomVideoUrl] = useState<string | undefined>();
  const [localPreviewUrl, setLocalPreviewUrl] = useState<string | undefined>();
  const [videoSessionId, setVideoSessionId] = useState<string | null>(null);
  const [customVideoName, setCustomVideoName] = useState<string | undefined>();
  const [autoStartRecordedInference, setAutoStartRecordedInference] = useState(false);
  const [videoDimensions, setVideoDimensions] = useState<{ width: number; height: number } | null>(null);
  const [initialUploadFile, setInitialUploadFile] = useState<File | undefined>();

  // Custom uploaded video handler (Directly in video canvas)
  const handleVideoUploaded = (url: string, name: string, sessionId?: string) => {
    const isRecordedStream = name.startsWith('recorded_camera');
    
    // If online camera is currently active, block upload with alert as per requirement (unless it's a recorded stream)
    const isCameraActive = (sourceType === 'oak-camera' || sourceType === 'webcam') && isRunning;
    if (isCameraActive && !isRecordedStream) {
      showToast('error', 'Disable camera before uploading video');
      addLog('Blocked upload: Disable camera before uploading video', 'anomaly');
      return;
    }

    if (sessionId) {
      setVideoSessionId(sessionId);
    }

    if (isRecordedStream) {
      // Auto-stop camera to smoothly transition into video preview
      setIsRunning(false);
      addLog('Camera recording finished. Ready for inference.', 'success');
    }

    if (name.toLowerCase().includes('_annotated') || name.toLowerCase().startsWith('annotated_')) {
      showToast('info', `Note: "${name}" is an already-annotated video. For clean inference, upload raw videos from D:/ml/test-videos/`);
      addLog(`Warning: "${name}" contains baked-in annotations from a previous run.`, 'anomaly');
    }

    // Always update local preview and customVideoUrl so the video frame shows immediately
    const isStreamUrl = url.includes('/video/stream/');
    setLocalPreviewUrl(url);
    setCustomVideoUrl(url);
    setCustomVideoName(name);
    setSourceType('uploaded-video');
    setAutoStartRecordedInference(false);
    setCameraStartingState('ready');

    if (isStreamUrl) {
      setIsRunning(true);
      showToast('success', `Inference started for "${name}"`);
      addLog(`Inference started: "${name}"`, 'success');
    } else if (sessionId) {
      // Auto-start inference immediately on uploaded session so no second button is needed
      setIsRunning(true);
      fetch(`${getApiBaseUrl()}/video/update_expected/${sessionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ count: expectedDucks })
      }).catch(() => {});
      fetch(`${getApiBaseUrl()}/video/start/${sessionId}`, { method: 'POST' })
        .then(() => {
          const streamUrl = `${getApiBaseUrl()}/video/stream/${sessionId}`;
          setCustomVideoUrl(streamUrl);
          showToast('success', `Inference started for "${name}"`);
          addLog(`Inference started: "${name}"`, 'success');
        })
        .catch(err => {
          console.error('Auto-start failed:', err);
          setIsRunning(false);
        });
    } else {
      setIsRunning(false);
      showToast('success', `Video "${name}" loaded.`);
      addLog(`Video loaded: "${name}"`, 'info');
    }
  };

  const handleClearVideo = () => {
    // If backend video was running, signal stop
    if (videoSessionId) {
      fetch(`${getApiBaseUrl()}/video/stop/${videoSessionId}`, { method: 'POST' })
        .catch(() => {});
    }
    setCustomVideoUrl(undefined);
    setLocalPreviewUrl(undefined);
    setVideoSessionId(null);
    setCustomVideoName(undefined);
    setSourceType('uploaded-video');
    setIsRunning(false);
    setDucks([]);
    setFramesProcessed(0);
    setFps(0);
    useInferenceStore.getState().resetStats();
    showToast('info', 'Video cleared.');
    addLog('Reset video canvas and cleared all detections.', 'info');
  };

  const startVideoInference = async () => { 
    setDucks([]); 
    useInferenceStore.getState().resetStats(); 
    resetBBoxCache();
    if (!videoSessionId) {
      showToast('error', 'Upload a video before starting inference.');
      return;
    }
    setFramesProcessed(0);
    setFps(0);
    setDucks([]);
    useInferenceStore.getState().resetStats();
    resetBBoxCache();
    setIsStarting(true);
    setIsRunning(true);
    try {
      // Sync the expected count to the backend RIGHT BEFORE starting inference!
      // This guarantees the backend always warms up with the number currently shown on screen.
      await fetch(`${getApiBaseUrl()}/video/update_expected/${videoSessionId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ count: expectedDucks })
      }).catch(err => console.error("Failed to sync expected ducks before start", err));

      const response = await fetch(`${getApiBaseUrl()}/video/start/${videoSessionId}`, { method: 'POST' });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.message || 'Unable to start inference');
      }
      showToast('success', 'Inference started');
      addLog('Inference active on uploaded video.', 'success');
    } catch (error) {
      setIsRunning(false);
      showToast('error', error instanceof Error ? error.message : 'Unable to start inference');
    } finally {
      setIsStarting(false);
    }
  };

  // A camera recording is already a completed input video. Start its normal
  // uploaded-video inference flow automatically after the upload returns a
  // backend session ID, so Record -> review -> inference is one workflow.
  useEffect(() => {
    if (!autoStartRecordedInference || !videoSessionId || sourceType !== 'uploaded-video') return;
    setAutoStartRecordedInference(false);
    void startVideoInference();
  }, [autoStartRecordedInference, videoSessionId, sourceType]);

  return {
    customVideoUrl,
    setCustomVideoUrl,
    localPreviewUrl,
    setLocalPreviewUrl,
    videoSessionId,
    setVideoSessionId,
    customVideoName,
    setCustomVideoName,
    autoStartRecordedInference,
    setAutoStartRecordedInference,
    videoDimensions,
    setVideoDimensions,
    initialUploadFile,
    setInitialUploadFile,
    handleVideoUploaded,
    handleClearVideo,
    startVideoInference
  };
}
