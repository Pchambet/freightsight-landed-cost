"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { btnPrimary, btnSecondary, input, label } from "@/components/layout/AppShell";
import { createContainerForOrder } from "@/features/start/actions";
import { CONTAINER_TYPES } from "@/lib/containerTypes";

/** Which box the order travelled in, and when it landed. The whole remainder of the order is loaded into it. */
export default function ContainerStep({ poId, today }: { poId: string; today: string }) {
  const t = useTranslations("start.container");
  const typeLabel = useTranslations("domain.containerType");
  const router = useRouter();
  const [number, setNumber] = useState("");
  const [arrived, setArrived] = useState(today);
  const [isoType, setIsoType] = useState("");
  const [conflict, setConflict] = useState<string | null>(null);
  const [pending, start] = useTransition();

  const go = (reuseId?: string) =>
    start(async () => {
      const r = await createContainerForOrder({ container_number: number, arrived_on: arrived, iso_type: isoType, po_id: poId, reuseId });
      if (!r.ok) {
        setConflict(r.existingId ?? null);
        return void toast.error(r.error);
      }
      router.push(`/start?po=${poId}&container=${r.id}`);
    });

  return (
    <form className="space-y-5" onSubmit={(e) => { e.preventDefault(); go(); }}>
      <div className="grid gap-4 sm:grid-cols-2 max-w-xl">
        <div>
          <label className={label} htmlFor="start-container">{t("number")}</label>
          <input id="start-container" value={number} onChange={(e) => { setNumber(e.target.value.toUpperCase()); setConflict(null); }} placeholder="MSCU1234567" pattern="[A-Za-z]{4}[0-9]{7}" title={t("format")} required className={`${input} font-mono uppercase`} />
          <p className="mt-1 text-xs text-slate-500">{t("format")}</p>
        </div>
        <div>
          <label className={label} htmlFor="start-arrived">{t("arrived")}</label>
          <input id="start-arrived" type="date" value={arrived} onChange={(e) => setArrived(e.target.value)} className={input} />
          <p className="mt-1 text-xs text-slate-500">{arrived ? t("arrivedHint") : t("arrivedMissing")}</p>
        </div>
        <div>
          <label className={label} htmlFor="start-type">{t("type")}</label>
          <select id="start-type" value={isoType} onChange={(e) => setIsoType(e.target.value)} aria-describedby="start-type-hint" className={input}>
            <option value="">{t("typeUnset")}</option>
            {CONTAINER_TYPES.map((c) => <option key={c} value={c}>{typeLabel(c)}</option>)}
          </select>
          <p id="start-type-hint" className="mt-1 text-xs text-slate-500">{t("typeHint")}</p>
        </div>
      </div>
      <p className="text-sm text-slate-600 max-w-2xl">{t("loadsHint")}</p>
      {conflict ? (
        <div role="alert" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-950 flex flex-wrap items-center gap-3 max-w-2xl">
          <span className="flex-1">{t("exists", { number })}</span>
          <button type="button" disabled={pending} onClick={() => go(conflict)} className={btnSecondary}>{t("useExisting")}</button>
        </div>
      ) : null}
      <button type="submit" disabled={pending} className={btnPrimary}>{pending ? t("saving") : t("next")}</button>
    </form>
  );
}
