import React from 'react';
import { Bird, ShieldAlert } from 'lucide-react';
import type { AnomalyStatus, DuckEntity } from '../../types';
import { useInferenceStore } from '../../store/inferenceStore';

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
  backendStatus,
}) => {
  const storeFps = useInferenceStore((state) => state.stats?.fps || 0);
  const displayFps = fps > 0 ? fps : (storeFps > 0 ? storeFps : 0);

  return (
    <div className="absolute bottom-3 left-1/2 transform -translate-x-1/2 z-30 pointer-events-auto animate-in fade-in slide-in-from-bottom-2 duration-300 max-w-[calc(100%-24px)]">
      <div className="flex items-center gap-3 sm:gap-5 px-3.5 sm:px-5 py-2 rounded-xl bg-[var(--bg-card)]/95 backdrop-blur-md border border-[var(--border-color)] shadow-lg text-[var(--text-primary)]">

        {/* Expected Ducks */}
        <div className="flex flex-col items-center">
          <span className="text-[9.5px] text-[var(--text-secondary)] uppercase tracking-wider font-semibold">
            Expected
          </span>
          <div className="flex items-center gap-1.5 mt-0.5">
            <Bird className="w-4 h-4 text-[var(--accent-pond)] shrink-0" />
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
          <div className="flex items-center gap-1.5 mt-0.5">
            <Bird className="w-4 h-4 text-[var(--accent-pond)] shrink-0" />
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
              <div className="flex items-center gap-1.5 mt-0.5">
                <ShieldAlert className="w-4 h-4 text-rose-400 shrink-0" />
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
              className={`text-base sm:text-lg font-black ${anomalyStatus.difference > 0
                ? 'text-[var(--status-anomaly-text)]'
                : anomalyStatus.difference < 0
                  ? 'text-[var(--status-warn-text)]'
                  : 'text-emerald-500 dark:text-emerald-400'
                }`}
            >
              {anomalyStatus.difference > 0 ? `+${anomalyStatus.difference}` : anomalyStatus.difference}
            </span>
          </div>
        </div>
      </div>
    </div>
  );
};
