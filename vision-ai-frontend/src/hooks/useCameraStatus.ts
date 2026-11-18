import { useState, useEffect, useMemo, useCallback, useRef } from 'react';
import type { CameraConfig, LogEntry } from '../types';
import { DEFAULT_CAMERA_CONFIG } from '../data/cameraData';
import { cameraService } from '../components/service/cameraService';
import { getApiBaseUrl } from '../lib/api';

function normalizeExposureMs(val: unknown, fallback: number = 16): number {
  if (typeof val !== 'number' || !Number.isFinite(val) || val <= 0) return fallback;
  const ms = val > 1000 ? Math.round(val / 1000) : Math.round(val);
  return Math.max(1, Math.min(33, ms));
}

function selectActiveCamera(cameras: any[]) {
  const sorted = [...cameras].sort((a: any, b: any) => (b.id ?? 0) - (a.id ?? 0));
  return (
    sorted.find((c: any) => c.ip_address && c.ip_address !== '127.0.0.1' && c.is_enabled) ||
    sorted.find((c: any) => c.is_enabled) ||
    sorted[0]
  );
}

export function useCameraStatus(
  addLog: (message: string, level?: LogEntry['level']) => void,
  showToast: (type: 'error' | 'success' | 'info', message: string) => void
) {
  const [cameraConfig, setCameraConfig] = useState<CameraConfig>({
    ...DEFAULT_CAMERA_CONFIG,
    connected: false,
  });
  const [isCameraDeviceActive, setIsCameraDeviceActive] = useState<boolean>(false);
  const [cameraStartingState, setCameraStartingState] = useState<'idle' | 'waking_camera' | 'waiting_frame' | 'ready'>('idle');
  const [cameraConnected, setCameraConnected] = useState<boolean | null>(null);
  const [isStreaming, setIsStreaming] = useState<boolean>(false);
  const [cameraError, setCameraError] = useState<string | null>(null);
  const wasOnlineRef = useRef<boolean>(false);

  const applyBackendCameraSettings = useCallback((settings: any, cameraId?: number | null) => {
    if (!settings || typeof settings !== 'object') return;
    setCameraConfig((prev) => ({
      ...prev,
      id: typeof cameraId === 'number' ? cameraId : (typeof settings.camera_id === 'number' ? settings.camera_id : prev.id),
      controlMode: (settings.control_mode === 'manual' || settings.control_mode === 'auto') ? settings.control_mode : prev.controlMode,
      exposure: normalizeExposureMs(settings.exposure, prev.exposure ?? 16),
      gain: typeof settings.gain === 'number' ? Math.round(settings.gain) : prev.gain,
      iso: typeof settings.gain === 'number' ? Math.round(settings.gain) : prev.iso,
      focus: typeof settings.focus === 'number' ? Math.round(settings.focus) : prev.focus,
      brightness: typeof settings.brightness === 'number' ? Math.round(settings.brightness) : prev.brightness,
      contrast: typeof settings.contrast === 'number' ? Math.round(settings.contrast) : prev.contrast,
      autoFocus: typeof settings.auto_focus === 'boolean' ? settings.auto_focus : prev.autoFocus,
      autoExposure: typeof settings.auto_exposure === 'boolean' ? settings.auto_exposure : prev.autoExposure,
    }));
  }, []);

  const refreshCameraConfig = useCallback(async (silent: boolean = false) => {
    try {
      const cameras = await cameraService.checkCamera();
      if (Array.isArray(cameras) && cameras.length > 0) {
        const activeCamera = selectActiveCamera(cameras);
        if (activeCamera) {
          const normalizedExposure = normalizeExposureMs(activeCamera.exposure, 16);
          setCameraConfig((prev) => ({
            ...prev,
            id: activeCamera.id,
            sourceName: activeCamera.name || prev.sourceName,
            ipAddress: activeCamera.ip_address || '',
            resolution: (activeCamera.resolution as any) || prev.resolution,
            targetFps: activeCamera.fps || prev.targetFps,
            rotationAngle: activeCamera.rotation_angle ?? prev.rotationAngle,
            controlMode: (activeCamera.control_mode as any) ?? prev.controlMode,
            exposure: normalizedExposure,
            gain: activeCamera.gain ?? prev.gain,
            iso: activeCamera.gain ?? prev.iso,
            focus: activeCamera.focus ?? prev.focus,
            brightness: activeCamera.brightness ?? prev.brightness,
            contrast: activeCamera.contrast ?? prev.contrast,
            autoFocus: activeCamera.auto_focus ?? prev.autoFocus,
            autoExposure: activeCamera.auto_exposure ?? prev.autoExposure,
            recordingFormat: (activeCamera.recording_format as any) || prev.recordingFormat || 'MP4',
          }));
          if (!silent) {
            addLog(`Loaded camera "${activeCamera.name}" from database [${activeCamera.resolution || '1080p'} @ ${activeCamera.fps || 30}fps]`, 'info');
          }
        }
      }
    } catch (err) {
      console.error("Could not fetch camera from database:", err);
    }
  }, [addLog]);

  // Automatically fetch latest saved camera from database on mount / refresh
  useEffect(() => {
    refreshCameraConfig(false);
  }, [refreshCameraConfig]);

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
            const justCameOnline = isOnline && !wasOnlineRef.current;
            wasOnlineRef.current = isOnline;

            setCameraConfig((prev) => (prev.connected !== isOnline ? { ...prev, connected: isOnline } : prev));
            setIsCameraDeviceActive(isOnline);
            setCameraConnected(isOnline);
            if (justCameOnline) {
              if (data.camera_settings) {
                applyBackendCameraSettings(data.camera_settings, data.camera_id);
              } else {
                refreshCameraConfig(true);
              }
            }
            if (data.last_error) {
              setCameraError(data.last_error);
            } else if (isOnline) {
              setCameraError(null);
            }
            // Only force false if the hardware is completely offline
            if (!isOnline) {
              setIsStreaming(false);
            }
          }
        } else {
          if (isMounted) {
            wasOnlineRef.current = false;
            setCameraConfig((prev) => (prev.connected ? { ...prev, connected: false } : prev));
            setIsCameraDeviceActive(false);
            setCameraConnected(false);
            setIsStreaming(false);
          }
        }
      } catch {
        if (isMounted) {
          wasOnlineRef.current = false;
          setCameraConfig((prev) => (prev.connected ? { ...prev, connected: false } : prev));
          setIsCameraDeviceActive(false);
          setCameraConnected(false);
          setIsStreaming(false);
        }
      }
    }

    checkCameraStatus();
    const interval = setInterval(checkCameraStatus, 5000);
    return () => {
      isMounted = false;
      clearInterval(interval);
    };
  }, [applyBackendCameraSettings, refreshCameraConfig]);

  // Dynamic camera config that accurately reflects hardware connection
  const effectiveCameraConfig = useMemo(() => ({
    ...cameraConfig,
    connected: isCameraDeviceActive,
  }), [cameraConfig, isCameraDeviceActive]);

  const startCameraStream = async () => {
    setCameraStartingState('waking_camera');
    try {
      setCameraError(null);
      if (!isCameraDeviceActive) {
        const startRes = await cameraService.start();
        if (startRes?.status === 'error') throw new Error(startRes.message || 'Camera start failed');
        if (startRes?.camera_settings) {
          applyBackendCameraSettings(startRes.camera_settings, startRes.camera_id);
        } else {
          await refreshCameraConfig(true);
        }
        setIsCameraDeviceActive(true);
      } else {
        await refreshCameraConfig(true);
      }
      const streamRes = await cameraService.startStream();
      if (streamRes?.status === 'error') throw new Error(streamRes.message || 'Stream start failed');
      setIsStreaming(true);
      setCameraStartingState('ready');
      setCameraError(null);
      showToast('success', 'Camera stream started');
      addLog('Camera stream started. Ready for display or inference.', 'success');
    } catch (error: any) {
      setCameraStartingState('ready');
      const errMsg = error?.response?.data?.message || error?.response?.data?.detail || error?.message || 'Unable to start camera stream';
      setCameraError(errMsg);
      showToast('error', errMsg);
      addLog(`Error: ${errMsg}`, 'error');
    }
  };

  const stopCameraStream = async (
    _isRunning?: boolean,
    setDucks?: (ducks: any[]) => void,
    setIsRunning?: (r: boolean) => void
  ) => {
    try { await cameraService.stopLiveInference(); } catch { /* stream can still stop */ }
    setIsRunning?.(false);
    setDucks?.([]);
    try {
      await cameraService.stopStream();
      setIsStreaming(false);
      showToast('info', 'Camera stream stopped');
      addLog('Camera stream stopped • Ready for fresh stream.', 'info');
    } catch {
      showToast('error', 'Unable to stop camera stream');
    }
  };

  return {
    cameraConfig,
    setCameraConfig,
    isCameraDeviceActive,
    setIsCameraDeviceActive,
    cameraStartingState,
    setCameraStartingState,
    cameraConnected,
    setCameraConnected,
    isStreaming,
    setIsStreaming,
    effectiveCameraConfig,
    startCameraStream,
    stopCameraStream,
    refreshCameraConfig,
    applyBackendCameraSettings,
    cameraError,
    setCameraError
  };
}
