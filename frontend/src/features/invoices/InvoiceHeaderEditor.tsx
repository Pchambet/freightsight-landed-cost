"use client";

import { useEffect, useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { btnSecondary, input, label } from "@/components/layout/AppShell";
import { correctInvoiceHeader } from "@/features/actions";

/** Where the review panel sends someone whose refusal may come from a misread number or forwarder. */
export const HEADER_EDITOR_ID = "en-tete";

/**
 * Opens the editor and puts the cursor in the number — or, on a page without it, goes to the
 * invoice's with the caller's router, where the hash opens it.
 */
export function openHeaderEditor(invoiceId: string, push: (href: string) => void) {
  const box = document.getElementById(HEADER_EDITOR_ID) as HTMLDetailsElement | null;
  if (!box) return push(`/invoices/${invoiceId}#${HEADER_EDITOR_ID}`);
  box.open = true;
  box.scrollIntoView({ block: "center" });
  box.querySelector<HTMLInputElement>("#header-number")?.focus();
}

/**
 * The header the reader took — forwarder, number, date — corrected from the PDF beside it, while the
 * invoice is being reviewed. Every guard against counting one invoice twice compares the number and
 * the forwarder: a date read as the number is a false « already recorded » today and a missed double
 * entry tomorrow. Only what changed is sent; an emptied field is cleared.
 */
export default function InvoiceHeaderEditor({
  invoiceId,
  vendor,
  invoiceNumber,
  invoiceDate,
}: {
  invoiceId: string;
  vendor: string | null;
  invoiceNumber: string | null;
  invoiceDate: string | null;
}) {
  const t = useTranslations("invoices.header");
  const router = useRouter();
  const [v, setV] = useState(vendor ?? "");
  const [n, setN] = useState(invoiceNumber ?? "");
  const [d, setD] = useState(invoiceDate ?? "");
  const [pending, start] = useTransition();
  const changed = {
    ...(v.trim() !== (vendor ?? "") ? { vendor: v.trim() } : {}),
    ...(n.trim() !== (invoiceNumber ?? "") ? { invoice_number: n.trim() } : {}),
    ...(d !== (invoiceDate ?? "") ? { invoice_date: d } : {}),
  };
  const dirty = Object.keys(changed).length > 0;

  // Arriving from a refusal on another page (the first-steps flow) lands here with the editor open.
  useEffect(() => {
    if (window.location.hash === `#${HEADER_EDITOR_ID}`) openHeaderEditor(invoiceId, (href) => router.push(href));
  }, [invoiceId, router]);

  return (
    <details id={HEADER_EDITOR_ID} className="mt-2 scroll-mt-6">
      <summary className="inline-flex cursor-pointer text-sm text-blue-700 underline underline-offset-2 hover:no-underline">{t("open")}</summary>
      <form
        className="mt-3 grid gap-3 sm:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_10rem_auto] sm:items-end max-w-3xl"
        onSubmit={(e) => {
          e.preventDefault();
          if (!dirty) return;
          start(async () => {
            const r = await correctInvoiceHeader(invoiceId, changed);
            if (!r.ok) return void toast.error(r.error);
            toast.success(t("saved"));
            router.refresh();
          });
        }}
      >
        <div>
          <label className={label} htmlFor="header-vendor">{t("vendor")}</label>
          <input id="header-vendor" value={v} onChange={(e) => setV(e.target.value)} className={input} />
        </div>
        <div>
          <label className={label} htmlFor="header-number">{t("number")}</label>
          <input id="header-number" value={n} onChange={(e) => setN(e.target.value)} className={`${input} font-mono`} />
        </div>
        <div>
          <label className={label} htmlFor="header-date">{t("date")}</label>
          <input id="header-date" type="date" value={d} onChange={(e) => setD(e.target.value)} className={input} />
        </div>
        <button type="submit" disabled={!dirty || pending} className={btnSecondary}>{t("save")}</button>
      </form>
      <p className="mt-2 text-xs text-slate-500 max-w-2xl">{t("hint")}</p>
    </details>
  );
}
