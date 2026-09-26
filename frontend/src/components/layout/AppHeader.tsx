"use client";

import { OrganizationSwitcher, UserButton } from "@clerk/nextjs";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useTranslations } from "next-intl";
import { useEffect, useRef, useState } from "react";
import { Bell, LifeBuoy, Menu, Ship, X } from "lucide-react";
import AppNav from "@/components/layout/AppNav";
import CommandPalette from "@/components/layout/CommandPalette";
import LocaleSwitcher from "@/components/layout/LocaleSwitcher";
import { useFocusTrap } from "@/hooks/useFocusTrap";
import { site } from "@/lib/site";

/** mailto: for the header's "Aide" entry — organisation and current page prefilled, never guessed (item 9). */
function helpMailto(subject: string, body: string): string {
  return `mailto:${site.contactEmail}?${[`subject=${encodeURIComponent(subject)}`, `body=${encodeURIComponent(body)}`].join("&")}`;
}

export default function AppHeader({ unreadAlerts = 0, orgName }: { unreadAlerts?: number; orgName?: string }) {
  const t = useTranslations();
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const menuBtnRef = useRef<HTMLButtonElement>(null);
  const closeBtnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const org = orgName || "—";
  // An action (not a static Link href) so the mailto is built at click time, with the real current
  // URL — reading window.location during render would not match the server-rendered markup.
  const openHelp = () => {
    window.location.href = helpMailto(t("help.subject", { org }), t("help.body", { org, url: window.location.href }));
  };

  // A navigation closes the drawer: adjust state during render, not in an effect.
  const [openedOn, setOpenedOn] = useState(pathname);
  if (open && openedOn !== pathname) {
    setOpenedOn(pathname);
    setOpen(false);
  }
  const openDrawer = () => {
    setOpenedOn(pathname);
    setOpen(true);
  };

  useEffect(() => {
    document.body.style.overflow = open ? "hidden" : "";
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  useEffect(() => {
    if (open) closeBtnRef.current?.focus();
  }, [open]);

  useFocusTrap(open, panelRef, menuBtnRef);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    if (open) window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  const brand = (dark: boolean) => (
    <Link href={site.appHome} className="flex items-center gap-2 shrink-0 rounded-md focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400">
      <span className="bg-blue-600 p-1.5 rounded-md">
        <Ship className="w-4 h-4 text-white" aria-hidden />
      </span>
      <span className={`text-lg font-semibold tracking-tight ${dark ? "text-white" : "text-slate-900"}`}>
        Freight<span className={dark ? "text-blue-400" : "text-blue-600"}>Sight</span>
      </span>
    </Link>
  );

  return (
    <>
      {/* A desk gets the sidebar; anything narrower gets the same navigation in a drawer. */}
      <aside className="hidden lg:flex fixed inset-y-0 left-0 z-40 w-60 flex-col gap-6 bg-navy-900 px-3 pt-4 pb-3 print:hidden">
        <div className="px-1.5">{brand(true)}</div>
        <AppNav unreadAlerts={unreadAlerts} onHelp={openHelp} />
      </aside>

      <header className="sticky top-0 z-30 bg-white/90 backdrop-blur border-b border-slate-200 print:hidden">
        <div className="px-4 sm:px-6 lg:px-8 h-14 flex items-center gap-3 sm:gap-4">
          <button
            ref={menuBtnRef}
            type="button"
            className="lg:hidden p-2 -ml-2 rounded-md text-slate-600 hover:bg-slate-100 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
            aria-expanded={open}
            aria-controls="mobile-nav"
            onClick={openDrawer}
          >
            <Menu className="w-5 h-5" aria-hidden />
            <span className="sr-only">{t("nav.openMenu")}</span>
          </button>
          <div className="lg:hidden hidden sm:block">{brand(false)}</div>

          <div className="min-w-0 flex-1">
            <CommandPalette />
          </div>

          {/* min-w-0 lets this group shrink below its content's natural width instead of forcing the header to
              scroll sideways at 390 px (audit B43); the org switcher itself is the part that used to overflow. */}
          <div className="ml-auto flex items-center gap-1 sm:gap-2 text-xs text-slate-500 shrink-0 min-w-0">
            <div className="hidden sm:block"><LocaleSwitcher /></div>
            <Link
              href="/alerts"
              aria-label={t("alerts.bell", { count: unreadAlerts })}
              className="relative p-1.5 rounded-md text-slate-500 hover:bg-slate-100 hover:text-slate-900 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 focus-visible:ring-offset-2 shrink-0"
            >
              <Bell className="w-4 h-4" />
              {unreadAlerts > 0 ? (
                <span className="absolute -top-0.5 -right-0.5 min-w-4 h-4 px-1 rounded-full bg-amber-500 text-white text-[10px] font-semibold flex items-center justify-center tabular-nums">
                  {unreadAlerts > 99 ? "99+" : unreadAlerts}
                </span>
              ) : null}
            </Link>
            <div className="min-w-0 max-w-[84px] sm:max-w-none overflow-hidden shrink">
              <OrganizationSwitcher
                hidePersonal
                afterSelectOrganizationUrl={site.appHome}
                afterCreateOrganizationUrl={site.appHome}
                appearance={{ elements: { organizationSwitcherTrigger: "max-w-full" } }}
              />
            </div>
            <div className="shrink-0">
              <UserButton>
                <UserButton.MenuItems>
                  <UserButton.Action label={t("nav.help")} onClick={openHelp} labelIcon={<LifeBuoy className="w-4 h-4" />} />
                </UserButton.MenuItems>
              </UserButton>
            </div>
            {orgName ? <span className="sr-only">{orgName}</span> : null}
          </div>
        </div>
      </header>

      {open ? (
        <div className="lg:hidden fixed inset-0 z-50">
          <button type="button" className="absolute inset-0 bg-navy-950/50" aria-label={t("nav.closeMenu")} onClick={() => setOpen(false)} />
          <div
            ref={panelRef}
            id="mobile-nav"
            role="dialog"
            aria-modal="true"
            aria-label={t("nav.main")}
            className="absolute inset-y-0 left-0 w-[min(100vw-3rem,18rem)] bg-navy-900 shadow-xl flex flex-col gap-5 px-3 pt-3 pb-3"
          >
            <div className="flex items-center justify-between pl-1.5">
              {brand(true)}
              <button
                ref={closeBtnRef}
                type="button"
                className="p-2 rounded-md text-slate-300 hover:bg-white/10 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-400"
                onClick={() => setOpen(false)}
              >
                <X className="w-5 h-5" aria-hidden />
                <span className="sr-only">{t("nav.closeMenu")}</span>
              </button>
            </div>
            <AppNav unreadAlerts={unreadAlerts} onNavigate={() => setOpen(false)} onHelp={openHelp} />
            <div className="sm:hidden border-t border-white/10 pt-3 px-1.5 [&_label]:text-slate-300 [&_select]:text-slate-200">
              <LocaleSwitcher />
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
