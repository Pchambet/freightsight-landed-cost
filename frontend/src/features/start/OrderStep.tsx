"use client";

import Link from "next/link";
import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { btnPrimary, btnSecondary, input, label } from "@/components/layout/AppShell";
import { createOrder, type OrderLineInput } from "@/features/start/actions";

const COLUMNS = ["sku", "description", "quantity", "unit_price", "unit_weight_kg", "unit_volume_cbm", "duty_rate_pct"] as const;
type Column = (typeof COLUMNS)[number];
const blank = (): OrderLineInput => ({ sku: "", description: "", quantity: "", unit_price: "", unit_weight_kg: "", unit_volume_cbm: "", duty_rate_pct: "" });
const NUMERIC: Column[] = ["quantity", "unit_price", "unit_weight_kg", "unit_volume_cbm", "duty_rate_pct"];

/**
 * The order, the way people already have it: in a spreadsheet. Copying a block of cells from Excel
 * and pasting it into any cell fills the grid from that cell, rightwards and downwards, adding rows
 * as needed — so "reference, designation, quantity, price" pasted in the first cell is a whole order.
 * Numbers are taken as typed ("12 500,50" included) and checked on the server, line by line.
 */
export default function OrderStep({ existing }: { existing: { id: string; label: string }[] }) {
  const t = useTranslations("start.order");
  const router = useRouter();
  const [head, setHead] = useState({ po_number: "", supplier_name: "", currency: "EUR", order_date: "" });
  const [rows, setRows] = useState<OrderLineInput[]>([blank(), blank(), blank()]);
  const [conflict, setConflict] = useState<string | null>(null);
  const [pending, start] = useTransition();

  const setCell = (r: number, c: Column, v: string) => setRows((prev) => prev.map((row, i) => (i === r ? { ...row, [c]: v } : row)));

  const onPaste = (r: number, c: Column) => (e: React.ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData("text");
    if (!/[\t\n]/.test(text)) return; // a single value: let the field take it
    e.preventDefault();
    const block = text.replace(/\r/g, "").split("\n").filter((line) => line.trim() !== "").map((line) => line.split("\t"));
    setRows((prev) => {
      const next = prev.map((row) => ({ ...row }));
      block.forEach((cells, dr) => {
        while (next.length <= r + dr) next.push(blank());
        cells.forEach((value, dc) => {
          const col = COLUMNS[COLUMNS.indexOf(c) + dc];
          if (col) next[r + dr][col] = value.trim();
        });
      });
      return next;
    });
    toast.success(t("pasted", { count: block.length }));
  };

  const submit = () =>
    start(async () => {
      setConflict(null);
      const r = await createOrder({ ...head, lines: rows });
      if (!r.ok) {
        if (r.existingId) setConflict(r.existingId);
        return void toast.error(r.error);
      }
      router.push(`/start?po=${r.id}`);
    });

  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <div>
          <label className={label} htmlFor="po-number">{t("number")}</label>
          <input id="po-number" className={input} value={head.po_number} onChange={(e) => setHead({ ...head, po_number: e.target.value })} placeholder="PO-2026-031" required />
        </div>
        <div>
          <label className={label} htmlFor="po-supplier">{t("supplier")}</label>
          <input id="po-supplier" className={input} value={head.supplier_name} onChange={(e) => setHead({ ...head, supplier_name: e.target.value })} placeholder="Ningbo Hanwei Housewares" />
        </div>
        <div>
          <label className={label} htmlFor="po-currency">{t("currency")}</label>
          <select id="po-currency" className={input} value={head.currency} onChange={(e) => setHead({ ...head, currency: e.target.value })}>
            {["EUR", "USD", "CNY", "GBP"].map((c) => <option key={c} value={c}>{c}</option>)}
          </select>
        </div>
        <div>
          <label className={label} htmlFor="po-date">{t("date")}</label>
          <input id="po-date" type="date" className={input} value={head.order_date} onChange={(e) => setHead({ ...head, order_date: e.target.value })} />
          {head.currency !== "EUR" ? <p className="mt-1 text-xs text-slate-500">{t("fxHint", { currency: head.currency })}</p> : null}
        </div>
      </div>

      <div>
        <div className="flex flex-wrap items-baseline justify-between gap-2 mb-2">
          <h3 className="text-sm font-semibold text-slate-900">{t("lines")}</h3>
          <p className="text-xs text-slate-500">{t("pasteHint")}</p>
        </div>
        <div className="overflow-x-auto rounded-lg border border-slate-200">
          <table className="min-w-full text-sm">
            <thead className="bg-slate-50">
              <tr>
                {COLUMNS.map((c) => (
                  <th key={c} scope="col" className={`px-2 py-2 text-xs font-medium text-slate-600 ${NUMERIC.includes(c) ? "text-right" : "text-left"}`}>
                    {t(`col.${c}`)}{c === "quantity" || c === "unit_price" ? <span className="text-red-600" aria-hidden> *</span> : null}
                  </th>
                ))}
                <th className="w-8" />
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-100">
              {rows.map((row, r) => (
                <tr key={r}>
                  {COLUMNS.map((c) => (
                    <td key={c} className="p-1">
                      <input
                        aria-label={`${t(`col.${c}`)} — ${t("lineNo", { line: r + 1 })}`}
                        value={row[c]}
                        onChange={(e) => setCell(r, c, e.target.value)}
                        onPaste={onPaste(r, c)}
                        inputMode={NUMERIC.includes(c) ? "decimal" : undefined}
                        className={`w-full rounded border border-transparent px-2 py-1.5 text-sm outline-none hover:border-slate-200 focus:border-blue-500 focus:ring-1 focus:ring-blue-500 ${c === "sku" ? "font-mono min-w-32" : c === "description" ? "min-w-44" : "text-right tabular-nums min-w-20"}`}
                      />
                    </td>
                  ))}
                  <td className="p-1 text-center">
                    <button type="button" onClick={() => setRows((prev) => (prev.length > 1 ? prev.filter((_, i) => i !== r) : [blank()]))} className="p-1.5 rounded text-slate-500 hover:text-red-600 hover:bg-red-50 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500">
                      <Trash2 className="w-4 h-4" aria-hidden />
                      <span className="sr-only">{t("removeLine", { line: r + 1 })}</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <button type="button" onClick={() => setRows((prev) => [...prev, blank()])} className="mt-2 inline-flex items-center gap-1 text-sm text-blue-700 hover:underline">
          <Plus className="w-4 h-4" aria-hidden />{t("addLine")}
        </button>
        <p className="mt-2 text-xs text-slate-500">{t("weightHint")}</p>
      </div>

      {conflict ? (
        <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950 flex flex-wrap items-center gap-3">
          <span className="flex-1">{t("exists", { number: head.po_number })}</span>
          <Link href={`/start?po=${conflict}`} className={btnSecondary}>{t("useExisting")}</Link>
        </div>
      ) : null}

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={submit} disabled={pending} className={btnPrimary}>{pending ? t("saving") : t("next")}</button>
        <Link href="/imports/new" className="text-sm text-blue-700 hover:underline">{t("importInstead")}</Link>
      </div>

      {existing.length > 0 ? (
        <form method="get" action="/start" className="pt-5 border-t border-slate-200 flex flex-wrap items-end gap-3">
          <div>
            <label className={label} htmlFor="existing-po">{t("orExisting")}</label>
            <select id="existing-po" name="po" className={`${input} !w-auto min-w-56`} defaultValue="">
              <option value="" disabled>{t("chooseExisting")}</option>
              {existing.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}
            </select>
          </div>
          <button type="submit" className={btnSecondary}>{t("continueExisting")}</button>
        </form>
      ) : null}
    </div>
  );
}
