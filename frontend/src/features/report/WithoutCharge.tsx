import Link from "next/link";
import { useTranslations } from "next-intl";
import { Info } from "lucide-react";

/**
 * Why a landed figure counts fewer containers than the FOB beside it: containers with no charge at
 * all, estimated or invoiced, which the API leaves out of every landed figure (`containers_without_charge`)
 * rather than pass their FOB off as a landed cost. Said next to the figures, never left to be guessed.
 */
export default function WithoutCharge({ containers, href = "/containers", className = "" }: { containers: number | null | undefined; href?: string | null; className?: string }) {
  const t = useTranslations("withoutCharge");
  if (!containers) return null;
  return (
    <p role="note" className={`flex items-start gap-2 rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-sm text-slate-700 ${className}`}>
      <Info className="mt-0.5 h-4 w-4 shrink-0 text-slate-500" aria-hidden />
      <span>
        {t("total", { count: containers })}
        {href ? (
          <>
            {" "}
            <Link href={href} className="font-medium underline underline-offset-2 hover:no-underline print:no-underline">{t("see")}</Link>
          </>
        ) : null}
      </span>
    </p>
  );
}

/** The same under a row: how many of its containers were left out, or, for one container or one of
 * its lines (no `count`), that it has no charge at all. */
export function WithoutChargeLine({ count }: { count?: number }) {
  const t = useTranslations("withoutCharge");
  if (count === 0) return null;
  return <span className="block text-xs font-normal text-slate-500">{count === undefined ? t("box") : t("count", { count })}</span>;
}
