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
const isNewDuck = (d: DuckEntity) => !d.provisional && d.statusEvent === 'added';
const isOtherDuck = (d: DuckEntity) =>
  !d.provisional && (d.statusEvent === 'other_present' || (d.species !== 'Duck' && d.species !== 'Hand'));
// BUG FIX: mlDataMapper.ts sets `isAnomaly: true` straight from the backend
// on EVERY present duck during a too_few_ducks episode (matching the
// backend's own this_box_color=RED-for-all-boxes behavior in that case) --
// and those ducks have no special statusEvent (not missing/added/other).
// This file previously never read duck.isAnomaly at all, so that whole
// episode rendered as plain "OK" green cards here while BoundingBoxOverlay
// correctly showed every box red on the same frame. This catches any duck
// the backend flagged anomalous that isn't already covered by one of the
// three named buckets above.
const isCountAnomalyDuck = (d: DuckEntity) =>
  !d.provisional && !isMissingDuck(d) && !isNewDuck(d) && !isOtherDuck(d) && d.isAnomaly === true;

interface DetectionCropCanvasProps {
  duck: DuckEntity;
}

// Thumbnails are pre-rendered base64 crops sent by the backend (ml_inference.py
// -> thumbnails[]), not live video captures -- so this stays a plain <img>.
// A missing duck keeps rendering whatever thumbnail it last had, because
// mapDetectionsToDucks() looks its id up in the full thumbnail history even
// after it stops appearing in live detections.
const DetectionCropCanvas = React.memo(({ duck }: DetectionCropCanvasProps) => {
  if (duck.thumbnail) {
    return (
      <div className="w-full h-full flex items-center justify-center overflow-hidden bg-black/10">
        <img
          src={duck.thumbnail}
          alt={`Crop ${duck.id}`}
          className="w-full h-full object-cover"
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
  const isNew = isNewDuck(duck);
  const isOther = isOtherDuck(duck);
  const isCountAnomaly = isCountAnomalyDuck(duck);
  const isAlert = !isProvisional && (isOther || isCountAnomaly);

  const handleClick = (e: React.MouseEvent) => {
    e.stopPropagation();
    // Missing cards have no real position on the canvas (x:-100, width:0),
    // so "selecting" one does nothing there -- let the parent decide what
    // clicking a missing card means instead (e.g. acknowledge it).
    if (isMissing && onToggleMissing) {
      onToggleMissing(duck.id);
      return;
    }
    if (isAlert) playDuckQuackSound();
    else playWaterDropSound();
    onSelect(duck.id);
  };

  let borderClasses = 'border border-[var(--border-color)] bg-[var(--bg-card)] hover:border-[var(--accent-pond)]';
  if (isMissing) {
    borderClasses = isSelected
      ? 'border border-dashed border-amber-400 bg-amber-500/25 shadow-[0_0_8px_rgba(245,158,11,0.3)]'
      : 'border border-dashed border-amber-500 dark:border-amber-400 bg-amber-500/10 hover:border-amber-500';
  } else if (isNew) {
    borderClasses = isSelected
      ? 'border border-cyan-400 bg-cyan-500/25 shadow-[0_0_8px_rgba(6,182,212,0.3)]'
      : 'border border-cyan-500/70 dark:border-cyan-400/70 bg-cyan-500/10 hover:border-cyan-400';
  } else if (isAlert) {
    borderClasses = isSelected
      ? 'border border-rose-400 bg-rose-500/25 shadow-[0_0_8px_rgba(244,63,94,0.3)]'
      : 'border border-rose-500 bg-rose-500/10 hover:border-rose-400 hover:shadow-xs';
  } else if (isProvisional) {
    borderClasses = isSelected
      ? 'border border-amber-400 bg-amber-500/25 shadow-[0_0_8px_rgba(245,158,11,0.3)]'
      : 'border border-amber-400/60 bg-amber-500/10 hover:border-amber-400';
  } else if (isSelected) {
    borderClasses = 'border border-[var(--accent-pond)] bg-[var(--accent-pond-subtle)] shadow-[0_0_8px_rgba(52,211,153,0.3)]';
  }

  const badgeLabel = isMissing ? 'MISSED'
    : isOther ? 'ALERT'
      : duck.statusEvent === 'hand_present' ? 'HAND'
        : isNew ? 'NEW'
          : isCountAnomaly ? 'ANOMALY'
            : isProvisional ? 'WARM'
              : 'OK';

  const barColorClasses = isMissing
    ? 'bg-amber-500/25 dark:bg-amber-950/60 border-amber-500/50 text-amber-900 dark:text-amber-200'
    : isNew
      ? 'bg-cyan-500/25 dark:bg-cyan-950/60 border-cyan-500/50 text-cyan-900 dark:text-cyan-200'
      : isAlert
        ? 'bg-rose-500/25 dark:bg-rose-950/60 border-rose-500/50 text-rose-900 dark:text-rose-200'
        : isProvisional
          ? 'bg-amber-500/20 dark:bg-amber-950/50 border-amber-500/40 text-amber-800 dark:text-amber-300'
          : 'bg-[var(--status-normal-bg)] border-[var(--status-normal-border)] text-[var(--status-normal-text)]';

  return (
    <div
      onClick={handleClick}
      className={`flex flex-col h-full w-full rounded-md overflow-hidden shadow-2xs group cursor-pointer transition-colors min-h-0 select-none relative ${borderClasses}`}
      title={`#${duck.id} ${duck.species} (${(duck.confidence * 100).toFixed(0)}%) - ${isMissing ? 'MISSING' : isNew ? 'NEW DETECTION' : isAlert ? 'ANOMALY ALERT' : isCountMismatch ? 'COUNT MISMATCH' : 'NORMAL'
        }`}
    >
      <div className="relative w-full flex-1 min-h-0 overflow-hidden bg-stone-950 flex items-center justify-center">
        <DetectionCropCanvas duck={duck} />

        {isMissing && (
          <div
            className="absolute top-0.5 right-0.5 w-3.5 h-3.5 rounded-full bg-amber-600 text-white flex items-center justify-center shadow-xs ring-1 ring-white/30"
            title="Missing"
          >
            <EyeOff className="w-2 h-2" />
          </div>
        )}
        {!isMissing && isNew && (
          <div
            className="absolute top-0.5 right-0.5 w-3.5 h-3.5 rounded-full bg-cyan-600 text-white flex items-center justify-center shadow-xs animate-pulse ring-1 ring-white/30"
            title="Newly added"
          >
            <Sparkles className="w-2 h-2" />
          </div>
        )}
        {!isMissing && !isNew && isAlert && (
          <div
            className="absolute top-0.5 right-0.5 w-3.5 h-3.5 rounded-full bg-[var(--status-anomaly-text)] text-white flex items-center justify-center shadow-xs text-[8.5px] font-black leading-none ring-1 ring-white/30"
            title="Alert"
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
            : isNew ? 'bg-cyan-400 animate-pulse'
              : isAlert ? 'bg-[var(--status-anomaly-text)] shadow-xs animate-pulse ring-1 ring-[var(--status-anomaly-text)]/60'
                : 'bg-[var(--status-normal-text)]'
            }`} />
          <span className={`${textSize} truncate font-mono font-bold leading-none select-none tracking-tight self-center`}>
            #{duck.id.padStart(2, '0')}
          </span>
        </div>
        {/* {(showBadge || isMissing || isAlert || isNew) && (
          <span className={`text-[6.5px] px-1 py-0.2 rounded font-black shrink-0 uppercase leading-none self-center ${
            isMissing
              ? 'bg-amber-500 text-white shadow-xs'
              : isNew
              ? 'bg-cyan-500 text-white shadow-xs'
              : isAlert
              ? 'bg-rose-600 text-white shadow-xs'
              : 'text-[var(--status-normal-text)]'
          }`}>
            {badgeLabel}
          </span>
        )} */}
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
  // Accepted so this component's prop shape matches DetectionDrawer.tsx's
  // call signature. No add-duck button is rendered -- gallery is display/
  // triage only, per request.
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
  const [filter, setFilter] = useState<'all' | 'missed' | 'alert'>('all');
  const isCountMismatch = useMemo(() => {
    return Boolean(
      anomalyStatus?.isAnomaly &&
      (anomalyStatus.type === 'OVER_COUNT' ||
        anomalyStatus.type === 'UNDER_COUNT' ||
        anomalyStatus.difference !== 0)
    );
  }, [anomalyStatus]);

  // Priority rank for sorting: other species (foreign/unknown) pinned at top,
  // everything else (including missing) stays in stable numeric ID order.
  const rankOf = (d: DuckEntity) => {
    if (d.statusEvent === 'other_present' || (d.species !== 'Duck' && d.species !== 'Hand')) return 0;
    return 1;
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
  const missedCount = useMemo(() => sortedDucks.filter((d) => !d.provisional && d.statusEvent === 'missing').length, [sortedDucks]);
  // Alert = unknown/foreign species OR any other duck the backend flagged
  // anomalous that isn't already a missing/new card (e.g. every present duck
  // during a too_few_ducks episode). Was previously only the foreign-species
  // half of this, so the Alert tab and its count silently missed count-
  // mismatch anomalies that were red on the video overlay.
  const alertCount = useMemo(
    () => sortedDucks.filter((d) => isOtherDuck(d) || isCountAnomalyDuck(d)).length,
    [sortedDucks]
  );

  const filteredDucks = useMemo(() => {
    if (filter === 'missed') return sortedDucks.filter((d) => !d.provisional && d.statusEvent === 'missing');
    if (filter === 'alert') return sortedDucks.filter((d) => isOtherDuck(d) || isCountAnomalyDuck(d));
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
      {/* Filter Tabs: All / Missed / Alert */}
      <div className="flex items-center justify-between gap-1.5 p-1 rounded-xl bg-[var(--bg-card-subtle)] border border-[var(--border-color)] shrink-0">
        <button
          onClick={() => { playWaterDropSound(); setFilter('all'); }}
          title="Show all tracked ducks"
          className={`flex-1 py-1 px-1.5 rounded-lg text-[10px] font-bold flex items-center justify-center gap-1.5 cursor-pointer transition-colors ${filter === 'all'
            ? 'bg-[var(--btn-primary-bg)] text-[var(--btn-primary-text)] shadow-xs'
            : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)]'
            }`}
        >
          <span>All</span>
          <span className="text-[8.5px] px-1.5 py-0.2 rounded-full bg-black/20 font-mono">{sortedDucks.length}</span>
        </button>

        <button
          onClick={() => { playWaterDropSound(); setFilter('missed'); }}
          title="Missing ducks"
          className={`flex-1 py-1 px-1.5 rounded-lg text-[10px] font-bold flex items-center justify-center gap-1.5 cursor-pointer transition-colors ${filter === 'missed'
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
          onClick={() => { playWaterDropSound(); setFilter('alert'); }}
          title="Unknown / foreign species"
          className={`flex-1 py-1 px-1.5 rounded-lg text-[10px] font-bold flex items-center justify-center gap-1.5 cursor-pointer transition-colors ${filter === 'alert'
            ? 'bg-red-600 text-white shadow-xs'
            : alertCount > 0
              ? 'text-red-600 bg-red-600/10 font-extrabold'
              : 'text-[var(--text-secondary)] hover:text-[var(--text-primary)] hover:bg-[var(--btn-secondary-hover)]'
            }`}
        >
          <span>Alert</span>
          <span className={`text-[8.5px] px-1.5 py-0.2 rounded-full font-mono ${alertCount > 0 ? 'bg-red-600 text-white' : 'bg-black/20'
            }`}>
            {alertCount}
          </span>
        </button>
      </div>

      {/* Grid of ALL tracked ducks -- STRICTLY NO INNER SCROLL, ALL FIT & FILL */}
      <div className="w-full flex-1 min-h-0 overflow-hidden">
        {filteredDucks.length === 0 ? (
          <div className="h-full flex flex-col items-center justify-center p-3 text-center text-[var(--text-secondary)] bg-[var(--bg-card-subtle)] rounded-xl border border-[var(--border-color)]">
            <span className="text-xs font-bold text-[var(--text-primary)]">
              {filter === 'missed' ? 'No Missing Ducks' : filter === 'alert' ? 'No Unknown Species' : 'No matching ducks'}
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
