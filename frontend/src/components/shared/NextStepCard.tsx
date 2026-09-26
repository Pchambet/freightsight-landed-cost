import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { btnPrimary, btnSecondary } from "@/components/layout/AppShell";

export type NextStepLink = { href: string; label: string };

/** One obvious next action at the top of a workflow screen. */
export default function NextStepCard({
  title,
  description,
  primary,
  secondary = [],
  variant = "default",
}: {
  title: string;
  description: string;
  primary: NextStepLink;
  secondary?: NextStepLink[];
  variant?: "default" | "success" | "warning";
}) {
  const styles = {
    default: "border-blue-200 bg-blue-50",
    success: "border-emerald-300 bg-emerald-50",
    warning: "border-amber-300 bg-amber-50",
  }[variant];

  return (
    <div className={`rounded-lg border px-4 py-4 mb-6 ${styles}`}>
      <p className="text-xs font-semibold uppercase tracking-wider text-slate-600">{title}</p>
      <p className="text-sm text-slate-800 mt-1">{description}</p>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Link href={primary.href} className={`${btnPrimary} inline-flex items-center`}>
          {primary.label}
          <ArrowRight className="w-4 h-4 ml-2" />
        </Link>
        {secondary.map((link) => (
          <Link key={link.href} href={link.href} className={btnSecondary}>
            {link.label}
          </Link>
        ))}
      </div>
    </div>
  );
}
