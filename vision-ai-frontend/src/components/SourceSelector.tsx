import React from 'react';
import { StreamSourceType } from '../types';
import { 
  Video, 
  Camera, 
  Play, 
  Square,
  Loader2
} from 'lucide-react';
import { playWaterDropSound } from '../utils/audio';
import { Badge } from './ui/Badge';
import { NumberStepper } from './ui/NumberStepper';


interface SourceSelectorProps {
  sourceType: StreamSourceType;
  onSourceChange: (type: StreamSourceType) => void;
  expectedDucks: number;
  onExpectedDucksChange: (count: number) => void;
  onOpenSettings?: () => void;
  onCustomVideoUploaded?: (videoUrl: string, fileName: string, sessionId?: string) => void;
  customVideoName?: string;
  customVideoUrl?: string;
  videoSessionId?: string | null;
  hasActiveVideo?: boolean;
  onClearCustomVideo?: () => void;
  isRunning?: boolean;
  onToggleRunning?: () => void;
  onStopInference?: () => void;
  onResumeInference?: () => void;
  onRequestSwitchMode?: (type: StreamSourceType) => void;
  isStreaming?: boolean;
  onStartStream?: () => void;
  onStopStream?: () => void;
  isCameraConnected?: boolean;
  cameraStartingState?: 'idle' | 'waking_camera' | 'waiting_frame' | 'ready';
}

export const SourceSelector: React.FC<SourceSelectorProps> = ({
  sourceType,
  onSourceChange,
  expectedDucks,
  onExpectedDucksChange,
  onOpenSettings: _onOpenSettings,
  customVideoName,
  customVideoUrl,
  hasActiveVideo = false,
  onClearCustomVideo: _onClearCustomVideo,
  isRunning = false,
  onToggleRunning,
  onStopInference,
  onResumeInference,
  onRequestSwitchMode,
  isStreaming = false,
  onStartStream,
  onStopStream,
  isCameraConnected = true,
  cameraStartingState = 'ready',
}) => {
  const handleSourceClick = (targetType: StreamSourceType) => {
    playWaterDropSound();
    if (onRequestSwitchMode) {
      onRequestSwitchMode(targetType);
    } else {
      onSourceChange(targetType);
    }
  };

  const isVideoMode = sourceType === 'sample-pond' || sourceType === 'uploaded-video';
  const isCameraMode = sourceType === 'oak-camera' || sourceType === 'webcam';
  // Only show stream controls if video is loaded or camera hardware is online
  const hasMediaToPlay = (isCameraMode && isCameraConnected) || (isVideoMode && (hasActiveVideo || Boolean(customVideoName)));
  const isBackendStream = Boolean(customVideoUrl && customVideoUrl.includes('/video/stream/'));

  return (
    <div className="w-full flex-shrink-0">
      <div className="flex flex-row items-center justify-between gap-1.5 sm:gap-4 p-1.5 sm:p-3 rounded-2xl bg-[var(--bg-card)] border border-[var(--border-color)] shadow-sm">
        
        {/* Source Toggle Pills */}
        <div className="flex bg-[var(--bg-card-subtle)] p-0.5 sm:p-1 rounded-xl border border-[var(--border-color)] flex-shrink-0">
          <button
            onClick={() => handleSourceClick('uploaded-video')}
            className={`flex items-center justify-center gap-1 sm:gap-2 px-2 sm:px-4 py-1.5 sm:py-2 rounded-lg text-xs font-bold transition-all cursor-pointer ${
              sourceType === 'uploaded-video'
                ? 'bg-[var(--btn-primary-bg)] text-[var(--btn-primary-text)] shadow-xs'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)]'
            }`}
          >
            <Video className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
            <span>Video<span className="hidden sm:inline"> File</span></span>
          </button>
          
          <button
            onClick={() => handleSourceClick('oak-camera')}
            className={`flex items-center justify-center gap-1 sm:gap-2 px-2 sm:px-4 py-1.5 sm:py-2 rounded-lg text-xs font-bold transition-all cursor-pointer ${
              sourceType === 'oak-camera'
                ? 'bg-[var(--btn-primary-bg)] text-[var(--btn-primary-text)] shadow-xs'
                : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)]'
            }`}
          >
            <Camera className="w-3.5 h-3.5 sm:w-4 sm:h-4" />
            <span>OAK<span className="hidden sm:inline"> Camera</span></span>
          </button>
        </div>

        {/* Global Expected Count & Stream Controls */}
        <div className="flex items-center justify-end gap-1.5 sm:gap-4 flex-shrink-0 ml-auto">
          {/* Expected Ducks Control */}
          <div className="flex items-center gap-1 sm:gap-3">
            <div className="flex items-center gap-1 sm:gap-2">
              <div className="w-1.5 h-1.5 rounded-full bg-[var(--accent-pond)] animate-pulse" />
              <span className="text-[10px] sm:text-xs font-bold text-[var(--text-primary)]">
                <span className="inline sm:hidden">Exp:</span>
                <span className="hidden sm:inline">Expected Ducks:</span>
              </span>
            </div>
            <NumberStepper 
              value={expectedDucks} 
              onChange={(val) => {
                playWaterDropSound();
                onExpectedDucksChange(val);
              }}
              min={1} 
              max={50}
            />
          </div>

          {/* Stream Play/Stop Controls */}
          {hasMediaToPlay && (
            <div className="flex items-center gap-1 sm:gap-1.5 pl-1.5 sm:pl-4 border-l border-[var(--border-color)]">
              {isCameraMode && cameraStartingState !== 'ready' ? (
                /* When Camera is Actively Waking up or Warming: provide busy state */
                <button
                  disabled
                  className="h-8 sm:h-9 flex items-center gap-1.5 px-3.5 sm:px-4 rounded-xl bg-amber-600/80 text-white font-bold text-[11px] sm:text-xs shadow-xs cursor-wait opacity-90 transition-all shrink-0"
                >
                  <Loader2 className="w-3.5 h-3.5 animate-spin" />
                  <span>{cameraStartingState === 'waking_camera' ? 'WAKING CAMERA...' : 'WARMING UP...'}</span>
                </button>
              ) : isCameraMode && !isStreaming ? (
                <button
                  onClick={() => {
                    playWaterDropSound();
                    onStartStream?.();
                  }}
                  title="Start camera stream"
                  className="h-8 sm:h-9 flex items-center gap-1 sm:gap-2 px-2.5 sm:px-5 rounded-xl bg-sky-600 hover:bg-sky-500 text-white font-bold text-[11px] sm:text-xs shadow-sm active:scale-95 cursor-pointer transition-all shrink-0"
                >
                  <Play className="w-3 h-3 sm:w-3.5 sm:h-3.5 fill-current" />
                  <span className="whitespace-nowrap">START<span className="hidden sm:inline"> STREAM</span></span>
                </button>
              ) : isRunning ? (
                /* When Running: provide STOP INFERENCE button */
                <div className="flex items-center gap-1 sm:gap-2">
                  <Badge variant="normal" size="sm" dot className="mr-1 hidden xl:inline-flex">
                    LIVE INFERENCE
                  </Badge>
                  <button
                    onClick={() => {
                      playWaterDropSound();
                      if (onStopInference) onStopInference();
                      else if (onToggleRunning) onToggleRunning();
                    }}
                    title="Stop AI inference"
                    className="h-8 sm:h-9 flex items-center gap-1 sm:gap-1.5 px-2.5 sm:px-4 rounded-xl bg-rose-600 hover:bg-rose-500 text-white font-bold text-[11px] sm:text-xs shadow-xs active:scale-95 cursor-pointer transition-all"
                  >
                    <Square className="w-3 h-3 sm:w-3.5 sm:h-3.5 fill-current" />
                    <span>STOP<span className="hidden sm:inline"> INFERENCE</span></span>
                  </button>
                </div>
              ) : (
                /* When Stopped / Paused: provide START INFERENCE */
                <div className="flex items-center gap-1 sm:gap-1.5">
                  <button
                    onClick={() => {
                      playWaterDropSound();
                      if (onResumeInference) onResumeInference();
                      else if (onToggleRunning) onToggleRunning();
                    }}
                    title="Start real-time YOLOv8 AI inference"
                    className="h-8 sm:h-9 flex items-center gap-1 sm:gap-2 px-2.5 sm:px-5 rounded-xl bg-emerald-600 hover:bg-emerald-500 text-white font-bold text-[11px] sm:text-xs shadow-sm hover:shadow-emerald-600/30 active:scale-95 cursor-pointer transition-all shrink-0"
                  >
                    <Play className="w-3 h-3 sm:w-3.5 sm:h-3.5 fill-current" />
                    <span className="whitespace-nowrap">START<span className="hidden sm:inline"> INFERENCE</span></span>
                  </button>
                </div>
              )}

              {isCameraMode && isStreaming && !isRunning && (
                <button
                  onClick={() => {
                    playWaterDropSound();
                    onStopStream?.();
                  }}
                  title="Stop camera stream"
                  className="h-8 sm:h-9 flex items-center gap-1 sm:gap-1.5 px-2.5 sm:px-4 rounded-xl bg-slate-600 hover:bg-slate-500 text-white font-bold text-[11px] sm:text-xs shadow-xs active:scale-95 cursor-pointer transition-all"
                >
                  <Square className="w-3 h-3 sm:w-3.5 sm:h-3.5 fill-current" />
                  <span>STOP<span className="hidden sm:inline"> STREAM</span></span>
                </button>
              )}

              {/* CLEAR button only visible for uploaded video */}
              {isVideoMode && (
                <button
                  onClick={() => {
                    playWaterDropSound();
                    if (_onClearCustomVideo) _onClearCustomVideo();
                  }}
                  title="Clear uploaded video"
                  className="h-8 sm:h-9 flex items-center gap-1 px-2 sm:px-3 rounded-xl bg-rose-600 hover:bg-rose-500 text-white border border-rose-500/50 text-[11px] sm:text-xs font-semibold active:scale-95 cursor-pointer transition-all shrink-0"
                >
                  <Square className="w-2.5 h-2.5 sm:w-3 sm:h-3 fill-current text-white" />
                  <span className="hidden sm:inline">{isBackendStream ? 'RESET' : 'CLEAR'}</span>
                </button>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
