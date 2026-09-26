import { getTranslations } from "next-intl/server";
import AppHeader from "@/components/layout/AppHeader";
import HashOpener from "@/components/layout/HashOpener";
import SampleDataBanner from "@/components/layout/SampleDataBanner";

export async function AppShell({ children, orgName, unreadAlerts = 0, hasSampleData = false }: { children: React.ReactNode; orgName?: string; unreadAlerts?: number; hasSampleData?: boolean }) {
  const t = await getTranslations();
  return (
    // overflow-x-hidden is the backstop for every page in this shell: several widgets (a header icon
    // near the viewport edge, a hover tooltip centered under a narrow card — see audit B11/B43) can
    // lay out wider than the viewport at 390 px without ever being visible; this guarantees the page
    // itself never scrolls sideways because of them. A table that genuinely needs to scroll still
    // does, inside its own `overflow-x-auto` container.
    <div className="min-h-screen overflow-x-clip lg:pl-60 print:pl-0">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:absolute focus:top-2 focus:left-2 focus:z-[60] focus:px-4 focus:py-2 focus:bg-white focus:text-slate-900 focus:rounded-md focus:ring-2 focus:ring-blue-500"
      >
        {t("common.skipToContent")}
      </a>
      <AppHeader unreadAlerts={unreadAlerts} orgName={orgName} />
      <HashOpener />
      {hasSampleData ? <SampleDataBanner /> : null}
      <main id="main-content" className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-8">{children}</main>
    </div>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: React.ReactNode; subtitle?: React.ReactNode; actions?: React.ReactNode }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-end justify-between gap-4 mb-6">
      <div className="min-w-0">
        <h1 className="text-2xl font-semibold">{title}</h1>
        {subtitle ? <p className="text-slate-500 mt-1 text-sm">{subtitle}</p> : null}
      </div>
      {actions ? <div className="shrink-0">{actions}</div> : null}
    </div>
  );
}

export function Card({ title, right, children, className }: { title?: React.ReactNode; right?: React.ReactNode; children: React.ReactNode; className?: string }) {
  return (
    <div className={`bg-white rounded-lg border border-slate-200 overflow-hidden ${className ?? ""}`}>
      {title ? (
        <div className="px-5 py-3.5 border-b border-slate-200 flex items-center justify-between gap-2">
          <h2 className="text-sm font-semibold">{title}</h2>
          {right}
        </div>
      ) : null}
      {children}
    </div>
  );
}

export const th = "px-4 py-2.5 text-left text-xs font-medium text-slate-500 uppercase tracking-wider whitespace-nowrap";
export const thRight = `${th} text-right`;
export const td = "px-4 py-2.5 whitespace-nowrap";
export const tdRight = `${td} text-right tabular-nums`;
export const input = "w-full border border-slate-300 rounded-md px-3 py-2 text-sm bg-white focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none";
export const label = "block text-xs font-medium text-slate-600 mb-1";
export const btnPrimary = "inline-flex items-center justify-center bg-slate-900 hover:bg-slate-800 disabled:opacity-50 text-white text-sm font-medium py-2 px-4 rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-500 focus-visible:ring-offset-2";
export const btnSecondary = "inline-flex items-center justify-center bg-white hover:bg-slate-50 disabled:opacity-50 text-slate-700 text-sm font-medium py-2 px-4 rounded-md border border-slate-300 focus:outline-none focus-visible:ring-2 focus-visible:ring-slate-500 focus-visible:ring-offset-2";
