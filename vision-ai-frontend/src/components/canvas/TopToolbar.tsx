import React from 'react';
import { Hand, ShieldAlert, Layers, Minimize2, Expand, Eye, EyeOff } from 'lucide-react';
import type { AnomalyStatus } from '../../types';
import { playWaterDropSound } from '../../utils/audio';

interface TopToolbarProps {
  isRunning: boolean;
  hasActiveVideo: boolean;
  feedMode: 'raw' | 'inference';
  onFeedModeChange: (mode: 'raw' | 'inference') => void;
  showAllBoxes: boolean;
  onToggleShowAllBoxes: () => void;
  isRecording: boolean;
  onToggleRecording: () => void;
  isFullscreen: boolean;
  onToggleFullscreen: () => void;
  showHUD: boolean;
  onToggleHUD: () => void;
  backendStatus?: string;
  isCameraSource: boolean;
  isVideoSource: boolean;
  anomalyStatus: AnomalyStatus;
  isStreaming: boolean;
  isFirstFrameLoaded: boolean;
}

export const TopToolbar: React.FC<TopToolbarProps> = ({
  isRunning,
  hasActiveVideo,
  feedMode,
  onFeedModeChange,
  showAllBoxes,
  onToggleShowAllBoxes,
  isRecording,
  onToggleRecording,
  isFullscreen,
  onToggleFullscreen,
  showHUD,
  onToggleHUD,
  isCameraSource,
  isVideoSource,
  anomalyStatus,
  isStreaming,
}) => {
  const isMediaActive = isRunning || hasActiveVideo || (isCameraSource && isStreaming);
  if (!isMediaActive) return null;

  return (
    <>
      {/* Top-Left Corner: Real-Time Status Badge */}
      {(isRunning || hasActiveVideo) && (
        <div className="absolute top-2 sm:top-3 left-2 sm:left-3 pointer-events-auto z-30 flex items-center shrink-0">
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
        </div>
      )}

      {/* Top-Right Corner: Action Controls */}
      <div className="absolute top-2 sm:top-3 right-2 sm:right-3 flex items-center gap-1 sm:gap-1.5 pointer-events-auto z-30 ml-auto shrink-0">
        {(isRunning || hasActiveVideo) && (
          <>
            {/* Feed toggle pill: RAW vs INFERENCE - Only shown for OAK camera, NOT for video upload */}
          {isCameraSource && (
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
          )}

          {/* Bounding Box Mode Toggle: Anomalies Only (Default) vs All Boxes */}
          {(feedMode === 'inference' || !isCameraSource) && (
            <button
              onClick={() => {
                playWaterDropSound();
                onToggleShowAllBoxes();
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
            onToggleRecording();
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
        onClick={onToggleFullscreen}
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

      {/* Quick HUD Visibility Toggle */}
      <button
        onClick={onToggleHUD}
        aria-label={showHUD ? 'Hide HUD overlay' : 'Show HUD overlay'}
        title={showHUD ? 'Hide HUD overlay' : 'Show HUD overlay'}
        className="w-7 sm:w-8 h-7 sm:h-8 flex items-center justify-center rounded-xl bg-[var(--btn-secondary-bg)] backdrop-blur-md border border-[var(--btn-secondary-border)] text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)] active:scale-95 shrink-0 shadow-xs cursor-pointer"
      >
        {showHUD ? <Eye className="w-3.5 h-3.5" /> : <EyeOff className="w-3.5 h-3.5 text-[var(--text-muted)]" />}
      </button>
    </div>
  </>
  );
};
