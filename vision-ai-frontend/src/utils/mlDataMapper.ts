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

export const mapDetectionsToDucks = (data: any, vw: number, vh: number, fallbackExpected?: number): DuckEntity[] => {
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

  const effectiveExpected =
    typeof data.expected_duck_count === 'number' && data.expected_duck_count > 0
      ? data.expected_duck_count
      : typeof fallbackExpected === 'number' && fallbackExpected > 0
      ? fallbackExpected
      : 0;

  // Gather present duck numeric IDs
  const presentDuckNumericIds: number[] = [];
  if (Array.isArray(data.detections)) {
    data.detections.forEach((det: any) => {
      const sp = String(det.class_name || det.species || '').toLowerCase();
      const isDuck = sp === '' || sp === 'duck';
      const hasId = det.id !== null && det.id !== undefined && Number(det.id) > 0;
      const isPresent = !det.status || det.status === 'present';
      if (isDuck && hasId && isPresent) {
        presentDuckNumericIds.push(Number(det.id));
      }
    });
    presentDuckNumericIds.sort((a, b) => a - b);
  }

  const detectedCount =
    typeof data.detected_duck_count === 'number' && data.detected_duck_count > 0
      ? data.detected_duck_count
      : presentDuckNumericIds.length;

  // 1. Over-count / Too Many Ducks (e.g. expected 17, 18 present):
  //    Per ML analyzer: highest-numbered present duck(s) beyond expected (Duck #18) are excess (RED),
  //    while ducks 1..17 remain normal (GREEN).
  const excessIdSet = new Set<number>();
  const isTooManyDucks = !isWarmingUp && effectiveExpected > 0 && presentDuckNumericIds.length > effectiveExpected;
  if (isTooManyDucks) {
    presentDuckNumericIds.slice(effectiveExpected).forEach((id: number) => excessIdSet.add(id));
  }

  // 2. Under-count / Too Few Ducks (e.g. expected 19, 18 present):
  //    Per ML analyzer (analyzer_new.py): when too_few_ducks occurs, excess_ids is empty, and
  //    this_box_color = box_color = RED (all detected present duck boxes take anomaly color RED).
  const isTooFewDucks = !isWarmingUp && effectiveExpected > 0 && (
    (detectedCount > 0 && detectedCount < effectiveExpected) ||
    (presentDuckNumericIds.length > 0 && presentDuckNumericIds.length < effectiveExpected) ||
    (Array.isArray(data.reasons) && (data.reasons.includes('too_few_ducks') || data.reasons.includes('too_few')))
  );

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
      const isExcess = !isProvisional && (d.excess === true || excessIdSet.has(Number(rawId)));

      let eventStatus: DuckEntity['statusEvent'] = undefined;
      if (isMissingDetection) {
        eventStatus = 'missing';
      } else if (!isProvisional && (isExcess || addedIds.includes(displayId) || addedIds.includes(Number(displayId)) || d.status === 'added')) {
        eventStatus = 'added';
      } else if (thumbObj?.event === 'confirmed' || thumbObj?.event === 'added') {
        eventStatus = 'confirmed';
      } else if (thumbObj?.event === 'other_present' || isOther) {
        eventStatus = 'other_present';
      }

      // 1. Check if backend/ML explicitly flagged this duck (isAnomaly, is_anomaly, or excess)
      const backendIsAnomaly =
        typeof d.isAnomaly === 'boolean'
          ? d.isAnomaly
          : typeof d.is_anomaly === 'boolean'
          ? d.is_anomaly
          : typeof d.excess === 'boolean'
          ? d.excess
          : undefined;

      // 2. An individual duck is an anomaly if:
      //    - Count was increased (under-count / too few ducks: all present duck boxes are RED per ML model)
      //    - It is flagged as excess (over-count: only excess duck(s) are RED)
      //    - The backend explicitly marked it (d.isAnomaly / d.is_anomaly / d.excess)
      //    - It is a missing duck, unknown/foreign species, unbound object, or added duck
      const isAnomaly = !isProvisional && (
        isTooFewDucks ||
        isExcess ||
        (backendIsAnomaly !== undefined ? backendIsAnomaly : false) ||
        isOther ||
        isHand ||
        isMissingDetection ||
        addedIds.includes(displayId) ||
        addedIds.includes(Number(displayId)) ||
        d.status === 'unbound' ||
        d.status === 'added'
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

  // Only render missing cards explicitly reported for this frame. Thumbnail
  // history is intentionally not evidence that a duck is currently missing:
  // it is a session-wide gallery cache and would otherwise turn normal later
  // frames into stale missing/anomaly frames.
  const resolvedMissingIds = new Set<string>();

  if (!isWarmingUp) {
    missingIds.forEach((mid: any) => resolvedMissingIds.add(String(mid)));
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
