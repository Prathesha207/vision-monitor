import { useState, useRef, useCallback } from 'react';

export function useRecording() {
  const [isRecording, setIsRecording] = useState(false);
  const [recordedVideoUrl, setRecordedVideoUrl] = useState<string | null>(null);
  const [recordedFile, setRecordedFile] = useState<File | null>(null);
  
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<BlobPart[]>([]);
  const animationFrameRef = useRef<number | null>(null);
  const hiddenCanvasRef = useRef<HTMLCanvasElement | null>(null);

  const startRecording = useCallback((imgElement: HTMLImageElement, width: number, height: number) => {
    if (isRecording) return;
    
    if (!hiddenCanvasRef.current) {
      hiddenCanvasRef.current = document.createElement('canvas');
    }
    const canvas = hiddenCanvasRef.current;
    canvas.width = width || 1920;
    canvas.height = height || 1080;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    // Draw initial frame immediately so captureStream has valid pixels right away
    try {
      if (imgElement && imgElement.naturalWidth > 0) {
        ctx.drawImage(imgElement, 0, 0, canvas.width, canvas.height);
      } else {
        ctx.fillStyle = '#000000';
        ctx.fillRect(0, 0, canvas.width, canvas.height);
      }
    } catch (e) {
      console.warn('[RECORD] Initial canvas draw notice:', e);
    }

    const mimeTypes = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm', 'video/mp4'];
    let selectedMimeType = '';
    for (const mime of mimeTypes) {
      if (MediaRecorder.isTypeSupported(mime)) {
        selectedMimeType = mime;
        break;
      }
    }
    if (!selectedMimeType) {
      selectedMimeType = 'video/webm';
    }

    const stream = canvas.captureStream(30);
    const mediaRecorder = new MediaRecorder(stream, { mimeType: selectedMimeType });
    mediaRecorderRef.current = mediaRecorder;
    chunksRef.current = [];

    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) {
        chunksRef.current.push(e.data);
      }
    };

    mediaRecorder.onstop = () => {
      if (animationFrameRef.current) {
        cancelAnimationFrame(animationFrameRef.current);
        animationFrameRef.current = null;
      }
      stream.getTracks().forEach(track => track.stop());

      const blob = new Blob(chunksRef.current, { type: selectedMimeType });
      if (blob.size < 5000) {
        console.warn(`[RECORD] Recording was too short or empty (${blob.size} bytes). Discarding.`);
        return;
      }

      const url = URL.createObjectURL(blob);
      setRecordedVideoUrl(url);
      
      const ext = selectedMimeType.includes('webm') ? 'webm' : 'mp4';
      const file = new File([blob], `recorded_camera_${Date.now()}.${ext}`, { type: selectedMimeType });
      setRecordedFile(file);
    };

    // Pass timeslice (250ms) so dataavailable fires continuously during recording
    mediaRecorder.start(250);
    setIsRecording(true);
    setRecordedVideoUrl(null);
    setRecordedFile(null);

    let lastTime = performance.now();
    const drawLoop = (time: number) => {
      if (time - lastTime >= 1000 / 30) {
        try {
          if (imgElement && imgElement.complete && imgElement.naturalWidth > 0) {
            ctx.drawImage(imgElement, 0, 0, canvas.width, canvas.height);
          }
        } catch (e) {
          // Ignore transient cross-origin or render glitches
        }
        lastTime = time;
      }
      animationFrameRef.current = requestAnimationFrame(drawLoop);
    };
    animationFrameRef.current = requestAnimationFrame(drawLoop);

  }, [isRecording]);

  const stopRecording = useCallback(() => {
    if (!isRecording) return;
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
      try {
        mediaRecorderRef.current.requestData();
      } catch {}
      mediaRecorderRef.current.stop();
    }
    if (animationFrameRef.current) {
      cancelAnimationFrame(animationFrameRef.current);
      animationFrameRef.current = null;
    }
    setIsRecording(false);
  }, [isRecording]);

  const clearRecording = useCallback(() => {
    if (recordedVideoUrl) URL.revokeObjectURL(recordedVideoUrl);
    setRecordedVideoUrl(null);
    setRecordedFile(null);
  }, [recordedVideoUrl]);

  return { isRecording, recordedVideoUrl, recordedFile, startRecording, stopRecording, clearRecording };
}
