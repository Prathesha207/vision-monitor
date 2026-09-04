import type { DuckEntity } from '../types';

// ---------- Frontend-side NMS: deduplicate overlapping detection boxes ----------
// Prevents ghost/stacked boxes when the ML model emits many overlapping
// detections for the same physical duck (common during motion blur / rotation).

export function boxIou(a: DuckEntity, b: DuckEntity): number {
  const x1 = Math.max(a.x, b.x);
  const y1 = Math.max(a.y, b.y);
  const x2 = Math.min(a.x + a.width, b.x + b.width);
  const y2 = Math.min(a.y + a.height, b.y + b.height);
  const interArea = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
  const unionArea = a.width * a.height + b.width * b.height - interArea;
  return unionArea > 0 ? interArea / unionArea : 0;
}

/** Check if center of box B falls inside box A */
export function centerInsideBox(kept: DuckEntity, candidate: DuckEntity): boolean {
  const cx = candidate.x + candidate.width / 2;
  const cy = candidate.y + candidate.height / 2;
  return (
    cx >= kept.x &&
    cx <= kept.x + kept.width &&
    cy >= kept.y &&
    cy <= kept.y + kept.height
  );
}

/** Check if one box is mostly contained inside the other (> 60% overlap with the smaller box) */
export function isContained(a: DuckEntity, b: DuckEntity): boolean {
  const x1 = Math.max(a.x, b.x);
  const y1 = Math.max(a.y, b.y);
  const x2 = Math.min(a.x + a.width, b.x + b.width);
  const y2 = Math.min(a.y + a.height, b.y + b.height);
  const interArea = Math.max(0, x2 - x1) * Math.max(0, y2 - y1);
  const smallerArea = Math.min(a.width * a.height, b.width * b.height);
  return smallerArea > 0 && interArea / smallerArea > 0.6;
}

/**
 * Are two boxes close enough to be the same object?
 * Uses FOUR checks — any one triggers suppression:
 * 1. IOU overlap > threshold
 * 2. Center of candidate falls inside the kept box
 * 3. Center of kept falls inside the candidate box
 * 4. One box mostly contained inside the other (>60% of smaller box area)
 */
export function isDuplicate(a: DuckEntity, b: DuckEntity, iouThreshold: number): boolean {
  if (boxIou(a, b) > iouThreshold) return true;
  if (centerInsideBox(a, b)) return true;
  if (centerInsideBox(b, a)) return true;
  if (isContained(a, b)) return true;
  return false;
}

export function dedupeDucks(ducks: DuckEntity[], iouThreshold = 0.25): DuckEntity[] {
  // Sort by confidence descending — keep the best box, suppress weaker duplicates
  const sorted = [...ducks].sort((a, b) => b.confidence - a.confidence);
  const kept: DuckEntity[] = [];
  for (const d of sorted) {
    if (!kept.some((k) => isDuplicate(k, d, iouThreshold))) {
      kept.push(d);
    }
  }
  return kept;
}
