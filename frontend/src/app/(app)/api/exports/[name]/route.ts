import { auth } from "@clerk/nextjs/server";
import { env } from "@/lib/env";
import { protectAction } from "@/lib/auth";

const NAMES = new Set(["landed-costs", "costs", "purchase-orders", "containers", "period-close", "period-drift", "period-accruals", "sku-costs", "audit-findings"]);

/** Streams a CSV export from the API with the caller's session; the browser never talks to the API directly. */
export async function GET(request: Request, { params }: { params: Promise<{ name: string }> }) {
  await protectAction();
  const { name } = await params;
  if (!NAMES.has(name)) return new Response(null, { status: 404 });
  const { getToken } = await auth();
  const token = await getToken();
  const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
  if (env.ORG_ID) headers["X-Org-Id"] = env.ORG_ID;
  const q = new URL(request.url).searchParams;
  const upstream = await fetch(`${env.API_URL}/api/v1/exports/${name}.csv?${q.toString()}`, { headers, cache: "no-store" });
  if (!upstream.ok || !upstream.body) return new Response(null, { status: upstream.status === 404 ? 404 : 502 });
  // A period export is named after its month (it never changes once closed); the others after today.
  // A period export is named after its month (it never changes once closed), the audit after the
  // dates it covers, the others after today.
  const period = q.get("period");
  const [from, to] = [q.get("period_from"), q.get("period_to")];
  const isDay = (v: string | null): v is string => !!v && /^\d{4}-\d{2}-\d{2}$/.test(v);
  const day =
    (name.startsWith("period-") || name === "sku-costs") && period && /^\d{4}-\d{2}$/.test(period)
      ? period
      : name === "audit-findings" && isDay(from) && isDay(to)
        ? `${from}_${to}`
        : new Date().toISOString().slice(0, 10);
  return new Response(upstream.body, {
    status: 200,
    headers: { "Content-Type": "text/csv; charset=utf-8", "Content-Disposition": `attachment; filename="freightsight-${name}-${day}.csv"`, "Cache-Control": "private, no-store" },
  });
}
