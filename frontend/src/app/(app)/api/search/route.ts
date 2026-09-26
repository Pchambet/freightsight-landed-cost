import { NextResponse } from "next/server";
import { api } from "@/lib/api/client";
import { protectAction } from "@/lib/auth";

/** Server-side proxy for the command palette: the browser never talks to the API itself. */
export async function GET(request: Request) {
  await protectAction();
  const q = (new URL(request.url).searchParams.get("q") ?? "").trim().slice(0, 80);
  if (q.length < 2) return NextResponse.json({ query: q, results: [] });
  const { data, error } = await api.GET("/api/v1/search", { params: { query: { q, limit: 12 } } });
  if (error || !data) return NextResponse.json({ query: q, results: [] }, { status: 502 });
  return NextResponse.json(data, { headers: { "Cache-Control": "no-store" } });
}
