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

    const mimeTypes = ['video/webm;codecs=vp9', 'video/webm;codecs=vp8', 'video/webm'];
    let selectedMimeType = '';
    for (const mime of mimeTypes) {
      if (MediaRecorder.isTypeSupported(mime)) {
        selectedMimeType = mime;
        break;
      }
    }
    if (!selectedMimeType) return;

    const stream = canvas.captureStream(30);
    const mediaRecorder = new MediaRecorder(stream, { mimeType: selectedMimeType });
    mediaRecorderRef.current = mediaRecorder;
    chunksRef.current = [];

    mediaRecorder.ondataavailable = (e) => {
      if (e.data && e.data.size > 0) chunksRef.current.push(e.data);
    };

    mediaRecorder.onstop = () => {
      const blob = new Blob(chunksRef.current, { type: selectedMimeType });
      const url = URL.createObjectURL(blob);
      setRecordedVideoUrl(url);
      
      const ext = selectedMimeType.includes('vp9') || selectedMimeType.includes('vp8') || selectedMimeType.includes('webm') ? 'webm' : 'mp4';
      const file = new File([blob], `recorded_camera_${Date.now()}.${ext}`, { type: selectedMimeType });
      setRecordedFile(file);
      stream.getTracks().forEach(track => track.stop());
    };

    mediaRecorder.start();
    setIsRecording(true);
    setRecordedVideoUrl(null);
    setRecordedFile(null);

    let lastTime = performance.now();
    const drawLoop = (time: number) => {
      if (time - lastTime >= 1000 / 30) {
        ctx.drawImage(imgElement, 0, 0, canvas.width, canvas.height);
        lastTime = time;
      }
      animationFrameRef.current = requestAnimationFrame(drawLoop);
    };
    animationFrameRef.current = requestAnimationFrame(drawLoop);

  }, [isRecording]);

  const stopRecording = useCallback(() => {
    if (!isRecording) return;
    if (mediaRecorderRef.current && mediaRecorderRef.current.state !== 'inactive') {
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
