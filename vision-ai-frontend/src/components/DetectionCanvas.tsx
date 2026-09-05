import React, { useRef, useEffect, useState, useMemo } from 'react';
import type { DuckEntity, StreamSourceType, AnomalyStatus } from '../types';
import { getApiBaseUrl } from '../lib/api';
import { useInferenceStore } from '../store/inferenceStore';
import { useRecording } from './hooks/useRecording';
import { playWaterDropSound, playDuckQuackSound } from '../utils/audio';

// Extracted Canvas Components
import { BoundingBoxOverlay } from './canvas/BoundingBoxOverlay';
import { VideoUploadCard } from './canvas/VideoUploadCard';
import { CameraOfflineCard } from './canvas/CameraOfflineCard';
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
  onRegisterTriggerUpload,
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
  
  const isHandPresent = 
    backendStats?.status === 'HAND' || 
    backendStats?.hand_detected === true || 
    anomalyStatus?.message?.includes('HAND') ||
    ducks.some((d) => d.species === 'Hand' || d.handDetected === true || d.statusEvent === 'hand_present');
  
  const isSceneAnomaly = anomalyStatus?.isAnomaly === true || backendStats?.status === 'ANOMALY';

  // Hooks
  const { isFullscreen, toggleFullscreen } = useFullscreen(containerRef);
  const { containerSize, fittedRect } = useContainerFit(containerRef, canvasRef, videoAspect, videoDimensions, isCameraSource);
  const { isDragOver, uploadProgress, isSelectingVideo, handleFileInputChange, handleSelectVideoAndStart, handleDragOver, handleDragLeave, handleDrop } = useVideoUpload(fileInputRef, expectedDucks, onCustomVideoUploaded, recordedFile, clearRecording, initialUploadFile);
  const { handleCanvasClick } = useRippleEffect(canvasRef, ducks, selectedDuckId, onSelectDuck);

  useEffect(() => {
    if (onRegisterTriggerUpload) {
      onRegisterTriggerUpload(handleSelectVideoAndStart);
    }
  }, [handleSelectVideoAndStart, onRegisterTriggerUpload]);

  // Cache buster for stream URL
  useEffect(() => {
    setStreamCacheBuster(Date.now());
    if (isRunning) setShowAllBoxes(false);
  }, [isRunning]);

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
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onDrop={(e) => handleDrop(e, isVideoSource)}
      className={`relative w-full flex-1 h-full min-h-[350px] lg:min-h-0 overflow-hidden border select-none group ${
        isFullscreen ? 'rounded-none border-none' : 'rounded-3xl'
      } ${
        isDragOver
          ? 'border-[var(--accent-pond)] ring-4 ring-[var(--accent-pond-subtle)]'
          : 'border-[var(--border-color)]'
      } shadow-sm`}
      style={{
        backgroundColor: (isWaitingForVideo || (!isCameraConnected && isCameraSource)) ? 'var(--bg-card)' : '#000000',
        ...(isFullscreen ? { width: '100%', height: '100%', minHeight: '100vh', maxHeight: '100vh' } : {})
      }}
    >
      <input type="file" ref={fileInputRef} onChange={handleFileInputChange} accept="video/*" className="hidden" />

      {isWaitingForVideo && (
        <VideoUploadCard
          uploadProgress={uploadProgress}
          isSelectingVideo={isSelectingVideo}
          isBackendConnected={isBackendConnected}
          onSelectVideoAndStart={handleSelectVideoAndStart}
        />
      )}

      {isCameraSource && !isCameraConnected && (
        <CameraOfflineCard
          onSwitchToVideo={() => onRequestSwitchMode?.('uploaded-video')}
          onRetryConnection={() => window.location.reload()}
          onCanvasClick={(e) => handleCanvasClick(e, containerRef)}
        />
      )}

      {/* 2 & 3. VIDEO & CAMERA VIEWPORT WITH TRUE ASPECT RATIO */}
      {(hasActiveVideo || (isCameraSource && isCameraConnected)) && (
        <div className="absolute inset-0 flex items-center justify-center pointer-events-auto bg-black" onClick={(e) => handleCanvasClick(e, containerRef)}>
          {/* STREAM VIEWPORT: If backend session is active (video or camera), render via <img> to support MJPEG streaming */}
          {(videoSessionId || isCameraSource) ? (
            <img
              ref={cameraImgRef}
              src={
                isCameraSource 
                  ? (isStreaming ? `${getApiBaseUrl()}/oak/inference/stream/live?t=${streamCacheBuster}` : undefined) 
                  : effectiveVideoUrl
              }
              className="absolute z-0 pointer-events-none object-contain rounded bg-black"
              style={fittedRect}
              alt="Stream"
              onLoad={(e) => {
                const tgt = e.target as HTMLImageElement;
                if (tgt.naturalWidth && tgt.naturalHeight) {
                  setVideoAspect(tgt.naturalWidth / tgt.naturalHeight);
                }
                setIsFirstFrameLoaded(true);
              }}
              onError={(e) => {
                if (isCameraSource && onCameraDeviceChange) onCameraDeviceChange(false);
              }}
            />
          ) : hasActiveVideo ? (
            /* Local MP4 video preview before backend session starts */
            <video
              ref={videoRef}
              src={customVideoUrl}
              className="absolute z-0 pointer-events-none rounded bg-black"
              style={fittedRect}
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

          <canvas ref={canvasRef} className="absolute inset-0 pointer-events-none z-10 rounded" style={fittedRect} />

          {/* AI Bounding Boxes: Only shown in INFERENCE mode */}
          {feedMode === 'inference' && (
            <BoundingBoxOverlay
              ducks={ducks}
              selectedDuckId={selectedDuckId}
              onSelectDuck={onSelectDuck}
              showAllBoxes={showAllBoxes}
              isHandPresent={isHandPresent}
              isFrameAnomaly={isSceneAnomaly}
            />
          )}

          {/* Hand detected warning border: Only shown in INFERENCE mode */}
          {feedMode === 'inference' && isHandPresent && (
            <div className="absolute inset-0 z-30 pointer-events-none border-4 border-amber-500/80 rounded" />
          )}
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
      />

      {showHUD && (!isWaitingForVideo || isCameraSource) && (
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
