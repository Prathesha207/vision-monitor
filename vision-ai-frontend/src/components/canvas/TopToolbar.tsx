import React from 'react';
import { Hand, ShieldAlert, CheckCircle2, Layers, Minimize2, Expand, Eye, EyeOff } from 'lucide-react';
import type { AnomalyStatus } from '../../types';
import { playWaterDropSound } from '../../utils/audio';

interface TopToolbarProps {
  isRunning: boolean;
  hasActiveVideo: boolean;
  feedMode: 'raw' | 'inference';
  onFeedModeChange: (mode: 'raw' | 'inference') => void;
  showAllBoxes: boolean;
  onToggleShowAllBoxes: () => void;
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
          <div className="flex items-center h-7 sm:h-8 px-2.5 sm:px-3 rounded-xl bg-[var(--bg-card)]/95 backdrop-blur-md border border-[var(--border-color)] shadow-xs shrink-0 select-none">
            {anomalyStatus.message === 'WARMING' ? (
              <div className="flex items-center gap-1.5 text-amber-500 font-bold text-xs sm:text-sm animate-pulse">
                <span className="w-2 h-2 rounded-full bg-amber-500 shrink-0 shadow-[0_0_6px_rgba(245,158,11,0.6)]" />
                <span>WARMING</span>
              </div>
            ) : anomalyStatus.message === 'HAND DETECTED' ? (
              <div className="flex items-center gap-1.5 text-amber-500 font-bold text-xs sm:text-sm animate-pulse">
                <Hand className="w-3.5 h-3.5 shrink-0" />
                <span>HAND</span>
              </div>
            ) : anomalyStatus.isAnomaly ? (
              <div className="flex items-center gap-1.5 text-[var(--status-anomaly-text)] font-bold text-xs sm:text-sm animate-pulse">
                <ShieldAlert className="w-3.5 h-3.5 shrink-0" />
                <span>ANOMALY</span>
              </div>
            ) : anomalyStatus.message === 'STANDBY' || anomalyStatus.message === 'READY' || anomalyStatus.message === 'NO CAMERA' ? (
              <div className="flex items-center gap-1.5 text-[var(--text-secondary)] font-bold text-xs sm:text-sm">
                <span className="w-2 h-2 rounded-full bg-[var(--accent-pond)] shrink-0" />
                <span>{anomalyStatus.message}</span>
              </div>
            ) : (
              <div className="flex items-center gap-1.5 text-emerald-500 dark:text-emerald-400 font-bold text-xs sm:text-sm">
                <CheckCircle2 className="w-3.5 h-3.5 shrink-0 text-emerald-500 dark:text-emerald-400" />
                <span>NORMAL</span>
              </div>
            )}
          </div>
        ) : isCameraSource && isStreaming ? (
          <div className="flex items-center h-7 sm:h-8 px-2.5 sm:px-3 rounded-xl bg-[var(--bg-card)]/95 backdrop-blur-md border border-[var(--border-color)] shadow-xs shrink-0 select-none">
            <div className="flex items-center gap-1.5 text-sky-400 font-bold text-xs sm:text-sm">
              <span className="w-2 h-2 rounded-full bg-sky-400 animate-pulse shrink-0 shadow-[0_0_6px_rgba(56,189,248,0.6)]" />
              <span>LIVE STREAM</span>
            </div>
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

        {/* Fullscreen Button */}
        <button
          onClick={onToggleFullscreen}
          aria-label={isFullscreen ? "Exit Fullscreen" : "Enter Fullscreen"}
          title={isFullscreen ? "Exit Fullscreen" : "Enter Fullscreen"}
          className="w-7 sm:w-8 h-7 sm:h-8 flex items-center justify-center rounded-xl bg-[var(--btn-secondary-bg)] backdrop-blur-md border border-[var(--btn-secondary-border)] text-[var(--text-primary)] hover:text-white hover:bg-[var(--btn-secondary-hover)] active:scale-95 shrink-0 shadow-xs cursor-pointer"
        >
          {isFullscreen ? (
            <Minimize2 className="w-4 h-4 text-[var(--text-primary)] dark:text-white" />
          ) : (
            <Expand className="w-4 h-4 text-[var(--text-primary)] dark:text-white" />
          )}
        </button>

        {/* Quick HUD Visibility Toggle */}
        {/* <button
          onClick={onToggleHUD}
          aria-label={showHUD ? 'Hide HUD overlay' : 'Show HUD overlay'}
          title={showHUD ? 'Hide HUD overlay' : 'Show HUD overlay'}
          className="w-7 sm:w-8 h-7 sm:h-8 flex items-center justify-center rounded-xl bg-[var(--btn-secondary-bg)] backdrop-blur-md border border-[var(--btn-secondary-border)] text-[var(--text-primary)] hover:text-white hover:bg-[var(--btn-secondary-hover)] active:scale-95 shrink-0 shadow-xs cursor-pointer"
        >
          {showHUD ? (
            <Eye className="w-4 h-4 text-[var(--text-primary)] dark:text-white" />
          ) : (
            <EyeOff className="w-4 h-4 text-[var(--text-muted)] dark:text-white/70" />
          )}
        </button> */}
      </div>
    </div>
  );
};
