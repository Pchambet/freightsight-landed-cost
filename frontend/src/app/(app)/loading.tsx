import { getTranslations } from "next-intl/server";

/**
 * Shown while a route segment's data is loading — every (app) page fetches server-side, so without
 * this the screen was a blank white flash between clicking a nav link and the page appearing.
 */
export default async function AppLoading() {
  const t = await getTranslations("common");
  return (
    <div role="status" aria-live="polite" className="animate-pulse">
      <span className="sr-only">{t("loading")}</span>
      <div className="flex items-center justify-between gap-4 mb-6">
        <div className="space-y-2">
          <div className="h-6 w-48 rounded bg-slate-200" />
          <div className="h-4 w-72 rounded bg-slate-100" />
        </div>
        <div className="h-9 w-28 rounded-md bg-slate-200" />
      </div>
      <div className="rounded-lg border border-slate-200 bg-white overflow-hidden">
        <div className="h-11 border-b border-slate-200 bg-slate-50" />
        {Array.from({ length: 6 }).map((_, i) => (
          <div key={i} className="h-12 border-b border-slate-100 last:border-0 flex items-center px-5">
            <div className="h-3.5 w-full max-w-sm rounded bg-slate-100" />
          </div>
        ))}
      </div>
    </div>
  );
}
