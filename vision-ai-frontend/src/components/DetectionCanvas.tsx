import React, { useRef, useEffect, useState, useMemo } from 'react';
import { DuckEntity, StreamSourceType, AnomalyStatus } from '../types';
import { 
  Minimize2, 
  AlertTriangle,
  CheckCircle2,
  Eye,
  EyeOff,
  UploadCloud,
  Loader2,
  Expand,
  Image as ImageIcon,
  Play,
  Pause,
  CameraOff,
  Video,
  RotateCcw,
  Camera,
  ShieldAlert,
  Layers
} from 'lucide-react';
import { playWaterDropSound, playDuckQuackSound } from '../utils/audio';
import { getApiBaseUrl, API_BASE_URL } from '../lib/api';
import { useRecording } from './hooks/useRecording';
import { useInferenceStore } from '../store/inferenceStore';

interface DetectionCanvasProps {
  ducks: DuckEntity[];
  setDucks: React.Dispatch<React.SetStateAction<DuckEntity[]>>;
  anomalyStatus: AnomalyStatus;
  feedMode: 'raw' | 'inference';
  onFeedModeChange: (mode: 'raw' | 'inference') => void;
  isRunning: boolean;
  isStarting?: boolean;
  onToggleRunning?: () => void;
  onStopInference?: () => void;
  onResumeInference?: () => void;
  isStreaming?: boolean;
  onRequestSwitchMode?: (type: StreamSourceType) => void;
  fps: number;
  sourceType: StreamSourceType;
  customVideoUrl?: string;
  videoSessionId?: string | null;
  customVideoName?: string;
  selectedDuckId: string | null;
  onSelectDuck: (id: string | null) => void;
  onCustomVideoUploaded?: (videoUrl: string, fileName: string, sessionId?: string) => void;
  onClearCustomVideo?: () => void;
  cameraStartingState?: 'idle' | 'waking_camera' | 'waiting_frame' | 'ready';
  onCameraDeviceChange?: (active: boolean) => void;
  expectedDucks?: number;
  videoDimensions?: { width: number; height: number } | null;
  isCameraConnected?: boolean;
  initialUploadFile?: File;
  isBackendConnected?: boolean;
}

export const DetectionCanvas: React.FC<DetectionCanvasProps> = ({
  ducks,
  setDucks,
  anomalyStatus,
  feedMode,
  onFeedModeChange,
  isRunning,
  isStarting,
  onToggleRunning,
  onStopInference,
  onResumeInference,
  isStreaming = false,
  onRequestSwitchMode,
  fps,
  sourceType,
  customVideoUrl,
  videoSessionId,
  customVideoName,
  selectedDuckId,
  onSelectDuck,
  onCustomVideoUploaded,
  onClearCustomVideo,
  cameraStartingState = 'ready',
  onCameraDeviceChange,
  expectedDucks = 18,
  videoDimensions,
  isCameraConnected = false,
  initialUploadFile,
  isBackendConnected = true,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const cameraImgRef = useRef<HTMLImageElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [isFullscreen, setIsFullscreen] = useState(false);
  const [showHUD, setShowHUD] = useState(true);
  const [showConfidence] = useState(true);
  const [showTrails] = useState(false);
  const [showAllBoxes, setShowAllBoxes] = useState<boolean>(false);
  const [isDragOver, setIsDragOver] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [videoAspect, setVideoAspect] = useState<number | null>(null);
  const [isFirstFrameLoaded, setIsFirstFrameLoaded] = useState<boolean>(false);
  const [containerSize, setContainerSize] = useState<{ width: number; height: number }>({ width: 0, height: 0 });
  const [isCameraDeviceActive, setIsCameraDeviceActive] = useState<boolean>(true);
  const [streamCacheBuster, setStreamCacheBuster] = useState<number>(Date.now());
  const [isSelectingVideo, setIsSelectingVideo] = useState<boolean>(false);

  const ripplesRef = useRef<{ x: number; y: number; radius: number; opacity: number }[]>([]);
  const trailHistoryRef = useRef<Record<string, { x: number; y: number }[]>>({});
  const ducksRef = useRef<DuckEntity[]>(ducks);
  const showTrailsRef = useRef<boolean>(showTrails);
  const feedModeRef = useRef<'raw' | 'inference'>(feedMode);

  const { isRecording, recordedFile, startRecording, stopRecording, clearRecording } = useRecording();

  ducksRef.current = ducks;
  showTrailsRef.current = showTrails;
  feedModeRef.current = feedMode;

  const isVideoSource = sourceType === 'uploaded-video' || sourceType === 'sample-pond';
  const isCameraSource = sourceType === 'oak-camera' || sourceType === 'webcam';
  const hasActiveVideo = isVideoSource && !!customVideoUrl;

  const backendStats = useInferenceStore((state) => state.stats);
  const isHandPresent = 
    backendStats?.status === 'HAND' || 
    backendStats?.hand_detected === true || 
    anomalyStatus?.message?.includes('HAND') ||
    ducks.some((d) => d.species === 'Hand' || d.handDetected === true || d.statusEvent === 'hand_present');

  const isSceneAnomaly = anomalyStatus?.isAnomaly === true || backendStats?.status === 'ANOMALY';

  // Refresh cache buster whenever running state changes (start or stop)
  useEffect(() => {
    setStreamCacheBuster(Date.now());
    if (isRunning) {
      setShowAllBoxes(false);
    }
  }, [isRunning]);

  // When backend confirms stopped status, refresh cache buster so final frame is loaded
  useEffect(() => {
    if (backendStats?.status === 'stopped' && !isRunning) {
      setStreamCacheBuster(Date.now());
    }
  }, [backendStats?.status, isRunning]);

  const effectiveVideoUrl = useMemo(() => {
    if (!hasActiveVideo) return undefined;
    if (videoSessionId) {
      if (isRunning) {
        return `${getApiBaseUrl()}/video/stream/${videoSessionId}?t=${streamCacheBuster}`;
      }
      // When stopped / paused: display the last inference frame from backend - NO BLACK SCREEN!
      return `${getApiBaseUrl()}/video/last_frame/${videoSessionId}?t=${streamCacheBuster}`;
    }
    return customVideoUrl;
  }, [customVideoUrl, hasActiveVideo, isRunning, videoSessionId, streamCacheBuster]);

  // Reset video aspect ratio on video change
  useEffect(() => {
    setVideoAspect(null);
  }, [effectiveVideoUrl, hasActiveVideo, sourceType]);

  // Reset first frame loaded flag ONLY when a new run begins, preserving last frame when stopped
  useEffect(() => {
    if (isRunning) {
      setIsFirstFrameLoaded(false);
    }
  }, [isRunning]);

  // Dynamic pixel-perfect aspect ratio fitting for video and inference bounding boxes
  const fittedRect = useMemo(() => {
    if (!containerSize.width || !containerSize.height) {
      return { width: '100%', height: '100%' };
    }
    const targetAspect = 
      (videoDimensions?.width && videoDimensions?.height 
        ? videoDimensions.width / videoDimensions.height 
        : null) || 
      videoAspect || 
      (16 / 9);
    const containerAspect = containerSize.width / containerSize.height;
    
    let w: number;
    let h: number;
    if (containerAspect > targetAspect) {
      // Container is wider -> fit to container height
      h = containerSize.height;
      w = Math.floor(containerSize.height * targetAspect);
    } else {
      // Container is taller -> fit to container width
      w = containerSize.width;
      h = Math.floor(containerSize.width / targetAspect);
    }

    return {
      width: `${w}px`,
      height: `${h}px`,
    };
  }, [containerSize, videoAspect, videoDimensions, isCameraSource]);

  // Auto-play local video ONLY in raw mode or when no inference session exists
  useEffect(() => {
    if (!videoRef.current) return;
    if (hasActiveVideo && (feedMode === 'raw' || !videoSessionId)) {
      videoRef.current.muted = true;
      videoRef.current.play().catch(() => {});
    } else {
      videoRef.current.pause();
    }
  }, [hasActiveVideo, customVideoUrl, feedMode, videoSessionId]);

  // Fullscreen listener with robust cross-browser API
  const toggleFullscreen = () => {
    playWaterDropSound();
    const elem = containerRef.current;
    if (!elem) return;

    const isFs = !!(
      document.fullscreenElement ||
      (document as unknown as { webkitFullscreenElement?: Element }).webkitFullscreenElement ||
      (document as unknown as { mozFullScreenElement?: Element }).mozFullScreenElement ||
      (document as unknown as { msFullscreenElement?: Element }).msFullscreenElement
    );

    if (!isFs) {
      if (elem.requestFullscreen) {
        elem.requestFullscreen().catch((err) => console.warn('Fullscreen error:', err));
      } else if ((elem as unknown as { webkitRequestFullscreen?: () => void }).webkitRequestFullscreen) {
        (elem as unknown as { webkitRequestFullscreen: () => void }).webkitRequestFullscreen();
      } else if ((elem as unknown as { mozRequestFullScreen?: () => void }).mozRequestFullScreen) {
        (elem as unknown as { mozRequestFullScreen: () => void }).mozRequestFullScreen();
      } else if ((elem as unknown as { msRequestFullscreen?: () => void }).msRequestFullscreen) {
        (elem as unknown as { msRequestFullscreen: () => void }).msRequestFullscreen();
      }
    } else {
      if (document.exitFullscreen) {
        document.exitFullscreen().catch(() => {});
      } else if ((document as unknown as { webkitExitFullscreen?: () => void }).webkitExitFullscreen) {
        (document as unknown as { webkitExitFullscreen: () => void }).webkitExitFullscreen();
      } else if ((document as unknown as { mozCancelFullScreen?: () => void }).mozCancelFullScreen) {
        (document as unknown as { mozCancelFullScreen: () => void }).mozCancelFullScreen();
      } else if ((document as unknown as { msExitFullscreen?: () => void }).msExitFullscreen) {
        (document as unknown as { msExitFullscreen: () => void }).msExitFullscreen();
      }
    }
  };

  useEffect(() => {
    const handleFsChange = () => {
      const isFs = !!(
        document.fullscreenElement ||
        (document as unknown as { webkitFullscreenElement?: Element }).webkitFullscreenElement ||
        (document as unknown as { mozFullScreenElement?: Element }).mozFullScreenElement ||
        (document as unknown as { msFullscreenElement?: Element }).msFullscreenElement
      );
      setIsFullscreen(isFs);
    };

    document.addEventListener('fullscreenchange', handleFsChange);
    document.addEventListener('webkitfullscreenchange', handleFsChange);
    document.addEventListener('mozfullscreenchange', handleFsChange);
    document.addEventListener('MSFullscreenChange', handleFsChange);

    // Efficient ResizeObserver for container size & canvas resolution calculation
    let rafId: number | null = null;
    let observer: ResizeObserver | null = null;
    if (containerRef.current) {
      observer = new ResizeObserver((entries) => {
        if (rafId !== null) cancelAnimationFrame(rafId);
        rafId = requestAnimationFrame(() => {
          for (const entry of entries) {
            if (entry.contentRect.width > 0 && entry.contentRect.height > 0) {
              setContainerSize({
                width: entry.contentRect.width,
                height: entry.contentRect.height,
              });

              if (canvasRef.current) {
                const dpr = Math.min(window.devicePixelRatio || 1, 2);
                const targetW = Math.round(entry.contentRect.width * dpr);
                const targetH = Math.round(entry.contentRect.height * dpr);
                if (canvasRef.current.width !== targetW || canvasRef.current.height !== targetH) {
                  canvasRef.current.width = targetW;
                  canvasRef.current.height = targetH;
                }
              }
            }
          }
        });
      });
      observer.observe(containerRef.current);
    }

    return () => {
      document.removeEventListener('fullscreenchange', handleFsChange);
      document.removeEventListener('webkitfullscreenchange', handleFsChange);
      document.removeEventListener('mozfullscreenchange', handleFsChange);
      document.removeEventListener('MSFullscreenChange', handleFsChange);
      if (rafId !== null) cancelAnimationFrame(rafId);
      if (observer) observer.disconnect();
    };
  }, []);

  // Duck physics simulation loop removed because real ML provides exact coordinates.

  // Water ripple animation loop
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    let animationFrameId: number;

    const render = () => {
      ctx.clearRect(0, 0, canvas.width, canvas.height);

      const updatedRipples: { x: number; y: number; radius: number; opacity: number }[] = [];
      for (let i = 0; i < ripplesRef.current.length; i++) {
        const r = ripplesRef.current[i];
        const newRadius = r.radius + 0.9;
        const newOpacity = r.opacity - 0.015;
        if (newOpacity > 0) {
          updatedRipples.push({
            x: r.x,
            y: r.y,
            radius: newRadius,
            opacity: newOpacity,
          });

          ctx.save();
          ctx.beginPath();
          ctx.arc(r.x, r.y, newRadius, 0, Math.PI * 2);
          ctx.strokeStyle = `rgba(186, 230, 253, ${newOpacity * 0.6})`;
          ctx.lineWidth = 2;
          ctx.stroke();
          ctx.restore();
        }
      }
      ripplesRef.current = updatedRipples;

      if (showTrailsRef.current && feedModeRef.current === 'inference') {
        const currentDucks = ducksRef.current;
        ctx.save();
        Object.entries(trailHistoryRef.current).forEach(([id, pts]) => {
          const points = pts as { x: number; y: number }[];
          const duck = currentDucks.find((d) => d.id === id);
          if (!duck || !points || points.length < 2) return;

          ctx.beginPath();
          const first = points[0];
          const px = (first.x / 100) * canvas.width;
          const py = (first.y / 100) * canvas.height;
          ctx.moveTo(px, py);

          for (let i = 1; i < points.length; i++) {
            const p = points[i];
            const cx = (p.x / 100) * canvas.width;
            const cy = (p.y / 100) * canvas.height;
            ctx.lineTo(cx, cy);
          }

          ctx.strokeStyle = duck.isAnomaly ? 'rgba(239, 68, 68, 0.4)' : 'rgba(16, 185, 129, 0.35)';
          ctx.lineWidth = 2.5;
          ctx.lineCap = 'round';
          ctx.stroke();
        });
        ctx.restore();
      }

      animationFrameId = requestAnimationFrame(render);
    };

    render();

    return () => cancelAnimationFrame(animationFrameId);
  }, []);

  // Process file upload via FastAPI backend
  const processUploadedFile = async (file: File) => {
    playWaterDropSound();
    
    // Real upload progress tracking using XMLHttpRequest
    setUploadProgress(0);

    const formData = new FormData();
    formData.append('file', file);
    formData.append('expected_ducks', expectedDucks.toString());

    const xhr = new XMLHttpRequest();
    xhr.open('POST', `${API_BASE_URL}/video/upload`);

    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) {
        const percent = Math.round((event.loaded / event.total) * 100);
        setUploadProgress(Math.min(99, percent));
      }
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          const data = JSON.parse(xhr.responseText);
          setUploadProgress(100);
          setTimeout(async () => {
            setUploadProgress(null);
            if (onCustomVideoUploaded) {
              try {
                // Auto-start inference immediately on uploaded video so user doesn't need to click a second time
                await fetch(`${getApiBaseUrl()}/video/update_expected/${data.session_id}`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ count: expectedDucks })
                }).catch(() => {});
                await fetch(`${getApiBaseUrl()}/video/start/${data.session_id}`, { method: 'POST' });
                const streamUrl = `${getApiBaseUrl()}/video/stream/${data.session_id}`;
                onCustomVideoUploaded(streamUrl, file.name, data.session_id);
              } catch (err) {
                console.error('Auto-start inference error, falling back to local preview:', err);
                const localBlobUrl = URL.createObjectURL(file);
                onCustomVideoUploaded(localBlobUrl, file.name, data.session_id);
              }
            }
          }, 350);
        } catch (e) {
          setUploadProgress(null);
        }
      } else {
        setUploadProgress(null);
        console.error('Upload failed with status:', xhr.status);
      }
    };

    xhr.onerror = () => {
      setUploadProgress(null);
      console.error('Network error during video upload');
    };

    xhr.send(formData);
  };

  useEffect(() => {
    if (recordedFile) {
      processUploadedFile(recordedFile);
      clearRecording(); // Ensure it is consumed
    }
  }, [recordedFile]);

  useEffect(() => {
    if (initialUploadFile) {
      processUploadedFile(initialUploadFile);
    }
  }, [initialUploadFile]);

  const handleFileInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) {
      processUploadedFile(file);
    }
  };

  const handleSelectVideoAndStart = async () => {
    // 1. Check if running in Electron desktop app with electronAPI.selectFile
    const electronApi = (window as any).electronAPI;
    if (electronApi && typeof electronApi.selectFile === 'function') {
      try {
        setIsSelectingVideo(true);
        const filePath = await electronApi.selectFile();
        if (!filePath) {
          setIsSelectingVideo(false);
          return;
        }

        const baseUrl = getApiBaseUrl();
        const res = await fetch(`${baseUrl}/video/inference/path`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            video_path: filePath,
            expected_ducks: expectedDucks,
          }),
        });

        if (!res.ok) {
          const errData = await res.json().catch(() => ({}));
          throw new Error(errData.message || 'Failed to start path inference');
        }

        const data = await res.json();
        const filename = data.video_name || filePath.split(/[/\\]/).pop() || 'video.mp4';

        if (onCustomVideoUploaded) {
          const streamUrl = `${baseUrl}/video/stream/${data.session_id}`;
          onCustomVideoUploaded(streamUrl, filename, data.session_id);
        }
      } catch (err: any) {
        console.error('Desktop video selection error:', err);
      } finally {
        setIsSelectingVideo(false);
      }
      return;
    }

    // 2. Browser fallback: open HTML5 file picker for web development
    fileInputRef.current?.click();
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    if (isVideoSource) {
      setIsDragOver(true);
    }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    if (!isVideoSource) return;
    const file = e.dataTransfer.files?.[0];
    if (file && file.type.startsWith('video/')) {
      processUploadedFile(file);
    }
  };

  // Canvas click ripple & selection
  const handleCanvasClick = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;

    const xPercent = (clickX / rect.width) * 100;
    const yPercent = (clickY / rect.height) * 100;

    ripplesRef.current.push({
      x: clickX,
      y: clickY,
      radius: 5,
      opacity: 0.85,
    });
    if (ripplesRef.current.length > 12) {
      ripplesRef.current.shift();
    }
    playWaterDropSound();

    const clickedDuck = ducks.find(
      (d) =>
        xPercent >= d.x &&
        xPercent <= d.x + d.width &&
        yPercent >= d.y &&
        yPercent <= d.y + d.height
    );

    if (clickedDuck) {
      playDuckQuackSound();
      onSelectDuck(clickedDuck.id === selectedDuckId ? null : clickedDuck.id);
    } else {
      if (selectedDuckId) {
        onSelectDuck(null);
      }
    }
  };

  const isWaitingForVideo = isVideoSource && !hasActiveVideo;

  return (
    <div
      ref={containerRef}
      id="detection-hero-viewport"
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={handleDrop}
      className={`relative w-full flex-1 h-full min-h-[350px] lg:min-h-0 overflow-hidden border select-none group ${
        isFullscreen ? 'rounded-none border-none' : 'rounded-3xl'
      } ${
        isDragOver
          ? 'border-[var(--accent-pond)] ring-4 ring-[var(--accent-pond-subtle)]'
          : 'border-[var(--border-color)]'
      } ${
        isWaitingForVideo
          ? 'bg-[var(--bg-card)]'
          : 'bg-[#0B1814]'
      } shadow-sm`}
      style={
        isFullscreen
          ? { width: '100%', height: '100%', minHeight: '100vh', maxHeight: '100vh' }
          : undefined
      }
    >
      {/* Hidden File Input for Video Selection */}
      <input
        type="file"
        ref={fileInputRef}
        onChange={handleFileInputChange}
        accept="video/*"
        className="hidden"
      />

      {/* ========================================================================= */}
      {/* 1. VIDEO SOURCE: CLEAN EMBEDDED UPLOAD CARD WITH 14PX INSET DASHED LINE  */}
      {/* ========================================================================= */}
      {isWaitingForVideo && (
        <div className="absolute inset-0 w-full h-full p-2.5 sm:p-3.5 z-20 pointer-events-none flex flex-col items-center justify-center">
          {/* Dashed line inset 14px (9px + 5px extra space) from the outer canvas container */}
          <div className="w-full h-full rounded-[14px] border-2 border-dashed border-[var(--border-color)] flex flex-col items-center justify-center text-center p-3 sm:p-6 lg:p-8 pointer-events-auto overflow-y-auto hidden-scrollbar">
            {/* Neat Proportional Icon */}
            <div className="text-[var(--accent-pond)] mb-2 sm:mb-3 shrink-0">
              <UploadCloud className="w-10 h-10 sm:w-12 sm:h-12 lg:w-14 lg:h-14 stroke-[1.75]" />
            </div>

            {/* Title & Instructions */}
            <h3 className="text-lg sm:text-xl lg:text-2xl font-bold text-[var(--text-primary)] mb-1 sm:mb-2 shrink-0">
              Select Video for Inference
            </h3>
            <p className="text-xs sm:text-sm lg:text-base text-[var(--text-secondary)] max-w-md mb-4 sm:mb-6 shrink-0">
              Choose a video from your computer to start AI inspection.
            </p>

            {/* Desktop Action Button / Upload Progress */}
            {uploadProgress === null ? (
              <div className="mb-4 sm:mb-6 shrink-0">
                <button
                  type="button"
                  disabled={!isBackendConnected || isSelectingVideo}
                  onClick={handleSelectVideoAndStart}
                  className={`flex items-center gap-2.5 px-6 sm:px-8 py-3 rounded-2xl font-bold text-sm sm:text-base shadow-md transition-all active:scale-95 ${
                    !isBackendConnected
                      ? 'bg-[var(--bg-card)] text-[var(--text-muted)] border border-[var(--border-color)] cursor-not-allowed opacity-70'
                      : isSelectingVideo
                      ? 'bg-[var(--btn-primary-bg)] text-[var(--btn-primary-text)] cursor-wait opacity-80'
                      : 'bg-[var(--btn-primary-bg)] hover:bg-[var(--btn-primary-hover)] text-[var(--btn-primary-text)] cursor-pointer hover:shadow-lg hover:shadow-[var(--accent-pond)]/20'
                  }`}
                >
                  {isSelectingVideo ? (
                    <>
                      <Loader2 className="w-4 h-4 sm:w-5 sm:h-5 animate-spin" />
                      <span>Selecting Video...</span>
                    </>
                  ) : !isBackendConnected ? (
                    <>
                      <span className="w-2 h-2 rounded-full bg-rose-500" />
                      <span>Backend Offline</span>
                    </>
                  ) : (
                    <>
                      <Play className="w-4 h-4 sm:w-5 sm:h-5 fill-current" />
                      <span> Start Inference</span>
                    </>
                  )}
                </button>
              </div>
            ) : (
              <div className="w-full max-w-sm mb-5 sm:mb-6 shrink-0">
                <div className="flex items-center justify-between text-xs text-[var(--text-secondary)] mb-1.5 font-medium">
                  <span className="flex items-center gap-1.5">
                    <Loader2 className="w-4 h-4 animate-spin text-[var(--accent-pond)]" />
                    Uploading video file...
                  </span>
                  <span className="font-bold font-mono text-[var(--accent-pond)]">
                    {Math.round(uploadProgress)}%
                  </span>
                </div>
                <div className="w-full h-2.5 bg-[var(--btn-secondary-border)] rounded-full overflow-hidden">
                  <div
                    className="h-full bg-[var(--accent-pond)] transition-all duration-150 rounded-full"
                    style={{ width: `${uploadProgress}%` }}
                  />
                </div>
              </div>
            )}

            {/* Supported Formats */}
            <div className="flex flex-wrap items-center justify-center gap-1.5 sm:gap-2.5 shrink-0">
              <span className="px-2.5 sm:px-3.5 py-0.5 sm:py-1 rounded-lg bg-[var(--bg-card-subtle)] border border-[var(--border-color)] text-[10px] sm:text-xs font-mono font-medium text-[var(--text-secondary)]">
                MP4
              </span>
              <span className="px-2.5 sm:px-3.5 py-0.5 sm:py-1 rounded-lg bg-[var(--bg-card-subtle)] border border-[var(--border-color)] text-[10px] sm:text-xs font-mono font-medium text-[var(--text-secondary)]">
                WebM
              </span>
              <span className="px-2.5 sm:px-3.5 py-0.5 sm:py-1 rounded-lg bg-[var(--bg-card-subtle)] border border-[var(--border-color)] text-[10px] sm:text-xs font-mono font-medium text-[var(--text-secondary)]">
                MOV
              </span>
              <span className="px-2.5 sm:px-3.5 py-0.5 sm:py-1 rounded-lg bg-[var(--bg-card-subtle)] border border-[var(--border-color)] text-[10px] sm:text-xs font-mono font-medium text-[var(--text-secondary)]">
                MKV
              </span>
            </div>
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 2 & 3. VIDEO & CAMERA VIEWPORT WITH TRUE ASPECT RATIO & ZERO STRETCHING   */}
      {/* ========================================================================= */}
      {(hasActiveVideo || isCameraSource) && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-none p-0 overflow-hidden">
          <div
            id="real-video-render-box"
            className="relative flex items-center justify-center pointer-events-auto select-none overflow-hidden"
            style={fittedRect}
          >
            {/* Normal MP4 Playback (Local) - ONLY active & visible when NOT in inference session */}
            {hasActiveVideo && (
              <video
                ref={(el) => {
                  if (el) {
                    el.muted = true;
                    el.defaultMuted = true;
                    el.playsInline = true;
                  }
                  (videoRef as any).current = el;
                }}
                key={customVideoUrl || 'main-player'}
                src={customVideoUrl}
                autoPlay={feedMode === 'raw' || !videoSessionId}
                loop
                muted
                playsInline
                preload="auto"
                controls={false}
                onCanPlay={(e) => {
                  e.currentTarget.muted = true;
                  if (feedMode === 'raw' || !videoSessionId) {
                    setIsFirstFrameLoaded(true);
                    e.currentTarget.play().catch(() => {});
                  } else {
                    e.currentTarget.pause();
                  }
                }}
                onLoadedData={(e) => {
                  e.currentTarget.muted = true;
                  if (feedMode === 'raw' || !videoSessionId) {
                    setIsFirstFrameLoaded(true);
                    e.currentTarget.play().catch(() => {});
                  } else {
                    e.currentTarget.pause();
                  }
                }}
                onLoadedMetadata={(e) => {
                  const v = e.currentTarget;
                  v.muted = true;
                  if (feedMode === 'raw' || !videoSessionId) {
                    setIsFirstFrameLoaded(true);
                    v.play().catch(() => {});
                  } else {
                    v.pause();
                  }
                  if (v.videoWidth && v.videoHeight) {
                    setVideoAspect(v.videoWidth / v.videoHeight);
                  }
                }}
                onError={(e) => {
                  const err = e.currentTarget.error;
                  console.error('VIDEO ERROR', err?.code, err?.message);
                }}
                className={`w-full h-full block object-contain select-none ${
                  feedMode === 'inference' && videoSessionId
                    ? 'absolute inset-0 -z-10 opacity-0 pointer-events-none'
                    : 'absolute inset-0 z-0 opacity-100 pointer-events-auto'
                }`}
              />
            )}

            {/* Backend Inference MJPEG Stream OR Last Frame when Stopped */}
            {hasActiveVideo && feedMode === 'inference' && videoSessionId && (
              <img
                key={`backend-frame-${videoSessionId}`}
                src={effectiveVideoUrl}
                alt="Backend Inference Stream"
                className={`w-full h-full block object-contain select-none pointer-events-auto absolute inset-0 z-10 ${
                  isFirstFrameLoaded ? 'opacity-100' : 'opacity-0 pointer-events-none'
                }`}
                onLoad={(e) => {
                  setIsFirstFrameLoaded(true);
                  const img = e.currentTarget;
                  if (img.naturalWidth && img.naturalHeight) {
                    setVideoAspect(img.naturalWidth / img.naturalHeight);
                  }
                }}
                onError={() => {
                  setVideoAspect(16 / 9);
                }}
              />
            )}

            {/* OAK Camera / Webcam Video Element */}
            {isCameraSource && isStreaming && (
              <img 
                ref={cameraImgRef}
                src={`${getApiBaseUrl()}/oak/stream`}
                alt="OAK Camera Stream"
                onLoad={(e) => {
                  setIsFirstFrameLoaded(true);
                  setIsCameraDeviceActive(true);
                  onCameraDeviceChange?.(true);
                  const img = e.currentTarget;
                  if (img.naturalWidth && img.naturalHeight) {
                    setVideoAspect(img.naturalWidth / img.naturalHeight);
                  } else {
                    setVideoAspect(16 / 9);
                  }
                }}
                onError={() => {
                  setIsCameraDeviceActive(false);
                  onCameraDeviceChange?.(false);
                }}
                className={`w-full h-full object-fill z-0 select-none pointer-events-auto ${!isCameraDeviceActive ? 'hidden' : 'block'}`}
              />
            )}

            {/* OAK Camera Offline / Disconnected State */}
            {isCameraSource && !isCameraConnected && (
              <div 
                onClick={handleCanvasClick}
                className="absolute inset-0 w-full h-full flex flex-col items-center justify-center text-center p-6 bg-[#0B1814] text-white z-10"
              >
                <div className="w-14 h-14 sm:w-16 sm:h-16 rounded-2xl bg-stone-900/90 border border-rose-500/40 flex items-center justify-center mb-3 sm:mb-4 text-rose-400 shadow-md">
                  <CameraOff className="w-7 h-7 sm:w-8 sm:h-8" />
                </div>
                
                <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-xl bg-stone-900/90 border border-rose-500/40 text-rose-200 text-xs font-semibold mb-2.5 shadow-sm">
                  <span className="w-1.5 h-1.5 rounded-full bg-rose-500 animate-pulse" />
                  NO OAK CAMERA DETECTED
                </div>

                <h3 className="text-base sm:text-lg lg:text-xl font-bold text-white mb-1">
                  OAK-D Hardware Offline
                </h3>
                <p className="text-xs sm:text-sm text-stone-400 max-w-md mb-5 leading-relaxed font-medium">
                  No Luxonis OAK-D / USB camera is currently connected. Connect a DepthAI device or switch back to Video mode to run inference.
                </p>

                <div className="flex flex-wrap items-center justify-center gap-2.5">
                  <button
                    type="button"
                    onClick={() => onRequestSwitchMode?.('uploaded-video')}
                    className="flex items-center gap-1.5 px-4 py-2 rounded-xl bg-emerald-700 hover:bg-emerald-600 text-white font-bold text-xs shadow-xs transition-all cursor-pointer active:scale-95"
                  >
                    <Video className="w-3.5 h-3.5" />
                    <span>Switch to Video Mode</span>
                  </button>
                  {onToggleRunning && (
                    <button
                      type="button"
                      onClick={() => {
                        playWaterDropSound();
                        onToggleRunning();
                      }}
                      className="flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-stone-800 hover:bg-stone-700 border border-stone-700 text-stone-200 font-semibold text-xs transition-all cursor-pointer active:scale-95"
                    >
                      <RotateCcw className="w-3.5 h-3.5" />
                      <span>Retry Connection</span>
                    </button>
                  )}
                </div>
              </div>
            )}

            {/* Ripple canvas layer sized directly to the active video rectangle */}
            <canvas
              ref={canvasRef}
              width={1280}
              height={720}
              className="absolute inset-0 w-full h-full pointer-events-none z-10"
            />

            {/* Inference Bounding Box Overlays (Mapped 1:1 on top of the real video pixels when active) */}
            {feedMode === 'inference' && (hasActiveVideo || isCameraSource) && !isHandPresent && (
              <div className="absolute inset-0 w-full h-full pointer-events-none z-20">
                {ducks
                  .filter((duck) => {
                    if (duck.species === 'Hand' || duck.handDetected) return false;
                    const isMissing = duck.statusEvent === 'missing';
                    const isVisible = showAllBoxes || duck.provisional || duck.isAnomaly || isMissing || duck.id === selectedDuckId;
                    return isVisible && Number.isFinite(duck.x) && Number.isFinite(duck.y) && Number.isFinite(duck.width) && Number.isFinite(duck.height) && duck.width > 0 && duck.height > 0;
                  })
                  .map((duck, idx) => {
                    const isProvisional = duck.provisional;
                    const isMissing = duck.statusEvent === 'missing';
                    const isAnomaly = !isProvisional && (duck.isAnomaly || isMissing);
                    
                    let borderColor = 'border-emerald-400/80 bg-emerald-500/5';
                    let bracketColor = 'border-emerald-300';
                    let tagStyle = 'bg-black/75 text-emerald-300 border border-emerald-500/30';
                    let confColor = 'text-emerald-400';
                    let statusText = isMissing ? 'MISSING' : (isProvisional ? 'WARMING_UP' : (isAnomaly ? 'ANOMALY' : 'NORMAL'));

                    if (isMissing) {
                      borderColor = 'border-2 border-dashed border-amber-500 bg-amber-500/15';
                      bracketColor = 'border-amber-400';
                      tagStyle = 'bg-amber-950/90 text-amber-200 border border-amber-500/80 font-bold shadow-xs';
                      confColor = 'text-amber-300 font-bold';
                    } else if (isProvisional) {
                      borderColor = 'border-amber-400/90 bg-amber-500/10';
                      bracketColor = 'border-amber-300';
                      tagStyle = 'bg-amber-950/90 text-amber-200 border border-amber-500/50';
                      confColor = 'text-amber-300 font-bold';
                    } else if (isAnomaly) {
                      borderColor = 'border-rose-500 bg-rose-500/10';
                      bracketColor = 'border-rose-400';
                      tagStyle = 'bg-rose-950/90 text-rose-200 border border-rose-500/80 font-bold shadow-xs';
                      confColor = 'text-rose-300 font-bold';
                    }

                    return (
                      <div
                        key={`bbox-${idx}-${duck.id}`}
                        onClick={(e) => {
                          e.stopPropagation();
                          playDuckQuackSound();
                          onSelectDuck(duck.id === selectedDuckId ? null : duck.id);
                        }}
                        style={{
                          left: `${duck.x}%`,
                          top: `${duck.y}%`,
                          width: `${duck.width}%`,
                          height: `${duck.height}%`,
                        }}
                        className={`absolute border rounded pointer-events-auto cursor-pointer ${borderColor}`}
                      >
                        {/* Subtle corner brackets */}
                        <span className={`absolute -top-0.5 -left-0.5 w-1.5 h-1.5 border-t-2 border-l-2 ${bracketColor}`} />
                        <span className={`absolute -top-0.5 -right-0.5 w-1.5 h-1.5 border-t-2 border-r-2 ${bracketColor}`} />
                        <span className={`absolute -bottom-0.5 -left-0.5 w-1.5 h-1.5 border-b-2 border-l-2 ${bracketColor}`} />
                        <span className={`absolute -bottom-0.5 -right-0.5 w-1.5 h-1.5 border-b-2 border-r-2 ${bracketColor}`} />

                        {/* Micro-tag */}
                        <div
                          className={`absolute -top-5 left-0 px-1.5 py-0.5 rounded text-[9px] font-mono font-medium whitespace-nowrap flex items-center gap-1 backdrop-blur-xs pointer-events-none ${tagStyle}`}
                        >
                          <span>
                            {isMissing
                              ? `#${duck.id} MISSING`
                              : isProvisional
                              ? 'WARMING_UP'
                              : isAnomaly
                              ? (duck.species === 'Duck' ? `#${duck.id}` : `⚠️ ${duck.species}`)
                              : `#${duck.id}`}
                          </span>
                          {showConfidence && !isMissing && (
                            <span className={`text-[8.5px] opacity-80 ${confColor}`}>
                              {(duck.confidence * 100).toFixed(0)}%
                            </span>
                          )}
                        </div>
                      </div>
                    );
                  })}
              </div>
            )}
          </div>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 4. CAMERA INITIALIZING / WARMUP OVERLAY                                    */}
      {/* ========================================================================= */}
      {isStarting && (
        <div className="absolute inset-0 z-25 bg-black/85 backdrop-blur-md flex flex-col items-center justify-center p-6 text-center animate-in fade-in duration-300">
          <div className="w-14 h-14 sm:w-16 sm:h-16 rounded-2xl bg-emerald-950/80 border border-emerald-700/60 flex items-center justify-center mb-4 text-emerald-400">
            <Loader2 className="w-7 h-7 sm:w-8 sm:h-8 animate-spin" />
          </div>
          <h3 className="text-base sm:text-lg font-bold text-white mb-1">
            Starting Inference...
          </h3>
          <p className="text-xs text-emerald-300/80 max-w-md">
            Please wait while we connect to the video stream and initialize the AI model.
          </p>
        </div>
      )}
      
      {!isStarting && isCameraSource && cameraStartingState !== 'ready' && (
        <div className="absolute inset-0 z-25 bg-black/85 backdrop-blur-md flex flex-col items-center justify-center p-6 text-center animate-in fade-in duration-300">
          <div className="w-14 h-14 sm:w-16 sm:h-16 rounded-2xl bg-emerald-950/80 border border-emerald-700/60 flex items-center justify-center mb-4 text-emerald-400">
            <Loader2 className="w-7 h-7 sm:w-8 sm:h-8 animate-spin" />
          </div>
          <h3 className="text-base sm:text-lg font-bold text-white mb-1">
            {cameraStartingState === 'waking_camera' && 'Waking up OAK Camera Stream...'}
            {cameraStartingState === 'waiting_frame' && 'Receiving First Live Frame (1080p)...'}
          </h3>
          <p className="text-xs text-emerald-300/80 max-w-md font-mono">
            {cameraStartingState === 'waking_camera' && 'Executing POST /oak/start -> Initializing sensor pipeline'}
            {cameraStartingState === 'waiting_frame' && 'Executing waitForFirstFrame() -> Connecting YOLOv8 inference buffer'}
          </p>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 5. INFERENCE INITIALIZING OVERLAY (For Video Uploads)                      */}
      {/* ========================================================================= */}
      {isVideoSource && hasActiveVideo && isRunning && !isFirstFrameLoaded && (
        <div className="absolute inset-0 z-25 bg-black/85 backdrop-blur-md flex flex-col items-center justify-center p-6 text-center animate-in fade-in duration-300">
          <div className="w-14 h-14 sm:w-16 sm:h-16 rounded-2xl bg-emerald-950/80 border border-emerald-700/60 flex items-center justify-center mb-4 text-emerald-400">
            <Loader2 className="w-7 h-7 sm:w-8 sm:h-8 animate-spin" />
          </div>
          <h3 className="text-base sm:text-lg font-bold text-white mb-1">
            Preparing inference stream...
          </h3>
          <p className="text-xs text-emerald-300/80 max-w-md font-mono">
            Processing the first video frame...
          </p>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 6. TOP FLOATING CONTROLS OVER CANVAS (MATCHING HEADER THEME & RADIUS)     */}
      {/* ========================================================================= */}
      {((hasActiveVideo && (!isRunning || isFirstFrameLoaded)) || (isCameraSource && cameraStartingState === 'ready')) && (
        <div className="absolute top-2 sm:top-3 right-2 sm:right-3 flex items-center gap-1 sm:gap-1.5 pointer-events-auto z-30 ml-auto shrink-0">
          
          {/* Real-Time Status & Mode Controls shown when active */}
          {(isRunning || hasActiveVideo) && (
            <>
              {/* Status Badge */}
              <div
                className={`flex items-center gap-1.5 h-7 sm:h-8 px-2.5 sm:px-3 rounded-xl border text-[10px] sm:text-xs font-black tracking-wide shrink-0 transition-all ${
                  anomalyStatus.message === 'WARMING'
                    ? 'bg-amber-500/20 text-amber-400 border-amber-500/50 shadow-xs animate-pulse'
                    : anomalyStatus.isAnomaly
                    ? 'bg-rose-600 text-white border-rose-400 shadow-lg shadow-rose-600/40 animate-pulse'
                    : 'bg-emerald-950/90 text-emerald-300 border-emerald-500/50 shadow-xs'
                }`}
              >
                {anomalyStatus.message === 'WARMING' ? (
                  <>
                    <span className="w-2 h-2 rounded-full bg-amber-400 animate-pulse shrink-0 shadow-[0_0_8px_rgba(251,191,36,0.8)]" />
                    <span className="font-black text-amber-400">WARMING</span>
                  </>
                ) : anomalyStatus.message === 'HAND DETECTED' ? (
                  <>
                    <AlertTriangle className="w-3.5 h-3.5 text-amber-300 shrink-0" />
                    <span className="font-black text-amber-200">HAND PRESENT</span>
                  </>
                ) : anomalyStatus.isAnomaly ? (
                  <>
                    <AlertTriangle className="w-3.5 h-3.5 fill-current text-white shrink-0" />
                    <span className="font-black text-white">ANOMALY</span>
                    {anomalyStatus.difference !== 0 && (
                      <span className="font-mono text-[9.5px] bg-black/30 px-1 py-0.5 rounded text-rose-100">
                        {anomalyStatus.difference > 0 ? `+${anomalyStatus.difference}` : anomalyStatus.difference}
                      </span>
                    )}
                    {(anomalyStatus.foreignCount ?? 0) > 0 && (
                      <span className="font-mono text-[9.5px] bg-black/30 px-1 py-0.5 rounded text-rose-100">
                        {anomalyStatus.foreignCount} Foreign
                      </span>
                    )}
                  </>
                ) : (
                  <>
                    <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse shrink-0 shadow-[0_0_8px_rgba(52,211,153,0.8)]" />
                    <span className="text-emerald-300 font-bold">NORMAL ({anomalyStatus.detectedCount})</span>
                  </>
                )}
              </div>

              {/* Feed toggle pill: RAW vs INFERENCE */}
              <div className="flex items-center h-7 sm:h-8 p-0.5 rounded-xl bg-[var(--bg-card)]/95 backdrop-blur-md border border-[var(--border-color)] shadow-xs shrink-0">
                <button
                  onClick={() => {
                    playWaterDropSound();
                    onFeedModeChange('raw');
                  }}
                  className={`h-6 sm:h-7 px-2 sm:px-3 rounded-lg text-[10px] sm:text-xs font-bold tracking-wide flex items-center justify-center cursor-pointer ${
                    feedMode === 'raw'
                      ? 'bg-[var(--btn-primary-bg)] text-[var(--btn-primary-text)] shadow-xs scale-100'
                      : 'text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)]'
                  }`}
                >
                  RAW
                </button>
                <button
                  onClick={() => {
                    playWaterDropSound();
                    onFeedModeChange('inference');
                  }}
                  className={`h-6 sm:h-7 px-2 sm:px-3 rounded-lg text-[10px] sm:text-xs font-bold tracking-wide flex items-center justify-center cursor-pointer ${
                    feedMode === 'inference'
                      ? 'bg-[var(--btn-primary-bg)] text-[var(--btn-primary-text)] shadow-xs scale-100'
                      : 'text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)]'
                  }`}
                >
                  INFERENCE
                </button>
              </div>

              {/* Bounding Box Mode Toggle: Anomalies Only (Default) vs All Boxes */}
              {feedMode === 'inference' && (
                <button
                  onClick={() => {
                    playWaterDropSound();
                    setShowAllBoxes((prev) => !prev);
                  }}
                  aria-label={showAllBoxes ? "Showing all bounding boxes. Click to show anomaly boxes only." : "Showing anomaly bounding boxes only. Click to show all boxes."}
                  title={showAllBoxes ? "Bounding Boxes: SHOWING ALL (Click for Anomalies Only)" : "Bounding Boxes: ANOMALIES ONLY (Click for All Boxes)"}
                  className={`h-7 sm:h-8 px-2 sm:px-2.5 flex items-center gap-1.5 rounded-xl backdrop-blur-md border text-[10px] sm:text-xs font-bold transition-all shrink-0 shadow-xs cursor-pointer active:scale-95 ${
                    !showAllBoxes
                      ? 'bg-[var(--status-anomaly-bg)] border-[var(--status-anomaly-border)] text-[var(--status-anomaly-text)] hover:opacity-90'
                      : 'bg-[var(--btn-secondary-bg)] border-[var(--btn-secondary-border)] text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)]'
                  }`}
                >
                  {!showAllBoxes ? (
                    <>
                      <ShieldAlert className="w-3.5 h-3.5 text-[var(--status-anomaly-text)] shrink-0" />
                      <span className="hidden sm:inline">Anomalies Only</span>
                    </>
                  ) : (
                    <>
                      <Layers className="w-3.5 h-3.5 text-[var(--text-primary)] shrink-0" />
                      <span className="hidden sm:inline">All Boxes</span>
                    </>
                  )}
                </button>
              )}
            </>
          )}

            {/* Recording Button (Camera Only) */}
            {isCameraSource && (
              <button
                disabled={!isStreaming && !isRecording}
                onClick={() => {
                  playWaterDropSound();
                  if (isRecording) {
                    stopRecording();
                  } else {
                    const imgEl = cameraImgRef.current;
                    if (imgEl) {
                      startRecording(imgEl, imgEl.naturalWidth || 1920, imgEl.naturalHeight || 1080);
                    }
                  }
                }}
                title={isStreaming ? (isRecording ? 'Stop recording' : 'Record live camera stream') : 'Start the camera stream before recording'}
                className={`h-7 sm:h-8 px-3 flex items-center gap-2 rounded-xl backdrop-blur-md border text-xs font-bold transition-all shrink-0 shadow-xs active:scale-95 ${
                  isRecording
                    ? 'bg-red-500/20 border-red-500/50 text-red-400 animate-pulse'
                    : isStreaming
                      ? 'bg-[var(--btn-secondary-bg)] border-[var(--btn-secondary-border)] text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)] cursor-pointer'
                      : 'bg-[var(--btn-secondary-bg)] border-[var(--btn-secondary-border)] text-[var(--text-muted)] opacity-50 cursor-not-allowed'
                }`}
              >
                <div className={`w-2.5 h-2.5 rounded-full ${isRecording ? 'bg-red-500' : 'bg-red-500/50'}`} />
                {isRecording ? 'RECORDING' : 'RECORD'}
              </button>
            )}

          {/* Fullscreen Button */}
          <button
            onClick={toggleFullscreen}
            aria-label={isFullscreen ? "Exit Fullscreen" : "Enter Fullscreen"}
            title={isFullscreen ? "Exit Fullscreen" : "Enter Fullscreen"}
            className="w-7 sm:w-8 h-7 sm:h-8 flex items-center justify-center rounded-xl bg-[var(--btn-secondary-bg)] backdrop-blur-md border border-[var(--btn-secondary-border)] text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)] active:scale-95 shrink-0 shadow-xs cursor-pointer"
          >
            {isFullscreen ? (
              <Minimize2 className="w-3.5 h-3.5 text-[var(--status-anomaly-text)]" />
            ) : (
              <Expand className="w-3.5 h-3.5" />
            )}
          </button>

          {/* Change Video / Media source button */}
          {isVideoSource && hasActiveVideo && (
            <button
              onClick={() => fileInputRef.current?.click()}
              aria-label="Upload different video"
              title="Upload different video file"
              className="w-7 sm:w-8 h-7 sm:h-8 flex items-center justify-center rounded-xl bg-[var(--btn-secondary-bg)] backdrop-blur-md border border-[var(--btn-secondary-border)] text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)] active:scale-95 shrink-0 shadow-xs cursor-pointer"
            >
              <ImageIcon className="w-3.5 h-3.5" />
            </button>
          )}

          {/* Quick HUD Visibility Toggle */}
          <button
            onClick={() => setShowHUD(!showHUD)}
            aria-label={showHUD ? 'Hide HUD overlay' : 'Show HUD overlay'}
            title={showHUD ? 'Hide HUD overlay' : 'Show HUD overlay'}
            className="w-7 sm:w-8 h-7 sm:h-8 flex items-center justify-center rounded-xl bg-[var(--btn-secondary-bg)] backdrop-blur-md border border-[var(--btn-secondary-border)] text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)] active:scale-95 shrink-0 shadow-xs cursor-pointer"
          >
            {showHUD ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5 text-[var(--text-muted)]" />}
          </button>
        </div>
      )}

      {/* ========================================================================= */}
      {/* 7. BOTTOM FLOATING DETECTION HUD (WHEN VIDEO OR CAMERA STREAM IS ACTIVE)  */}
      {/* ========================================================================= */}
      {showHUD && (isRunning || hasActiveVideo) && ((hasActiveVideo && isFirstFrameLoaded) || (isCameraSource && cameraStartingState === 'ready')) && (
        <div className="absolute bottom-3 left-1/2 transform -translate-x-1/2 z-30 pointer-events-auto animate-in fade-in slide-in-from-bottom-2 duration-300 max-w-[calc(100%-24px)]">
          <div className="flex items-center gap-3 sm:gap-5 px-3.5 sm:px-5 py-2 rounded-xl bg-[var(--bg-card)]/95 backdrop-blur-md border border-[var(--border-color)] shadow-lg text-[var(--text-primary)]">
            
            {/* Expected Ducks */}
            <div className="flex flex-col items-center">
              <span className="text-[9.5px] text-[var(--text-secondary)] uppercase tracking-wider font-semibold">
                Expected
              </span>
              <div className="flex items-center gap-1 mt-0.5">
                <span className="text-sm">🦆</span>
                <span className="text-base sm:text-lg font-black text-[var(--text-primary)]">
                  {anomalyStatus.expectedCount}
                </span>
              </div>
            </div>

            <div className="h-6 w-[1px] bg-[var(--border-color)]" />

            {/* Detected Ducks */}
            <div className="flex flex-col items-center">
              <span className="text-[9.5px] text-[var(--text-secondary)] uppercase tracking-wider font-semibold">
                Ducks
              </span>
              <div className="flex items-center gap-1 mt-0.5">
                <span className="text-sm">🦆</span>
                <span className="text-base sm:text-lg font-black text-[var(--text-primary)]">
                  {anomalyStatus.detectedCount}
                </span>
              </div>
            </div>

            {/* Non-Duck Foreign items if any */}
            {(anomalyStatus.foreignCount ?? 0) > 0 && (
              <>
                <div className="h-6 w-[1px] bg-[var(--border-color)]" />
                <div className="flex flex-col items-center">
                  <span className="text-[9.5px] text-rose-400 uppercase tracking-wider font-semibold">
                    Non-Duck
                  </span>
                  <div className="flex items-center gap-1 mt-0.5">
                    <span className="text-sm">⚠️</span>
                    <span className="text-base sm:text-lg font-black text-rose-400">
                      {anomalyStatus.foreignCount}
                    </span>
                  </div>
                </div>
              </>
            )}

            <div className="h-6 w-[1px] bg-[var(--border-color)]" />

            {/* Difference */}
            <div className="flex flex-col items-center">
              <span className="text-[9.5px] text-[var(--text-secondary)] uppercase tracking-wider font-semibold">
                Diff
              </span>
              <div className="mt-0.5">
                <span
                  className={`text-base sm:text-lg font-black ${
                    anomalyStatus.difference > 0
                      ? 'text-[var(--status-anomaly-text)]'
                      : anomalyStatus.difference < 0
                      ? 'text-[var(--status-warn-text)]'
                      : 'text-[var(--status-normal-text)]'
                  }`}
                >
                  {anomalyStatus.difference > 0 ? `+${anomalyStatus.difference}` : anomalyStatus.difference}
                </span>
              </div>
            </div>

            <div className="h-6 w-[1px] bg-[var(--border-color)]" />

            {/* Status Pill */}
            <div className="flex flex-col items-center">
              <span className="text-[9.5px] text-[var(--text-secondary)] uppercase tracking-wider font-semibold">
                Status
              </span>
              <div className="flex items-center gap-1 mt-0.5">
                {anomalyStatus.message === 'WARMING' ? (
                  <div className="flex items-center gap-1 text-amber-500 font-bold text-xs sm:text-sm animate-pulse">
                    <span className="w-2 h-2 rounded-full bg-amber-500 shrink-0" />
                    <span>WARMING</span>
                  </div>
                ) : anomalyStatus.message === 'HAND DETECTED' ? (
                  <div className="flex items-center gap-1 text-amber-500 font-bold text-xs sm:text-sm animate-pulse">
                    <AlertTriangle className="w-3.5 h-3.5 fill-current opacity-80" />
                    <span>HAND</span>
                  </div>
                ) : anomalyStatus.isAnomaly ? (
                  <div className="flex items-center gap-1 text-[var(--status-anomaly-text)] font-bold text-xs sm:text-sm animate-pulse">
                    <AlertTriangle className="w-3.5 h-3.5 fill-current opacity-80" />
                    <span>ANOMALY</span>
                  </div>
                ) : (
                  <div className="flex items-center gap-1 text-[var(--status-normal-text)] font-bold text-xs sm:text-sm">
                    <CheckCircle2 className="w-3.5 h-3.5 fill-current opacity-80" />
                    <span>NORMAL</span>
                  </div>
                )}
              </div>
            </div>

            <div className="hidden sm:block h-6 w-[1px] bg-[var(--border-color)]" />

            {/* FPS */}
            <div className="hidden sm:flex flex-col items-center">
              <span className="text-[9.5px] text-[var(--text-secondary)] uppercase tracking-wider font-semibold">
                FPS
              </span>
              <span className="text-base sm:text-lg font-mono font-bold text-[var(--text-primary)] mt-0.5">
                {fps.toFixed(1)}
              </span>
            </div>
          </div>
        </div>
      )}

      {/* Drag Over Hint Overlay */}
      {isDragOver && (
        <div className="absolute inset-0 z-50 bg-emerald-950/90 backdrop-blur-md flex flex-col items-center justify-center p-6 text-center pointer-events-none">
          <UploadCloud className="w-16 h-16 text-emerald-400 mb-3 animate-bounce" />
          <h2 className="text-xl font-bold text-white">Drop video here to upload to canvas</h2>
          <p className="text-xs text-emerald-300 mt-1">Video will immediately load for YOLOv8 object detection</p>
        </div>
      )}
    </div>
  );
};
