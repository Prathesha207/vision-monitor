import React from 'react';
import { ShieldAlert } from 'lucide-react';
import type { DuckEntity } from '../../types';
import { playDuckQuackSound } from '../../utils/audio';

interface BoundingBoxOverlayProps {
  ducks: DuckEntity[];
  selectedDuckId: string | null;
  onSelectDuck: (id: string | null) => void;
  showAllBoxes: boolean;
  isHandPresent: boolean;
  isSceneAnomaly?: boolean;
  isCountMismatch?: boolean;
}

export const BoundingBoxOverlay: React.FC<BoundingBoxOverlayProps> = ({
  ducks,
  selectedDuckId,
  onSelectDuck,
  showAllBoxes,
  isHandPresent,
  isSceneAnomaly = false,
  isCountMismatch = false,
}) => {
  if (isHandPresent) return null;

  const showConfidence = true;

  return (
    <div className="absolute inset-0 w-full h-full pointer-events-none z-20">
      {ducks
        .filter((duck) => {
          if (duck.species === 'Hand' || duck.handDetected) return false;
          const isMissing = duck.statusEvent === 'missing';
          const isAnomalyDuck = duck.provisional || duck.isAnomaly || isMissing || isSceneAnomaly || isCountMismatch;
          // In "Anomalies Only" mode, show anomaly ducks or all ducks if the scene has an anomaly (such as count mismatch)
          const isVisible = showAllBoxes || isAnomalyDuck;
          return isVisible && Number.isFinite(duck.x) && Number.isFinite(duck.y) && Number.isFinite(duck.width) && Number.isFinite(duck.height) && duck.width > 0 && duck.height > 0;
        })
        .map((duck, idx) => {
          const isProvisional = duck.provisional;
          const isMissing = duck.statusEvent === 'missing';
          const isAnomaly = !isProvisional && (duck.isAnomaly || isMissing || isSceneAnomaly || isCountMismatch);
          const isSelected = duck.id === selectedDuckId;
          
          let borderColor = 'border-emerald-400/80 bg-emerald-500/5';
          let bracketColor = 'border-emerald-300';
          let tagStyle = 'bg-black/75 text-emerald-300 border border-emerald-500/30';
          let confColor = 'text-emerald-400';

          if (isMissing) {
            // Missing duck: RED dashed bounding box with alert tag
            borderColor = 'border-2 border-dashed border-rose-500 bg-rose-500/20 animate-pulse';
            bracketColor = 'border-rose-400';
            tagStyle = 'bg-rose-950/90 text-rose-200 border border-rose-500/80 font-bold shadow-xs';
            confColor = 'text-rose-300 font-bold';
          } else if (isProvisional) {
            borderColor = 'border-amber-400/90 bg-amber-500/10';
            bracketColor = 'border-amber-300';
            tagStyle = 'bg-amber-950/90 text-amber-200 border border-amber-500/50';
            confColor = 'text-amber-300 font-bold';
          } else if (isAnomaly) {
            // Count mismatch, added duck, foreign species, or scene anomaly: RED bounding box
            borderColor = 'border-2 border-rose-500 bg-rose-500/15 shadow-sm shadow-rose-500/30';
            bracketColor = 'border-rose-400';
            tagStyle = 'bg-rose-950/90 text-rose-200 border border-rose-500/80 font-bold shadow-xs';
            confColor = 'text-rose-300 font-bold';
          }

          if (isSelected) {
            borderColor += ' ring-2 ring-white shadow-md';
          }

          return (
            <div
              key={`bbox-${idx}-${duck.id}`}
              role="button"
              tabIndex={0}
              aria-label={`Select duck ${duck.id}`}
              onClick={(e) => {
                e.stopPropagation();
                playDuckQuackSound();
                onSelectDuck(duck.id === selectedDuckId ? null : duck.id);
              }}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.stopPropagation();
                  e.preventDefault();
                  playDuckQuackSound();
                  onSelectDuck(duck.id === selectedDuckId ? null : duck.id);
                }
              }}
              style={{
                left: `${duck.x}%`,
                top: `${duck.y}%`,
                width: `${duck.width}%`,
                height: `${duck.height}%`,
              }}
              className={`absolute border rounded pointer-events-auto cursor-pointer ${borderColor}`}
            >
              <span className={`absolute -top-0.5 -left-0.5 w-1.5 h-1.5 border-t-2 border-l-2 ${bracketColor}`} />
              <span className={`absolute -top-0.5 -right-0.5 w-1.5 h-1.5 border-t-2 border-r-2 ${bracketColor}`} />
              <span className={`absolute -bottom-0.5 -left-0.5 w-1.5 h-1.5 border-b-2 border-l-2 ${bracketColor}`} />
              <span className={`absolute -bottom-0.5 -right-0.5 w-1.5 h-1.5 border-b-2 border-r-2 ${bracketColor}`} />

              <div
                className={`absolute -top-5 left-0 px-1.5 py-0.5 rounded text-[9px] font-mono font-medium whitespace-nowrap flex items-center gap-1 backdrop-blur-xs pointer-events-none ${tagStyle}`}
              >
                <span>
                  {isMissing ? (
                    <span className="inline-flex items-center gap-0.5 font-black text-rose-200">
                      <ShieldAlert className="w-2.5 h-2.5 text-rose-300" />
                      <span>#{duck.id} MISSING</span>
                    </span>
                  ) : isProvisional ? (
                    'WARMING_UP'
                  ) : isAnomaly ? (
                    <span className="inline-flex items-center gap-0.5 font-black text-rose-200">
                      <ShieldAlert className="w-2.5 h-2.5 text-rose-300" />
                      <span>{duck.species === 'Duck' ? `#${duck.id}` : duck.species}</span>
                    </span>
                  ) : (
                    `#${duck.id}`
                  )}
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
  );
};
