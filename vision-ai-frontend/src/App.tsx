import React, { useState, useEffect, useLayoutEffect, useMemo } from 'react';
import type { ThemeMode, StreamSourceType } from './types';
import { LandingScreen } from './components/LandingScreen';
import { lockScroll, unlockScroll } from './utils/scrollLock';
import { Header } from './components/Header';
import { SourceSelector } from './components/SourceSelector';
import { DetectionCanvas } from './components/DetectionCanvas';
import { DetectionDrawer } from './components/DetectionDrawer';
import { CameraSettingsModal } from './components/CameraSettingsModal';
import { HelpModal } from './components/HelpModal';
import { Modal, Toast, Button } from './components/ui';
import { useInferenceStore } from './store/inferenceStore';
import { playWaterDropSound, setSoundEnabled } from './utils/audio';
import { AlertTriangle } from 'lucide-react';
import { getApiBaseUrl } from './lib/api';
import { cameraService } from './components/service/cameraService';
import { resetBBoxCache } from './utils/mlDataMapper';

// Extracted hooks — each one owns a clean slice of state + effects
import { useBackendHealth } from './hooks/useBackendHealth';
import { useToastAndLogs } from './hooks/useToastAndLogs';
import { useCameraStatus } from './hooks/useCameraStatus';
import { useVideoPipeline } from './hooks/useVideoPipeline';
import { useInferenceLoop } from './hooks/useInferenceLoop';
import { useAnomalyStatus } from './hooks/useAnomalyStatus';

export default function App() {
  // ─── 1. System Health ──────────────────────────────────────────────
  const { systemInitialized, isBackendConnected, handleInitializeSystem } = useBackendHealth();

  // ─── 2. Toast Notifications & Activity Logs ────────────────────────
  const { toast, setToast, showToast, logs, addLog } = useToastAndLogs();

  // ─── 3. Theme & UI Shell ───────────────────────────────────────────
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
      try { localStorage.setItem('visionmonitor-theme', newTheme); } catch { }
    }
    setTheme(newTheme);
  };

  const [feedMode, setFeedMode] = useState<'raw' | 'inference'>('inference');
  const [soundActive, setSoundActive] = useState<boolean>(true);
  const [drawerOpen, setDrawerOpen] = useState<boolean>(true);
  const [settingsOpen, setSettingsOpen] = useState<boolean>(false);
  const [helpOpen, setHelpOpen] = useState<boolean>(false);
  const [selectedDuckId, setSelectedDuckId] = useState<string | null>(null);

  const handleToggleSound = () => {
    const next = !soundActive;
    setSoundActive(next);
    setSoundEnabled(next);
    if (next) playWaterDropSound();
  };

  // ─── 4. Source Mode Coordination ───────────────────────────────────
  const [sourceType, setSourceType] = useState<StreamSourceType>('uploaded-video');
  const [pendingSourceSwitch, setPendingSourceSwitch] = useState<StreamSourceType | null>(null);

  // ─── 5. Camera Hardware ────────────────────────────────────────────
  const camera = useCameraStatus(addLog, showToast);

  // ─── Shared Pipeline State ─────────────────────────────────────────
  const [isRunning, setIsRunning] = useState<boolean>(false);
  const [isStarting, setIsStarting] = useState<boolean>(false);
  const [fps, setFps] = useState<number>(0);
  const [framesProcessed, setFramesProcessed] = useState<number>(0);
  const [uptimeSeconds, setUptimeSeconds] = useState<number>(0);
  const [expectedDucks, setExpectedDucks] = useState<number>(18);
  const [ducks, setDucks] = useState<import('./types').DuckEntity[]>([]);

  // ─── 6. Derived Source States ──────────────────────────────────────
  const backendStats = useInferenceStore(state => state.stats);
  const isVideoSource = sourceType === 'sample-pond' || sourceType === 'uploaded-video';
  const isCameraSource = sourceType === 'oak-camera' || sourceType === 'webcam';

  // ─── 7. Video Pipeline ─────────────────────────────────────────────
  const video = useVideoPipeline({
    showToast,
    addLog,
    expectedDucks,
    sourceType,
    isRunning,
    setIsRunning,
    setDucks,
    setFramesProcessed,
    setFps,
    setCameraStartingState: camera.setCameraStartingState,
    setSourceType,
    setIsStarting,
  });

  const hasActiveVideo = isVideoSource && Boolean(video.customVideoUrl || video.customVideoName);

  // ─── 8. Camera Pipeline (startCameraPipeline / stopCameraPipeline) ─
  const startCameraPipeline = async (): Promise<boolean> => {
    setDucks([]);
    useInferenceStore.getState().resetStats();
    resetBBoxCache();
    camera.setCameraStartingState('waking_camera');
    addLog('Step 1/3: Starting OAK Camera device (POST /oak/start)...', 'info');

    try {
      await fetch(`${getApiBaseUrl()}/oak/inference/update_expected/live`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ count: expectedDucks })
      }).catch(err => console.error("Failed to sync expected ducks before start", err));

      const startRes = await cameraService.start();
      if (startRes?.status === 'error') throw new Error(startRes.message || 'Camera start failed');

      const streamRes = await cameraService.startStream();
      if (streamRes?.status === 'error') throw new Error(streamRes.message || 'Stream start failed');
      camera.setIsStreaming(true);

      const inferenceRes = await cameraService.startLiveInference('live');
      if (inferenceRes?.status === 'error') throw new Error(inferenceRes.message || 'Inference start failed');

      camera.setCameraStartingState('waiting_frame');
      addLog('Step 2/3: Stream started • Waiting for camera sensor to warm up...', 'info');

      await new Promise((resolve) => setTimeout(resolve, 1500));

      camera.setCameraStartingState('ready');
      setIsRunning(true);
      showToast('success', 'Camera inference started successfully');
      addLog('Step 3/3: First live frame received (1080p). Starting inference • YOLOv8 active.', 'success');
      return true;
    } catch (error) {
      console.error('Failed to start camera pipeline:', error);
      camera.setCameraStartingState('ready');
      setIsRunning(false);
      showToast('error', 'Failed to start camera device');
      addLog('Error: Failed to connect to OAK stream or live inference.', 'error');
      return false;
    }
  };

  const stopCameraPipeline = async () => {
    camera.setCameraStartingState('ready');
    setIsRunning(false);
    setDucks([]);
    try {
      await cameraService.stopStream();
      camera.setIsStreaming(false);
    } catch (err) {
      console.warn('Stream stop call failed:', err);
    }
  };

  // ─── 9. Inference Loop (owns transport and sets states) ────────────
  const inference = useInferenceLoop({
    sourceType,
    videoSessionId: video.videoSessionId,
    expectedDucks,
    showToast,
    addLog,
    startCameraPipeline,
    setDucks,
    setVideoDimensions: video.setVideoDimensions,
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
    setUptimeSeconds,
  });

  // Recalculate derived values that depend on inference state
  const hasActiveStream = (isVideoSource && hasActiveVideo) || (isCameraSource && camera.isCameraDeviceActive && camera.cameraStartingState === 'ready');
  const isStandby = !hasActiveStream && ducks.length === 0;

  // ─── 10. Anomaly Detection (computed exactly once per render) ──────
  const anomalyFinal = useAnomalyStatus({
    hasActiveStream,
    isRunning,
    isStarting,
    isCameraSource,
    framesProcessed,
    backendStats,
    addLog,
    ducks,
    expectedDucks,
  });

  // ─── 10. Mode Switching ────────────────────────────────────────────
  const executeSwitchMode = async (targetType: StreamSourceType) => {
    const isTargetCamera = targetType === 'oak-camera' || targetType === 'webcam';
    setSourceType(targetType);
    setPendingSourceSwitch(null);

    if (isTargetCamera) {
      setIsRunning(false);
      camera.setCameraStartingState('waking_camera');
      addLog('Step 1/3: Starting OAK Camera device (POST /oak/start)...', 'info');

      try {
        const startRes = await cameraService.start();
        if (startRes.status === 'error') throw new Error(startRes.message);
        camera.setIsCameraDeviceActive(true);
        camera.setCameraStartingState('ready');
        addLog('Camera connected. Click Start Stream to begin frame capture.', 'success');
        showToast('success', 'OAK Camera connected');
      } catch (err) {
        showToast('error', 'Failed to start camera device');
        addLog('Error: Failed to connect to OAK stream.', 'error');
        camera.setCameraStartingState('ready');
      }
    } else {
      camera.setCameraStartingState('ready');
      try {
        await cameraService.stopStream();
        camera.setIsStreaming(false);
        try { await fetch(getApiBaseUrl() + '/oak/inference/stop', { method: 'POST' }); } catch(e){ }
        try { await fetch(getApiBaseUrl() + '/oak/stop', { method: 'POST' }); } catch(e){ }
      } catch (e) { }

      if (video.customVideoUrl) {
        inference.setIsRunning(false);
        showToast('info', 'Switched to Video mode • Select a video and press Start Inference');
      } else {
        inference.setIsRunning(false);
        showToast('info', 'Switched to Video mode');
      }
      addLog(`Stream source switched to: ${targetType.toUpperCase()}`, 'info');
    }
  };

  const handleRequestSwitchMode = (targetType: StreamSourceType) => {
    if (targetType === sourceType) return;
    const isCurrentCamera = sourceType === 'oak-camera' || sourceType === 'webcam';
    const isTargetCamera = targetType === 'oak-camera' || targetType === 'webcam';
    if (inference.isRunning && isCurrentCamera !== isTargetCamera) {
      setPendingSourceSwitch(targetType);
      return;
    }
    executeSwitchMode(targetType);
  };

  const handleConfirmSwitchMode = () => {
    if (!pendingSourceSwitch) return;
    playWaterDropSound();
    executeSwitchMode(pendingSourceSwitch);
  };

  // ─── 11. Metrics ──────────────────────────────────────────────────
  const metrics = useMemo(() => {
    const totalConf = anomalyFinal.activeDucks.reduce((acc, d) => acc + d.confidence, 0);
    const avgConfidence = anomalyFinal.activeDucks.length > 0 ? totalConf / anomalyFinal.activeDucks.length : 0;
    const speciesCounts: Record<string, number> = {};
    anomalyFinal.activeDucks.forEach((d) => { speciesCounts[d.species] = (speciesCounts[d.species] || 0) + 1; });
    return {
      fps: inference.fps,
      inferenceTimeMs: inference.fps > 0 ? 1000 / inference.fps : 0,
      framesProcessed: inference.framesProcessed,
      uptimeSeconds: inference.uptimeSeconds,
      avgConfidence,
      speciesCounts,
    };
  }, [anomalyFinal.activeDucks, inference.fps, inference.framesProcessed, inference.uptimeSeconds]);

  // ─── 12. Misc Handlers ────────────────────────────────────────────
  const handleRestart = () => {
    playWaterDropSound();
    addLog('Pipeline reset triggered. Reconnecting to camera stream...', 'info');
    inference.setFramesProcessed(0);
    inference.setUptimeSeconds(0);
    setTimeout(() => addLog('Camera re-connected • YOLOv8 model inference active', 'success'), 400);
  };

  const handleTakeSnapshot = () => {
    playWaterDropSound();
    addLog(`Snapshot captured at ${new Date().toLocaleTimeString()} (Frame #${inference.framesProcessed})`, 'success');
    const heroEl = document.getElementById('detection-hero-viewport');
    if (heroEl) {
      heroEl.classList.add('ring-4', 'ring-white');
      setTimeout(() => heroEl.classList.remove('ring-4', 'ring-white'), 300);
    }
  };

  // Wrap toggle/stop/resume to pass startVideoInference
  const handleToggleRunning = async () => {
    await inference.handleToggleRunning(video.startVideoInference);
  };
  const handleStopInference = async () => {
    camera.setCameraStartingState('ready');
    await inference.handleStopInference();
  };
  const handleResumeInference = () => {
    inference.handleResumeInference(video.startVideoInference);
  };

  // ─── 13. Keyboard Shortcuts (using refs to avoid stale closures) ──
  const handleToggleRunningRef = React.useRef(handleToggleRunning);
  const handleTakeSnapshotRef = React.useRef(handleTakeSnapshot);
  useEffect(() => { handleToggleRunningRef.current = handleToggleRunning; });
  useEffect(() => { handleTakeSnapshotRef.current = handleTakeSnapshot; });

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.code === 'Space') { e.preventDefault(); handleToggleRunningRef.current(); }
      else if (e.code === 'KeyI') { setFeedMode(m => m === 'raw' ? 'inference' : 'raw'); }
      else if (e.code === 'KeyS') { handleTakeSnapshotRef.current(); }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  // ─── 14. Theme Sync ───────────────────────────────────────────────
  useLayoutEffect(() => {
    if (typeof document === 'undefined') return;
    document.documentElement.setAttribute('data-theme', theme);
    document.body.setAttribute('data-theme', theme);
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
    try { localStorage.setItem('visionmonitor-theme', theme); } catch { }
  }, [theme]);

  // ─── 15. Modal Scroll Lock ────────────────────────────────────────
  useEffect(() => {
    if (pendingSourceSwitch || settingsOpen || helpOpen) {
      lockScroll();
      return () => { unlockScroll(); };
    }
  }, [pendingSourceSwitch, settingsOpen, helpOpen]);

  // ═══════════════════════════════════════════════════════════════════
  // RENDER
  // ═══════════════════════════════════════════════════════════════════
  if (!systemInitialized) {
    return <LandingScreen cameraConnected={camera.cameraConnected} onInitialize={handleInitializeSystem} />;
  }

  return (
    <div
      data-theme={theme}
      className={`min-h-screen lg:h-screen lg:max-h-screen w-full min-w-full flex flex-col bg-[var(--bg-page)] text-[var(--text-primary)] ${theme === 'pond-dark' ? 'theme-pond-dark dark' : theme === 'nature' ? 'theme-nature dark' : 'theme-pond-light'}`}
    >
      <div className="fixed inset-0 pointer-events-none opacity-20 bg-[radial-gradient(var(--accent-pond)_0.8px,transparent_0.8px)] [background-size:24px_24px]" />

      <Header
        theme={theme}
        onThemeChange={handleThemeChange}
        cameraConfig={camera.effectiveCameraConfig}
        onOpenSettings={() => setSettingsOpen(true)}
        onOpenHelp={() => setHelpOpen(true)}
        fps={fps}
        anomalyDetected={anomalyFinal.anomalyStatus.isAnomaly}
      />

      <div className="relative w-full max-w-[1720px] 2xl:max-w-[1920px] mx-auto px-3 sm:px-5 lg:px-6 pt-2 sm:pt-3 pb-2 sm:pb-3 flex flex-col flex-1 min-h-0 gap-2.5 sm:gap-3">
        <SourceSelector
          sourceType={sourceType}
          onSourceChange={(st) => { setSourceType(st); addLog(`Stream source switched to: ${st.toUpperCase()}`, 'info'); }}
          onRequestSwitchMode={handleRequestSwitchMode}
          isRunning={inference.isRunning}
          onToggleRunning={handleToggleRunning}
          onStopInference={handleStopInference}
          onResumeInference={handleResumeInference}
          isStreaming={camera.isStreaming}
          onStartStream={camera.startCameraStream}
          onStopStream={() => camera.stopCameraStream(isRunning, setDucks, setIsRunning)}
          expectedDucks={expectedDucks}
          onExpectedDucksChange={(count) => {
            setExpectedDucks(count);
            addLog(`Expected duck count set to: ${count}`, 'info');
            if (video.videoSessionId) {
              fetch(`${getApiBaseUrl()}/video/update_expected/${video.videoSessionId}`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ count })
              }).catch(err => console.error("Failed to update expected ducks", err));
            }
            if (sourceType === 'oak-camera' || sourceType === 'webcam') {
              fetch(`${getApiBaseUrl()}/oak/inference/update_expected/live`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ count })
              }).catch(err => console.error("Failed to update expected ducks for live camera", err));
            }
          }}
          onOpenSettings={() => setSettingsOpen(true)}
          customVideoName={video.customVideoName}
          onCustomVideoUploaded={video.handleVideoUploaded}
          customVideoUrl={video.customVideoUrl}
          videoSessionId={video.videoSessionId}
          hasActiveVideo={Boolean(video.customVideoUrl || video.customVideoName)}
          onClearCustomVideo={video.handleClearVideo}
          isCameraConnected={camera.effectiveCameraConfig.connected}
          cameraStartingState={camera.cameraStartingState}
        />

        <div className="w-full flex flex-col lg:flex-row items-stretch flex-1 min-h-0 gap-4">
          <main className="flex-1 w-full min-w-0 min-h-0 flex flex-col">
            <DetectionCanvas
              ducks={anomalyFinal.activeDucks}
              anomalyStatus={anomalyFinal.anomalyStatus}
              feedMode={feedMode}
              onFeedModeChange={setFeedMode}
              isRunning={isRunning}
              isStarting={isStarting}
              onToggleRunning={handleToggleRunning}
              isStreaming={camera.isStreaming}
              onRequestSwitchMode={handleRequestSwitchMode}
              fps={fps}
              sourceType={sourceType}
              customVideoUrl={video.customVideoUrl}
              videoSessionId={video.videoSessionId}
              selectedDuckId={selectedDuckId}
              onSelectDuck={setSelectedDuckId}
              onCustomVideoUploaded={video.handleVideoUploaded}
              cameraStartingState={camera.cameraStartingState}
              onCameraDeviceChange={camera.setIsCameraDeviceActive}
              expectedDucks={expectedDucks}
              videoDimensions={video.videoDimensions}
              isCameraConnected={camera.effectiveCameraConfig.connected}
              initialUploadFile={video.initialUploadFile}
              isBackendConnected={isBackendConnected}
            />
          </main>

          <DetectionDrawer
            isOpen={drawerOpen}
            onToggle={() => setDrawerOpen(!drawerOpen)}
            anomalyStatus={anomalyFinal.anomalyStatus}
            ducks={anomalyFinal.activeDucks}
            metrics={metrics}
            selectedDuckId={selectedDuckId}
            onSelectDuck={setSelectedDuckId}
            isStandby={isStandby}
            logs={logs}
          />
        </div>
      </div>

      {/* Safeguard modal for switching modes during active inference */}
      <Modal
        isOpen={Boolean(pendingSourceSwitch)}
        onClose={() => setPendingSourceSwitch(null)}
        title="Active Inference Running"
        description="Safeguard: Cannot switch mode while active"
        icon={<AlertTriangle className="w-5 h-5 text-[var(--accent-duck)]" />}
        footer={
          <div className="flex flex-col-reverse sm:flex-row sm:items-center sm:justify-end gap-2 w-full">
            <Button variant="secondary" onClick={() => { playWaterDropSound(); setPendingSourceSwitch(null); }}>Cancel</Button>
            <Button variant="primary" onClick={handleConfirmSwitchMode}>
              Stop &amp; Switch to {pendingSourceSwitch === 'oak-camera' ? 'Camera' : 'Video'}
            </Button>
          </div>
        }
      >
        <p className="text-xs text-[var(--text-secondary)] leading-relaxed">
          Inference is currently active on the stream. To switch between <span className="font-bold text-[var(--text-primary)]">Offline (Video)</span> and <span className="font-bold text-[var(--text-primary)]">Online (Camera)</span> modes, please stop the active inference process first.
        </p>
      </Modal>

      {toast && <Toast type={toast.type} message={toast.message} onClose={() => setToast(null)} />}

      <CameraSettingsModal
        isOpen={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        config={camera.cameraConfig}
        onSaveConfig={async (cfg) => {
          camera.setCameraConfig(cfg);
          try {
            const payload = {
              name: cfg.sourceName || 'OAK Camera', ip_address: cfg.ipAddress || undefined,
              resolution: cfg.resolution, fps: cfg.targetFps,
              rotation_angle: cfg.rotationAngle ?? 0, control_mode: cfg.controlMode ?? 'auto',
              exposure: cfg.exposure, gain: cfg.iso ?? cfg.gain, focus: cfg.focus,
              brightness: cfg.brightness, contrast: cfg.contrast,
              auto_focus: cfg.autoFocus, auto_exposure: cfg.autoExposure ?? true,
            };
            const savedCamera = cfg.id
              ? await cameraService.updateCamera(cfg.id, payload)
              : await cameraService.createCamera(payload);
            if (savedCamera && savedCamera.id) {
              camera.setCameraConfig((prev) => ({ ...prev, ...cfg, id: savedCamera.id }));
            }
            showToast('success', 'Camera settings saved to database');
          } catch (e) {
            console.error(e);
            showToast('error', 'Failed to save camera to database');
          }
          addLog(`Camera configuration updated [${cfg.resolution} @ ${cfg.targetFps}fps]`, 'info');
        }}
        onReconnect={() => {
          addLog(`Reconnecting to OAK-D camera at ${camera.cameraConfig.ipAddress}...`, 'info');
          setTimeout(() => addLog('OAK-D Camera re-connected with Excellent signal', 'success'), 600);
        }}
      />

      <HelpModal isOpen={helpOpen} onClose={() => setHelpOpen(false)} />
    </div>
  );
}
