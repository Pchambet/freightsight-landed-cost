import Link from "next/link";
import { ArrowRight } from "lucide-react";
import { btnPrimary } from "@/components/layout/AppShell";

export type ContainerLink = { id: string; container_number: string };

/** After an invoice is confirmed, send the person to the container whose landed cost changed. */
export default function InvoiceContinueLinks({
  containers,
  title,
  hint,
  primaryLabel,
}: {
  containers: ContainerLink[];
  title: string;
  hint: string;
  primaryLabel: (containerNumber: string) => string;
}) {
  if (containers.length === 0) return null;
  return (
    <div className="rounded-md border border-emerald-300 bg-emerald-50 px-4 py-3 mb-6">
      <p className="text-sm font-medium text-emerald-950">{title}</p>
      <p className="text-sm text-emerald-800 mt-1">{hint}</p>
      <div className="mt-3 flex flex-wrap gap-2">
        {containers.map((c) => (
          <Link key={c.id} href={`/container/${c.id}#couts-unitaires`} className={`${btnPrimary} inline-flex items-center`}>
            {primaryLabel(c.container_number)}
            <ArrowRight className="w-4 h-4 ml-2" />
          </Link>
        ))}
      </div>
    </div>
  );
}
