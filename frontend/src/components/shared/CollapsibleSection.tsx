import { ChevronDown } from "lucide-react";

/** Secondary content: visible on demand so the primary story stays above the fold. */
export default function CollapsibleSection({
  title,
  hint,
  defaultOpen = false,
  children,
  id,
}: {
  title: string;
  hint?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
  id?: string;
}) {
  return (
    <details id={id} open={defaultOpen} className="group bg-white rounded-lg border border-slate-200 overflow-hidden">
      <summary className="flex items-center justify-between gap-3 px-5 py-3.5 cursor-pointer list-none [&::-webkit-details-marker]:hidden hover:bg-slate-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-blue-500">
        <div>
          <span className="text-sm font-semibold text-slate-800">{title}</span>
          {hint ? <span className="block text-xs text-slate-500 mt-0.5">{hint}</span> : null}
        </div>
        <ChevronDown className="w-4 h-4 text-slate-500 shrink-0 transition-transform group-open:rotate-180" />
      </summary>
      <div className="border-t border-slate-100">{children}</div>
    </details>
  );
}
