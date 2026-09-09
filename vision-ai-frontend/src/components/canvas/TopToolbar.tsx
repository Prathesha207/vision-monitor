import React from 'react';
import { Hand, ShieldAlert, Layers, Minimize2, Expand, Eye, EyeOff, Video, Disc, Clock, Loader2 } from 'lucide-react';
import type { AnomalyStatus } from '../../types';
import { playWaterDropSound } from '../../utils/audio';

const formatRecordingTime = (totalSeconds: number) => {
  const mins = Math.floor(totalSeconds / 60);
  const secs = totalSeconds % 60;
  return `${mins.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
};

interface TopToolbarProps {
  isRunning: boolean;
  hasActiveVideo: boolean;
  feedMode: 'raw' | 'inference';
  onFeedModeChange: (mode: 'raw' | 'inference') => void;
  showAllBoxes: boolean;
  onToggleShowAllBoxes: () => void;
  isRecording: boolean;
  isSaving?: boolean;
  recordingDuration?: number;
  onToggleRecording: () => void;
  isFullscreen: boolean;
  onToggleFullscreen: () => void;
  showHUD: boolean;
  onToggleHUD: () => void;
  backendStatus?: string;
  isCameraSource: boolean;
  isVideoSource: boolean;
  hasCameraRecording?: boolean;
  anomalyStatus: AnomalyStatus;
  isStreaming: boolean;
  isFirstFrameLoaded: boolean;
  framesProcessed?: number;
}

export const TopToolbar: React.FC<TopToolbarProps> = ({
  isRunning,
  hasActiveVideo,
  feedMode,
  onFeedModeChange,
  showAllBoxes,
  onToggleShowAllBoxes,
  isRecording,
  isSaving = false,
  recordingDuration = 0,
  onToggleRecording,
  isFullscreen,
  onToggleFullscreen,
  showHUD,
  onToggleHUD,
  isCameraSource,
  isVideoSource: _isVideoSource,
  hasCameraRecording,
  anomalyStatus,
  isStreaming,
  framesProcessed = 0,
}) => {
  const isMediaActive = isRunning || hasActiveVideo || (isCameraSource && isStreaming);
  if (!isMediaActive) return null;

  const hasInferenceResult = framesProcessed > 0 || (anomalyStatus.detectedCount ?? 0) > 0;
  const showInferenceStatus = isRunning || hasInferenceResult;

  return (
    <div className="absolute top-0 left-0 right-0 p-2 sm:p-3 flex items-center justify-between gap-2 pointer-events-none z-30 max-w-full overflow-hidden">
      {/* Top-Left Corner: Real-Time Status Badge or Live Stream Indicator */}
      <div className="pointer-events-auto flex items-center gap-2 min-w-0 shrink-0">
        {showInferenceStatus ? (
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
                <Hand className="w-3.5 h-3.5 text-amber-300 shrink-0" />
                <span className="font-black text-amber-200">HAND PRESENT</span>
              </>
            ) : anomalyStatus.isAnomaly ? (
              <>
                <ShieldAlert className="w-3.5 h-3.5 text-white shrink-0" />
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
        ) : isCameraSource && isStreaming ? (
          <div className="flex items-center gap-1.5 h-7 sm:h-8 px-2.5 sm:px-3 rounded-xl border text-[10px] sm:text-xs font-black tracking-wide shrink-0 bg-sky-950/90 text-sky-300 border-sky-500/50 shadow-xs">
            <span className="w-2 h-2 rounded-full bg-sky-400 animate-pulse shrink-0 shadow-[0_0_8px_rgba(56,189,248,0.8)]" />
            <span>LIVE STREAM</span>
          </div>
        ) : null}
      </div>

      {/* Top-Right Corner: Action Controls */}
      <div className="pointer-events-auto flex items-center gap-1 sm:gap-1.5 flex-nowrap justify-end shrink min-w-0">
        {showInferenceStatus && (
          <>
            {/* Feed toggle pill: RAW vs INFERENCE - Only shown for OAK camera */}
            {isCameraSource && (
              <div className="flex items-center h-7 sm:h-8 p-0.5 rounded-xl bg-[var(--bg-card)]/95 backdrop-blur-md border border-[var(--border-color)] shadow-xs shrink-0">
                <button
                  onClick={() => {
                    playWaterDropSound();
                    onFeedModeChange('raw');
                  }}
                  className={`h-6 sm:h-7 px-2 sm:px-2.5 rounded-lg text-[10px] sm:text-xs font-bold tracking-wide flex items-center justify-center cursor-pointer transition-all ${
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
                  className={`h-6 sm:h-7 px-2 sm:px-2.5 rounded-lg text-[10px] sm:text-xs font-bold tracking-wide flex items-center justify-center cursor-pointer transition-all ${
                    feedMode === 'inference'
                      ? 'bg-[var(--btn-primary-bg)] text-[var(--btn-primary-text)] shadow-xs scale-100'
                      : 'text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)]'
                  }`}
                >
                  INFERENCE
                </button>
              </div>
            )}

            {/* Bounding Box Mode Toggle: Anomalies Only vs All Boxes */}
            {(feedMode === 'inference' || !isCameraSource) && (
              <button
                onClick={() => {
                  playWaterDropSound();
                  onToggleShowAllBoxes();
                }}
                aria-label={showAllBoxes ? "Showing all bounding boxes. Click for anomalies only." : "Showing anomaly bounding boxes only. Click for all boxes."}
                title={showAllBoxes ? "Bounding Boxes: SHOWING ALL (Click for Anomalies Only)" : "Bounding Boxes: ANOMALIES ONLY (Click for All Boxes)"}
                className={`h-7 sm:h-8 px-2.5 sm:px-3 flex items-center gap-1.5 rounded-xl backdrop-blur-md border shadow-xs transition-all cursor-pointer active:scale-95 shrink-0 ${
                  !showAllBoxes
                    ? 'bg-red-600 border-red-600 text-white hover:bg-red-700 shadow-sm shadow-red-500/25'
                    : 'bg-[var(--btn-secondary-bg)] border-[var(--btn-secondary-border)] text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)]'
                }`}
              >
                {!showAllBoxes ? (
                  <>
                    <ShieldAlert className="w-4 h-4 text-white shrink-0" />
                    <span className="text-xs font-bold text-white">Anomalies Only</span>
                  </>
                ) : (
                  <>
                    <Layers className="w-4 h-4 text-[var(--text-primary)] shrink-0" />
                    <span className="text-xs font-bold text-[var(--text-primary)]">All Boxes</span>
                  </>
                )}
              </button>
            )}
          </>
        )}

        {/* Recording Button (Camera Only) */}
        {isCameraSource && (
          <button
            disabled={hasCameraRecording || isSaving}
            onClick={() => {
              if (hasCameraRecording || isSaving) return;
              playWaterDropSound();
              onToggleRecording();
            }}
            title={
              hasCameraRecording
                ? 'Cannot record while reviewing a recorded clip. Return to live camera feed first.'
                : isSaving
                  ? 'Finalizing recording... please wait'
                  : isRecording
                    ? 'Click to stop recording'
                    : isStreaming
                      ? 'Click to record live camera stream'
                      : 'Start camera stream and begin recording'
            }
            className={`group h-7 sm:h-8 px-2 sm:px-2.5 flex items-center gap-1.5 sm:gap-2 rounded-xl backdrop-blur-md border text-[10px] sm:text-xs font-bold transition-all shrink-0 shadow-xs active:scale-95 ${
              hasCameraRecording
                ? 'bg-[var(--btn-secondary-bg)] border-[var(--btn-secondary-border)] text-[var(--text-muted)] opacity-40 cursor-not-allowed'
                : isSaving
                  ? 'bg-rose-700/90 text-white border-rose-400/80 shadow-md cursor-wait'
                  : isRecording
                    ? 'bg-rose-600 hover:bg-rose-500 text-white border-rose-400 shadow-lg shadow-rose-600/40 animate-pulse ring-2 ring-rose-500/30 cursor-pointer'
                    : 'bg-rose-50 hover:bg-rose-100 border-rose-300 text-rose-700 dark:bg-rose-950/60 dark:hover:bg-rose-900/80 dark:border-rose-500/50 dark:text-rose-200 dark:hover:text-white hover:shadow-rose-600/25 cursor-pointer'
            }`}
          >
            {isSaving ? (
              <>
                <Loader2 className="w-3.5 h-3.5 animate-spin text-white shrink-0" />
                <span className="font-bold text-[11px] uppercase tracking-wide">SAVING...</span>
                <div className="flex items-center gap-1 font-mono text-[10px] sm:text-[10.5px] font-bold bg-black/35 border border-white/20 px-1.5 py-0.5 rounded-md text-white/90">
                  <Clock className="w-2.5 h-2.5 text-rose-200 shrink-0" />
                  <span>{formatRecordingTime(recordingDuration)}</span>
                </div>
              </>
            ) : isRecording ? (
              <>
                {/* Live pulsing dot indicator */}
                <span className="relative flex h-2.5 w-2.5 shrink-0">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-white opacity-85" />
                  <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-white shadow-xs" />
                </span>
                <Disc className="w-3.5 h-3.5 text-white shrink-0 animate-spin [animation-duration:3s]" />
                <span className="font-black text-[11px] sm:text-xs tracking-wider uppercase text-white">REC</span>
                {/* Active Live Timer */}
                <div className="flex items-center gap-1 font-mono text-[10.5px] sm:text-[11px] font-bold bg-black/35 border border-white/20 px-1.5 sm:px-2 py-0.5 rounded-lg text-white tracking-wider shadow-inner">
                  <Clock className="w-3 h-3 text-rose-200 shrink-0" />
                  <span>{formatRecordingTime(recordingDuration)}</span>
                </div>
              </>
            ) : (
              <>
                {/* Anomaly red dot indicator */}
                <span className="w-2.5 h-2.5 rounded-full bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.8)] shrink-0" />
                <Video className="w-3.5 h-3.5 text-rose-500 dark:text-rose-400 shrink-0 group-hover:scale-110 transition-transform" />
                <span className="font-bold text-[11px] sm:text-xs tracking-wide">REC</span>
                {/* Standby Time badge */}
                <div className="flex items-center gap-1 font-mono text-[10px] sm:text-[10.5px] font-semibold bg-rose-100/80 border border-rose-300 dark:bg-black/30 dark:border-rose-500/30 px-1.5 py-0.5 rounded-md text-rose-600 dark:text-rose-300/90">
                  <Clock className="w-2.5 h-2.5 text-rose-500 dark:text-rose-400/80 shrink-0" />
                  <span>00:00</span>
                </div>
              </>
            )}
          </button>
        )}

        {/* Fullscreen Button */}
        <button
          onClick={onToggleFullscreen}
          aria-label={isFullscreen ? "Exit Fullscreen" : "Enter Fullscreen"}
          title={isFullscreen ? "Exit Fullscreen" : "Enter Fullscreen"}
          className="w-7 sm:w-8 h-7 sm:h-8 flex items-center justify-center rounded-xl bg-[var(--btn-secondary-bg)] backdrop-blur-md border border-[var(--btn-secondary-border)] text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)] active:scale-95 shrink-0 shadow-xs cursor-pointer"
        >
          {isFullscreen ? (
            <Minimize2 className="w-4 h-4 text-rose-500 dark:text-rose-400" />
          ) : (
            <Expand className="w-4 h-4" />
          )}
        </button>

        {/* Quick HUD Visibility Toggle */}
        <button
          onClick={onToggleHUD}
          aria-label={showHUD ? 'Hide HUD overlay' : 'Show HUD overlay'}
          title={showHUD ? 'Hide HUD overlay' : 'Show HUD overlay'}
          className="w-7 sm:w-8 h-7 sm:h-8 flex items-center justify-center rounded-xl bg-[var(--btn-secondary-bg)] backdrop-blur-md border border-[var(--btn-secondary-border)] text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)] active:scale-95 shrink-0 shadow-xs cursor-pointer"
        >
          {showHUD ? <Eye className="w-4 h-4" /> : <EyeOff className="w-4 h-4 text-[var(--text-muted)]" />}
        </button>
      </div>
    </div>
  );
};
