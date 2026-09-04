import { useRef, useEffect } from 'react';
import type { DuckEntity } from '../types';
import { playWaterDropSound, playDuckQuackSound } from '../utils/audio';

export const useRippleEffect = (
  canvasRef: React.RefObject<HTMLCanvasElement | null>,
  ducks: DuckEntity[],
  selectedDuckId: string | null,
  onSelectDuck: (id: string | null) => void
) => {
  const ripplesRef = useRef<{ x: number; y: number; radius: number; opacity: number }[]>([]);
  const ducksRef = useRef<DuckEntity[]>(ducks);
  ducksRef.current = ducks;

  const startRippleAnimation = () => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;
    
    let animationFrameId: number;
    
    const render = () => {
      if (ripplesRef.current.length === 0) {
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        return; // stop RAF loop
      }
      
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      const updatedRipples: { x: number; y: number; radius: number; opacity: number }[] = [];
      
      for (let i = 0; i < ripplesRef.current.length; i++) {
        const r = ripplesRef.current[i];
        const newRadius = r.radius + 0.9;
        const newOpacity = r.opacity - 0.015;
        if (newOpacity > 0) {
          updatedRipples.push({
            x: r.x,
            y: r.y,
            radius: newRadius,
            opacity: newOpacity,
          });

          ctx.save();
          ctx.beginPath();
          ctx.arc(r.x, r.y, newRadius, 0, Math.PI * 2);
          ctx.strokeStyle = `rgba(186, 230, 253, ${newOpacity * 0.6})`;
          ctx.lineWidth = 2;
          ctx.stroke();
          ctx.restore();
        }
      }
      ripplesRef.current = updatedRipples;
      animationFrameId = requestAnimationFrame(render);
    };
    
    render();
  };

  useEffect(() => {
    // Cleanup if unmounted while animating
    return () => {
      ripplesRef.current = [];
    };
  }, []);

  const handleCanvasClick = (e: React.MouseEvent<HTMLDivElement>, containerRef: React.RefObject<HTMLDivElement | null>) => {
    if (!containerRef.current) return;
    const rect = containerRef.current.getBoundingClientRect();
    const clickX = e.clientX - rect.left;
    const clickY = e.clientY - rect.top;

    const xPercent = (clickX / rect.width) * 100;
    const yPercent = (clickY / rect.height) * 100;

    const wasEmpty = ripplesRef.current.length === 0;

    ripplesRef.current.push({
      x: clickX,
      y: clickY,
      radius: 5,
      opacity: 0.85,
    });
    if (ripplesRef.current.length > 12) {
      ripplesRef.current.shift();
    }
    
    if (wasEmpty && canvasRef.current) {
      startRippleAnimation();
    }

    playWaterDropSound();

    const clickedDuck = ducks.find(
      (d) =>
        xPercent >= d.x &&
        xPercent <= d.x + d.width &&
        yPercent >= d.y &&
        yPercent <= d.y + d.height
    );

    if (clickedDuck) {
      playDuckQuackSound();
      onSelectDuck(clickedDuck.id === selectedDuckId ? null : clickedDuck.id);
    } else {
      if (selectedDuckId) {
        onSelectDuck(null);
      }
    }
  };

  return { handleCanvasClick };
};
