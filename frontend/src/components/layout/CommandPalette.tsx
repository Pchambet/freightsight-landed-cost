"use client";

import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { ClipboardList, Container, CornerDownLeft, FileUp, Plus, Receipt, Search, Settings, Ship, Tags, Truck, Upload, type LucideIcon } from "lucide-react";
import { NAV_GROUPS } from "@/components/layout/navConfig";
import { skuHref } from "@/features/skus/href";
import { useFocusTrap } from "@/hooks/useFocusTrap";

/** One row of `GET /api/v1/search` — see the route handler at app/(app)/api/search. */
export type SearchHit = {
  kind: "container" | "purchase_order" | "sku" | "invoice" | "supplier" | "shipment";
  id: string | null;
  label: string;
  sublabel: string | null;
  sku: string | null;
};

type Entry = { id: string; group: "results" | "pages" | "actions"; icon: LucideIcon; label: string; sub?: string; mono?: boolean; href: string };

const HIT_ICON: Record<SearchHit["kind"], LucideIcon> = { container: Container, purchase_order: ClipboardList, sku: Tags, invoice: Receipt, supplier: Truck, shipment: Ship };

function hitHref(h: SearchHit): string | null {
  switch (h.kind) {
    case "container":
      return h.id ? `/container/${h.id}` : null;
    case "purchase_order":
      return h.id ? `/purchase-orders/${h.id}` : null;
    case "invoice":
      return h.id ? `/invoices/${h.id}` : null;
    case "sku":
      return h.sku ? skuHref(h.sku) : null;
    case "shipment":
      return h.id ? `/containers?shipment_id=${h.id}` : null;
    case "supplier":
      return `/purchase-orders?q=${encodeURIComponent(h.label)}`;
  }
}

/** Same folding as the API: case, accents, and the spaces and dashes people type inside a number. */
const fold = (s: string) => s.normalize("NFD").replace(/\p{Diacritic}/gu, "").toLowerCase().replace(/[\s-]+/g, "");

/**
 * One box to reach anything: a container by its number, an order, a SKU, an invoice — or a page or
 * an action of the app. ⌘K / Ctrl+K from anywhere, "/" when no field has the focus. It is a
 * combobox over a listbox (the input keeps the focus, the arrows move `aria-activedescendant`), so
 * a screen reader announces each row as it is reached.
 */
export default function CommandPalette() {
  const t = useTranslations();
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  // What the last finished lookup answered, and for which term: rows that belong to an older term
  // are simply not shown, so nothing has to be reset when the box is emptied or closed.
  const [answer, setAnswer] = useState<{ term: string; rows: SearchHit[] }>({ term: "", rows: [] });
  const [cursor, setCursor] = useState(0);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const isMac = useSyncExternalStore(
    () => () => {},
    () => /Mac|iPhone|iPad/.test(navigator.platform),
    () => false,
  );
  const term = q.trim();
  const hits = useMemo(() => (term.length >= 2 && answer.term === term ? answer.rows : []), [answer, term]);
  const searching = open && term.length >= 2 && answer.term !== term;

  const close = useCallback(() => {
    setOpen(false);
    setQ("");
    setCursor(0);
  }, []);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && (e.target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName));
      if ((e.key === "k" || e.key === "K") && (e.metaKey || e.ctrlKey)) {
        e.preventDefault();
        setOpen((o) => !o);
      } else if (e.key === "/" && !typing && !e.metaKey && !e.ctrlKey && !e.altKey) {
        e.preventDefault();
        setOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  useFocusTrap(open, panelRef, triggerRef);
  useEffect(() => {
    if (!open) return;
    inputRef.current?.focus();
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = "";
    };
  }, [open]);

  // Debounced lookup; a newer keystroke aborts the request of the older one, so results never
  // arrive out of order.
  useEffect(() => {
    if (!open || term.length < 2) return;
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      let rows: SearchHit[] = [];
      try {
        const res = await fetch(`/api/search?q=${encodeURIComponent(term)}`, { signal: controller.signal });
        const body: unknown = res.ok ? await res.json() : null;
        if (body && typeof body === "object" && Array.isArray((body as { results?: unknown }).results)) rows = (body as { results: SearchHit[] }).results;
      } catch {
        // offline, or aborted by the next keystroke: an aborted answer is dropped just below
      }
      if (!controller.signal.aborted) setAnswer({ term, rows });
    }, 160);
    return () => {
      controller.abort();
      window.clearTimeout(timer);
    };
  }, [term, open]);

  const entries = useMemo<Entry[]>(() => {
    const needle = fold(term);
    const match = (label: string) => needle === "" || fold(label).includes(needle);
    const results: Entry[] = hits.flatMap((h, i) => {
      const href = hitHref(h);
      return href ? [{ id: `hit-${i}`, group: "results" as const, icon: HIT_ICON[h.kind] ?? Search, label: h.label, sub: [t(`palette.kind.${h.kind}`), h.sublabel].filter(Boolean).join(" · "), mono: h.kind !== "supplier", href }] : [];
    });
    const pages: Entry[] = [
      ...NAV_GROUPS.flatMap((g) => g.items.map((n) => ({ id: `page-${n.key}`, group: "pages" as const, icon: n.icon, label: t(`nav.${n.key}`), href: n.href }))),
      { id: "page-settings", group: "pages" as const, icon: Settings, label: t("nav.settings"), href: "/settings" },
    ].filter((e) => match(e.label));
    const actions: Entry[] = [
      { id: "act-start", group: "actions" as const, icon: Plus, label: t("palette.action.newFile"), href: "/start" },
      { id: "act-container", group: "actions" as const, icon: Container, label: t("palette.action.addContainer"), href: "/containers#nouveau" },
      { id: "act-invoice", group: "actions" as const, icon: FileUp, label: t("palette.action.uploadInvoice"), href: "/invoices#depot" },
      { id: "act-import", group: "actions" as const, icon: Upload, label: t("palette.action.importOrders"), href: "/imports/new" },
    ].filter((e) => match(e.label));
    return [...results, ...actions, ...pages];
  }, [hits, term, t]);

  const active = Math.min(cursor, Math.max(entries.length - 1, 0));
  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  const go = (e: Entry) => {
    close();
    router.push(e.href);
  };

  const onInputKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setCursor(entries.length ? (active + 1) % entries.length : 0);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setCursor(entries.length ? (active - 1 + entries.length) % entries.length : 0);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (entries[active]) go(entries[active]);
    } else if (e.key === "Escape") {
      e.preventDefault();
      close();
    }
  };

  const groups: Entry["group"][] = ["results", "actions", "pages"];

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        className="group flex items-center gap-2 w-full max-w-md h-9 rounded-lg border border-slate-200 bg-slate-50 hover:bg-white hover:border-slate-300 px-3 text-sm text-slate-500 transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
      >
        <Search className="w-4 h-4 shrink-0 text-slate-400" aria-hidden />
        <span className="flex-1 truncate text-left">{t("palette.trigger")}</span>
        <kbd className="hidden sm:inline-flex items-center rounded border border-slate-200 bg-white px-1.5 py-0.5 text-[11px] font-sans font-medium text-slate-500">{isMac ? "⌘K" : "Ctrl K"}</kbd>
      </button>

      {open ? (
        <div className="fixed inset-0 z-[70] flex items-start justify-center p-4 pt-[12vh]">
          <button type="button" tabIndex={-1} className="absolute inset-0 bg-navy-950/50 backdrop-blur-[2px]" aria-label={t("palette.close")} onClick={close} />
          <div ref={panelRef} role="dialog" aria-modal="true" aria-label={t("palette.title")} className="relative w-full max-w-xl rounded-xl bg-white shadow-pop ring-1 ring-slate-900/10 overflow-hidden animate-rise">
            <div className="flex items-center gap-3 px-4 border-b border-slate-200">
              <Search className="w-4 h-4 shrink-0 text-slate-400" aria-hidden />
              <input
                ref={inputRef}
                value={q}
                onChange={(e) => {
                  setQ(e.target.value);
                  setCursor(0);
                }}
                onKeyDown={onInputKey}
                role="combobox"
                aria-expanded="true"
                aria-controls="palette-list"
                aria-activedescendant={entries[active] ? `palette-${entries[active].id}` : undefined}
                aria-autocomplete="list"
                autoComplete="off"
                spellCheck={false}
                placeholder={t("palette.placeholder")}
                className="flex-1 h-12 bg-transparent text-sm text-slate-900 placeholder:text-slate-400 outline-none"
              />
              <kbd className="rounded border border-slate-200 px-1.5 py-0.5 text-[11px] font-sans text-slate-500">Esc</kbd>
            </div>

            <ul ref={listRef} id="palette-list" role="listbox" aria-label={t("palette.title")} className="max-h-[52vh] overflow-y-auto p-2">
              {groups.map((g) => {
                const rows = entries.filter((e) => e.group === g);
                if (rows.length === 0) return null;
                return (
                  <li key={g} role="presentation">
                    <div className="px-2 pt-2 pb-1 text-[11px] font-medium uppercase tracking-wider text-slate-500">{t(`palette.group.${g}`)}</div>
                    <ul role="group">
                      {rows.map((e) => {
                        const index = entries.indexOf(e);
                        const on = index === active;
                        return (
                          <li
                            key={e.id}
                            id={`palette-${e.id}`}
                            role="option"
                            aria-selected={on}
                            data-index={index}
                            onPointerMove={() => setCursor(index)}
                            onClick={() => go(e)}
                            className={`flex items-center gap-3 rounded-lg px-2.5 py-2 text-sm cursor-pointer ${on ? "bg-slate-100" : ""}`}
                          >
                            <e.icon className={`w-4 h-4 shrink-0 ${on ? "text-blue-600" : "text-slate-400"}`} aria-hidden />
                            <span className={`truncate text-slate-900 ${e.mono && e.group === "results" ? "font-mono" : ""}`}>{e.label}</span>
                            {e.sub ? <span className="truncate text-xs text-slate-500">{e.sub}</span> : null}
                            {on ? <CornerDownLeft className="ml-auto w-3.5 h-3.5 shrink-0 text-slate-400" aria-hidden /> : null}
                          </li>
                        );
                      })}
                    </ul>
                  </li>
                );
              })}
              {entries.length === 0 ? <li role="presentation" className="px-3 py-8 text-center text-sm text-slate-500">{searching ? t("palette.searching") : t("palette.nothing", { q: term })}</li> : null}
            </ul>

            <div className="flex items-center gap-4 border-t border-slate-200 bg-slate-50 px-4 py-2 text-[11px] text-slate-500">
              <span><kbd className="font-sans">↑↓</kbd> {t("palette.hintMove")}</span>
              <span><kbd className="font-sans">↵</kbd> {t("palette.hintOpen")}</span>
              <span className="ml-auto" aria-live="polite">{searching ? t("palette.searching") : term.length >= 2 ? t("palette.count", { count: hits.length }) : t("palette.hintType")}</span>
            </div>
          </div>
        </div>
      ) : null}
    </>
  );
}
