import React, { useRef, useEffect, useState, useMemo } from 'react';
import type { DuckEntity, StreamSourceType, AnomalyStatus } from '../types';
import { getApiBaseUrl } from '../lib/api';
import { useInferenceStore } from '../store/inferenceStore';
import { useRecording } from './hooks/useRecording';
import { playWaterDropSound } from '../utils/audio';
import { cameraService } from './service/cameraService';

import { Video, Loader2 } from 'lucide-react';

// Extracted Canvas Components
import { BoundingBoxOverlay } from './canvas/BoundingBoxOverlay';
import { CameraOfflineCard } from './canvas/CameraOfflineCard';
import { CameraStandbyCard } from './canvas/CameraStandbyCard';
import { TopToolbar } from './canvas/TopToolbar';
import { StatusBar } from './canvas/StatusBar';
import { LoadingOverlay } from './canvas/LoadingOverlay';

// Extracted Canvas Hooks
import { useFullscreen } from '../hooks/useFullscreen';
import { useContainerFit } from '../hooks/useContainerFit';
import { useVideoUpload } from '../hooks/useVideoUpload';
import { useRippleEffect } from '../hooks/useRippleEffect';

interface DetectionCanvasProps {
  ducks: DuckEntity[];
  setDucks?: React.Dispatch<React.SetStateAction<DuckEntity[]>>;
  anomalyStatus: AnomalyStatus;
  feedMode: 'raw' | 'inference';
  onFeedModeChange: (mode: 'raw' | 'inference') => void;
  isRunning: boolean;
  isStarting?: boolean;
  onToggleRunning?: () => void;
  onStopInference?: () => void;
  onResumeInference?: () => void;
  isStreaming?: boolean;
  onStartStream?: () => void;
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
  onRegisterTriggerUpload?: (trigger: () => void) => void;
  lastCameraFrame?: string;
  onRetryConnection?: () => void;
  framesProcessed?: number;
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
  onStartStream,
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
  onRegisterTriggerUpload,
  lastCameraFrame,
  onRetryConnection,
  framesProcessed = 0,
}) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const cameraImgRef = useRef<HTMLImageElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  const [showHUD, setShowHUD] = useState(true);
  const [showAllBoxes, setShowAllBoxes] = useState<boolean>(false);
  const [videoAspect, setVideoAspect] = useState<number | null>(null);
  const [isFirstFrameLoaded, setIsFirstFrameLoaded] = useState<boolean>(false);
  const [streamCacheBuster, setStreamCacheBuster] = useState<number>(Date.now());

  const { isRecording, recordedFile, startRecording, stopRecording, clearRecording } = useRecording();
  const backendStats = useInferenceStore((state) => state.stats);

  const isVideoSource = sourceType === 'uploaded-video' || sourceType === 'sample-pond';
  const isCameraSource = sourceType === 'oak-camera' || sourceType === 'webcam';
  const hasActiveVideo = isVideoSource && !!customVideoUrl;
  const isWaitingForVideo = isVideoSource && !hasActiveVideo;
  const isCameraOffline = isCameraSource && !isCameraConnected;

  const effectiveFramesProcessed = framesProcessed || backendStats?.frames_processed || 0;
  const hasInferenceResult = effectiveFramesProcessed > 0 && ducks.length > 0;
  
  const isHandPresent = 
    backendStats?.status === 'HAND' || 
    backendStats?.hand_detected === true || 
    anomalyStatus?.message?.includes('HAND') ||
    ducks.some((d) => d.species === 'Hand' || d.handDetected === true || d.statusEvent === 'hand_present');
  
  // anomalyStatus is the reconciled current-frame verdict used by every
  // status surface. Do not reintroduce a stale raw backend status here.
  const isSceneAnomaly = anomalyStatus?.isAnomaly === true;

  // Hooks
  const { isFullscreen, toggleFullscreen } = useFullscreen(containerRef);
  const { fittedRect } = useContainerFit(containerRef, canvasRef, videoAspect, videoDimensions, isCameraSource);
  const { uploadProgress, isSelectingVideo, handleFileInputChange, handleSelectVideoAndStart } = useVideoUpload(fileInputRef, expectedDucks, onCustomVideoUploaded, recordedFile, clearRecording, initialUploadFile);
  const { handleCanvasClick } = useRippleEffect(canvasRef, ducks, selectedDuckId, onSelectDuck, showAllBoxes, isSceneAnomaly);

  useEffect(() => {
    if (onRegisterTriggerUpload) {
      onRegisterTriggerUpload(handleSelectVideoAndStart);
    }
  }, [handleSelectVideoAndStart, onRegisterTriggerUpload]);

  // Cache buster for stream URL
  useEffect(() => {
    setStreamCacheBuster(Date.now());
    if (isRunning) setShowAllBoxes(false);
  }, [isRunning, videoSessionId]);

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
      return `${getApiBaseUrl()}/video/last_frame/${videoSessionId}?t=${streamCacheBuster}`;
    }
    return customVideoUrl;
  }, [customVideoUrl, hasActiveVideo, isRunning, videoSessionId, streamCacheBuster]);

  // Reset states on source change
  useEffect(() => {
    setVideoAspect(null);
  }, [effectiveVideoUrl, hasActiveVideo, sourceType]);

  useEffect(() => {
    if (isRunning) {
      setIsFirstFrameLoaded(false);
      // Safety timeout: ensure loading overlay never gets stuck if img.onLoad does not fire
      const timer = setTimeout(() => {
        setIsFirstFrameLoaded(true);
      }, 800);
      return () => clearTimeout(timer);
    }
  }, [isRunning]);

  // Once backend starts processing frames or ducks arrive, mark first frame loaded immediately
  useEffect(() => {
    if ((backendStats?.frames_processed && backendStats.frames_processed > 0) || ducks.length > 0) {
      setIsFirstFrameLoaded(true);
    }
    if (backendStats?.video_width && backendStats?.video_height) {
      setVideoAspect(backendStats.video_width / backendStats.video_height);
    }
  }, [backendStats?.frames_processed, backendStats?.video_width, backendStats?.video_height, ducks.length]);

  // Video autoplay behavior for local preview
  useEffect(() => {
    if (!videoRef.current) return;
    if (hasActiveVideo && !videoSessionId) {
      videoRef.current.muted = true;
      videoRef.current.play().catch(() => {});
    } else {
      videoRef.current.pause();
    }
  }, [hasActiveVideo, customVideoUrl, videoSessionId]);

  return (
    <div
      ref={containerRef}
      id="detection-hero-viewport"
      className={`relative w-full flex-1 h-full min-h-[350px] lg:min-h-0 overflow-hidden border select-none group ${
        isFullscreen ? 'rounded-none border-none' : 'rounded-3xl'
      } border-[var(--border-color)] shadow-sm`}
      style={{
        backgroundColor: (isWaitingForVideo || (!isCameraConnected && isCameraSource)) ? 'var(--bg-card)' : '#000000',
        ...(isFullscreen ? { width: '100%', height: '100%', minHeight: '100vh', maxHeight: '100vh' } : {})
      }}
    >
      <input type="file" ref={fileInputRef} onChange={handleFileInputChange} accept="video/*" className="hidden" />

      {/* Video Source Standby View */}
      {isWaitingForVideo && uploadProgress === null && (
        <div
          onClick={handleSelectVideoAndStart}
          className="absolute inset-0 w-full h-full flex flex-col items-center justify-center text-center p-6 select-none z-10 cursor-pointer"
          style={{ backgroundColor: 'var(--bg-card)' }}
        >
          <div className="w-14 h-14 sm:w-16 sm:h-16 rounded-2xl bg-emerald-500/10 border border-emerald-500/30 flex items-center justify-center mb-3 sm:mb-4 text-emerald-400 shadow-md">
            <Video className="w-7 h-7 sm:w-8 sm:h-8" />
          </div>
          <div className="inline-flex items-center gap-1.5 px-3 py-1 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 text-xs font-semibold mb-2.5 shadow-sm">
            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
            VIDEO INFERENCE STANDBY
          </div>
          <h3 className="text-base sm:text-lg lg:text-xl font-bold text-[var(--text-primary)] mb-1">
            Video Source Standby
          </h3>
          <p className="text-xs sm:text-sm text-[var(--text-secondary)] max-w-md mb-2 leading-relaxed font-medium">
            Click <span className="font-bold text-emerald-400">START INFERENCE</span> above to select a video file and begin analysis.
          </p>
          {isSelectingVideo && (
            <div className="flex items-center gap-2 mt-3 text-xs sm:text-sm text-emerald-400 font-medium">
              <Loader2 className="w-4 h-4 animate-spin" />
              <span>Opening video...</span>
            </div>
          )}
        </div>
      )}

      {/* Video Uploading & Initializing Indicator */}
      {isWaitingForVideo && uploadProgress !== null && (
        <div className="absolute inset-0 z-20 flex flex-col items-center justify-center bg-black/80 backdrop-blur-xs p-6 select-none">
          <div className="w-full max-w-xs flex flex-col items-center">
            <div className="flex items-center justify-between w-full text-xs text-[var(--text-secondary)] mb-2 font-medium">
              <span className="flex items-center gap-2 text-emerald-400">
                <Loader2 className="w-4 h-4 animate-spin" />
                Uploading & Initializing...
              </span>
              <span className="font-bold font-mono text-emerald-400">
                {Math.round(uploadProgress)}%
              </span>
            </div>
            <div className="w-full h-2 bg-white/10 rounded-full overflow-hidden">
              <div
                className="h-full bg-emerald-500 transition-all duration-150 rounded-full"
                style={{ width: `${uploadProgress}%` }}
              />
            </div>
          </div>
        </div>
      )}

      {isCameraSource && !isCameraConnected && (
        <CameraOfflineCard
          onSwitchToVideo={() => onRequestSwitchMode?.('uploaded-video')}
          onRetryConnection={onRetryConnection || (async () => {
            try {
              await cameraService.start();
              await cameraService.startStream();
            } catch (e) {
              console.error('Retry connection failed:', e);
              window.location.reload();
            }
          })}
          onCanvasClick={handleCanvasClick}
        />
      )}

      {isCameraSource && isCameraConnected && !isStreaming && (
        <CameraStandbyCard
          onStartStream={onStartStream}
          onSwitchToVideo={() => onRequestSwitchMode?.('uploaded-video')}
          onCanvasClick={handleCanvasClick}
        />
      )}

      {/* 2 & 3. VIDEO & CAMERA VIEWPORT WITH TRUE ASPECT RATIO */}
      {(hasActiveVideo || (isCameraSource && isCameraConnected && isStreaming)) && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-auto bg-black">
          <div className="relative shrink-0" style={fittedRect} onClick={handleCanvasClick}>
            {/* STREAM VIEWPORT: If backend session is active (video or camera), render via <img> to support MJPEG streaming */}
            {(videoSessionId || isCameraSource) ? (
              <img
                ref={cameraImgRef}
                crossOrigin="anonymous"
                src={
                  isCameraSource
                    ? `${getApiBaseUrl()}/oak/inference/stream/live?t=${streamCacheBuster}`
                    : effectiveVideoUrl
                }
                className="absolute inset-0 z-0 h-full w-full pointer-events-none rounded bg-black object-contain"
                alt="Stream"
                onLoad={(e) => {
                  const tgt = e.target as HTMLImageElement;
                  if (tgt.naturalWidth && tgt.naturalHeight) {
                    setVideoAspect(tgt.naturalWidth / tgt.naturalHeight);
                  }
                  setIsFirstFrameLoaded(true);
                }}
                onError={() => {
                  console.warn('[DetectionCanvas] Camera stream frame interrupted, retrying...');
                }}
              />
            ) : hasActiveVideo ? (
              /* Local MP4 video preview before backend session starts */
              <video
                ref={videoRef}
                src={customVideoUrl}
                className="absolute inset-0 z-0 h-full w-full pointer-events-none rounded bg-black object-contain"
                loop
                muted
                playsInline
                onLoadedMetadata={(e) => {
                  const tgt = e.target as HTMLVideoElement;
                  if (tgt.videoWidth && tgt.videoHeight) {
                    setVideoAspect(tgt.videoWidth / tgt.videoHeight);
                  }
                  setIsFirstFrameLoaded(true);
                }}
              />
            ) : null}

            <canvas ref={canvasRef} className="absolute inset-0 z-10 h-full w-full pointer-events-none rounded" />

            {/* AI Bounding Boxes: Shown in INFERENCE mode or always for video upload */}
            {(isRunning || hasInferenceResult) && (feedMode === 'inference' || !isCameraSource) && ducks.length > 0 && (
              <BoundingBoxOverlay
                ducks={ducks}
                selectedDuckId={selectedDuckId}
                onSelectDuck={onSelectDuck}
                showAllBoxes={showAllBoxes}
                isHandPresent={isHandPresent}
                isSceneAnomaly={isSceneAnomaly}
                isCountMismatch={anomalyStatus.difference !== 0}
              />
            )}

            {/* Hand detected warning border: Shown in INFERENCE mode or always for video upload */}
            {(feedMode === 'inference' || !isCameraSource) && isHandPresent && (
              <div className="absolute inset-0 z-30 pointer-events-none border-4 border-amber-500/80 rounded" />
            )}
          </div>
        </div>
      )}

      <LoadingOverlay
        isStarting={isStarting}
        isCameraSource={isCameraSource}
        isVideoSource={isVideoSource}
        cameraStartingState={cameraStartingState}
        hasActiveVideo={hasActiveVideo}
        isRunning={isRunning}
        isFirstFrameLoaded={isFirstFrameLoaded}
        isCameraConnected={isCameraConnected}
      />

      <TopToolbar
        isRunning={isRunning}
        hasActiveVideo={hasActiveVideo}
        feedMode={feedMode}
        onFeedModeChange={onFeedModeChange}
        showAllBoxes={showAllBoxes}
        onToggleShowAllBoxes={() => { playWaterDropSound(); setShowAllBoxes(!showAllBoxes); }}
        isRecording={isRecording}
        onToggleRecording={() => {
          if (isRecording) {
            playWaterDropSound();
            stopRecording();
          } else {
            playWaterDropSound();
            if (cameraImgRef.current) {
              startRecording(cameraImgRef.current, videoDimensions?.width || 1920, videoDimensions?.height || 1080);
            }
          }
        }}
        isFullscreen={isFullscreen}
        onToggleFullscreen={toggleFullscreen}
        showHUD={showHUD}
        onToggleHUD={() => { playWaterDropSound(); setShowHUD(!showHUD); }}
        backendStatus={backendStats?.status}
        isCameraSource={isCameraSource}
        isVideoSource={isVideoSource}
        anomalyStatus={anomalyStatus}
        isStreaming={isStreaming}
        isFirstFrameLoaded={isFirstFrameLoaded}
        framesProcessed={effectiveFramesProcessed}
      />

      {showHUD && !isCameraOffline && (isRunning || isStarting || hasInferenceResult) && (
        <StatusBar
          anomalyStatus={anomalyStatus}
          fps={fps}
          backendStatus={backendStats?.status}
          ducks={ducks}
          expectedDucks={expectedDucks}
        />
      )}
    </div>
  );
};
