/**
 * A trend at the size of a word: no axis, no labels, the last point marked. The line stretches with
 * its box (`preserveAspectRatio="none"` + a non-scaling stroke), so it needs no measuring and
 * renders on the server. The end dot is a box laid over the SVG, which keeps it round at any width.
 */
export default function Sparkline({ values, className }: { values: number[]; className?: string }) {
  const pts = values.filter((v) => Number.isFinite(v));
  if (pts.length < 2) return null;
  const lo = Math.min(...pts);
  const hi = Math.max(...pts);
  const span = hi - lo || 1;
  // 8 % of headroom on both sides so the stroke and the end dot are never cut by the box.
  const y = (v: number) => 92 - ((v - lo) / span) * 84;
  const x = (i: number) => (i / (pts.length - 1)) * 100;
  const d = pts.map((v, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(" ");
  return (
    <div className={`relative ${className ?? "h-8 w-24"}`} aria-hidden>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" className="absolute inset-0 w-full h-full overflow-visible">
        <path d={d} fill="none" className="stroke-slate-400" strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      </svg>
      <span className="absolute w-2 h-2 -translate-x-1/2 -translate-y-1/2 rounded-full bg-viz-freight ring-2 ring-white" style={{ left: "100%", top: `${y(pts[pts.length - 1])}%` }} />
    </div>
  );
}
