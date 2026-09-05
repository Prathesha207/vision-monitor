import { useRef, useEffect } from 'react';
import type { DuckEntity } from '../types';
import { playWaterDropSound, playDuckQuackSound } from '../utils/audio';

export const useRippleEffect = (
  canvasRef: React.RefObject<HTMLCanvasElement | null>,
  ducks: DuckEntity[],
  selectedDuckId: string | null,
  onSelectDuck: (id: string | null) => void,
  showAllBoxes: boolean = false,
  isSceneAnomaly: boolean = false
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
      
      ripplesRef.current.forEach((ripple) => {
        ripple.radius += 2.5;
        ripple.opacity -= 0.025;
        
        if (ripple.opacity > 0) {
          ctx.beginPath();
          ctx.arc(ripple.x, ripple.y, ripple.radius, 0, Math.PI * 2);
          ctx.strokeStyle = `rgba(56, 189, 248, ${ripple.opacity})`;
          ctx.lineWidth = 2;
          ctx.stroke();
          updatedRipples.push(ripple);
        }
      });
      
      ripplesRef.current = updatedRipples;
      animationFrameId = requestAnimationFrame(render);
    };
    
    animationFrameId = requestAnimationFrame(render);
  };

  useEffect(() => {
    // Cleanup if unmounted while animating
    return () => {
      ripplesRef.current = [];
    };
  }, []);

  const handleCanvasClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const container = e.currentTarget;
    if (!container) return;
    const rect = container.getBoundingClientRect();
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

    // Only hit-test ducks that are currently visible to the user on the screen.
    // Clicking on the video canvas should NEVER secretly select an invisible normal duck!
    const clickedDuck = ducks.find(
      (d) =>
        (showAllBoxes || d.provisional || d.isAnomaly || d.statusEvent === 'missing' || isSceneAnomaly) &&
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
