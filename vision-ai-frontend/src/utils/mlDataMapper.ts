import { DuckEntity } from '../types';
import { useInferenceStore } from '../store/inferenceStore';

/**
 * Maps raw backend ML dictionary into frontend-friendly DuckEntity objects.
 *
 * Ground rule: this function only TRANSLATES what ml_inference.py already
 * decided (per-detection isAnomaly/provisional, added_ids, missing_ids,
 * other_ids, thumbnails). It must not invent its own anomaly logic on top --
 * that caused the two bugs this rewrite fixes:
 *   1. Every duck getting flagged red whenever detected_duck_count !=
 *      expected_duck_count, instead of just the specific duck(s) the
 *      backend actually flagged.
 *   2. Nothing rendering during warmup / early inference, because every
 *      detection with a still-null id got force-marked provisional and
 *      then filtered out of activeDucks -- even once the backend itself
 *      had already stopped calling it provisional.
 */
// Persistent cache of last known valid bounding box coordinates for each duck ID
const lastKnownBBoxes = new Map<string, { x: number; y: number; width: number; height: number }>();

export const resetBBoxCache = () => {
  lastKnownBBoxes.clear();
};

export const mapDetectionsToDucks = (data: any, vw: number, vh: number): DuckEntity[] => {
  const incomingDucks: DuckEntity[] = [];
  const addedIds = data.added_ids || [];
  const missingIds = data.missing_ids || [];
  const isWarmingUp = data.status === 'WARMING' || !data.anchor_locked;

  // Find thumbnails. Ensure we do not drop thumbnails when data.thumbnails is an empty array [] (which is truthy in JS!)
  const rawDataThumbs = Array.isArray(data.thumbnails) ? data.thumbnails : [];
  const storeThumbs = Array.isArray(useInferenceStore.getState().stats?.thumbnails)
    ? useInferenceStore.getState().stats.thumbnails
    : [];
  const allThumbnails = rawDataThumbs.length >= storeThumbs.length ? rawDataThumbs : storeThumbs;

  if (Array.isArray(data.detections)) {
    data.detections.forEach((d: any, idx: number) => {
      let px = 0, py = 0, pw = 0, ph = 0;
      if (d.bbox && d.bbox.length === 4) {
        px = (d.bbox[0] / vw) * 100;
        py = (d.bbox[1] / vh) * 100;
        pw = ((d.bbox[2] - d.bbox[0]) / vw) * 100;
        ph = ((d.bbox[3] - d.bbox[1]) / vh) * 100;
      } else if (d.box && d.box.length === 4) {
        px = (d.box[0] / vw) * 100;
        py = (d.box[1] / vh) * 100;
        pw = ((d.box[2] - d.box[0]) / vw) * 100;
        ph = ((d.box[3] - d.box[1]) / vh) * 100;
      }
      const species = String(d.class_name || d.species || '').toLowerCase();
      const isDuck = species === 'duck';
      const isHand = species === 'hand';
      // DuckAnalyzer emits id: -1 (not null) for every detection before the
      // anchor locks -- see analyzer.py lines 819/1158. Multiple detections
      // in the same frame all carry that same -1 during warmup, so without
      // this check they'd all collapse to id "-1" and collide as identical
      // React keys in the gallery grid, which leaves orphaned/duplicated
      // DOM nodes behind across the ~30-100ms poll cycle instead of being
      // cleanly replaced frame to frame.
      const hasLockedId = d.id !== null && d.id !== undefined && Number(d.id) !== -1;
      const isProvisional = d.provisional === true || isWarmingUp || !hasLockedId;
      const rawId = hasLockedId ? String(d.id) : `prov-${idx + 1}`;
      // Duck and other trackers use independent numeric ID spaces.
      const isOther = !isDuck && !isHand;
      const displayId = isOther ? `other-${rawId}` : rawId;

      const thumbObj = allThumbnails.slice().reverse().find((t: any) =>
        (String(t.id) === rawId || Number(t.id) === Number(rawId)) && (isOther ? t.event === 'other_present' : t.event !== 'other_present')
      );
      const isMissingDetection = !isProvisional && (d.status === 'missing' || missingIds.includes(displayId) || missingIds.includes(Number(displayId)));
      if (pw > 0 && ph > 0 && px >= 0 && py >= 0) {
        lastKnownBBoxes.set(displayId, { x: px, y: py, width: pw, height: ph });
      } else if (isMissingDetection) {
        const cached = lastKnownBBoxes.get(displayId);
        if (cached) {
          px = cached.x;
          py = cached.y;
          pw = cached.width;
          ph = cached.height;
        }
      }
      let eventStatus: DuckEntity['statusEvent'] = undefined;
      if (isMissingDetection) {
        eventStatus = 'missing';
      } else if (!isProvisional && (addedIds.includes(displayId) || addedIds.includes(Number(displayId)) || d.status === 'added')) {
        eventStatus = 'added';
      } else if (thumbObj?.event === 'confirmed' || thumbObj?.event === 'added') {
        eventStatus = 'confirmed';
      } else if (thumbObj?.event === 'other_present' || isOther) {
        eventStatus = 'other_present';
      }

      // Warming up / provisional detections are NEVER anomalies or errors!
      const backendIsAnomaly = typeof d.isAnomaly === 'boolean' ? d.isAnomaly : undefined;
      const isAnomaly = backendIsAnomaly ?? (
        isOther || isHand ||
        (!isProvisional && (
          isMissingDetection ||
          addedIds.includes(displayId) ||
          addedIds.includes(Number(displayId)) ||
          d.status === 'unbound' ||
          d.status === 'added'
        ))
      );

      incomingDucks.push({
        id: displayId,
        species: isHand ? 'Hand' : isDuck ? 'Duck' : 'Unknown',
        confidence: d.confidence || d.conf || (isMissingDetection ? 0.0 : 0.9),
        x: px,
        y: py,
        width: pw,
        height: ph,
        vx: 0,
        vy: 0,
        heading: 0,
        isAnomaly: isAnomaly,
        thumbnail: thumbObj?.thumbnail,
        provisional: isProvisional,
        statusEvent: isHand ? 'hand_present' : eventStatus,
        handDetected: isHand
      });
    });
  }

  // Collect all duck IDs that are actively detected (and NOT missing) in this frame
  const detectedIds = new Set<string>();
  incomingDucks.forEach(d => {
    if (d.species === 'Duck' && d.statusEvent !== 'missing') {
      detectedIds.add(String(d.id));
    }
  });

  // Track all missing IDs (from backend's missing_ids array)
  // ABSOLUTELY NO missing ducks during warmup!
  const resolvedMissingIds = new Set<string>();

  if (!isWarmingUp) {
    missingIds.forEach((mid: any) => resolvedMissingIds.add(String(mid)));

    // If anchor is locked and we have active detections with real locked IDs (not provisional),
    // any confirmed duck that has disappeared from detections is missing!
    const hasLockedDetections = incomingDucks.some(d => !d.provisional && !d.id.startsWith('prov-'));
    if (data.anchor_locked && hasLockedDetections) {
      allThumbnails.forEach((t: any) => {
        if ((t.event === 'confirmed' || t.event === 'added') && (t.species === 'duck' || !t.species)) {
          const sid = String(t.id);
          if (!detectedIds.has(sid)) {
            resolvedMissingIds.add(sid);
          }
        }
      });
    }
  }

  // Inject missing ducks so they appear in the gallery.
  // They stay in their numeric position, displaying their last known thumbnail, styled as 'missing'.
  resolvedMissingIds.forEach((stringMid: string) => {
    const existing = incomingDucks.find(d => String(d.id) === stringMid);
    const cached = lastKnownBBoxes.get(stringMid);
    if (existing) {
      existing.statusEvent = 'missing';
      existing.isAnomaly = true;
      if ((existing.width <= 0 || existing.height <= 0 || existing.x < 0) && cached) {
        existing.x = cached.x;
        existing.y = cached.y;
        existing.width = cached.width;
        existing.height = cached.height;
      }
      return;
    }

    const thumbObj = allThumbnails.slice().reverse().find((t: any) =>
      String(t.id) === stringMid || Number(t.id) === Number(stringMid)
    );
    incomingDucks.push({
      id: stringMid,
      species: 'Duck',
      confidence: 0.0,
      x: cached ? cached.x : -100,
      y: cached ? cached.y : -100,
      width: cached ? cached.width : 0,
      height: cached ? cached.height : 0,
      vx: 0, vy: 0, heading: 0,
      isAnomaly: true,
      thumbnail: thumbObj?.thumbnail,
      provisional: false,
      statusEvent: 'missing'
    });
  });

  return incomingDucks;
};