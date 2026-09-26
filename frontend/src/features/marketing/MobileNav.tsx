"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import AccountLink from "@/features/marketing/AccountLink";
import { Menu, X } from "lucide-react";

type NavItem = { href: string; label: string };

/**
 * Burger menu for the marketing header below md (768px). At 390px the nav links, "Se connecter"
 * and the booking button don't fit on one row: the button used to wrap onto three lines and
 * Sécurité, the page a DSI looks for, was unreachable without scrolling the whole page (A4 audit,
 * table A, row A4). Moving the links in here is what keeps the booking button on a single line.
 */
export default function MobileNav({ items }: { items: NavItem[] }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setOpen(false); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="md:hidden">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        aria-controls="mobile-nav-panel"
        aria-label={open ? "Fermer le menu" : "Ouvrir le menu"}
        className="p-2 -mr-2 rounded-md text-slate-600 hover:bg-slate-100"
      >
        {open ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
      </button>
      {open && (
        <div id="mobile-nav-panel" className="absolute inset-x-0 top-14 border-b border-slate-200 bg-white shadow-sm">
          <nav className="max-w-6xl mx-auto px-5 py-3 flex flex-col text-sm">
            {items.map((n) => (
              <Link key={n.href} href={n.href} onClick={() => setOpen(false)} className="px-2 py-2.5 rounded-md text-slate-600 hover:bg-slate-100 hover:text-slate-900">
                {n.label}
              </Link>
            ))}
            <AccountLink onClick={() => setOpen(false)} className="px-2 py-2.5 rounded-md text-slate-600 hover:bg-slate-100 hover:text-slate-900" />
          </nav>
        </div>
      )}
    </div>
  );
}
