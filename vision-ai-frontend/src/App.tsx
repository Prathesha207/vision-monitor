import { mapDetectionsToDucks } from './utils/mlDataMapper';
import { LandingScreen } from './components/LandingScreen';
import { lockScroll, unlockScroll } from './utils/scrollLock';
/**
 * @license
 * SPDX-License-Identifier: Apache-2.0
 */

import React, { useState, useEffect, useLayoutEffect, useMemo, useCallback } from 'react';
import {
  ThemeMode,
  StreamSourceType,
  DuckEntity,
  AnomalyStatus,
  CameraConfig,
  LogEntry,
  DetectionMetrics
} from './types';
import { DEFAULT_CAMERA_CONFIG } from './data/cameraData';
import { cameraService } from './components/service/cameraService';
import { Header } from './components/Header';
import { SourceSelector } from './components/SourceSelector';
import { DetectionCanvas } from './components/DetectionCanvas';
import { DetectionDrawer } from './components/DetectionDrawer';
import { CameraSettingsModal } from './components/CameraSettingsModal';
import { HelpModal } from './components/HelpModal';
import { Modal, Toast, Button } from './components/ui';
import { useInferenceStore } from './store/inferenceStore';
import {
  playWaterDropSound,
  playAnomalyAlertSound,
  playNormalSound,
  setSoundEnabled
} from './utils/audio';
import { AlertTriangle } from 'lucide-react';
import confetti from 'canvas-confetti';
import { getApiBaseUrl, API_BASE_URL } from './lib/api';

// ---------- Frontend-side NMS: deduplicate overlapping detection boxes ----------
// Prevents ghost/stacked boxes when the ML model emits many overlapping
// detections for the same physical duck (common during motion blur / rotation).

function boxIou(a: DuckEntity, b: DuckEntity): number {
  const x1 = Math.max(a.x, b.x);
  const y1 = Math.max(a.y, b.y);
  const x2 = Math.min(a.x + a.width, b.x + b.width);
  const y2 = Math.min(a.y + a.height, b.y + b.height);
  const interArea = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
  const unionArea = a.width * a.height + b.width * b.height - interArea;
  return unionArea > 0 ? interArea / unionArea : 0;
}

/** Check if center of box B falls inside box A */
function centerInsideBox(kept: DuckEntity, candidate: DuckEntity): boolean {
  const cx = candidate.x + candidate.width / 2;
  const cy = candidate.y + candidate.height / 2;
  return (
    cx >= kept.x &&
    cx <= kept.x + kept.width &&
    cy >= kept.y &&
    cy <= kept.y + kept.height
  );
}

/** Check if one box is mostly contained inside the other (> 60% overlap with the smaller box) */
function isContained(a: DuckEntity, b: DuckEntity): boolean {
  const x1 = Math.max(a.x, b.x);
  const y1 = Math.max(a.y, b.y);
  const x2 = Math.min(a.x + a.width, b.x + b.width);
  const y2 = Math.min(a.y + a.height, b.y + b.height);
  const interArea = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
  const smallerArea = Math.min(a.width * a.height, b.width * b.height);
  return smallerArea > 0 && interArea / smallerArea > 0.6;
}

/**
 * Are two boxes close enough to be the same object?
 * Uses FOUR checks â€” any one triggers suppression:
 * 1. IOU overlap > threshold
 * 2. Center of candidate falls inside the kept box
 * 3. Center of kept falls inside the candidate box
 * 4. One box mostly contained inside the other (>60% of smaller box area)
 */
function isDuplicate(a: DuckEntity, b: DuckEntity, iouThreshold: number): boolean {
  if (boxIou(a, b) > iouThreshold) return true;
  if (centerInsideBox(a, b)) return true;
  if (centerInsideBox(b, a)) return true;
  if (isContained(a, b)) return true;
  return false;
}

function dedupeDucks(ducks: DuckEntity[], iouThreshold = 0.25): DuckEntity[] {
  // Sort by confidence descending â€” keep the best box, suppress weaker duplicates
  const sorted = [...ducks].sort((a, b) => b.confidence - a.confidence);
  const kept: DuckEntity[] = [];
  for (const d of sorted) {
    if (!kept.some((k) => isDuplicate(k, d, iouThreshold))) {
      kept.push(d);
    }
  }
  return kept;
}
export default function App() {
  // 0. System initialization and live backend health tracking
  const [systemInitialized, setSystemInitialized] = useState<boolean>(false);
  const [isBackendConnected, setIsBackendConnected] = useState<boolean>(true);

  // Background health polling to keep main page status up to date
  useEffect(() => {
    let isMounted = true;
    const checkBackend = async () => {
      try {
        const res = await fetch(`${getApiBaseUrl()}/health`, { method: 'GET', cache: 'no-store' });
        if (isMounted) setIsBackendConnected(res.ok);
      } catch {
        if (isMounted) setIsBackendConnected(false);
      }
    };
    checkBackend();
    const interval = setInterval(checkBackend, 3000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  // 1. Theme and UI State
  const [theme, setTheme] = useState<ThemeMode>(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem('visionmonitor-theme') as ThemeMode | null;
      if (saved && (saved === 'pond-light' || saved === 'pond-dark' || saved === 'nature')) {
        return saved;
      }
    }
    return 'pond-light';
  });
  const handleThemeChange = (newTheme: ThemeMode) => {
    if (typeof document !== 'undefined') {
      document.documentElement.setAttribute('data-theme', newTheme);
      document.body.setAttribute('data-theme', newTheme);
      const themeClass = newTheme === 'pond-dark' ? 'theme-pond-dark dark' : newTheme === 'nature' ? 'theme-nature dark' : 'theme-pond-light';
      document.documentElement.className = themeClass;
      document.body.className = themeClass;
      try {
        localStorage.setItem('visionmonitor-theme', newTheme);
      } catch { }
    }
    setTheme(newTheme);
  };
  // Default to idle so the canvas does not look like inference is already running
  // before the user explicitly starts the model or selects a live camera stream.
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [isStarting, setIsStarting] = useState<boolean>(false);
  const [feedMode, setFeedMode] = useState<'raw' | 'inference'>('inference');
  const [sourceType, setSourceType] = useState<StreamSourceType>('uploaded-video');
  const [soundActive, setSoundActive] = useState<boolean>(true);
  const [cameraStartingState, setCameraStartingState] = useState<'idle' | 'waking_camera' | 'waiting_frame' | 'ready'>('idle');
  const [cameraConnected, setCameraConnected] = useState<boolean | null>(null);
  const [initialUploadFile, setInitialUploadFile] = useState<File | undefined>();

  // Mode switch safeguard modal state
  const [pendingSourceSwitch, setPendingSourceSwitch] = useState<StreamSourceType | null>(null);

  // Toast notification state
  const [toast, setToast] = useState<{ type: 'error' | 'success' | 'info'; message: string } | null>(null);

  const showToast = useCallback((type: 'error' | 'success' | 'info', message: string) => {
    setToast({ type, message });
    setTimeout(() => {
      setToast((prev) => (prev?.message === message ? null : prev));
    }, 4000);
  }, []);

  // 2. Collapsible Drawers & Modals State
  const [drawerOpen, setDrawerOpen] = useState<boolean>(true);
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  const [helpOpen, setHelpOpen] = useState<boolean>(false);

  // 3. Detection & Entities State
  const [expectedDucks, setExpectedDucks] = useState<number>(18);
  const [ducks, setDucks] = useState<DuckEntity[]>([]);
  const [selectedDuckId, setSelectedDuckId] = useState<string | null>(null);
  const [cameraConfig, setCameraConfig] = useState<CameraConfig>({
    ...DEFAULT_CAMERA_CONFIG,
    connected: false,
  });
  const [isCameraDeviceActive, setIsCameraDeviceActive] = useState<boolean>(false);
  const [customVideoUrl, setCustomVideoUrl] = useState<string | undefined>();
  const [localPreviewUrl, setLocalPreviewUrl] = useState<string | undefined>();
  const [videoSessionId, setVideoSessionId] = useState<string | null>(null);
  const [autoStartRecordedInference, setAutoStartRecordedInference] = useState(false);
  const [customVideoName, setCustomVideoName] = useState<string | undefined>();
  const [videoDimensions, setVideoDimensions] = useState<{ width: number; height: number } | null>(null);

  // 4. Metrics & Logs State
  const [logs, setLogs] = useState<LogEntry[]>([]);
  const [framesProcessed, setFramesProcessed] = useState<number>(0);
  const [fps, setFps] = useState<number>(0);
  const [uptimeSeconds, setUptimeSeconds] = useState<number>(0); // ~6 mins

  // Sound switch sync
  const handleToggleSound = () => {
    const next = !soundActive;
    setSoundActive(next);
    setSoundEnabled(next);
    if (next) playWaterDropSound();
  };

  // Helper to add log entry
  const addLog = useCallback((message: string, level: LogEntry['level'] = 'info') => {
    const now = new Date();
    const timeStr = now.toTimeString().split(' ')[0];
    const newEntry: LogEntry = {
      id: `log-${Date.now()}-${Math.random().toString(36).substring(2, 5)}`,
      timestamp: timeStr,
      message,
      level,
    };
    setLogs((prev) => [...prev.slice(-35), newEntry]);
  }, []);

  // Automatically fetch latest saved camera from database on mount / refresh
  useEffect(() => {
    async function loadSavedCamera() {
      try {
        const cameras = await cameraService.checkCamera();
        if (Array.isArray(cameras) && cameras.length > 0) {
          const activeCamera =
            [...cameras].sort((a: any, b: any) => (b.id ?? 0) - (a.id ?? 0)).find((c: any) => c.is_enabled) ||
            cameras[0];
          if (activeCamera) {
            setCameraConfig((prev) => ({
              ...prev,
              id: activeCamera.id,
              sourceName: activeCamera.name || prev.sourceName,
              ipAddress: activeCamera.ip_address || '',
              resolution: (activeCamera.resolution as any) || prev.resolution,
              targetFps: activeCamera.fps || prev.targetFps,
              rotationAngle: activeCamera.rotation_angle ?? prev.rotationAngle,
              controlMode: (activeCamera.control_mode as any) ?? prev.controlMode,
              exposure: activeCamera.exposure ?? prev.exposure,
              gain: activeCamera.gain ?? prev.gain,
              iso: activeCamera.gain ?? prev.iso,
              focus: activeCamera.focus ?? prev.focus,
              brightness: activeCamera.brightness ?? prev.brightness,
              contrast: activeCamera.contrast ?? prev.contrast,
              autoFocus: activeCamera.auto_focus ?? prev.autoFocus,
              autoExposure: activeCamera.auto_exposure ?? prev.autoExposure,
            }));
            addLog(`Loaded camera "${activeCamera.name}" from database [${activeCamera.resolution || '1080p'} @ ${activeCamera.fps || 30}fps]`, 'info');
          }
        }
      } catch (err) {
        console.error("Could not fetch camera from database:", err);
      }
    }
    loadSavedCamera();
  }, [addLog]);

  // Periodically check live camera hardware connection status
  useEffect(() => {
    let isMounted = true;
    async function checkCameraStatus() {
      try {
        const baseUrl = getApiBaseUrl();
        const res = await fetch(`${baseUrl}/oak/health`);
        if (res.ok) {
          const data = await res.json();
          if (isMounted) {
            const isOnline = Boolean(data.connected || data.running || data.streaming);
            setCameraConfig((prev) => (prev.connected !== isOnline ? { ...prev, connected: isOnline } : prev));
            setIsCameraDeviceActive(isOnline);
            setCameraConnected(isOnline);
          }
        } else {
          if (isMounted) {
            setCameraConfig((prev) => (prev.connected ? { ...prev, connected: false } : prev));
            setIsCameraDeviceActive(false);
            setCameraConnected(false);
          }
        }
      } catch {
        if (isMounted) {
          setCameraConfig((prev) => (prev.connected ? { ...prev, connected: false } : prev));
          setIsCameraDeviceActive(false);
          setCameraConnected(false);
        }
      }
    }

    checkCameraStatus();
    const interval = setInterval(checkCameraStatus, 5000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, []);

  const handleInitializeSystem = () => {
    setSystemInitialized(true);
  };

  const isVideoSource = sourceType === 'sample-pond' || sourceType === 'uploaded-video';
  const hasActiveVideo = isVideoSource && Boolean(customVideoUrl || customVideoName);
  const isCameraSource = sourceType === 'oak-camera' || sourceType === 'webcam';
  const hasActiveStream = (isVideoSource && hasActiveVideo) || (isCameraSource && isCameraDeviceActive && cameraStartingState === 'ready');
  // Camera streaming and AI inference are independent. A live camera feed
  // must not be labeled inactive merely because inference is paused.
  const isStandby = !hasActiveStream && ducks.length === 0;

  // Active ducks currently on the stream
  const activeDucks = useMemo(() => {
    if (!hasActiveStream && ducks.length === 0) return [];
    return ducks;
  }, [hasActiveStream, ducks]);

  const backendStats = useInferenceStore(state => state.stats);
  const backendStatus = backendStats.status;

  // Compute Anomaly Status in Real Time (Preserves state when stopped until CLEAR is clicked)
  const anomalyStatus: AnomalyStatus = useMemo(() => {
    if (!hasActiveStream && ducks.length === 0) {
      return {
        isAnomaly: false,
        type: 'NONE',
        message: isCameraSource ? 'NO CAMERA' : 'STANDBY',
        subMessage: isCameraSource
          ? 'No Luxonis OAK-D / USB camera connected'
          : 'Waiting for video stream...',
        detectedCount: 0,
        expectedCount: expectedDucks,
        difference: 0,
        foreignSpecies: [],
      };
    }

    if (isRunning && (isStarting || backendStatus === 'WARMING' || framesProcessed === 0)) {
      return {
        isAnomaly: false,
        type: 'NONE',
        message: 'WARMING',
        subMessage: 'Warming up AI engine and acquiring targets...',
        detectedCount: backendStats.detected_duck_count,
        expectedCount: expectedDucks,
        difference: 0,
        foreignSpecies: [],
      };
    }

    const detectedCount = backendStats.detected_duck_count > 0 ? backendStats.detected_duck_count : ducks.length;
    const foreignCount = backendStats.detected_other_toy_count;
    const expectedFromMl = backendStats.expected_duck_count > 0 ? backendStats.expected_duck_count : expectedDucks;
    const difference = detectedCount - expectedFromMl;
    const foreignSpecies = foreignCount > 0 ? ['Unknown'] : [];

    const isCountMismatch = detectedCount !== expectedFromMl;
    const hasForeign = foreignCount > 0;
    
    // In ML v1.0.8, status='HAND' directly implies an anomaly UI block
    const isAnomaly = backendStatus === 'ANOMALY' || backendStatus === 'HAND';
    let message = backendStatus === 'HAND' ? 'HAND DETECTED' : 'NORMAL';
    let subMessage = `${detectedCount} ducks detected in target area. Count matches expected (${expectedFromMl}).`;
    let type: AnomalyStatus['type'] = 'NONE';

    if (backendStatus === 'HAND') {
      type = 'UNKNOWN';
      message = 'HAND DETECTED';
      subMessage = 'Hand detected in frame. Evaluation paused until hand is removed.';
    } else if (backendStatus === 'ANOMALY') {
      message = 'ANOMALY';
      
      if (hasForeign && isCountMismatch) {
        type = 'FOREIGN_SPECIES';
        subMessage = `${foreignCount} non-duck detected & duck count: ${detectedCount}/${expectedFromMl} (${difference > 0 ? `+${difference}` : difference})`;
      } else if (hasForeign) {
        type = 'FOREIGN_SPECIES';
        subMessage = `Duck count normal (${detectedCount}/${expectedFromMl}), but ${foreignCount} non-duck detected.`;
      } else if (isCountMismatch) {
        if (difference > 0) {
          type = 'OVER_COUNT';
          const addedIds = backendStats.added_ids || [];
          const added = addedIds.length > 0 ? ` (Added: ${addedIds.join(', ')})` : '';
          subMessage = `+${difference} above expected count (${detectedCount} detected, ${expectedFromMl} expected)${added}`;
        } else {
          type = 'UNDER_COUNT';
          const missingIds = backendStats.missing_ids || [];
          const missing = missingIds.length > 0 ? ` (Missing: ${missingIds.join(', ')})` : '';
          subMessage = `${Math.abs(difference)} missing ducks (${detectedCount} detected, ${expectedFromMl} expected)${missing}`;
        }
      } else {
        type = 'UNKNOWN';
        subMessage = 'AI Engine flagged an anomaly (Check stream).';
      }
    }

    return {
      isAnomaly,
      type,
      message,
      subMessage,
      detectedCount,
      expectedCount: expectedFromMl,
      difference,
      foreignSpecies,
      foreignCount,
    };
  }, [
    hasActiveStream, isRunning, isStarting, expectedDucks, isCameraSource,
    backendStatus, framesProcessed, backendStats
  ]);

  // Track previous anomaly state to trigger sound effect and log transitions
  const prevAnomalyRef = React.useRef(anomalyStatus.isAnomaly);
  const isAnomaly = anomalyStatus.isAnomaly;
  const subMessage = anomalyStatus.subMessage;

  useEffect(() => {
    if (!hasActiveStream || !isRunning) {
      prevAnomalyRef.current = false;
      return;
    }
    if (prevAnomalyRef.current !== isAnomaly) {
      if (isAnomaly) {
        playAnomalyAlertSound();
        addLog(`Anomalous Activity: ${subMessage}`, 'anomaly');
      } else {
        playNormalSound();
        addLog(`Status Normalized: Expected (${anomalyStatus.expectedCount}) matches Detected (${anomalyStatus.detectedCount})`, 'success');
        /*try {
          confetti({
            particleCount: 30,
            spread: 60,
            origin: { y: 0.8 },
            colors: ['#10B981', '#34D399', '#FBBF24'],
          });
        } catch {
          // ignore
        }*/
      }
      prevAnomalyRef.current = isAnomaly;
    }
  }, [isAnomaly, subMessage, anomalyStatus.expectedCount, anomalyStatus.detectedCount, addLog, hasActiveStream, isRunning]);

  // Metrics must always originate from a backend inference stream; never
  // fabricate FPS or frame counts while an uploaded video is idle.
  useEffect(() => {
    if (!isRunning || !hasActiveStream) setFps(0);
  }, [isRunning, hasActiveStream]);

  // Live backend ML updates use two transports:
  // - OAK/webcam: one JSON inference result per WebSocket message.
  // - Uploaded video: repeated JSON snapshots from GET /video/status/{id}.
  // Both paths update the same Zustand stats and convert detections into
  // percentage-based overlay entities for the canvas.
  useEffect(() => {
    if (!isRunning || !hasActiveStream) return;
    
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
            // The backend sends stats and detections together. The annotated
            // frame, when present, is a separate `frame` data URI in payload.
            useInferenceStore.getState().setStats(data);
            if (data.status !== 'queued' && data.status !== 'error' && data.status !== 'idle' && data.status !== 'stopped') {
              setFps(data.metrics?.fps || data.fps || 0);
              setFramesProcessed(data.frames_processed || 0);
              setUptimeSeconds(Math.floor((data.frames_processed || 0) / (data.metrics?.fps || data.fps || 30)));
              
              const vw = data.video_width || 1920;
              const vh = data.video_height || 1080;
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

        console.log('poll frame:', data.frames_processed, 'ducks:', data.detections?.map((d: any) => `${d.id}:${d.bbox}`));
        
        // Update the global Zustand store with pure ML statistics
        useInferenceStore.getState().setStats(data);
        
        if (data.status !== 'queued' && data.status !== 'error' && data.status !== 'idle' && data.status !== 'stopped') {
          setFps(data.fps || 0);
          setFramesProcessed(data.frames_processed || 0);
          setUptimeSeconds(Math.floor((data.frames_processed || 0) / (data.fps || 30)));
          
          if (data.video_width && data.video_height) {
            setVideoDimensions({ width: data.video_width, height: data.video_height });
          }

          const vw = data.video_width || 1280;
          const vh = data.video_height || 720;
          const incomingDucks = mapDetectionsToDucks(data, vw, vh);

          // Render exactly what the ML model sends â€” 1:1 mapping
          console.log('frame', data.frames_processed, 'detections:', incomingDucks.length);
          if (data.status !== "HAND" && !data.hand_detected) {
            setDucks(incomingDucks);
          }
        } else if (data.status === 'error') {
          console.error('[VisionAI] Inference error:', data.reasons);
          setIsRunning(false);
          setVideoSessionId(null);
          showToast('error', `Inference failed: ${data.reasons?.join(', ') || 'Unknown error'}`);
          addLog(`Inference failed: ${data.reasons?.join(', ') || 'Unknown error'}`, 'anomaly');
          return;
        }
        
        // Dynamically pace polling with backend throughput (Option C)
        const pollInterval = Math.max(30, Math.min(100, Math.floor(1000 / (data?.fps || 25))));
        if (isMounted) {
          timeoutId = setTimeout(pollBackend, pollInterval);
        }
        return;
        
      } catch(err) {
        // Network error during polling, just wait for next tick
      } finally {
        if (isMounted && !timeoutId) {
          timeoutId = setTimeout(pollBackend, 50);
        }
      }
    };
    
    // Start the sequential polling loop
    pollBackend();
    
    return () => {
      isMounted = false;
      clearTimeout(timeoutId);
    };
  }, [isRunning, hasActiveStream, videoSessionId, sourceType]);

  // Dynamic camera config that accurately reflects hardware connection
  const effectiveCameraConfig = useMemo(() => ({
    ...cameraConfig,
    connected: isCameraDeviceActive,
  }), [cameraConfig, isCameraDeviceActive]);

  // Detection metrics
  const metrics: DetectionMetrics = useMemo(() => {
    const totalConf = ducks.reduce((acc, d) => acc + d.confidence, 0);
    const avgConfidence = ducks.length > 0 ? totalConf / ducks.length : 0;
    const speciesCounts: Record<string, number> = {};
    ducks.forEach((d) => {
      speciesCounts[d.species] = (speciesCounts[d.species] || 0) + 1;
    });

    return {
      fps,
      inferenceTimeMs: fps > 0 ? 1000 / fps : 0,
      framesProcessed,
      uptimeSeconds,
      avgConfidence,
      speciesCounts,
    };
  }, [ducks, fps, framesProcessed, uptimeSeconds]);
  // Preset selection removed - Real ML models handle dynamically incoming objects
  // Custom uploaded video handler (Directly in video canvas)
  const handleCustomVideoUploaded = (url: string, name: string, sessionId?: string) => {
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

  const handleClearCustomVideo = () => {
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

  // SAFEGUARD: Handle switching source between offline (video) and online (camera)
  const executeSwitchMode = async (targetType: StreamSourceType) => {
    const isTargetCamera = targetType === 'oak-camera' || targetType === 'webcam';

    setSourceType(targetType);
    setPendingSourceSwitch(null);

    if (isTargetCamera) {
      // Step 1: Start camera startup sequence with visual overlay
      setIsRunning(false);
      setCameraStartingState('waking_camera');
      addLog('Step 1/3: Starting OAK Camera device (POST /oak/start)...', 'info');

      try {
        const startRes = await cameraService.start();
        if (startRes.status === 'error') throw new Error(startRes.message);

        setIsCameraDeviceActive(true);
        setCameraStartingState('ready');
        addLog('Camera connected. Click Start Stream to begin frame capture.', 'success');

        setTimeout(() => {
          setCameraStartingState('ready');
          setIsRunning(false);
          showToast('success', 'OAK Camera connected');
        }, 1500); // Wait a bit for the device to connect and return images
      } catch (err) {
        showToast('error', 'Failed to start camera device');
        addLog('Error: Failed to connect to OAK stream.', 'error');
        setCameraStartingState('ready');
      }
    } else {
      // Switching to Video mode
      setCameraStartingState('ready');
      try {
        await cameraService.stopStream();
        setIsStreaming(false);
        try { await fetch(getApiBaseUrl() + '/oak/inference/stop', { method: 'POST' }); } catch(e){ }
        try { await fetch(getApiBaseUrl() + '/oak/stop', { method: 'POST' }); } catch(e){ }
      } catch (e) { }

      if (customVideoUrl) {
        setIsRunning(false);
        showToast('info', 'Switched to Video mode â€¢ Select a video and press Start Inference');
      } else {
        setIsRunning(false);
        showToast('info', 'Switched to Video mode');
      }
      addLog(`Stream source switched to: ${targetType.toUpperCase()}`, 'info');
    }
  };

  const handleRequestSwitchMode = (targetType: StreamSourceType) => {
    if (targetType === sourceType) return;

    const isCurrentCamera = sourceType === 'oak-camera' || sourceType === 'webcam';
    const isTargetCamera = targetType === 'oak-camera' || targetType === 'webcam';

    // If inference is running and user switches between camera and video, show safeguard confirmation modal
    if (isRunning && isCurrentCamera !== isTargetCamera) {
      setPendingSourceSwitch(targetType);
      return;
    }

    executeSwitchMode(targetType);
  };

  const handleConfirmSwitchMode = () => {
    if (!pendingSourceSwitch) return;
    playWaterDropSound();
    const target = pendingSourceSwitch;
    executeSwitchMode(target);
  };

  const startCameraPipeline = async () => { setDucks([]); useInferenceStore.getState().resetStats();
    setCameraStartingState('waking_camera');
    addLog('Step 1/3: Starting OAK Camera device (POST /oak/start)...', 'info');

    try {
      // Sync expected count to backend right before starting
      await fetch(`${getApiBaseUrl()}/oak/inference/update_expected/live`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ count: expectedDucks })
      }).catch(err => console.error("Failed to sync expected ducks before start", err));

      const startRes = await cameraService.start();
      if (startRes?.status === 'error') throw new Error(startRes.message || 'Camera start failed');

      const streamRes = await cameraService.startStream();
      if (streamRes?.status === 'error') throw new Error(streamRes.message || 'Stream start failed');
      setIsStreaming(true);

      const inferenceRes = await cameraService.startLiveInference('live');
      if (inferenceRes?.status === 'error') throw new Error(inferenceRes.message || 'Inference start failed');

      setCameraStartingState('waiting_frame');
      addLog('Step 2/3: Stream started â€¢ Waiting for camera sensor to warm up...', 'info');

      await new Promise((resolve) => setTimeout(resolve, 1500));

      setCameraStartingState('ready');
      setIsRunning(true);
      showToast('success', 'Camera inference started successfully');
      addLog('Step 3/3: First live frame received (1080p). Starting inference â€¢ YOLOv8 active.', 'success');
      return true;
    } catch (error) {
      console.error('Failed to start camera pipeline:', error);
      setCameraStartingState('ready');
      setIsRunning(false);
      showToast('error', 'Failed to start camera device');
      addLog('Error: Failed to connect to OAK stream or live inference.', 'error');
      return false;
    }
  };

  const stopCameraPipeline = async () => {
    setCameraStartingState('ready');
    setIsRunning(false);
    setDucks([]);

    try {
      await cameraService.stopStream();
      setIsStreaming(false);
    } catch (err) {
      console.warn('Stream stop call failed:', err);
    }
  };

  const startCameraStream = async () => {
    setCameraStartingState('waking_camera');
    try {
      if (!isCameraDeviceActive) {
        const startRes = await cameraService.start();
        if (startRes?.status === 'error') throw new Error(startRes.message || 'Camera start failed');
        setIsCameraDeviceActive(true);
      }
      const streamRes = await cameraService.startStream();
      if (streamRes?.status === 'error') throw new Error(streamRes.message || 'Stream start failed');
      setIsStreaming(true);
      setCameraStartingState('ready');
      showToast('success', 'Camera stream started');
      addLog('Camera stream started. Ready for display or inference.', 'success');
    } catch (error) {
      setCameraStartingState('ready');
      showToast('error', error instanceof Error ? error.message : 'Unable to start camera stream');
      addLog('Camera stream failed to start.', 'error');
    }
  };

  const stopCameraStream = async () => {
    if (isRunning) {
      try { await cameraService.stopLiveInference(); } catch { /* stream can still stop */ }
      setIsRunning(false);
      setDucks([]);
    }
    try {
      await cameraService.stopStream();
      setIsStreaming(false);
      showToast('info', 'Camera stream stopped');
      addLog('Camera stream stopped.', 'info');
    } catch {
      showToast('error', 'Unable to stop camera stream');
    }
  };

  const startVideoInference = async () => { setDucks([]); useInferenceStore.getState().resetStats();
    if (!videoSessionId) {
      showToast('error', 'Upload a video before starting inference.');
      return;
    }
    setFramesProcessed(0);
    setFps(0);
    setDucks([]);
    useInferenceStore.getState().resetStats();
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

  // SEQUENTIAL ONLINE / CAMERA MODE STARTUP FLOW
  const handleToggleRunning = async () => {
    playWaterDropSound();
    if (isRunning) {
      // Stopping inference (Pausing / suspending)
      setCameraStartingState('ready');
      showToast('info', 'Inference paused. Click Resume or Start.');
      addLog('Inference paused â€¢ Model evaluation temporarily suspended.', 'info');
      if (sourceType === 'oak-camera' || sourceType === 'webcam') {
        try { await cameraService.stopLiveInference(); } catch (e) { }
      }
      if (videoSessionId) {
        fetch(`${getApiBaseUrl()}/video/stop/${videoSessionId}`, { method: 'POST' })
          .catch(() => {});
      }
    } else {
      // Starting inference
      const isCameraMode = sourceType === 'oak-camera' || sourceType === 'webcam';
      if (isCameraMode) {
        setIsStarting(true);
        setTimeout(() => setIsStarting(false), 1000);
        await startCameraPipeline();
      } else {
        await startVideoInference();
      }
    }
  };

  // Explicit Stop Inference (Pauses inference and preserves state/cards on screen)
  const handleStopInference = () => {
    playWaterDropSound();
    setCameraStartingState('ready');
    setIsRunning(false);
    
    // Stop the backend ML process if it's a video session
    if (videoSessionId) {
      fetch(`${getApiBaseUrl()}/video/stop/${videoSessionId}`, { method: 'POST' })
        .catch(err => console.error("Failed to stop backend inference", err));
    }

    if (sourceType === 'oak-camera' || sourceType === 'webcam') {
      cameraService.stopLiveInference().catch(() => {});
    }
    
    showToast('info', 'Inference paused. Last state retained.');
    addLog('Inference stopped • Detections and side cards preserved.', 'info');
  };

  // Explicit Resume/Start Inference
  const handleResumeInference = () => {
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
    setIsStarting(true);
    setTimeout(() => setIsStarting(false), 1000);
    setIsRunning(true);
    setCameraStartingState('ready');
    if (sourceType === 'oak-camera' || sourceType === 'webcam') {
      cameraService.startLiveInference('live')
        .then((result) => {
          if (result?.status === 'error') throw new Error(result.message || 'Inference start failed');
          showToast('success', 'Inference started');
          addLog('AI inference started on the live camera stream.', 'success');
        })
        .catch((error) => {
          setIsRunning(false);
          showToast('error', error instanceof Error ? error.message : 'Unable to start inference');
        });
    }
    if (videoSessionId) {
      fetch(`${getApiBaseUrl()}/video/start/${videoSessionId}`, { method: 'POST' })
        .catch(err => console.error("Failed to start backend inference", err));
    }
    showToast('success', 'Inference started');
    addLog('Inference active on video stream.', 'success');
  };

  // Restart pipeline handler
  const handleRestart = () => {
    playWaterDropSound();
    addLog('Pipeline reset triggered. Reconnecting to camera stream...', 'info');
    setFramesProcessed(0);
    setUptimeSeconds(0);
    setTimeout(() => {
      addLog('Camera re-connected â€¢ YOLOv8 model inference active', 'success');
    }, 400);
  };

  // Snapshot capture handler
  const handleTakeSnapshot = () => {
    playWaterDropSound();
    addLog(`Snapshot captured at ${new Date().toLocaleTimeString()} (Frame #${framesProcessed})`, 'success');

    const heroEl = document.getElementById('detection-hero-viewport');
    if (heroEl) {
      heroEl.classList.add('ring-4', 'ring-white');
      setTimeout(() => heroEl.classList.remove('ring-4', 'ring-white'), 300);
    }
  };

  // Keyboard shortcut listener
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.code === 'Space') {
        e.preventDefault();
        handleToggleRunning();
      } else if (e.code === 'KeyI') {
        setFeedMode((m) => (m === 'raw' ? 'inference' : 'raw'));
      } else if (e.code === 'KeyS') {
        handleTakeSnapshot();
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [isRunning, sourceType]);

  // Theme synchronization with document element and body
  useLayoutEffect(() => {
    if (typeof document === 'undefined') return;

    // Apply data-theme attribute for instant universal CSS variable resolution
    document.documentElement.setAttribute('data-theme', theme);
    document.body.setAttribute('data-theme', theme);

    // Synchronize classNames for standard theme and Tailwind dark variant
    document.documentElement.classList.remove('theme-pond-light', 'theme-pond-dark', 'theme-nature', 'dark');
    document.body.classList.remove('theme-pond-light', 'theme-pond-dark', 'theme-nature', 'dark');

    if (theme === 'pond-dark') {
      document.documentElement.classList.add('theme-pond-dark', 'dark');
      document.body.classList.add('theme-pond-dark', 'dark');
    } else if (theme === 'nature') {
      document.documentElement.classList.add('theme-nature', 'dark');
      document.body.classList.add('theme-nature', 'dark');
    } else {
      document.documentElement.classList.add('theme-pond-light');
      document.body.classList.add('theme-pond-light');
    }

    try {
      localStorage.setItem('visionmonitor-theme', theme);
    } catch {
      // Ignore storage errors in restricted contexts
    }
  }, [theme]);

  // Lock body and html scroll when modal is open to completely eliminate outer page scrollbars
  useEffect(() => {
    if (pendingSourceSwitch || settingsOpen || helpOpen) {
      lockScroll();
      return () => {
        unlockScroll();
      };
    }
  }, [pendingSourceSwitch, settingsOpen, helpOpen]);

  if (!systemInitialized) {
    return (
      <LandingScreen
        cameraConnected={cameraConnected}
        onInitialize={handleInitializeSystem}
      />
    );
  }

  return (
    <div
      data-theme={theme}
      className={`min-h-screen lg:h-screen lg:max-h-screen w-full min-w-full flex flex-col bg-[var(--bg-page)] text-[var(--text-primary)] ${theme === 'pond-dark' ? 'theme-pond-dark dark' : theme === 'nature' ? 'theme-nature dark' : 'theme-pond-light'
        }`}
    >

      {/* Subtle organic pond water ripple background backdrop */}
      <div className="fixed inset-0 pointer-events-none opacity-20 bg-[radial-gradient(var(--accent-pond)_0.8px,transparent_0.8px)] [background-size:24px_24px]" />



      {/* Sticky Full-Width Header */}
      <Header
        theme={theme}
        onThemeChange={handleThemeChange}
        cameraConfig={effectiveCameraConfig}
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenHelp={() => setHelpOpen(true)}
        fps={fps}
        anomalyDetected={anomalyStatus.isAnomaly}
      />

      <div className="relative w-full max-w-[1720px] 2xl:max-w-[1920px] mx-auto px-3 sm:px-5 lg:px-6 pt-2 sm:pt-3 pb-2 sm:pb-3 flex flex-col flex-1 min-h-0 gap-2.5 sm:gap-3">

        {/* ========================================================================= */}
        {/* 2. SOURCE SELECTOR & EXPECTED DUCKS CONTROL BAR                         */}
        {/* ========================================================================= */}
        <SourceSelector
          sourceType={sourceType}
          onSourceChange={(st) => {
            setSourceType(st);
            addLog(`Stream source switched to: ${st.toUpperCase()}`, 'info');
          }}
          onRequestSwitchMode={handleRequestSwitchMode}
          isRunning={isRunning}
          onToggleRunning={handleToggleRunning}
          onStopInference={handleStopInference}
          onResumeInference={handleResumeInference}
          isStreaming={isStreaming}
          onStartStream={startCameraStream}
          onStopStream={stopCameraStream}
          expectedDucks={expectedDucks}
          onExpectedDucksChange={(count) => {
            setExpectedDucks(count);
            addLog(`Expected duck count set to: ${count}`, 'info');
            // Always push to the backend whenever a session exists -- not just
            // while running. Previously this was gated on `isRunning`, so
            // changing the count while stopped/paused only updated local UI
            // state and never reached session["expected_ducks"] server-side.
            if (videoSessionId) {
              fetch(`${getApiBaseUrl()}/video/update_expected/${videoSessionId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ count: count })
              }).catch(err => console.error("Failed to update expected ducks", err));
            }
            if (sourceType === 'oak-camera' || sourceType === 'webcam') {
              fetch(`${getApiBaseUrl()}/oak/inference/update_expected/live`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ count: count })
              }).catch(err => console.error("Failed to update expected ducks for live camera", err));
            }
          }}
          onOpenSettings={() => setSettingsOpen(true)}
          customVideoName={customVideoName}
          onCustomVideoUploaded={handleCustomVideoUploaded}
          customVideoUrl={customVideoUrl}
          videoSessionId={videoSessionId}
          hasActiveVideo={Boolean(customVideoUrl || customVideoName)}
          onClearCustomVideo={handleClearCustomVideo}
          isCameraConnected={effectiveCameraConfig.connected}
          cameraStartingState={cameraStartingState}
        />

        {/* ========================================================================= */}
        {/* 3. MAIN HERO DETECTION WORKSPACE WITH COLLAPSIBLE DRAWERS                */}
        {/* ========================================================================= */}
        <div className="w-full flex flex-col lg:flex-row items-stretch flex-1 min-h-0 gap-4">

          {/* HERO VIDEO / INFERENCE CANVAS (DOMINANT CENTER ELEMENT) */}
          <main className="flex-1 w-full min-w-0 min-h-0 flex flex-col">
            <DetectionCanvas
              ducks={activeDucks}
              setDucks={setDucks}
              anomalyStatus={anomalyStatus}
              feedMode={feedMode}
              onFeedModeChange={setFeedMode}
              isRunning={isRunning}
              isStarting={isStarting}
              onToggleRunning={handleToggleRunning}
              onStopInference={handleStopInference}
              onResumeInference={handleResumeInference}
              isStreaming={isStreaming}
              onRequestSwitchMode={handleRequestSwitchMode}
              fps={fps}
              sourceType={sourceType}
              customVideoUrl={customVideoUrl}
              videoSessionId={videoSessionId}
              customVideoName={customVideoName}
              selectedDuckId={selectedDuckId}
              onSelectDuck={setSelectedDuckId}
              onCustomVideoUploaded={handleCustomVideoUploaded}
              onClearCustomVideo={handleClearCustomVideo}
              cameraStartingState={cameraStartingState}
              onCameraDeviceChange={setIsCameraDeviceActive}
              expectedDucks={expectedDucks}
              videoDimensions={videoDimensions}
              isCameraConnected={effectiveCameraConfig.connected}
              initialUploadFile={initialUploadFile}
              isBackendConnected={isBackendConnected}
            />
          </main>

          {/* Collapsible Right Detection Drawer */}
          {drawerOpen && (
            <DetectionDrawer
              isOpen={drawerOpen}
              onToggle={() => setDrawerOpen(!drawerOpen)}
              anomalyStatus={anomalyStatus}
              ducks={activeDucks}
              metrics={metrics}
              selectedDuckId={selectedDuckId}
              onSelectDuck={setSelectedDuckId}
              isStandby={isStandby}
              logs={logs}
            />
          )}

          {!drawerOpen && (
            <DetectionDrawer
              isOpen={false}
              onToggle={() => setDrawerOpen(true)}
              anomalyStatus={anomalyStatus}
              ducks={activeDucks}
              metrics={metrics}
              selectedDuckId={selectedDuckId}
              onSelectDuck={setSelectedDuckId}
              isStandby={isStandby}
              logs={logs}
            />
          )}
        </div>

      </div>

      {/* ========================================================================= */}
      {/* 4. SAFEGUARD MODAL FOR SWITCHING MODES DURING ACTIVE INFERENCE            */}
      {/* ========================================================================= */}
      <Modal
        isOpen={Boolean(pendingSourceSwitch)}
        onClose={() => setPendingSourceSwitch(null)}
        title="Active Inference Running"
        description="Safeguard: Cannot switch mode while active"
        icon={<AlertTriangle className="w-5 h-5 text-[var(--accent-duck)]" />}
        footer={
          <div className="flex flex-col-reverse sm:flex-row sm:items-center sm:justify-end gap-2 w-full">
            <Button
              variant="secondary"
              onClick={() => {
                playWaterDropSound();
                setPendingSourceSwitch(null);
              }}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={handleConfirmSwitchMode}
            >
              Stop &amp; Switch to {pendingSourceSwitch === 'oak-camera' ? 'Camera' : 'Video'}
            </Button>
          </div>
        }
      >
        <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
          Inference is currently active on the stream. To switch between <span className="font-bold text-[var(--text-primary)]">Offline (Video)</span> and <span className="font-bold text-[var(--text-primary)]">Online (Camera)</span> modes, please stop the active inference process first.
        </p>
      </Modal>

      {/* ========================================================================= */}
      {/* 5. TOAST NOTIFICATION CORNER ALERT                                       */}
      {/* ========================================================================= */}
      {toast && (
        <Toast
          type={toast.type}
          message={toast.message}
          onClose={() => setToast(null)}
        />
      )}

      {/* ========================================================================= */}
      {/* 6. MODALS & POPUPS                                                       */}
      {/* ========================================================================= */}
      <CameraSettingsModal
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        config={cameraConfig}
        onSaveConfig={async (cfg) => {
          setCameraConfig(cfg);
          try {
            const payload = {
              name: cfg.sourceName || 'OAK Camera',
              ip_address: cfg.ipAddress || undefined,
              resolution: cfg.resolution,
              fps: cfg.targetFps,
              rotation_angle: cfg.rotationAngle ?? 0,
              control_mode: cfg.controlMode ?? 'auto',
              exposure: cfg.exposure,
              gain: cfg.iso ?? cfg.gain,
              focus: cfg.focus,
              brightness: cfg.brightness,
              contrast: cfg.contrast,
              auto_focus: cfg.autoFocus,
              auto_exposure: cfg.autoExposure ?? true,
            };

            const savedCamera = cfg.id
              ? await cameraService.updateCamera(cfg.id, payload)
              : await cameraService.createCamera(payload);

            if (savedCamera && savedCamera.id) {
              setCameraConfig((prev) => ({
                ...prev,
                ...cfg,
                id: savedCamera.id,
              }));
            }
            showToast('success', 'Camera settings saved to database');
          } catch (e) {
            console.error(e);
            showToast('error', 'Failed to save camera to database');
          }
          addLog(`Camera configuration updated [${cfg.resolution} @ ${cfg.targetFps}fps]`, 'info');
        }}
        onReconnect={() => {
          addLog(`Reconnecting to OAK-D camera at ${cameraConfig.ipAddress}...`, 'info');
          setTimeout(() => addLog('OAK-D Camera re-connected with Excellent signal', 'success'), 600);
        }}
      />

      <HelpModal
        isOpen={helpOpen}
        onClose={() => setHelpOpen(false)}
      />
    </div>
  );
}
