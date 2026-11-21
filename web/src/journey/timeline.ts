// One scroll position, 0..1, drives everything in the journey: the camera, the cities and the words on top.
export const T = {
  intro: [0, 0.07], rise: [0.07, 0.17], traffic: [0.17, 0.26], decide: [0.26, 0.42], batch: [0.42, 0.52],
  day: [0.52, 0.66], chicago: [0.66, 0.79], cambridge: [0.79, 0.92], outro: [0.92, 1],
} as const;
export type ChapterKey = keyof typeof T;
export const CHAPTERS: { key: ChapterKey; label: string }[] = [
  { key: 'intro', label: 'Streets' }, { key: 'rise', label: 'Buildings' }, { key: 'traffic', label: 'Fleet' },
  { key: 'decide', label: 'One decision' }, { key: 'batch', label: 'Whole city' }, { key: 'day', label: 'A real day' },
  { key: 'chicago', label: 'Chicago' }, { key: 'cambridge', label: 'Any city' }, { key: 'outro', label: 'Your turn' },
];
// Inside "decide": the rider appears, routes are scored, ORCA decides, the chosen car drives.
export const DECIDE = { rider: 0.275, routes: [0.295, 0.345] as const, decide: 0.36, drive: [0.372, 0.412] as const };
export const DAY = [0.54, 0.645] as const;   // the 24 hours play across this range

export const smooth = (a: number, b: number, x: number) => { const t = Math.min(1, Math.max(0, (x - a) / (b - a))); return t * t * (3 - 2 * t); };
export const lerp = (a: number, b: number, t: number) => a + (b - a) * t;
export const chapterAt = (p: number) => CHAPTERS.findIndex((c) => p >= T[c.key][0] && p < T[c.key][1]);
