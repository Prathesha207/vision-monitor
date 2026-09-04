import React from 'react';
import { AlertTriangle, CheckCircle2 } from 'lucide-react';
import type { AnomalyStatus, DuckEntity } from '../../types';

interface StatusBarProps {
  anomalyStatus: AnomalyStatus;
  fps: number;
  backendStatus?: string;
  ducks?: DuckEntity[];
  expectedDucks?: number;
}

export const StatusBar: React.FC<StatusBarProps> = ({
  anomalyStatus,
  fps,
}) => {
  return (
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
  );
};
