import Link from "next/link";
import { ArrowLeft, ChevronRight } from "lucide-react";

/**
 * Wayfinding on detail pages: a back link, and the current page name only when it says something
 * the h1 right below does not already say — repeating it read as a duplicated title
 * (e.g. "Importer des commandes" as both the breadcrumb and the h1, one line apart).
 */
export default function Breadcrumb({ backHref, backLabel, current }: { backHref: string; backLabel: string; current?: string }) {
  return (
    <nav aria-label="Breadcrumb" className="flex items-center text-sm text-slate-500 mb-4 min-w-0">
      <Link href={backHref} className="hover:text-slate-900 flex items-center gap-1.5 shrink-0">
        <ArrowLeft className="w-4 h-4" aria-hidden />
        {backLabel}
      </Link>
      {current ? (
        <>
          <ChevronRight className="w-4 h-4 mx-2 text-slate-300 shrink-0" aria-hidden />
          <span className="text-slate-900 truncate" aria-current="page">{current}</span>
        </>
      ) : null}
    </nav>
  );
}
