import type { ContainerResponse, ImportJobResponse, LandedCostReport, LoadResponse, PurchaseOrderSummary } from "@/lib/api/client";
import { NEXT_IMPORT_KIND } from "@/lib/domain";

export type WorkflowStep = {
  descriptionKey: string;
  descriptionParams?: Record<string, string | number>;
  primary: { href: string; labelKey: string; labelParams?: Record<string, string | number> };
  secondary: { href: string; labelKey: string; labelParams?: Record<string, string | number> }[];
  variant: "default" | "success" | "warning";
};

const RISKY = new Set(["MEDIUM", "HIGH", "INCURRING"]);

const DEPOSIT_INVOICE = { href: "/invoices", labelKey: "container.depositInvoice" };

export function containerWorkflowStep(
  container: ContainerResponse,
  loads: LoadResponse[],
  report: LandedCostReport,
  invoicesToReview: number,
): WorkflowStep {
  const num = container.container_number;
  const hasLoads = loads.length > 0;
  const actualCosts = report.costs.some((c) => c.status === "ACTUAL");
  const unallocated = report.totals.unallocated !== "0.00";
  const hasUnitCosts = report.lines.length > 0;
  const atRisk = RISKY.has(container.dnd_risk);

  if (!hasLoads) {
    return {
      descriptionKey: "container.noLoads",
      primary: { href: "#chargements", labelKey: "container.loadAction" },
      secondary: [{ href: "/purchase-orders", labelKey: "container.viewPos" }],
      variant: "warning",
    };
  }

  if (invoicesToReview > 0) {
    return {
      descriptionKey: "container.invoicesWaiting",
      descriptionParams: { count: invoicesToReview },
      primary: { href: "/invoices?filter=review", labelKey: "container.reviewInvoices" },
      secondary: [{ href: "#couts-unitaires", labelKey: "container.seeUnitCosts" }],
      variant: "warning",
    };
  }

  if (!actualCosts) {
    return {
      descriptionKey: "container.noCosts",
      primary: { href: "/invoices", labelKey: "container.depositInvoice" },
      secondary: [{ href: "#ajouter-cout", labelKey: "container.addCostManual" }],
      variant: "default",
    };
  }

  if (unallocated) {
    return {
      descriptionKey: "container.unallocated",
      primary: { href: "#ventilation", labelKey: "container.fixAllocation" },
      secondary: [DEPOSIT_INVOICE],
      variant: "warning",
    };
  }

  if (atRisk) {
    return {
      descriptionKey: "container.dndRisk",
      descriptionParams: { container: num },
      primary: { href: "#suivi", labelKey: "container.checkTracking" },
      secondary: [
        DEPOSIT_INVOICE,
        { href: "#couts-unitaires", labelKey: "container.seeUnitCosts" },
        { href: "/reports", labelKey: "container.viewReports" },
      ],
      variant: "warning",
    };
  }

  if (hasUnitCosts) {
    return {
      descriptionKey: "container.unitCostsReady",
      primary: { href: "#couts-unitaires", labelKey: "container.seeUnitCosts" },
      secondary: [
        DEPOSIT_INVOICE,
        { href: "/reports", labelKey: "container.viewReports" },
        { href: "#ventilation", labelKey: "container.detailBreakdown" },
      ],
      variant: "success",
    };
  }

  return {
    descriptionKey: "container.default",
    descriptionParams: { container: num },
    primary: { href: "#ventilation", labelKey: "container.detailBreakdown" },
    secondary: [DEPOSIT_INVOICE, { href: "/reports", labelKey: "container.viewReports" }],
    variant: "default",
  };
}

export function containersHubStep(
  todos: { containerId: string; containerNumber: string; kind: string }[],
  invoicesToReview: number,
  topContainer?: { id: string; number: string },
  atRiskAtTerminal?: { id: string; number: string }[],
): WorkflowStep | null {
  if (invoicesToReview > 0) {
    return {
      descriptionKey: "hub.invoicesWaiting",
      descriptionParams: { count: invoicesToReview },
      primary: { href: "/invoices?filter=review", labelKey: "hub.reviewInvoices" },
      secondary: [],
      variant: "warning",
    };
  }
  const risky = atRiskAtTerminal?.[0];
  if (risky) {
    return {
      descriptionKey: "hub.dndRisk",
      descriptionParams: { container: risky.number },
      primary: {
        href: `/container/${risky.id}#suivi`,
        labelKey: "hub.checkTracking",
        labelParams: { container: risky.number },
      },
      secondary: [
        { href: "/invoices", labelKey: "hub.depositInvoice" },
        { href: `/container/${risky.id}#couts-unitaires`, labelKey: "hub.seeUnitCosts", labelParams: { container: risky.number } },
      ],
      variant: "warning",
    };
  }
  const urgent = todos.find((t) => t.kind === "lfdOver" || t.kind === "lfd") ?? todos[0];
  if (urgent) {
    return {
      descriptionKey: urgent.kind === "lfdOver" || urgent.kind === "lfd" ? "hub.lfd" : "hub.todo",
      descriptionParams: { container: urgent.containerNumber },
      primary: {
        href: `/container/${urgent.containerId}`,
        labelKey: "hub.openContainer",
        labelParams: { container: urgent.containerNumber },
      },
      secondary: [{ href: "/invoices", labelKey: "hub.depositInvoice" }],
      variant: "warning",
    };
  }
  if (topContainer) {
    return {
      descriptionKey: "hub.explore",
      primary: {
        href: `/container/${topContainer.id}#couts-unitaires`,
        labelKey: "hub.seeUnitCosts",
        labelParams: { container: topContainer.number },
      },
      secondary: [{ href: "/reports", labelKey: "container.viewReports" }],
      variant: "success",
    };
  }
  return null;
}

export function invoicesHubStep(review: number, latestReviewId?: string): WorkflowStep {
  if (review > 0 && latestReviewId) {
    return {
      descriptionKey: "invoices.reviewWaiting",
      descriptionParams: { count: review },
      primary: { href: `/invoices/${latestReviewId}`, labelKey: "invoices.openNext" },
      secondary: [{ href: "/containers", labelKey: "invoices.backContainers" }],
      variant: "warning",
    };
  }
  return {
    descriptionKey: "invoices.deposit",
    primary: { href: "#depot", labelKey: "invoices.depositAction" },
    secondary: [{ href: "/containers", labelKey: "invoices.backContainers" }],
    variant: "default",
  };
}

const DEMO_SKU = "TYR-20555R16-91V";

export function reportsHubStep(
  skus: { sku: string }[],
  topContainer?: { id: string; number: string },
): WorkflowStep {
  const topSku = skus.find((s) => s.sku === DEMO_SKU)?.sku ?? skus[0]?.sku;
  if (topContainer) {
    return {
      descriptionKey: "reports.explore",
      descriptionParams: { sku: topSku ?? "" },
      primary: {
        href: `/container/${topContainer.id}#couts-unitaires`,
        labelKey: "reports.openContainer",
        labelParams: { container: topContainer.number },
      },
      secondary: [{ href: "/reports/variance", labelKey: "reports.viewVariance" }],
      variant: "success",
    };
  }
  return {
    descriptionKey: "reports.default",
    primary: { href: "/reports/variance", labelKey: "reports.viewVariance" },
    secondary: [{ href: "/containers", labelKey: "reports.backContainers" }],
    variant: "default",
  };
}

/**
 * `next` is the invoice waiting for review that comes after this one in drop order, and how many
 * wait: a quarter dropped at once is reviewed one invoice after the other, without going back to the
 * list between two.
 */
export function invoiceDetailStep(
  status: string,
  container?: { id: string; number: string },
  next?: { id: string; waiting: number },
): WorkflowStep | null {
  const toNext = next ? { href: `/invoices/${next.id}`, labelKey: "invoiceDetail.nextReview" } : null;
  const skip = next ? [{ href: `/invoices/${next.id}`, labelKey: "invoiceDetail.skip" }] : [];
  if (status === "NEEDS_REVIEW") {
    return {
      descriptionKey: "invoiceDetail.review",
      primary: { href: "#lignes", labelKey: "invoiceDetail.reviewAction" },
      secondary: [...skip, { href: "/invoices", labelKey: "invoiceDetail.backList" }],
      variant: "warning",
    };
  }
  if (status === "CONFIRMED" && toNext && next) {
    return {
      descriptionKey: "invoiceDetail.confirmedNext",
      descriptionParams: { count: next.waiting },
      primary: toNext,
      secondary: container
        ? [{ href: `/container/${container.id}#couts-unitaires`, labelKey: "invoiceDetail.seeUnitCosts", labelParams: { container: container.number } }]
        : [],
      variant: "success",
    };
  }
  if (status === "CONFIRMED" && container) {
    return {
      descriptionKey: "invoiceDetail.confirmed",
      primary: {
        href: `/container/${container.id}#couts-unitaires`,
        labelKey: "invoiceDetail.seeUnitCosts",
        labelParams: { container: container.number },
      },
      secondary: [
        { href: `/container/${container.id}`, labelKey: "invoiceDetail.openContainer", labelParams: { container: container.number } },
        { href: "/reports", labelKey: "container.viewReports" },
      ],
      variant: "success",
    };
  }
  if (status === "REJECTED" && toNext && next) {
    return {
      descriptionKey: "invoiceDetail.rejectedNext",
      descriptionParams: { count: next.waiting },
      primary: toNext,
      secondary: [{ href: "/invoices", labelKey: "invoiceDetail.backList" }],
      variant: "default",
    };
  }
  if (status === "UPLOADED" || status === "EXTRACTING") {
    return {
      descriptionKey: "invoiceDetail.extracting",
      primary: { href: "#lignes", labelKey: "invoiceDetail.waitLines" },
      secondary: skip,
      variant: "default",
    };
  }
  if (status === "FAILED") {
    return {
      descriptionKey: "invoiceDetail.failed",
      primary: { href: "#lignes", labelKey: "invoiceDetail.retryAction" },
      secondary: [...skip, { href: "/invoices", labelKey: "invoiceDetail.backList" }],
      variant: "warning",
    };
  }
  return null;
}

export function importsHubStep(jobs: Pick<ImportJobResponse, "id" | "status" | "kind" | "created_at" | "undone_at">[]): WorkflowStep {
  const pending = jobs.find((j) => j.status === "PARSED" || j.status === "VALIDATED");
  if (pending) {
    return {
      descriptionKey: "imports.resume",
      primary: { href: `/imports/new?job=${pending.id}`, labelKey: "imports.resumeAction" },
      secondary: [{ href: "/imports/new", labelKey: "imports.newImport" }],
      variant: "warning",
    };
  }
  if (jobs.length === 0) {
    return {
      descriptionKey: "imports.start",
      primary: { href: "/imports/new", labelKey: "imports.startAction" },
      secondary: [{ href: "/containers", labelKey: "imports.sampleData" }],
      variant: "default",
    };
  }
  // The quarter goes on from the last file that went in: the next kind, then the audit's list.
  const last = jobs.filter((j) => j.status === "DONE" && !j.undone_at).sort((a, b) => b.created_at.localeCompare(a.created_at))[0];
  const next = last ? NEXT_IMPORT_KIND[last.kind] : undefined;
  if (last && next) {
    return {
      descriptionKey: "imports.next",
      descriptionParams: { last: last.kind, next },
      primary: { href: `/imports/new?kind=${next}`, labelKey: "imports.nextAction", labelParams: { next } },
      secondary: [{ href: "/cost-audit", labelKey: "imports.prepareAudit" }],
      variant: "default",
    };
  }
  if (last?.kind === "COSTS") {
    return {
      descriptionKey: "imports.costsDone",
      primary: { href: "/cost-audit", labelKey: "imports.prepareAudit" },
      secondary: [{ href: "/containers", labelKey: "imports.goContainers" }],
      variant: "success",
    };
  }
  return {
    descriptionKey: "imports.done",
    primary: { href: "/purchase-orders", labelKey: "imports.viewPos" },
    secondary: [{ href: "/containers", labelKey: "imports.goContainers" }],
    variant: "success",
  };
}

export function importNewStep(resuming: boolean): WorkflowStep {
  if (resuming) {
    return {
      descriptionKey: "importNew.resume",
      primary: { href: "#wizard", labelKey: "importNew.continue" },
      secondary: [{ href: "/imports", labelKey: "importNew.backList" }],
      variant: "warning",
    };
  }
  return {
    descriptionKey: "importNew.upload",
    primary: { href: "#wizard", labelKey: "importNew.uploadAction" },
    secondary: [{ href: "/containers", labelKey: "imports.sampleData" }],
    variant: "default",
  };
}

/**
 * The next-step card for the import wizard itself, computed from the wizard's own client-side step
 * instead of the server-rendered "resuming or not" snapshot the page loaded with: the
 * card used to keep telling you to "choose a file" while you were already on the validation
 * report.
 */
export function importWizardStep(step: "upload" | "mapping" | "review" | "done", resuming: boolean, hasErrors: boolean): WorkflowStep {
  if (step === "mapping") {
    return {
      descriptionKey: "importNew.mapping",
      primary: { href: "#wizard", labelKey: "importNew.mappingAction" },
      secondary: resuming ? [] : [{ href: "/imports", labelKey: "importNew.backList" }],
      variant: "default",
    };
  }
  if (step === "review") {
    return {
      descriptionKey: hasErrors ? "importNew.reviewErrors" : "importNew.reviewOk",
      primary: { href: "#wizard", labelKey: "importNew.reviewAction" },
      secondary: [],
      variant: hasErrors ? "warning" : "default",
    };
  }
  if (step === "done") {
    return {
      descriptionKey: "importNew.done",
      primary: { href: "/purchase-orders", labelKey: "importNew.doneAction" },
      secondary: [{ href: "/containers", labelKey: "imports.goContainers" }],
      variant: "success",
    };
  }
  return importNewStep(resuming);
}

export function purchaseOrdersHubStep(pos: PurchaseOrderSummary[]): WorkflowStep {
  if (pos.length === 0) {
    return {
      descriptionKey: "pos.empty",
      primary: { href: "/imports/new", labelKey: "pos.import" },
      secondary: [{ href: "/containers", labelKey: "imports.sampleData" }],
      variant: "default",
    };
  }
  const unloaded = pos.find((p) => !p.container_numbers?.length);
  if (unloaded) {
    return {
      descriptionKey: "pos.notLoaded",
      descriptionParams: { po: unloaded.po_number },
      primary: { href: `/purchase-orders/${unloaded.id}`, labelKey: "pos.openPo", labelParams: { po: unloaded.po_number } },
      secondary: [{ href: "/containers", labelKey: "pos.goContainers" }],
      variant: "warning",
    };
  }
  return {
    descriptionKey: "pos.ready",
    primary: { href: "/containers", labelKey: "pos.goContainers" },
    secondary: [{ href: "/invoices", labelKey: "container.depositInvoice" }],
    variant: "success",
  };
}

export function purchaseOrderDetailStep(
  report: LandedCostReport,
  container?: { id: string; number: string },
): WorkflowStep {
  const loaded = report.lines.length > 0;
  const hasCosts = report.costs.some((c) => c.status === "ACTUAL");

  if (!loaded) {
    return {
      descriptionKey: "poDetail.notLoaded",
      primary: { href: "/containers", labelKey: "poDetail.loadInContainer" },
      secondary: [{ href: "/purchase-orders", labelKey: "poDetail.backList" }],
      variant: "warning",
    };
  }
  if (!hasCosts) {
    return {
      descriptionKey: "poDetail.noCosts",
      primary: { href: "/invoices", labelKey: "container.depositInvoice" },
      secondary: container
        ? [{ href: `/container/${container.id}`, labelKey: "poDetail.openContainer", labelParams: { container: container.number } }]
        : [],
      variant: "default",
    };
  }
  if (container) {
    return {
      descriptionKey: "poDetail.unitCosts",
      primary: {
        href: `/container/${container.id}#couts-unitaires`,
        labelKey: "poDetail.seeUnitCosts",
        labelParams: { container: container.number },
      },
      secondary: [{ href: "#ventilation", labelKey: "container.detailBreakdown" }],
      variant: "success",
    };
  }
  return {
    descriptionKey: "poDetail.ready",
    primary: { href: "#ventilation", labelKey: "container.detailBreakdown" },
    secondary: [{ href: "/containers", labelKey: "pos.goContainers" }],
    variant: "success",
  };
}

export function settingsHubStep(hasRateCards: boolean): WorkflowStep {
  if (!hasRateCards) {
    return {
      descriptionKey: "settings.noRateCards",
      primary: { href: "#rate-cards", labelKey: "settings.addRateCards" },
      secondary: [{ href: "/containers", labelKey: "settings.backContainers" }],
      variant: "default",
    };
  }
  return {
    descriptionKey: "settings.ready",
    primary: { href: "/containers", labelKey: "settings.backContainers" },
    secondary: [{ href: "#erp", labelKey: "settings.connectErp" }],
    variant: "success",
  };
}

export function varianceHubStep(pairs: number): WorkflowStep {
  if (pairs === 0) {
    return {
      descriptionKey: "variance.empty",
      primary: { href: "/containers", labelKey: "variance.backContainers" },
      secondary: [{ href: "/invoices", labelKey: "variance.depositInvoice" }],
      variant: "default",
    };
  }
  return {
    descriptionKey: "variance.hasData",
    primary: { href: "/reports", labelKey: "variance.backReports" },
    secondary: [{ href: "/containers", labelKey: "variance.backContainers" }],
    variant: "success",
  };
}
