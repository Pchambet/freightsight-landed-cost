"use client";

import { useEffect, useState, useTransition, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { AlertTriangle, CheckCircle2, ExternalLink, History, Info, Loader2, RefreshCw, Send, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { btnPrimary, btnSecondary, Card } from "@/components/layout/AppShell";
import { Money } from "@/components/shared/Money";
import { forgetErpPush, listErpPushes, previewErpLandedCost, pushErpLandedCost, type ErpLandedCostPreview, type ErpPushResponse } from "@/features/actions";
import { dateTimeShort } from "@/lib/format";

const KNOWN_BLOCKERS = new Set(["no_erp_connection", "no_actual_costs", "no_receipt", "multiple_receipts", "not_valued", "unmatched_line", "already_pushed", "pushed_cost_changed", "pushed_cost_gone", "ambiguous_line", "erp_currency_mismatch"]);

// `pushed_cost_changed`/`pushed_cost_gone` are advisory since the ERP lane's change: `pushable` can
// be true while they are still listed (a stale draft to clean up in Odoo, not something blocking
// this push). The schema has no `blocking` flag to read yet (needs_other_lane), so these two known
// codes are told apart from an actual refusal by their code rather than by a field.
const ADVISORY_BLOCKERS = new Set(["pushed_cost_changed", "pushed_cost_gone"]);

type Loaded = { preview: ErpLandedCostPreview; pushes: ErpPushResponse[] };
type State = { loading: boolean; data: Loaded | null; error: string | null };

function OdooLink({ href, children, className }: { href: string; children: ReactNode; className?: string }) {
  return (
    <a href={href} target="_blank" rel="noopener noreferrer" className={className ?? "inline-flex items-center gap-1 text-blue-700 hover:text-blue-900 underline underline-offset-2"}>
      {children}
      <ExternalLink className="w-3.5 h-3.5 shrink-0" aria-hidden />
    </a>
  );
}

async function loadAll(containerId: string): Promise<{ ok: true; data: Loaded } | { ok: false; error: string }> {
  const [p, l] = await Promise.all([previewErpLandedCost(containerId), listErpPushes(containerId)]);
  if (!p.ok) return p;
  return { ok: true, data: { preview: p.data, pushes: l.ok ? l.data : [] } };
}

/**
 * The landed cost as it would land in the ERP, shown before it exists. Each push carries only the
 * costs no document carries yet, so a cost already in Odoo is shown as such and left out of the
 * total. The preview is loaded after the page: it reads the ERP live, and a slow Odoo must not slow
 * the container page down. A push creates a draft only; forgetting one is a marked row plus an
 * audit entry, never a deletion.
 */
export default function LandedCostPush({ containerId, currency, embedded = false }: { containerId: string; currency: string; embedded?: boolean }) {
  const t = useTranslations("erp.push");
  const tp = useTranslations("erp.pushes");
  const locale = useLocale();
  const router = useRouter();
  const [state, setState] = useState<State>({ loading: true, data: null, error: null });
  const { loading, data, error } = state;
  const [pending, start] = useTransition();
  const [open, setOpen] = useState(false);
  const [forgetting, setForgetting] = useState<{ id: string; reason: string } | null>(null);

  const apply = (r: Awaited<ReturnType<typeof loadAll>>) =>
    setState(r.ok ? { loading: false, data: r.data, error: null } : { loading: false, data: null, error: r.error });
  useEffect(() => {
    let alive = true;
    loadAll(containerId).then((r) => {
      if (alive) apply(r);
    });
    return () => {
      alive = false;
    };
  }, [containerId]);
  const load = () => {
    setState((s) => ({ ...s, loading: true, error: null }));
    loadAll(containerId).then(apply);
  };

  const push = () =>
    start(async () => {
      const r = await pushErpLandedCost(containerId);
      if (!r.ok) return void toast.error(r.error);
      const pushedName = r.data.odoo_name ?? String(r.data.odoo_id);
      toast.success(t("pushed", { name: pushedName }), {
        duration: 8000,
        action: r.data.record_url
          ? { label: t("openInOdoo"), onClick: () => window.open(r.data.record_url!, "_blank", "noopener,noreferrer") }
          : undefined,
      });
      load();
      router.refresh();
    });

  const forget = () =>
    start(async () => {
      if (!forgetting) return;
      const r = await forgetErpPush(containerId, forgetting.id, forgetting.reason);
      if (!r.ok) return void toast.error(r.error, { duration: 10000 });
      toast.success(tp("forgotten_ok"));
      setForgetting(null);
      load();
      router.refresh();
    });

  const preview = data?.preview ?? null;
  const pushes = data?.pushes ?? [];
  const blockers = preview?.blockers ?? [];
  /** The blocker in the reader's language, from its code and moving parts; a code this screen does not know yet gets a neutral sentence, never the API's English one. */
  const blockerText = (b: { code: string; message: string; params?: Record<string, string> }) => {
    if (!KNOWN_BLOCKERS.has(b.code)) return t("blocker.unknown");
    const params = { ...(b.params ?? {}) };
    if (b.code === "unmatched_line" && !params.sku) params.sku = t("blocker.noSku");
    return t(`blocker.${b.code}` as never, params as never);
  };
  const nothingNew = blockers.some((b) => b.code === "already_pushed");
  const going = preview ? preview.costs.filter((c) => !c.pushed_as) : [];
  const latestLivePush = pushes.find((p) => !p.forgotten_at && p.record_url);

  return (
    <Card title={embedded ? undefined : t("title")}>
      <p className="text-sm text-slate-500 mb-3">{t("hint")}</p>
      {loading ? (
        <div className="flex items-center text-sm text-slate-500"><Loader2 className="w-4 h-4 mr-2 animate-spin" />{t("loading")}</div>
      ) : error ? (
        <div className="flex flex-wrap items-center gap-3 text-sm">
          <span className="inline-flex items-center text-red-700"><AlertTriangle className="w-4 h-4 mr-2" />{error}</span>
          <button type="button" onClick={load} className={btnSecondary}><RefreshCw className="w-4 h-4 mr-2" />{t("retry")}</button>
        </div>
      ) : preview ? (
        <div className="space-y-3">
          {nothingNew && preview.already_pushed_as ? (
            <div className="flex items-start text-sm text-emerald-800 bg-emerald-50 border border-emerald-200 rounded-md px-3 py-2">
              <CheckCircle2 className="w-4 h-4 mr-2 mt-0.5 shrink-0" />
              <span>
                {t("alreadyPushed", { name: preview.already_pushed_as })}
                {latestLivePush?.record_url ? (
                  <span className="block mt-1">
                    <OdooLink href={latestLivePush.record_url} className="inline-flex items-center gap-1 text-emerald-900 hover:text-emerald-950 underline underline-offset-2 font-medium">
                      {t("openInOdoo")}
                    </OdooLink>
                  </span>
                ) : null}
                <span className="block text-xs text-emerald-700 mt-0.5">{t("draftReminder")}</span>
              </span>
            </div>
          ) : null}

          {blockers.filter((b) => b.code !== "already_pushed").map((b, i) =>
            ADVISORY_BLOCKERS.has(b.code) ? (
              <div key={`${b.code}-${i}`} className="flex items-start text-sm text-blue-900 bg-blue-50 border border-blue-200 rounded-md px-3 py-2">
                <Info className="w-4 h-4 mr-2 mt-0.5 shrink-0 text-blue-600" />
                <span>{blockerText(b)}</span>
              </div>
            ) : (
              <div key={`${b.code}-${i}`} className="flex items-start text-sm text-amber-900 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
                <AlertTriangle className="w-4 h-4 mr-2 mt-0.5 shrink-0 text-amber-600" />
                <span>{blockerText(b)}</span>
              </div>
            ),
          )}

          {preview.costs.length > 0 ? (
            <div className="border border-slate-200 rounded-md overflow-hidden">
              <div className="flex items-center justify-between px-3 py-2 bg-slate-50 text-sm">
                <span className="text-slate-700">
                  {preview.receipt_name ? t("receipt", { name: preview.receipt_name }) : t("noReceipt")}
                  <span className="text-slate-500"> · {going.length ? t("willGo", { count: going.length }) : t("nothingNew")}</span>
                </span>
                <span className="font-semibold tabular-nums"><Money amount={preview.total} currency={currency} /></span>
              </div>
              <ul className="divide-y divide-slate-100">
                {preview.costs.map((c, i) => (
                  <li key={i} className={`px-3 py-2 text-sm ${c.pushed_as ? "text-slate-500" : ""}`}>
                    <div className="flex items-center justify-between gap-3">
                      <span className={c.pushed_as ? "" : "text-slate-800"}>
                        {c.label}
                        {c.pushed_as ? <span className="ml-2 inline-flex items-center rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200 px-2 py-0.5 text-xs"><CheckCircle2 className="w-3 h-3 mr-1" />{t("pushedAs", { name: c.pushed_as })}</span> : null}
                      </span>
                      <span className={`tabular-nums ${c.pushed_as ? "line-through" : ""}`}><Money amount={c.amount} currency={currency} /></span>
                    </div>
                    {open && c.lines.length ? (
                      <ul className="mt-1 space-y-0.5">
                        {c.lines.map((l) => (
                          <li key={l.move_id} className="flex items-center justify-between text-xs text-slate-500 pl-3">
                            <span>{l.sku ? <span className="font-mono text-slate-600">{l.sku}</span> : null} {l.product_name} <span className="text-slate-500">× {l.quantity}</span></span>
                            <span className="tabular-nums"><Money amount={l.amount} currency={currency} /></span>
                          </li>
                        ))}
                      </ul>
                    ) : null}
                  </li>
                ))}
              </ul>
              <button type="button" onClick={() => setOpen((v) => !v)} className="w-full text-left text-xs text-slate-500 hover:text-slate-700 px-3 py-1.5 border-t border-slate-100">
                {open ? t("hideLines") : t("showLines")}
              </button>
            </div>
          ) : null}

          <div className="flex flex-wrap items-center gap-3">
            <button type="button" onClick={push} disabled={pending || !preview.pushable} className={btnPrimary} title={preview.pushable ? undefined : t("notPushable")}>
              {pending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Send className="w-4 h-4 mr-2" />}{t("push")}
            </button>
            <button type="button" onClick={load} disabled={pending} className={btnSecondary}><RefreshCw className="w-4 h-4 mr-2" />{t("refresh")}</button>
            <span className="text-xs text-slate-500">{t("draftReminder")}</span>
          </div>

          {pushes.length > 0 ? (
            <div className="pt-3 border-t border-slate-100">
              <div className="flex items-center text-xs text-slate-500 uppercase tracking-wider mb-2"><History className="w-3.5 h-3.5 mr-1.5" />{tp("title")}</div>
              <ul className="space-y-2">
                {pushes.map((p) => (
                  <li key={p.id} className={`text-sm ${p.forgotten_at ? "text-slate-500" : "text-slate-700"}`}>
                    <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
                      {p.record_url && !p.forgotten_at ? (
                        <OdooLink href={p.record_url} className={`font-medium ${p.forgotten_at ? "line-through" : ""}`}>
                          {p.odoo_name ?? `#${p.odoo_id}`}
                        </OdooLink>
                      ) : (
                        <span className={`font-medium ${p.forgotten_at ? "line-through" : ""}`}>{p.odoo_name ?? `#${p.odoo_id}`}</span>
                      )}
                      <span className="text-xs">{tp("costCount", { count: p.cost_ids.length })} · {tp("created", { date: dateTimeShort(p.created_at, locale) })}{p.adopted ? ` · ${tp("adopted")}` : ""}</span>
                      {p.forgotten_at ? (
                        <span className="text-xs">{tp("forgotten", { date: dateTimeShort(p.forgotten_at, locale) })}{p.forgotten_reason ? ` · ${tp("forgottenReason", { reason: p.forgotten_reason })}` : ""}</span>
                      ) : forgetting?.id !== p.id ? (
                        <button type="button" onClick={() => setForgetting({ id: p.id, reason: "" })} disabled={pending} className="inline-flex items-center text-xs text-slate-500 hover:text-slate-800 underline underline-offset-2" title={tp("forgetHint")}>
                          <Undo2 className="w-3.5 h-3.5 mr-1" />{tp("forget")}
                        </button>
                      ) : null}
                    </div>
                    {forgetting?.id === p.id ? (
                      <div className="mt-2 rounded-md border border-slate-200 bg-slate-50 p-3 space-y-2">
                        <p className="text-xs text-slate-600">{tp("forgetHint")}</p>
                        <label className="block text-xs text-slate-600">
                          {tp("reasonLabel")}
                          <input type="text" value={forgetting.reason} onChange={(e) => setForgetting({ id: p.id, reason: e.target.value })} placeholder={tp("reasonPlaceholder")} className="mt-1 w-full rounded-md border border-slate-300 px-2 py-1.5 text-sm text-slate-900" />
                        </label>
                        <div className="flex gap-2">
                          <button type="button" onClick={forget} disabled={pending || !forgetting.reason.trim()} className={btnSecondary}>
                            {pending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Undo2 className="w-4 h-4 mr-2" />}{tp("confirm")}
                          </button>
                          <button type="button" onClick={() => setForgetting(null)} disabled={pending} className="text-sm text-slate-500 hover:text-slate-800 px-2">{tp("cancel")}</button>
                        </div>
                      </div>
                    ) : null}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}
        </div>
      ) : null}
    </Card>
  );
}
