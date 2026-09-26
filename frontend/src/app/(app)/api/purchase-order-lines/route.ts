import { NextResponse } from "next/server";
import { api } from "@/lib/api/client";
import { protectAction } from "@/lib/auth";

/** Small server-side proxy so the loads editor can list a PO's lines without exposing the API. */
export async function GET(request: Request) {
  await protectAction();
  const poId = new URL(request.url).searchParams.get("po_id");
  if (!poId) return NextResponse.json({ error: "po_id required" }, { status: 400 });
  const { data, error } = await api.GET("/api/v1/purchase-orders/{po_id}", { params: { path: { po_id: poId } } });
  if (error || !data) return NextResponse.json({ error: "not found" }, { status: 404 });
  return NextResponse.json(data.lines.map((l) => ({ id: l.id, line_no: l.line_no, sku: l.sku, quantity: l.quantity })));
}
