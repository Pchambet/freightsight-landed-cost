import type { InvoiceLineResponse } from "@/lib/api/client";
import type { ContainerLink } from "@/features/invoices/InvoiceContinueLinks";
import type { Targets } from "@/features/invoices/ReviewPanel";

export function containerLinksFromLines(lines: InvoiceLineResponse[], targets: Targets): ContainerLink[] {
  const seen = new Set<string>();
  const out: ContainerLink[] = [];
  for (const line of lines) {
    if (line.scope !== "CONTAINER" || !line.target_id) continue;
    const match = targets.CONTAINER.find((t) => t.id === line.target_id);
    if (!match || seen.has(match.id)) continue;
    seen.add(match.id);
    out.push({ id: match.id, container_number: match.label });
  }
  return out;
}
