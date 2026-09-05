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
      };
    }

    // When inference is not running and no detections exist yet: Standby/Ready (difference is 0, not -18!)
    if (!isRunning && framesProcessed === 0 && ducks.length === 0) {
      return {
        isAnomaly: false,
        type: 'NONE',
        message: isCameraSource ? 'READY' : 'STANDBY',
        subMessage: isCameraSource
          ? 'Camera connected • Click Start Stream or Start Inference'
          : 'Video loaded • Click Start Inference to begin analysis',
        detectedCount: 0,
        expectedCount: expectedDucks,
        difference: 0,
        foreignSpecies: [],
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
      };
    }

    const detectedCount = backendStats.detected_duck_count > 0 ? backendStats.detected_duck_count : ducks.length;
    const foreignCount = backendStats.detected_other_toy_count;
    const expectedFromMl = backendStats.expected_duck_count > 0 ? backendStats.expected_duck_count : expectedDucks;
    const difference = detectedCount - expectedFromMl;
    const foreignSpecies = foreignCount > 0 ? ['Unknown'] : [];

    const isCountMismatch = detectedCount !== expectedFromMl;
    const hasForeign = foreignCount > 0;
    
    const isAnomaly = backendStatus === 'ANOMALY' || backendStatus === 'HAND';
    let message = backendStatus === 'HAND' ? 'HAND DETECTED' : 'NORMAL';
    let subMessage = `${detectedCount} ducks detected in target area. Count matches expected (${expectedFromMl}).`;
    let type: AnomalyStatus['type'] = 'NONE';

    if (backendStatus === 'HAND') {
      type = 'UNKNOWN';
      message = 'HAND DETECTED';
      subMessage = 'Hand detected in frame. Evaluation paused until hand is removed.';
    } else if (backendStatus === 'ANOMALY') {
      message = 'ANOMALY';
      
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
          const missingIds = backendStats.missing_ids || [];
          const missing = missingIds.length > 0 ? ` (Missing: ${missingIds.join(', ')})` : '';
          subMessage = `${Math.abs(difference)} missing ducks (${detectedCount} detected, ${expectedFromMl} expected)${missing}`;
        }
      } else {
        type = 'UNKNOWN';
        subMessage = 'AI Engine flagged an anomaly (Check stream).';
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
