export type ThemeMode = 'pond-light' | 'pond-dark' | 'nature';

export type StreamSourceType = 'sample-pond' | 'oak-camera' | 'uploaded-video' | 'webcam';

export interface DuckEntity {
  id: string;
  species: 'Duck' | 'Unknown' | 'Hand';
  isAnomaly: boolean;
  confidence: number;
  x: number; // percentage 0 - 100
  y: number; // percentage 0 - 100
  width: number;
  height: number;
  vx: number;
  vy: number;
  heading: number;
  targetX?: number;
  targetY?: number;
  quackTime?: number;
  thumbnail?: string;
  provisional?: boolean;
  statusEvent?: 'added' | 'missing' | 'other_present' | 'confirmed' | 'hand_present';
  handDetected?: boolean;
}

export type AnomalyType = 'NONE' | 'OVER_COUNT' | 'UNDER_COUNT' | 'FOREIGN_SPECIES' | 'OUT_OF_BOUNDS' | 'UNKNOWN';

export interface AnomalyStatus {
  isAnomaly: boolean;
  type: AnomalyType;
  message: string;
  subMessage: string;
  detectedCount: number;
  expectedCount: number;
  difference: number;
  foreignSpecies: string[];
  foreignCount?: number;
}

export interface CameraConfig {
  id?: number;
  sourceName: string;
  resolution: '1920x1080' | '1280x720';
  targetFps: number;
  rotationAngle?: number;
  controlMode?: 'auto' | 'manual';
  exposure: number; // 0 - 100
  gain?: number;
  focus?: number;
  brightness: number; // -50 to +50
  contrast: number; // 0 to 100
  iso: number;
  autoFocus: boolean;
  autoExposure?: boolean;
  connected: boolean;
  connectionQuality: 'Excellent' | 'Good' | 'Fair' | 'Poor';
  ipAddress: string;
}

export interface ProcessStep {
  id: string;
  label: string;
  status: 'completed' | 'active' | 'pending' | 'error';
  timestamp?: string;
}

export interface LogEntry {
  id: string;
  timestamp: string;
  message: string;
  level: 'info' | 'success' | 'warn' | 'error' | 'anomaly';
}

export interface DetectionMetrics {
  fps: number;
  inferenceTimeMs: number;
  framesProcessed: number;
  uptimeSeconds: number;
  avgConfidence: number;
  speciesCounts: Record<string, number>;
}
