"use client";

import { useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useLocale, useTranslations } from "next-intl";
import { FileUp, Loader2, X } from "lucide-react";
import { toast } from "sonner";
import { btnPrimary, btnSecondary, label } from "@/components/layout/AppShell";
import { uploadInvoice } from "@/features/actions";
import { MAX_UPLOAD_BYTES, fileSize, isTooLarge } from "@/lib/uploads";

const TYPES = ["application/pdf", "image/png", "image/jpeg"];
const readable = (f: File) => TYPES.includes(f.type) || /\.(pdf|png|jpe?g)$/i.test(f.name);

type Status = "ready" | "tooLarge" | "badType" | "sending" | "created" | "duplicate" | "failed";
type Item = { key: string; file: File; status: Status; invoiceId?: string; error?: string };

const keyOf = (f: File) => `${f.name}|${f.size}|${f.lastModified}`;
const sent = (s: Status) => s === "created" || s === "duplicate" || s === "failed";

const STATUS_STYLE: Record<Status, string> = {
  ready: "text-slate-600",
  sending: "text-blue-700",
  created: "text-emerald-700",
  duplicate: "text-amber-800",
  failed: "text-red-700",
  tooLarge: "text-red-700",
  badType: "text-red-700",
};

/**
 * One invoice, or a quarter's worth dropped at once. The files go one after the other — Next
 * dispatches a page's Server Actions one at a time, so a "parallel" queue would only pretend — and
 * each gets its own outcome: uploaded, already received (the same file: its invoice is linked),
 * refused. A single file goes straight to its review, as a drop always did; several stay here, and
 * the first one opens the review, whose "next invoice" walks through the rest.
 */
export default function UploadForm() {
  const t = useTranslations("invoices.upload");
  const tu = useTranslations("common.upload");
  const locale = useLocale();
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [run, setRun] = useState<{ done: number; total: number } | null>(null);
  const [over, setOver] = useState(false);
  const running = run !== null;

  const add = (files: FileList | null) => {
    if (!files?.length || running) return;
    setItems((prev) => {
      // After a finished batch, a new choice starts a new list: the table below holds what was sent.
      const kept = prev.some((i) => sent(i.status)) ? [] : prev;
      const seen = new Set(kept.map((i) => i.key));
      const fresh = [...files]
        .filter((f) => !seen.has(keyOf(f)))
        .map((f): Item => ({ key: keyOf(f), file: f, status: !readable(f) ? "badType" : isTooLarge(f) ? "tooLarge" : "ready" }));
      return [...kept, ...fresh];
    });
    if (inputRef.current) inputRef.current.value = "";
  };

  const update = (key: string, patch: Partial<Item>) => setItems((prev) => prev.map((i) => (i.key === key ? { ...i, ...patch } : i)));

  const send = async () => {
    const queue = items.filter((i) => i.status === "ready");
    if (!queue.length || running) return;
    setRun({ done: 0, total: queue.length });
    const outcomes: Partial<Item>[] = [];
    for (const [n, item] of queue.entries()) {
      update(item.key, { status: "sending" });
      const fd = new FormData();
      fd.append("file", item.file);
      // Refused before the action runs (connection dropped, a body over a limit): it rejects.
      const r = await uploadInvoice(fd).catch(() => null);
      const outcome: Partial<Item> = !r
        ? { status: "failed", error: t("failed") }
        : r.ok
          ? { status: "created", invoiceId: r.data.id }
          : r.duplicateOf
            ? { status: "duplicate", invoiceId: r.duplicateOf }
            : { status: "failed", error: r.error };
      update(item.key, outcome);
      outcomes.push(outcome);
      setRun({ done: n + 1, total: queue.length });
    }
    setRun(null);
    const only = outcomes[0];
    if (items.length === 1 && only?.invoiceId) {
      if (only.status === "duplicate") toast.info(t("duplicateOpened"));
      else toast.success(t("done"));
      router.push(`/invoices/${only.invoiceId}`);
      return;
    }
    router.refresh();
  };

  const count = (s: Status) => items.filter((i) => i.status === s).length;
  const ready = count("ready");
  const firstCreated = items.find((i) => i.status === "created")?.invoiceId;
  const finished = !running && items.some((i) => sent(i.status));

  const statusText = (i: Item) => {
    switch (i.status) {
      case "tooLarge":
        return t("status.tooLarge", { max: fileSize(MAX_UPLOAD_BYTES, locale) });
      case "failed":
        return t("status.failed", { reason: i.error ?? t("failed") });
      default:
        return t(`status.${i.status}`);
    }
  };

  return (
    <div className="p-5 space-y-4">
      <div>
        <span id="invoice-file-label" className={label}>{t("fileLabel")}</span>
        <label
          aria-labelledby="invoice-file-label"
          onDragOver={(e) => {
            e.preventDefault();
            if (!running) setOver(true);
          }}
          onDragLeave={() => setOver(false)}
          onDrop={(e) => {
            e.preventDefault();
            setOver(false);
            add(e.dataTransfer.files);
          }}
          className={`flex items-center gap-3 rounded-md border border-dashed px-4 py-4 focus-within:ring-2 focus-within:ring-blue-500 ${
            running ? "border-slate-200 cursor-default" : over ? "border-blue-400 bg-blue-50 cursor-copy" : "border-slate-300 hover:bg-slate-50 cursor-pointer"
          }`}
        >
          <FileUp className="w-5 h-5 text-slate-400 shrink-0" aria-hidden />
          <span className="text-sm text-slate-600">{t("chooseMany")}</span>
          <input
            ref={inputRef}
            id="invoice-file"
            type="file"
            multiple
            accept="application/pdf,image/png,image/jpeg"
            className="sr-only"
            disabled={running}
            onChange={(e) => add(e.target.files)}
          />
        </label>
      </div>

      {items.length ? (
        <ul className="divide-y divide-slate-100 rounded-md border border-slate-200" aria-label={t("listLabel")}>
          {items.map((i) => (
            <li key={i.key} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2 text-sm">
              <span className="min-w-0 flex-1 truncate font-medium text-slate-800">{i.file.name}</span>
              <span className="text-xs text-slate-500 tabular-nums">{fileSize(i.file.size, locale)}</span>
              <span className={`inline-flex items-center gap-1.5 ${STATUS_STYLE[i.status]}`}>
                {i.status === "sending" ? <Loader2 className="w-3.5 h-3.5 animate-spin" aria-hidden /> : null}
                {statusText(i)}
              </span>
              {i.invoiceId ? (
                <Link href={`/invoices/${i.invoiceId}`} className="text-blue-700 underline underline-offset-2 hover:no-underline">
                  {t("open")}<span className="sr-only"> {i.file.name}</span>
                </Link>
              ) : null}
              {!running && !sent(i.status) && i.status !== "sending" ? (
                <button
                  type="button"
                  aria-label={t("remove", { name: i.file.name })}
                  onClick={() => setItems((prev) => prev.filter((x) => x.key !== i.key))}
                  className="-m-1 p-1 rounded text-slate-500 hover:text-red-600 focus:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                >
                  <X className="w-4 h-4" aria-hidden />
                </button>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
      {count("tooLarge") ? <p className="text-sm text-slate-600">{tu("tooLargeInvoice")}</p> : null}

      <div className="flex flex-wrap items-center gap-3">
        <button type="button" onClick={send} disabled={!ready || running} className={btnPrimary}>
          {running ? <Loader2 className="w-4 h-4 mr-2 animate-spin" aria-hidden /> : null}
          {running ? t("uploading") : t("submitMany", { count: ready })}
        </button>
        {finished && firstCreated && items.length > 1 ? (
          <Link href={`/invoices/${firstCreated}`} className={btnSecondary}>{t("reviewFirst")}</Link>
        ) : null}
        {/* Present before anything happens, so a screen reader hears the progress and the outcome. */}
        <p role="status" className="text-sm text-slate-600">
          {running ? t("progress", { done: run.done, total: run.total }) : finished ? t("summary", { created: count("created"), duplicate: count("duplicate"), failed: count("failed") }) : ""}
        </p>
      </div>
    </div>
  );
}
