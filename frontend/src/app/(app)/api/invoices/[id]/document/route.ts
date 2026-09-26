import { auth } from "@clerk/nextjs/server";
import { env } from "@/lib/env";
import { protectAction } from "@/lib/auth";

/** Streams the invoice document through the app so the browser never talks to the API directly. */
export async function GET(_request: Request, { params }: { params: Promise<{ id: string }> }) {
  await protectAction();
  const { id } = await params;
  const { getToken } = await auth();
  const token = await getToken();
  const headers: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};
  if (env.ORG_ID) headers["X-Org-Id"] = env.ORG_ID;
  const upstream = await fetch(`${env.API_URL}/api/v1/invoices/${encodeURIComponent(id)}/document`, { headers, cache: "no-store" });
  if (!upstream.ok || !upstream.body) return new Response(null, { status: upstream.status === 404 ? 404 : 502 });
  return new Response(upstream.body, {
    status: 200,
    headers: {
      "Content-Type": upstream.headers.get("content-type") ?? "application/octet-stream",
      "Content-Disposition": upstream.headers.get("content-disposition") ?? "inline",
      "Cache-Control": "private, no-store",
      "X-Content-Type-Options": "nosniff",
    },
  });
}
