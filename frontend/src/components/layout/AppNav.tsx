"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { LifeBuoy, Settings } from "lucide-react";
import { NAV_GROUPS } from "@/components/layout/navConfig";

const row = "group flex items-center gap-2.5 rounded-md px-2.5 py-2 text-sm transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400";
const idle = "text-slate-300 hover:bg-white/5 hover:text-white";
const current = "bg-white/10 text-white font-medium";

/** The application's navigation, on the navy ground: the sidebar on a desk, the drawer on a phone. */
export default function AppNav({ onNavigate, unreadAlerts = 0, onHelp }: { onNavigate?: () => void; unreadAlerts?: number; onHelp: () => void }) {
  const t = useTranslations("nav");
  const pathname = usePathname();
  const settingsActive = pathname === "/settings";

  return (
    <nav className="flex flex-1 flex-col gap-5 min-h-0" aria-label={t("main")}>
      <div className="flex-1 overflow-y-auto space-y-5 pr-1">
        {NAV_GROUPS.map((g) => (
          <div key={g.key}>
            <div className="px-2.5 mb-1 text-[11px] font-medium uppercase tracking-wider text-slate-400">{t(g.key)}</div>
            <ul className="space-y-0.5">
              {g.items.map((n) => {
                const active = n.match(pathname);
                const badge = n.badge && unreadAlerts > 0 ? (unreadAlerts > 99 ? "99+" : unreadAlerts) : null;
                return (
                  <li key={n.href}>
                    <Link href={n.href} onClick={onNavigate} aria-current={active ? "page" : undefined} className={`${row} ${active ? current : idle}`}>
                      <n.icon className={`w-4 h-4 shrink-0 ${active ? "text-blue-400" : "text-slate-500 group-hover:text-slate-300"}`} aria-hidden />
                      <span className="flex-1 truncate">{t(n.key)}</span>
                      {badge ? (
                        <span className="min-w-5 h-5 px-1.5 rounded-full bg-amber-400 text-navy-950 text-[11px] font-semibold flex items-center justify-center tabular-nums">{badge}</span>
                      ) : null}
                    </Link>
                  </li>
                );
              })}
            </ul>
          </div>
        ))}
      </div>

      <ul className="space-y-0.5 border-t border-white/10 pt-3">
        <li>
          <Link href="/settings" onClick={onNavigate} aria-current={settingsActive ? "page" : undefined} className={`${row} ${settingsActive ? current : idle}`}>
            <Settings className={`w-4 h-4 shrink-0 ${settingsActive ? "text-blue-400" : "text-slate-500 group-hover:text-slate-300"}`} aria-hidden />
            {t("settings")}
          </Link>
        </li>
        <li>
          <button type="button" onClick={onHelp} className={`${row} ${idle} w-full text-left`}>
            <LifeBuoy className="w-4 h-4 shrink-0 text-slate-500 group-hover:text-slate-300" aria-hidden />
            {t("help")}
          </button>
        </li>
      </ul>
    </nav>
  );
}
