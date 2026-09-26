"use client";

import { useTransition } from "react";
import { useRouter } from "next/navigation";
import { useTranslations } from "next-intl";
import { FilePlus2 } from "lucide-react";
import { toast } from "sonner";
import { btnSecondary } from "@/components/layout/AppShell";
import { createProductsFromOrders } from "@/features/actions";

/** One product sheet per SKU already seen on an order, filled from what the orders last said. */
export default function CreateSheetsButton() {
  const t = useTranslations("skus.sheets");
  const router = useRouter();
  const [pending, startTransition] = useTransition();
  return (
    <button
      type="button"
      disabled={pending}
      className={btnSecondary}
      title={t("hint")}
      onClick={() =>
        startTransition(async () => {
          const result = await createProductsFromOrders();
          if (!result.ok) return void toast.error(result.error);
          toast.success(result.data.created > 0 ? t("created", { count: result.data.created }) : t("none"));
          router.refresh();
        })
      }
    >
      <FilePlus2 className="w-4 h-4 mr-1.5" aria-hidden />
      {pending ? t("pending") : t("create")}
    </button>
  );
}
