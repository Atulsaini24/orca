import Lenis from 'lenis';
import { useEffect } from 'react';

/** Inertial smooth scrolling (Lenis) for the whole page; in-page links glide to their section. Off for reduced motion. */
export function useSmoothScroll() {
  useEffect(() => {
    if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const lenis = new Lenis({ lerp: 0.1, smoothWheel: true });
    document.documentElement.classList.add('lenis');
    let raf = 0;
    const loop = (t: number) => { lenis.raf(t); raf = requestAnimationFrame(loop); };
    raf = requestAnimationFrame(loop);
    const onClick = (e: MouseEvent) => {
      const a = (e.target as HTMLElement).closest('a[href^="#"]') as HTMLAnchorElement | null;
      if (!a || a.hash.length < 2) return;
      const el = document.querySelector(a.hash); if (!el) return;
      e.preventDefault(); lenis.scrollTo(el as HTMLElement, { offset: -72 }); history.replaceState(null, '', a.hash);
    };
    document.addEventListener('click', onClick);
    return () => { cancelAnimationFrame(raf); document.removeEventListener('click', onClick); lenis.destroy(); document.documentElement.classList.remove('lenis'); };
  }, []);
}
