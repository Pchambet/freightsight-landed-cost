import { getTranslations } from "next-intl/server";
import NextStepCard from "@/components/shared/NextStepCard";
import type { WorkflowStep } from "@/features/workflow/steps";

export default async function WorkflowNextStep({ step }: { step: WorkflowStep }) {
  const t = await getTranslations("workflow");
  const label = (key: string, params?: Record<string, string | number>) => t(key as never, params as never);

  return (
    <NextStepCard
      title={t("nextStep")}
      description={label(step.descriptionKey, step.descriptionParams)}
      variant={step.variant}
      primary={{ href: step.primary.href, label: label(step.primary.labelKey, step.primary.labelParams) }}
      secondary={step.secondary.map((s) => ({ href: s.href, label: label(s.labelKey, s.labelParams) }))}
    />
  );
}
