"use client";

import { useRef, useTransition } from "react";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { Loader2, Plug, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { Card, btnPrimary, btnSecondary, input, label, td, th } from "@/components/layout/AppShell";
import { connectErp, disconnectErp, syncErp } from "@/features/actions";
import type { ErpConnection as Conn, ErpSyncRun } from "@/lib/api/client";
import { dateTimeShort } from "@/lib/format";

export default function ErpConnection({ connection, runs }: { connection: Conn | null; runs: ErpSyncRun[] }) {
  const t = useTranslations("erp");
  const locale = useLocale();
  const [pending, start] = useTransition();
  const formRef = useRef<HTMLFormElement>(null);
  const router = useRouter();

  const connect = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const f = new FormData(e.currentTarget);
    start(async () => {
      const r = await connectErp({ url: String(f.get("url") ?? ""), database: String(f.get("database") ?? ""), login: String(f.get("login") ?? ""), api_key: String(f.get("api_key") ?? "") });
      if (!r.ok) return void toast.error(r.error);
      toast.success(t("connected"));
      formRef.current?.reset();
      router.refresh();
    });
  };
  const sync = (full = false) =>
    start(async () => {
      const r = await syncErp(full);
      if (!r.ok) return void toast.error(r.error);
      if (r.data.status === "FAILED") toast.error(r.data.error ? `${t("syncFailed")} · ${t("lastErrorGeneric")}` : t("syncFailed"));
      else toast.success(t("synced", { orders: r.data.orders, lines: r.data.lines }));
      router.refresh();
    });
  const disconnect = () =>
    start(async () => {
      const r = await disconnectErp();
      if (!r.ok) return void toast.error(r.error);
      toast.success(t("disconnected"));
      router.refresh();
    });

  return (
    <Card title={t("title")} right={<span className="text-xs text-slate-500 max-w-md text-right">{t("hint")}</span>}>
      {connection ? (
        <div className="p-5 space-y-4">
          <div className="flex flex-wrap items-center gap-3">
            <span className="inline-flex items-center gap-1.5 text-sm font-medium text-emerald-700"><Plug className="w-4 h-4" />{t("connected")}</span>
            <span className="text-sm text-slate-600">{t("connectedDetail", { kind: connection.kind === "ODOO" ? "Odoo" : connection.kind, version: connection.server_version ?? "", company: connection.company ?? "" })}</span>
            <span className="text-xs text-slate-500">· {connection.url} · {connection.database} · {connection.login}</span>
          </div>
          {/* connection.last_error is a raw, uncoded backend string (now readable prose, but still
              English on record) — shown as a generic sentence rather than verbatim; see needs_other_lane. */}
          <div className="text-xs text-slate-500">{connection.last_sync_at ? t("lastSync", { date: dateTimeShort(connection.last_sync_at, locale) }) : t("neverSynced")}{connection.last_error ? <span className="text-red-600"> · {t("lastErrorGeneric")}</span> : null}</div>
          {connection.consecutive_failures > 0 ? (
            <div className="text-xs text-amber-800 bg-amber-50 border border-amber-200 rounded-md px-3 py-2">
              {t("unreachable", { count: connection.consecutive_failures })}
              {connection.retry_after ? ` · ${t("retryAfter", { date: dateTimeShort(connection.retry_after, locale) })}` : ""}
              {` · ${t("retryNowHint")}`}
            </div>
          ) : null}
          <div className="flex gap-2">
            <button type="button" onClick={() => sync(false)} disabled={pending} className={btnPrimary}>{pending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <RefreshCw className="w-4 h-4 mr-2" />}{pending ? t("syncing") : t("sync")}</button>
            <button type="button" onClick={() => sync(true)} disabled={pending} title={t("fullSyncHint")} className={btnSecondary}>{t("fullSync")}</button>
            <button type="button" onClick={disconnect} disabled={pending} className="text-sm text-slate-500 px-2 hover:underline">{t("disconnect")}</button>
          </div>
          <div>
            <div className="text-xs font-medium uppercase tracking-wider text-slate-500 mb-2">{t("runs")}</div>
            {runs.length === 0 ? <p className="text-xs text-slate-500">{t("noRuns")}</p> : (
              <table className="min-w-full text-sm divide-y divide-slate-200">
                <thead><tr><th className={th}>{t("run.started")}</th><th className={th}>{t("run.status")}</th><th className={th}>{t("run.orders")}</th><th className={th}>{t("run.lines")}</th><th className={th}>{t("run.error")}</th></tr></thead>
                <tbody className="divide-y divide-slate-200">
                  {runs.map((r) => (
                    <tr key={r.id}><td className={`${td} tabular-nums`}>{dateTimeShort(r.started_at, locale)}</td><td className={td}><span className={r.status === "FAILED" ? "text-red-700" : r.status === "SUCCEEDED" ? "text-emerald-700" : ""}>{t(`status.${r.status}`)}</span></td><td className={`${td} tabular-nums`}>{r.purchase_orders}</td><td className={`${td} tabular-nums`}>{r.lines}</td><td className={`${td} text-xs text-slate-500`}>{r.error ? t("lastErrorGeneric") : ""}</td></tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>
        </div>
      ) : (
        <form ref={formRef} onSubmit={connect} className="p-5 grid gap-3 md:grid-cols-2">
          <div className="md:col-span-2"><label className={label} htmlFor="erp-url">{t("url")}</label><input id="erp-url" name="url" type="url" required placeholder="https://mycompany.odoo.com" className={input} /></div>
          <div><label className={label} htmlFor="erp-db">{t("database")}</label><input id="erp-db" name="database" required className={input} /></div>
          <div><label className={label} htmlFor="erp-login">{t("login")}</label><input id="erp-login" name="login" required autoComplete="off" className={input} /></div>
          <div className="md:col-span-2"><label className={label} htmlFor="erp-key">{t("apiKey")}</label><input id="erp-key" name="api_key" type="password" required autoComplete="new-password" className={`${input} font-mono`} /><p className="text-xs text-slate-500 mt-1">{t("apiKeyHint")}</p></div>
          <div className="md:col-span-2"><button type="submit" disabled={pending} className={btnPrimary}>{pending ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <Plug className="w-4 h-4 mr-2" />}{t("connect")}</button></div>
        </form>
      )}
    </Card>
  );
}
