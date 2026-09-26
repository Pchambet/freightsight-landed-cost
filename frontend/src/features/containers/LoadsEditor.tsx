"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { Trash2 } from "lucide-react";
import { toast } from "sonner";
import { btnSecondary, input, label } from "@/components/layout/AppShell";
import { replaceLoads } from "@/features/actions";
import type { LoadResponse, PurchaseOrderSummary } from "@/lib/api/client";
import { qty } from "@/lib/format";

type Row = { po_line_id: string; po_number: string; line_no: number; sku: string | null; quantity: string; line_quantity: string };

export default function LoadsEditor({ containerId, loads, purchaseOrders }: { containerId: string; loads: LoadResponse[]; purchaseOrders: PurchaseOrderSummary[] }) {
  const t = useTranslations("loads");
  const tc = useTranslations("common");
  const locale = useLocale();
  const [rows, setRows] = useState<Row[]>(loads.map((l) => ({ ...l, quantity: l.quantity })));
  const [pending, startTransition] = useTransition();
  const [lineId, setLineId] = useState("");
  const [lineQty, setLineQty] = useState("");
  const [poId, setPoId] = useState("");
  const [lines, setLines] = useState<{ id: string; line_no: number; sku: string | null; quantity: string }[]>([]);
  const router = useRouter();

  const loadLines = async (id: string) => {
    setPoId(id);
    setLineId("");
    setLines([]);
    if (!id) return;
    const res = await fetch(`/api/purchase-order-lines?po_id=${id}`);
    if (res.ok) setLines(await res.json());
  };

  const save = (next: Row[]) =>
    startTransition(async () => {
      const result = await replaceLoads(containerId, next.map((r) => ({ po_line_id: r.po_line_id, quantity: r.quantity })));
      if (!result.ok) return void toast.error(result.error);
      toast.success(t("updated"));
      setRows(next);
      router.refresh();
    });

  const add = () => {
    const line = lines.find((l) => l.id === lineId);
    const po = purchaseOrders.find((p) => p.id === poId);
    if (!line || !po || !lineQty) return;
    if (rows.some((r) => r.po_line_id === line.id)) return void toast.error(t("alreadyLoaded"));
    save([...rows, { po_line_id: line.id, po_number: po.po_number, line_no: line.line_no, sku: line.sku, quantity: lineQty, line_quantity: line.quantity }]);
    setLineId("");
    setLineQty("");
  };

  return (
    <div className="p-5 space-y-4">
      {rows.length === 0 ? (
        <p className="text-sm text-slate-500">{t("empty")}</p>
      ) : (
        <ul className="divide-y divide-slate-100 text-sm">
          {rows.map((r) => (
            <li key={r.po_line_id} className="flex items-center justify-between py-2 gap-2">
              <div>
                <span className="font-medium">{r.po_number}</span> <span className="text-slate-500">{t("lineLabel", { lineNo: r.line_no })}{r.sku ? ` · ${r.sku}` : ""}</span>
                <div className="text-xs text-slate-500 tabular-nums">{t("quantityOf", { loaded: qty(r.quantity, locale), total: qty(r.line_quantity, locale) })}</div>
              </div>
              <button type="button" disabled={pending} onClick={() => save(rows.filter((x) => x.po_line_id !== r.po_line_id))} className="text-slate-500 hover:text-red-600" aria-label={t("remove")}>
                <Trash2 className="w-4 h-4" />
              </button>
            </li>
          ))}
        </ul>
      )}
      <div className="border-t border-slate-100 pt-4 space-y-3">
        <div>
          <label className={label} htmlFor="po">{t("purchaseOrder")}</label>
          <select id="po" value={poId} onChange={(e) => void loadLines(e.target.value)} className={input}>
            <option value="">{tc("select")}</option>
            {purchaseOrders.map((p) => <option key={p.id} value={p.id}>{p.po_number}{p.supplier_name ? ` · ${p.supplier_name}` : ""}</option>)}
          </select>
        </div>
        <div className="grid grid-cols-3 gap-2">
          <div className="col-span-2">
            <label className={label} htmlFor="line">{t("line")}</label>
            <select id="line" value={lineId} onChange={(e) => setLineId(e.target.value)} disabled={!lines.length} className={input}>
              <option value="">{poId ? (lines.length ? tc("select") : tc("loading")) : tc("empty")}</option>
              {lines.map((l) => <option key={l.id} value={l.id}>#{l.line_no}{l.sku ? ` ${l.sku}` : ""} · {qty(l.quantity, locale)}</option>)}
            </select>
          </div>
          <div>
            <label className={label} htmlFor="lqty">{t("quantity")}</label>
            <input id="lqty" inputMode="decimal" value={lineQty} onChange={(e) => setLineQty(e.target.value)} className={`${input} tabular-nums`} />
          </div>
        </div>
        <button type="button" onClick={add} disabled={pending || !lineId || !lineQty} className={btnSecondary}>{t("add")}</button>
      </div>
    </div>
  );
}
