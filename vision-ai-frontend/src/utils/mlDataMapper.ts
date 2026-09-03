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
export const mapDetectionsToDucks = (data: any, vw: number, vh: number): DuckEntity[] => {
  const incomingDucks: DuckEntity[] = [];
  const addedIds = data.added_ids || [];
  const missingIds = data.missing_ids || [];

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
      const rawId = d.id !== null && d.id !== undefined ? String(d.id) : `prov-${idx + 1}`;
      // Duck and other trackers use independent numeric ID spaces.
      const isOther = !isDuck && !isHand;
      const displayId = isOther ? `other-${rawId}` : rawId;

      const thumbObj = allThumbnails.slice().reverse().find((t: any) =>
        (String(t.id) === rawId || Number(t.id) === Number(rawId)) && (isOther ? t.event === 'other_present' : t.event !== 'other_present')
      );
      let eventStatus: DuckEntity['statusEvent'] = undefined;
      if (addedIds.includes(displayId) || addedIds.includes(Number(displayId))) eventStatus = 'added';
      else if (thumbObj?.event === 'confirmed' || thumbObj?.event === 'added') eventStatus = 'confirmed';
      else if (thumbObj?.event === 'other_present') eventStatus = 'other_present';

      // Trust the backend's own per-detection verdict first -- DuckAnalyzer
      // already knows whether THIS box is anomalous (added / other-species /
      // hand / whatever). Only fall back to re-deriving it from other ML
      // signals (species class, added_ids membership) if the backend didn't
      // send the field at all -- never override it after the fact.
      const backendIsAnomaly = typeof d.isAnomaly === 'boolean' ? d.isAnomaly : undefined;
      const isAnomaly = backendIsAnomaly ?? (
        isOther || isHand ||
        addedIds.includes(displayId) ||
        addedIds.includes(Number(displayId))
      );

      // Trust the backend's own provisional flag. Do NOT force provisional
      // just because data.status === 'WARMING' or the id is still null --
      // that was hiding real, currently-visible detections from the gallery
      // for as long as warmup/anchor-locking took.
      const isProvisional = d.provisional === true;

      incomingDucks.push({
        id: displayId,
        species: isHand ? 'Hand' : isDuck ? 'Duck' : 'Unknown',
        confidence: d.confidence || d.conf || 0.9,
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

  // Collect all duck IDs that are actively detected in this frame
  const detectedIds = new Set<string>();
  incomingDucks.forEach(d => {
    if (d.species === 'Duck') detectedIds.add(String(d.id));
  });

  // Track all missing IDs (from backend's missing_ids array)
  const resolvedMissingIds = new Set<string>(missingIds.map(String));

  // If anchor is locked, any confirmed duck that has disappeared from detections is missing!
  // This guarantees that Duck #15's card is NEVER removed even if the backend misses or delays missing_ids.
  if (data.anchor_locked || data.status === 'NORMAL' || data.status === 'ANOMALY') {
    allThumbnails.forEach((t: any) => {
      if ((t.event === 'confirmed' || t.event === 'added') && (t.species === 'duck' || !t.species)) {
        const sid = String(t.id);
        if (!detectedIds.has(sid)) {
          resolvedMissingIds.add(sid);
        }
      }
    });
  }

  // Inject missing ducks so they appear in the gallery.
  // They stay in their numeric position, displaying their last known thumbnail, styled as 'missing'.
  resolvedMissingIds.forEach((stringMid: string) => {
    if (detectedIds.has(stringMid)) return;

    const thumbObj = allThumbnails.slice().reverse().find((t: any) =>
      String(t.id) === stringMid || Number(t.id) === Number(stringMid)
    );
    incomingDucks.push({
      id: stringMid,
      species: 'Duck',
      confidence: 1.0,
      x: -100, y: -100, width: 0, height: 0, // off screen
      vx: 0, vy: 0, heading: 0,
      isAnomaly: true,
      thumbnail: thumbObj?.thumbnail,
      provisional: false,
      statusEvent: 'missing'
    });
  });

  return incomingDucks;
};