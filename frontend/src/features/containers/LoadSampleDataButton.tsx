"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { Sparkles } from "lucide-react";
import { toast } from "sonner";
import { btnPrimary, btnSecondary } from "@/components/layout/AppShell";
import { loadSampleData } from "@/features/actions";

/**
 * Empty-state shortcut. `reference` fills the organization with one demo shipment, enough to learn
 * the screens; `history` with six months of arrivals, which is what the overview and the SKU
 * curves need to say anything.
 */
export default function LoadSampleDataButton({ profile = "reference", primary = false }: { profile?: "reference" | "history"; primary?: boolean }) {
  const t = useTranslations("containers.sample");
  const [pending, startTransition] = useTransition();
  const router = useRouter();

  return (
    <button
      type="button"
      disabled={pending}
      className={primary ? btnPrimary : btnSecondary}
      onClick={() =>
        startTransition(async () => {
          const result = await loadSampleData(profile);
          if (!result.ok) return void toast.error(result.error);
          toast.success(t("loaded"));
          router.refresh();
        })
      }
    >
      <Sparkles className="w-4 h-4 mr-1.5" />
      {pending ? t("loading") : t(profile === "history" ? "loadHistory" : "load")}
    </button>
  );
}
