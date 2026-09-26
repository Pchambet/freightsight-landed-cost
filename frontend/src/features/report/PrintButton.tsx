"use client";

import { Printer } from "lucide-react";
import { btnSecondary } from "@/components/layout/AppShell";

/** The browser's own print dialog is the PDF export: "Save as PDF" is one of its printers everywhere. */
export default function PrintButton({ label }: { label: string }) {
  return (
    <button type="button" onClick={() => window.print()} className={btnSecondary}>
      <Printer className="w-4 h-4 mr-1.5" aria-hidden />
      {label}
    </button>
  );
}
