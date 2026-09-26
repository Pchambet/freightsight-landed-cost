"use client";

import { useState, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { toast } from "sonner";
import { btnSecondary, input, label } from "@/components/layout/AppShell";
import { addTrackingEvent } from "@/features/actions";
import { MILESTONE_CODES, type MilestoneCode } from "@/lib/domain";

/** Manual event entry: the form is the ManualProvider's user interface. */
export default function AddEventForm({ containerId }: { containerId: string }) {
  const t = useTranslations("timeline");
  const codeLabel = useTranslations("domain.milestone");
  const [code, setCode] = useState<MilestoneCode>("DISCHARGED");
  const [when, setWhen] = useState(() => new Date().toISOString().slice(0, 16));
  const [estimate, setEstimate] = useState(false);
  const [unlocode, setUnlocode] = useState("");
  const [locationName, setLocationName] = useState("");
  const [vessel, setVessel] = useState("");
  const [voyage, setVoyage] = useState("");
  const [note, setNote] = useState("");
  const [open, setOpen] = useState(false);
  const [pending, startTransition] = useTransition();
  const router = useRouter();

  const submit = () =>
    startTransition(async () => {
      const result = await addTrackingEvent(containerId, {
        code,
        occurred_at: `${when}:00Z`,
        is_estimate: estimate,
        location_unlocode: unlocode.trim() ? unlocode.trim().toUpperCase() : null,
        location_name: locationName.trim() || null,
        vessel_name: vessel.trim() || null,
        voyage: voyage.trim() || null,
        note: note.trim() || null,
      });
      if (!result.ok) return void toast.error(result.error);
      toast.success(t("added"));
      setOpen(false);
      setNote("");
      router.refresh();
    });

  if (!open) {
    return <button type="button" onClick={() => setOpen(true)} className={btnSecondary}>{t("add")}</button>;
  }

  return (
    <div className="space-y-3">
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={label} htmlFor="ev-code">{t("code")}</label>
          <select id="ev-code" value={code} onChange={(e) => setCode(e.target.value as MilestoneCode)} className={input}>
            {MILESTONE_CODES.map((c) => <option key={c} value={c}>{codeLabel(c)}</option>)}
          </select>
        </div>
        <div>
          <label className={label} htmlFor="ev-when">{t("occurredAt")}</label>
          <input id="ev-when" type="datetime-local" value={when} onChange={(e) => setWhen(e.target.value)} className={input} />
        </div>
      </div>
      <label className="flex items-center gap-2 text-sm text-slate-700">
        <input type="checkbox" checked={estimate} onChange={(e) => setEstimate(e.target.checked)} />
        {t("isEstimate")}
      </label>
      <div className="grid grid-cols-2 gap-3">
        <div>
          <label className={label} htmlFor="ev-loc">{t("location")}</label>
          <input id="ev-loc" maxLength={5} value={unlocode} onChange={(e) => setUnlocode(e.target.value)} className={`${input} font-mono uppercase`} />
        </div>
        <div>
          <label className={label} htmlFor="ev-locname">{t("locationName")}</label>
          <input id="ev-locname" value={locationName} onChange={(e) => setLocationName(e.target.value)} className={input} />
        </div>
        <div>
          <label className={label} htmlFor="ev-vessel">{t("vessel")}</label>
          <input id="ev-vessel" value={vessel} onChange={(e) => setVessel(e.target.value)} className={input} />
        </div>
        <div>
          <label className={label} htmlFor="ev-voyage">{t("voyage")}</label>
          <input id="ev-voyage" value={voyage} onChange={(e) => setVoyage(e.target.value)} className={input} />
        </div>
      </div>
      <div>
        <label className={label} htmlFor="ev-note">{t("note")}</label>
        <input id="ev-note" value={note} onChange={(e) => setNote(e.target.value)} className={input} />
      </div>
      <div className="flex gap-2">
        <button type="button" onClick={submit} disabled={pending} className={btnSecondary}>{t("submit")}</button>
        <button type="button" onClick={() => setOpen(false)} className="text-sm text-slate-500 px-2">×</button>
      </div>
    </div>
  );
}
