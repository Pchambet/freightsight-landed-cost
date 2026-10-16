import ScrollRegion from "@/components/shared/ScrollRegion";
import type { Subprocessor } from "@/features/marketing/subprocessors";

export default function SubprocessorTable({ label, rows, className }: { label: string; rows: Subprocessor[]; className?: string }) {
  return (
    <ScrollRegion label={label} className={`rounded-lg border border-slate-200 ${className ?? ""}`}>
      <table className="min-w-full text-sm">
        <thead className="bg-slate-50 text-xs uppercase tracking-wider text-slate-500">
          <tr>
            <th className="text-left px-4 py-2.5">Fournisseur</th>
            <th className="text-left px-4 py-2.5">Rôle</th>
            <th className="text-left px-4 py-2.5">Localisation</th>
            <th className="text-left px-4 py-2.5">Transfert hors UE</th>
            <th className="text-left px-4 py-2.5">Données concernées</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-200">
          {rows.map((r) => (
            <tr key={r.name}>
              <td className="px-4 py-3 align-top font-medium whitespace-nowrap">{r.name}</td>
              <td className="px-4 py-3 align-top text-slate-700">{r.role}</td>
              <td className="px-4 py-3 align-top text-slate-700">{r.location}</td>
              <td className="px-4 py-3 align-top text-slate-700">{r.transfer}</td>
              <td className="px-4 py-3 align-top text-slate-700">{r.data}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </ScrollRegion>
  );
}
