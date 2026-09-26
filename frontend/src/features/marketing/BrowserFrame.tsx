/** A window around a live piece of the product: enough chrome to say "this is the application". */
export default function BrowserFrame({ path, children, className }: { path: string; children: React.ReactNode; className?: string }) {
  return (
    <div className={`rounded-xl border border-slate-200 bg-slate-50 shadow-[0_24px_70px_-24px_rgba(12,21,41,.35)] overflow-hidden ${className ?? ""}`}>
      <div className="flex items-center gap-2 border-b border-slate-200 bg-white px-3.5 py-2.5" aria-hidden>
        <span className="flex gap-1.5">
          <span className="w-2.5 h-2.5 rounded-full bg-slate-200" />
          <span className="w-2.5 h-2.5 rounded-full bg-slate-200" />
          <span className="w-2.5 h-2.5 rounded-full bg-slate-200" />
        </span>
        <span className="mx-auto max-w-[70%] truncate rounded-md bg-slate-100 px-3 py-0.5 text-[11px] text-slate-600">freightsight · {path}</span>
      </div>
      <div className="p-4 sm:p-5">{children}</div>
    </div>
  );
}
