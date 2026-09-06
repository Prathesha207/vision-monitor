import { useState, useMemo, useRef, useEffect } from 'react';
import type { AnomalyStatus, DuckEntity, LogEntry, DetectionMetrics } from '../types';
import { playAnomalyAlertSound, playNormalSound } from '../utils/audio';

export function useAnomalyStatus({
  hasActiveStream,
  isRunning,
  isStarting,
  isCameraSource,
  framesProcessed,
  backendStats,
  addLog,
  ducks,
  expectedDucks,
}: {
  hasActiveStream: boolean;
  isRunning: boolean;
  isStarting: boolean;
  isCameraSource: boolean;
  framesProcessed: number;
  backendStats: any;
  addLog: (message: string, level?: LogEntry['level']) => void;
  ducks: DuckEntity[];
  expectedDucks: number;
}) {
  const activeDucks = useMemo(() => {
    if (!hasActiveStream && ducks.length === 0) return [];
    return ducks;
  }, [hasActiveStream, ducks]);

  const backendStatus = backendStats.status;

  const anomalyStatus: AnomalyStatus = useMemo(() => {
    if (!hasActiveStream && ducks.length === 0) {
      return {
        isAnomaly: false,
        type: 'NONE',
        message: isCameraSource ? 'NO CAMERA' : 'STANDBY',
        subMessage: isCameraSource
          ? 'No Luxonis OAK-D / USB camera connected'
          : 'Waiting for video stream...',
        detectedCount: 0,
        expectedCount: expectedDucks,
        difference: 0,
        foreignSpecies: [],
        foreignCount: 0,
      };
    }

    // When inference is not running: Standby / Ready / Live Stream (never flag count mismatch or false anomaly)
    if (!isRunning) {
      const duckCount = ducks.filter((d) => d.species === 'Duck' && d.statusEvent !== 'missing').length;
      return {
        isAnomaly: false,
        type: 'NONE',
        message: isCameraSource
          ? (hasActiveStream ? 'LIVE STREAM' : 'READY')
          : (hasActiveStream ? (duckCount > 0 ? 'READY' : 'VIDEO READY') : 'STANDBY'),
        subMessage: isCameraSource
          ? (hasActiveStream ? 'Live camera feed active • Click Start Inference' : 'Camera ready • Click Start Stream or Start Inference')
          : (hasActiveStream ? 'Video loaded • Click Start Inference to begin analysis' : 'Waiting for video stream...'),
        detectedCount: duckCount,
        expectedCount: expectedDucks,
        difference: 0,
        foreignSpecies: [],
        foreignCount: 0,
      };
    }

    if (isRunning && (isStarting || backendStatus === 'WARMING' || framesProcessed === 0)) {
      return {
        isAnomaly: false,
        type: 'NONE',
        message: 'WARMING',
        subMessage: 'Warming up AI engine and acquiring targets...',
        detectedCount: backendStats.detected_duck_count,
        expectedCount: expectedDucks,
        difference: 0,
        foreignSpecies: [],
        foreignCount: 0,
      };
    }

    // The backend count is the count for the current inference frame. Do not
    // fall back when it is zero: doing so can reuse gallery cards from the
    // prior frame and make the status disagree with the displayed count.
    const backendDetected = Number(backendStats.detected_duck_count);
    const detectedCount = Number.isFinite(backendDetected)
      ? backendDetected
      : ducks.filter((duck) => duck.species === 'Duck' && duck.statusEvent !== 'missing').length;
    const backendExpected = Number(backendStats.expected_duck_count);
    const expectedFromMl = Number.isFinite(backendExpected) && backendExpected > 0
      ? backendExpected
      : expectedDucks;
    const backendForeign = Number(backendStats.detected_other_toy_count);
    const foreignCount = Number.isFinite(backendForeign)
      ? backendForeign
      : ducks.filter((duck) => duck.species === 'Unknown' && !duck.provisional).length;
    const missingIds = Array.isArray(backendStats.missing_ids) ? backendStats.missing_ids : [];
    const hasMissingDuck = missingIds.length > 0 || ducks.some((duck) =>
      !duck.provisional && duck.statusEvent === 'missing'
    );
    const difference = detectedCount - expectedFromMl;
    const foreignSpecies = foreignCount > 0 ? ['Unknown'] : [];

    const isCountMismatch = detectedCount !== expectedFromMl;
    const hasForeign = foreignCount > 0;
    const hasHand = backendStatus === 'HAND' || backendStats.hand_detected === true;

    // This is the sole client verdict. A bare/stale backend status is not
    // enough: current-frame evidence must support it. This keeps every UI
    // surface in agreement when status packets and detection packets arrive
    // at slightly different times.
    const isAnomaly = hasHand || isCountMismatch || hasMissingDuck || hasForeign;
    let message = hasHand ? 'HAND DETECTED' : isAnomaly ? 'ANOMALY' : 'NORMAL';
    let subMessage = `${detectedCount} ducks detected in target area. Count matches expected (${expectedFromMl}).`;
    let type: AnomalyStatus['type'] = 'NONE';

    if (hasHand) {
      type = 'UNKNOWN';
      message = 'HAND DETECTED';
      subMessage = 'Hand detected in frame. Evaluation paused until hand is removed.';
    } else if (isAnomaly) {
      if (hasForeign && isCountMismatch) {
        type = 'FOREIGN_SPECIES';
        subMessage = `${foreignCount} non-duck detected & duck count: ${detectedCount}/${expectedFromMl} (${difference > 0 ? `+${difference}` : difference})`;
      } else if (hasForeign) {
        type = 'FOREIGN_SPECIES';
        subMessage = `Duck count normal (${detectedCount}/${expectedFromMl}), but ${foreignCount} non-duck detected.`;
      } else if (isCountMismatch) {
        if (difference > 0) {
          type = 'OVER_COUNT';
          const addedIds = backendStats.added_ids || [];
          const added = addedIds.length > 0 ? ` (Added: ${addedIds.join(', ')})` : '';
          subMessage = `+${difference} above expected count (${detectedCount} detected, ${expectedFromMl} expected)${added}`;
        } else {
          type = 'UNDER_COUNT';
          const missing = missingIds.length > 0 ? ` (Missing: ${missingIds.join(', ')})` : '';
          subMessage = `${Math.abs(difference)} missing ducks (${detectedCount} detected, ${expectedFromMl} expected)${missing}`;
        }
      } else if (hasMissingDuck) {
        type = 'MISSING_DUCK';
        const missing = missingIds.length > 0 ? `: ${missingIds.join(', ')}` : '';
        subMessage = `A tracked duck is missing from this frame${missing}.`;
      } else {
        type = 'FOREIGN_SPECIES';
        subMessage = `${foreignCount} non-duck detected.`;
      }
    }

    return {
      isAnomaly,
      type,
      message,
      subMessage,
      detectedCount,
      expectedCount: expectedFromMl,
      difference,
      foreignSpecies,
      foreignCount,
    };
  }, [
    hasActiveStream, isRunning, isStarting, expectedDucks, isCameraSource,
    backendStatus, framesProcessed, backendStats, ducks.length
  ]);

  const prevAnomalyRef = useRef(anomalyStatus.isAnomaly);
  const isAnomaly = anomalyStatus.isAnomaly;
  const subMessage = anomalyStatus.subMessage;

  useEffect(() => {
    if (!hasActiveStream || !isRunning) {
      prevAnomalyRef.current = false;
      return;
    }
    if (prevAnomalyRef.current !== isAnomaly) {
      if (isAnomaly) {
        playAnomalyAlertSound();
        addLog(`Anomalous Activity: ${subMessage}`, 'anomaly');
      } else {
        playNormalSound();
        addLog(`Status Normalized: Expected (${anomalyStatus.expectedCount}) matches Detected (${anomalyStatus.detectedCount})`, 'success');
      }
      prevAnomalyRef.current = isAnomaly;
    }
  }, [isAnomaly, subMessage, anomalyStatus.expectedCount, anomalyStatus.detectedCount, addLog, hasActiveStream, isRunning]);

  return {
    activeDucks,
    anomalyStatus,
  };
}
