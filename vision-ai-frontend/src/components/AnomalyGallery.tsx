import React, { useState, useMemo, memo, useCallback } from 'react';
import { Sparkles, EyeOff } from 'lucide-react';
import { DuckEntity, AnomalyStatus } from '../types';
import { playWaterDropSound, playDuckQuackSound } from '../utils/audio';

// ---------------------------------------------------------------------- //
// Shared per-duck status checks. These MUST stay in one place: the card
// component, the tab badge counts, and the tab filters all need to agree
// on which bucket a duck falls into, or the counts/tabs and the actual
// card colors drift apart (see isCountAnomalyDuck below for the bug that
// caused).
// ---------------------------------------------------------------------- //
const isMissingDuck = (d: DuckEntity) => !d.provisional && d.statusEvent === 'missing';
const isFalseMaskDuck = (d: DuckEntity) =>
  !d.provisional && (
    d.statusEvent === 'false_mask' ||
    (d as any).false_mask === true ||
    (d as any).falseMask === true ||
    (d as any).mask_anomaly === 'false_mask' ||
    (d as any).maskAnomaly === 'false_mask'
  );
const isExcessDuck = (d: DuckEntity) =>
  !d.provisional && !isFalseMaskDuck(d) && (
    d.statusEvent === 'excess' ||
    (d as any).excess === true
  );
const isOtherDuck = (d: DuckEntity) =>
  !d.provisional && (
    d.statusEvent === 'other_present' ||
    d.id.startsWith('other-') ||
    (d.species !== 'Duck' && d.species !== 'Hand')
  );
const isNewDuck = (d: DuckEntity) =>
  !d.provisional && !isExcessDuck(d) && !isFalseMaskDuck(d) && d.statusEvent === 'added';

interface DetectionCropCanvasProps {
  duck: DuckEntity;
}

// Thumbnails are pre-rendered base64 crops sent by the backend (video_inference_service.py
// -> thumbnails[]), not live video captures -- so this stays a plain <img>.
// A missing duck keeps rendering whatever thumbnail it last had, because
// mapDetectionsToDucks() looks its id up in the full thumbnail history even
// after it stops appearing in live detections.
const DetectionCropCanvas = React.memo(({ duck }: DetectionCropCanvasProps) => {
  if (duck.thumbnail) {
    return (
      <div className="w-full h-full flex items-center justify-center overflow-hidden bg-stone-800">
        <img
          src={duck.thumbnail}
          alt={`Crop ${duck.id}`}
          className="w-full h-full object-cover"
          style={{ filter: 'brightness(1.08) contrast(1.05)' }}
        />
      </div>
    );
  }

  return (
    <div className="w-full h-full flex items-center justify-center overflow-hidden bg-black/5">
      <span className="font-bold text-xs opacity-20 text-[var(--text-primary)]">?</span>
    </div>
  );
}, (previous, next) => previous.duck.id === next.duck.id && previous.duck.thumbnail === next.duck.thumbnail);

interface DuckGalleryCardProps {
  duck: DuckEntity;
  isSelected: boolean;
  textSize: string;
  showBadge: boolean;
  showConfidence: boolean;
  isCountMismatch?: boolean;
  onSelect: (id: string) => void;
  onToggleMissing?: (id: string) => void;
}

const DuckGalleryCard: React.FC<DuckGalleryCardProps> = memo(({
  duck,
  isSelected,
  textSize,
  showBadge,
  showConfidence,
  isCountMismatch = false,
  onSelect,
  onToggleMissing,
}) => {
  const isProvisional = duck.provisional === true;
  const isMissing = isMissingDuck(duck);
  const isFalseMask = isFalseMaskDuck(duck);
  const isExcess = isExcessDuck(duck);
  const isOther = isOtherDuck(duck);
  const isNew = isNewDuck(duck);

  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    // Missing cards have no real position on the canvas (x:-100, width:0),
    // so "selecting" one does nothing there -- let the parent decide what
    // clicking a missing card means instead (e.g. acknowledge it).
    if (isMissing && onToggleMissing) {
      onToggleMissing(duck.id);
      return;
    }
    if (isOther || isFalseMask || isExcess) playDuckQuackSound();
    else playWaterDropSound();
    onSelect(duck.id);
  };

  let borderClasses = 'border border-[var(--border-color)] bg-[var(--bg-card)] hover:border-[var(--accent-pond)]';
  if (isMissing) {
    borderClasses = isSelected
      ? 'border border-dashed border-amber-400 bg-amber-500/25 shadow-[0_0_8px_rgba(245,158,11,0.3)]'
      : 'border border-dashed border-amber-500 dark:border-amber-400 bg-amber-500/10 hover:border-amber-500';
  } else if (isFalseMask) {
    borderClasses = isSelected
      ? 'border-2 border-rose-400 bg-rose-500/30 shadow-[0_0_12px_rgba(244,63,94,0.4)]'
      : 'border-2 border-rose-500 bg-rose-500/15 hover:border-rose-400';
  } else if (isExcess) {
    borderClasses = isSelected
      ? 'border-2 border-amber-400 bg-amber-500/30 shadow-[0_0_12px_rgba(245,158,11,0.4)]'
      : 'border-2 border-amber-500 bg-amber-500/15 hover:border-amber-400';
  } else if (isOther) {
    borderClasses = isSelected
      ? 'border border-rose-400 bg-rose-500/25 shadow-[0_0_8px_rgba(244,63,94,0.3)]'
      : 'border border-rose-500 bg-rose-500/10 hover:border-rose-400 hover:shadow-xs';
  } else if (isNew) {
    borderClasses = isSelected
      ? 'border border-cyan-400 bg-cyan-500/25 shadow-[0_0_8px_rgba(6,182,212,0.3)]'
      : 'border border-cyan-500/70 dark:border-cyan-400/70 bg-cyan-500/10 hover:border-cyan-400';
  } else if (isProvisional) {
    borderClasses = isSelected
      ? 'border border-amber-400 bg-amber-500/25 shadow-[0_0_8px_rgba(245,158,11,0.3)]'
      : 'border border-amber-400/60 bg-amber-500/10 hover:border-amber-400';
  } else if (isSelected) {
    borderClasses = 'border border-[var(--accent-pond)] bg-[var(--accent-pond-subtle)] shadow-[0_0_8px_rgba(52,211,153,0.3)]';
  }

  const badgeLabel = isMissing ? 'MISSED'
    : isFalseMask ? 'EXCESS MASK'
      : isExcess ? 'EXCESS'
        : isOther ? 'OTHER'
          : duck.statusEvent === 'hand_present' ? 'HAND'
            : isNew ? 'NEW'
              : isProvisional ? 'WARM'
                : 'OK';

  const barColorClasses = isMissing
    ? 'bg-[var(--status-warn-bg)] border-[var(--status-warn-border)] text-[var(--status-warn-text)]'
    : isFalseMask
      ? 'bg-[var(--status-anomaly-bg)] border-[var(--status-anomaly-border)] text-[var(--status-anomaly-text)]'
      : isExcess
        ? 'bg-amber-500/20 border-amber-500/40 text-amber-600 dark:text-amber-300'
        : isNew
          ? 'bg-cyan-500/20 border-cyan-500/40 text-cyan-600 dark:text-cyan-400'
          : isOther
            ? 'bg-[var(--status-anomaly-bg)] border-[var(--status-anomaly-border)] text-[var(--status-anomaly-text)]'
            : isProvisional
              ? 'bg-[var(--status-warn-bg)] border-[var(--status-warn-border)] text-[var(--status-warn-text)]'
              : 'bg-[var(--status-normal-bg)] border-[var(--status-normal-border)] text-[var(--status-normal-text)]';

  return (
    <div
      onClick={handleClick}
      className={`flex flex-col h-full w-full rounded-md overflow-hidden shadow-2xs group cursor-pointer transition-colors min-h-0 select-none relative ${borderClasses}`}
      title={`#${duck.id} ${duck.species} (${(duck.confidence * 100).toFixed(0)}%) - ${isMissing ? 'MISSING' : isFalseMask ? 'EXCESS MASK / FALSE MASK' : isExcess ? 'EXCESS DUCK' : isNew ? 'NEW DETECTION' : isOther ? 'OTHER SPECIES' : isCountMismatch ? 'COUNT MISMATCH' : 'NORMAL'
        }`}
    >
      <div className="relative w-full flex-1 min-h-0 overflow-hidden bg-stone-800 flex items-center justify-center">
        <DetectionCropCanvas duck={duck} />

        {isMissing && (
          <div
            className="absolute top-0.5 right-0.5 w-3.5 h-3.5 rounded-full bg-amber-600 text-white flex items-center justify-center shadow-xs ring-1 ring-white/30"
            title="Missing"
          >
            <EyeOff className="w-2 h-2" />
          </div>
        )}
        {!isMissing && isFalseMask && (
          <div
            className="absolute top-0.5 right-0.5 px-1 py-0.2 rounded-full bg-rose-600 text-white flex items-center justify-center shadow-xs text-[7px] font-black leading-none ring-1 ring-white/30 animate-pulse"
            title="Excess mask / false mask"
          >
            MASK
          </div>
        )}
        {!isMissing && !isFalseMask && isExcess && (
          <div
            className="absolute top-0.5 right-0.5 px-1 py-0.2 rounded-full bg-amber-600 text-white flex items-center justify-center shadow-xs text-[7px] font-black leading-none ring-1 ring-white/30 animate-pulse"
            title="Excess duck above expected count"
          >
            EXCESS
          </div>
        )}
        {!isMissing && !isFalseMask && !isExcess && isNew && (
          <div
            className="absolute top-0.5 right-0.5 w-3.5 h-3.5 rounded-full bg-cyan-600 text-white flex items-center justify-center shadow-xs animate-pulse ring-1 ring-white/30"
            title="Newly added"
          >
            <Sparkles className="w-2 h-2" />
          </div>
        )}
        {/* Round badge at top right: ONLY shown for other species / foreign objects */}
        {!isMissing && !isFalseMask && !isExcess && !isNew && isOther && (
          <div
            className="absolute top-0.5 right-0.5 w-3.5 h-3.5 rounded-full bg-[var(--status-anomaly-text)] text-white flex items-center justify-center shadow-xs text-[8.5px] font-black leading-none ring-1 ring-white/30"
            title="Other species"
          >
            !
          </div>
        )}

        {showConfidence && !isMissing && (
          <div className="absolute bottom-0.5 right-0.5 px-1 py-0.2 rounded bg-black/80 text-[7px] font-mono font-bold text-white leading-none backdrop-blur-xs">
            {(duck.confidence * 100).toFixed(0)}%
          </div>
        )}
      </div>

      <div className={`flex items-center justify-between px-1 py-0.5 gap-1 border-t shrink-0 font-bold ${barColorClasses}`}>
        <div className="flex items-center gap-1 min-w-0 flex-nowrap whitespace-nowrap overflow-hidden">
          <span className={`w-1.5 h-1.5 rounded-full shrink-0 flex-none self-center ${isMissing ? 'bg-amber-500 animate-pulse'
            : isFalseMask ? 'bg-rose-500 shadow-xs animate-pulse ring-1 ring-rose-500/60'
            : isExcess ? 'bg-amber-400 shadow-xs animate-pulse ring-1 ring-amber-400/60'
            : isNew ? 'bg-cyan-400 animate-pulse'
            : isOther ? 'bg-[var(--status-anomaly-text)] shadow-xs animate-pulse ring-1 ring-[var(--status-anomaly-text)]/60'
            : 'bg-[var(--status-normal-text)]'
            }`} />
          <span className={`${textSize} truncate font-mono font-bold leading-none select-none tracking-tight self-center`}>
            {isFalseMask ? `#${duck.id.padStart(2, '0')} MASK` : isExcess ? `#${duck.id.padStart(2, '0')} EXC` : `#${duck.id.padStart(2, '0')}`}
          </span>
        </div>
      </div>
    </div>
  );
}, (prev, next) => (
  prev.duck.id === next.duck.id &&
  prev.duck.species === next.duck.species &&
  prev.duck.confidence === next.duck.confidence &&
  prev.duck.isAnomaly === next.duck.isAnomaly &&
  prev.duck.statusEvent === next.duck.statusEvent &&
  prev.duck.thumbnail === next.duck.thumbnail &&
  prev.isSelected === next.isSelected &&
  prev.textSize === next.textSize &&
  prev.showBadge === next.showBadge &&
  prev.showConfidence === next.showConfidence &&
  prev.isCountMismatch === next.isCountMismatch
));

DuckGalleryCard.displayName = 'DuckGalleryCard';

interface DetectionGalleryProps {
  ducks: DuckEntity[];
  selectedDuckId?: string | null;
  onSelectDuck?: (id: string | null) => void;
  onAddDuck?: (species?: DuckEntity['species'], isAnomaly?: boolean) => void;
  onToggleMissingDuck?: (id: string) => void;
  anomalyStatus?: AnomalyStatus;
  expectedCount?: number;
}

export const DetectionGallery: React.FC<DetectionGalleryProps> = ({
  ducks,
  selectedDuckId,
  onSelectDuck,
  onToggleMissingDuck,
  anomalyStatus,
  expectedCount,
}) => {
  const [filter, setFilter] = useState<'all' | 'excess' | 'missed' | 'others'>('all');
  const isCountMismatch = useMemo(() => {
    return Boolean(
      anomalyStatus?.isAnomaly &&
      (anomalyStatus.type === 'OVER_COUNT' ||
        anomalyStatus.type === 'UNDER_COUNT' ||
        anomalyStatus.difference !== 0)
    );
  }, [anomalyStatus]);

  // Priority rank for sorting:
  // 1. Other species (foreign/unknown)
  // 2. Excess mask / false mask
  // 3. Excess duck (above expected count)
  // 4. Missing duck
  // 5. Normal tracked ducks in numeric order
  const rankOf = (d: DuckEntity) => {
    if (isOtherDuck(d)) return 0;
    if (isFalseMaskDuck(d)) return 1;
    if (isExcessDuck(d)) return 2;
    if (isMissingDuck(d)) return 3;
    return 4;
  };

  const sortedDucks = useMemo(() => {
    const seen = new Set<string>();
    const validDucks = ducks.filter((d) => {
      if (d.species === 'Hand' || d.handDetected) return false;
      if (seen.has(d.id)) return false;
      seen.add(d.id);
      return true;
    });

    return validDucks.sort((a, b) => {
      const rankDiff = rankOf(a) - rankOf(b);
      if (rankDiff !== 0) return rankDiff;
      const numA = parseInt(a.id.replace(/\D/g, ''), 10) || 0;
      const numB = parseInt(b.id.replace(/\D/g, ''), 10) || 0;
      return numA - numB;
    });
  }, [ducks]);

  // Missed = ducks that disappeared from the scene (excluding provisional warmup)
  const missedCount = useMemo(() => sortedDucks.filter(isMissingDuck).length, [sortedDucks]);
  // Others = unknown / foreign species only
  const othersCount = useMemo(() => sortedDucks.filter(isOtherDuck).length, [sortedDucks]);
  // Excess = excess ducks or excess / false masks
  const excessCount = useMemo(() => sortedDucks.filter((d) => isExcessDuck(d) || isFalseMaskDuck(d)).length, [sortedDucks]);

  const filteredDucks = useMemo(() => {
    if (filter === 'excess') return sortedDucks.filter((d) => isExcessDuck(d) || isFalseMaskDuck(d));
    if (filter === 'missed') return sortedDucks.filter(isMissingDuck);
    if (filter === 'others') return sortedDucks.filter(isOtherDuck);
    // 'all' always includes every duck — missing cards stay in their ID position, just flagged.
    return sortedDucks;
  }, [sortedDucks, filter]);

  const count = filteredDucks.length;
  const { cols, rows, textSize, showBadge } = useMemo(() => {
    if (count <= 1) return { cols: 1, rows: 1, textSize: 'text-[12px]', showBadge: true };
    if (count <= 2) return { cols: 2, rows: 1, textSize: 'text-[11px]', showBadge: true };
    if (count <= 4) return { cols: 2, rows: 2, textSize: 'text-[10px]', showBadge: true };
    if (count <= 6) return { cols: 3, rows: 2, textSize: 'text-[9.5px]', showBadge: true };
    if (count <= 8) return { cols: 4, rows: 2, textSize: 'text-[9px]', showBadge: true };
    if (count <= 10) return { cols: 5, rows: 2, textSize: 'text-[8.5px]', showBadge: false };
    if (count <= 12) return { cols: 4, rows: 3, textSize: 'text-[8.5px]', showBadge: false };
    if (count <= 15) return { cols: 5, rows: 3, textSize: 'text-[8px]', showBadge: false };
    if (count <= 18) return { cols: 6, rows: 3, textSize: 'text-[7.5px]', showBadge: false };
    if (count <= 20) return { cols: 5, rows: 4, textSize: 'text-[7.5px]', showBadge: false };
    const c = 6;
    const r = Math.max(1, Math.ceil(count / c));
    return { cols: c, rows: r, textSize: 'text-[7.5px]', showBadge: false };
  }, [count]);

  const handleSelect = useCallback((id: string) => {
    onSelectDuck?.(selectedDuckId === id ? null : id);
  }, [onSelectDuck, selectedDuckId]);

  if (ducks.length === 0) return null;

  return (
    <div className="flex flex-col gap-1.5 w-full flex-1 min-h-0 overflow-hidden">
      {/* Filter Tabs: All / Missed / Others */}
      <div className="flex items-center justify-between gap-1.5 p-1 rounded-xl bg-[var(--bg-card-subtle)] border border-[var(--border-color)] shrink-0">
        <button
          onClick={() => { playWaterDropSound(); setFilter('all'); }}
          title="Show all tracked ducks"
          className={`flex-1 py-1 px-1 rounded-lg text-[10px] font-bold flex items-center justify-center gap-1 cursor-pointer transition-colors ${filter === 'all'
            ? 'bg-[var(--btn-primary-bg)] text-[var(--btn-primary-text)] shadow-xs'
            : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)]'
            }`}
        >
          <span>All</span>
          <span className="text-[8.5px] px-1.5 py-0.2 rounded-full bg-black/20 font-mono">{sortedDucks.length}</span>
        </button>

        <button
          onClick={() => { playWaterDropSound(); setFilter('excess'); }}
          title="Excess ducks and excess/false masks"
          className={`flex-1 py-1 px-1 rounded-lg text-[10px] font-bold flex items-center justify-center gap-1 cursor-pointer transition-colors ${filter === 'excess'
            ? 'bg-amber-600 text-white shadow-xs'
            : excessCount > 0
              ? 'text-amber-500 bg-amber-500/15 font-extrabold animate-pulse'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)]'
            }`}
        >
          <span>Excess</span>
          <span className={`text-[8.5px] px-1.5 py-0.2 rounded-full font-mono ${excessCount > 0 ? 'bg-amber-500 text-white animate-pulse' : 'bg-black/20'}`}>
            {excessCount}
          </span>
        </button>

        <button
          onClick={() => { playWaterDropSound(); setFilter('missed'); }}
          title="Missing ducks"
          className={`flex-1 py-1 px-1 rounded-lg text-[10px] font-bold flex items-center justify-center gap-1 cursor-pointer transition-colors ${filter === 'missed'
            ? 'bg-amber-500 text-white shadow-xs'
            : missedCount > 0
              ? 'text-amber-600 dark:text-amber-400 bg-amber-500/10 font-extrabold'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)]'
            }`}
        >
          <span>Missed</span>
          <span className={`text-[8.5px] px-1.5 py-0.2 rounded-full font-mono ${missedCount > 0 ? 'bg-amber-500 text-white animate-pulse' : 'bg-black/20'
            }`}>
            {missedCount}
          </span>
        </button>

        <button
          onClick={() => { playWaterDropSound(); setFilter('others'); }}
          title="Other species / foreign objects"
          className={`flex-1 py-1 px-1 rounded-lg text-[10px] font-bold flex items-center justify-center gap-1 cursor-pointer transition-colors ${filter === 'others'
            ? 'bg-rose-600 text-white shadow-xs'
            : othersCount > 0
              ? 'text-rose-600 bg-rose-600/10 font-extrabold'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)]'
            }`}
        >
          <span>Others</span>
          <span className={`text-[8.5px] px-1.5 py-0.2 rounded-full font-mono ${othersCount > 0 ? 'bg-rose-600 text-white' : 'bg-black/20'
            }`}>
            {othersCount}
          </span>
        </button>
      </div>

      {/* Grid of ALL tracked ducks -- STRICTLY NO INNER SCROLL, ALL FIT & FILL */}
      <div className="w-full flex-1 min-h-0 overflow-hidden">
        {filteredDucks.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center p-3 text-center text-[var(--text-secondary)] bg-[var(--bg-card-subtle)] rounded-xl border border-[var(--border-color)]">
            <span className="text-xs font-bold text-[var(--text-primary)]">
              {filter === 'excess' ? 'No Excess Ducks / Masks' : filter === 'missed' ? 'No Missing Ducks' : filter === 'others' ? 'No Other Species' : 'No matching ducks'}
            </span>
            <span className="text-[10px] text-[var(--text-secondary)] mt-0.5">Change filter above to view all items</span>
          </div>
        ) : (
          <div
            className="grid gap-1 w-full h-full overflow-hidden"
            style={{
              gridTemplateColumns: `repeat(${cols}, minmax(0, 1fr))`,
              gridTemplateRows: `repeat(${rows}, minmax(0, 1fr))`,
            }}
          >
            {filteredDucks.map((duck) => (
              <DuckGalleryCard
                key={`duck-card-${duck.id}`}
                duck={duck}
                isSelected={selectedDuckId === duck.id}
                textSize={textSize}
                showBadge={showBadge}
                showConfidence={count <= 12}
                isCountMismatch={isCountMismatch}
                onSelect={handleSelect}
                onToggleMissing={onToggleMissingDuck}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  );
};

// Export backward compatibility alias
export const AnomalyGallery = DetectionGallery;
