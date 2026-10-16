import Link from "next/link";
import { useTranslations } from "next-intl";
import { AlertTriangle } from "lucide-react";

/**
 * Why a figure built on the goods' value reads "—": lines whose order has no exchange rate, counted by
 * the API (`fob_missing_lines`). Said next to the figure, never left for the reader to guess. `href`
 * leads to where the rate is set; a page read outside the application (a shared link) passes none.
 */
export default function FobMissing({ lines, href = "/purchase-orders", className = "" }: { lines: number | null | undefined; href?: string | null; className?: string }) {
  const t = useTranslations("fobMissing");
  if (!lines) return null;
  return (
    <p role="note" className={`flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-900 ${className}`}>
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" aria-hidden />
      <span>
        {t("total", { count: lines })}
        {href ? (
          <>
            {" "}
            <Link href={href} className="font-medium underline underline-offset-2 hover:no-underline print:no-underline">{t("setRate")}</Link>
          </>
        ) : null}
      </span>
    </p>
  );
}

/** The same, for one line or one row of a table: a few words under the dash. */
export function FobMissingLine({ href }: { href?: string | null }) {
  const t = useTranslations("fobMissing");
  return (
    <span className="block text-xs font-normal text-amber-800">
      {href ? <Link href={href} className="underline underline-offset-2 hover:no-underline">{t("line")}</Link> : t("line")}
    </span>
  );
}
