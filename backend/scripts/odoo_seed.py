#!/usr/bin/env python3
"""Seed a throwaway Odoo with the exact state a landed cost can be pushed into.

Everything the write-back needs and nothing else: a product category valued in real time with
FIFO, one storable product carrying a SKU, a confirmed purchase order, and a *validated* receipt.
Odoo refuses a landed cost at validation when the category is `standard` / `manual_periodic` —
which is what every demo product is — so the category is the whole point of this script.

Pure stdlib and pure XML-RPC: no Odoo screen, no FreightSight import. Run it against a disposable
instance only (see docs/odoo-questions.md section 5); it creates records.

    python scripts/odoo_seed.py --url http://localhost:8169 --db fstest

Idempotent: every object is looked up before it is created, so a second run reuses the first run's
records and prints the same summary. It never validates anything but the receipt, and it writes
no landed cost — that is what we are testing.
"""

from __future__ import annotations

import argparse
import sys
import xmlrpc.client
from typing import Any

# The SKU is the only identifier Odoo and FreightSight share, so it is what the container's cost
# lines must carry on our side. Keep these three in step with whatever you create in FreightSight —
# same SKU and unit price as the tyre PO line in `app.domain.sample_data` (docs/demo-checklist.md's
# Odoo scene reuses that story rather than inventing a second one; a design partner not running the
# tyre demo data should seed this into its own, separate organization — see that checklist).
SKU = "TYR-20555R16-91V"
PRODUCT_NAME = "Pneu tourisme 205/55 R16 91V"
QUANTITY = 100.0
UNIT_PRICE = 14.0

CATEGORY_NAME = "FreightSight FIFO"
FREIGHT_SKU = "FS-FREIGHT"
VENDOR_NAME = "Zhejiang Kaiyuan Tyre Co."


class Odoo:
    def __init__(self, url: str, db: str, login: str, password: str) -> None:
        self.db, self.login, self.password = db, login, password
        self._models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object", allow_none=True)
        common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common", allow_none=True)
        # A wrong password is not an exception here: authenticate returns False, and every later
        # call fails somewhere unrecognisable. Say so now.
        uid: Any = common.authenticate(db, login, password, {})
        if not uid:
            raise SystemExit(f"authentication refused for {login!r} on {db!r}")
        self.uid = int(uid)
        version: Any = common.version()
        self.version = version["server_version"]

    def kw(self, model: str, method: str, *args: Any, **kwargs: Any) -> Any:
        try:
            return self._models.execute_kw(
                self.db, self.uid, self.password, model, method, list(args), kwargs
            )
        except xmlrpc.client.Fault as exc:
            # Odoo returns the whole Python traceback; only its last line means anything to a human.
            reason = (exc.faultString or "").strip().splitlines()[-1]
            raise SystemExit(f"{model}.{method} failed: {reason}") from exc

    def ensure(self, model: str, domain: list[Any], values: dict[str, Any]) -> int:
        """Find one record matching `domain`, or create it. Returns its id."""
        found = self.kw(model, "search", domain, limit=1, order="id")
        if found:
            return int(found[0])
        return int(self.kw(model, "create", values))


def _books_in_euros(o: Odoo) -> None:
    """The demo organisation works in EUR, and FreightSight refuses to write into books kept in
    another currency (`erp_currency_mismatch`). A fresh Odoo database is "My Company", in USD:
    switch it while it is still empty. Odoo refuses the change once a journal item exists, and the
    demo data books some on its own — then the seed carries on and says what that means."""
    # The company this login works in, not the first of the table: a database with demo data has
    # two, and the other one sorts first.
    mine = o.kw("res.users", "read", [o.uid], fields=["company_id"])[0]["company_id"][0]
    company = o.kw("res.company", "read", [mine], fields=["name", "currency_id"])[0]
    currency = company["currency_id"][1]
    if currency == "EUR":
        return
    if o.kw("account.move.line", "search_count", [["company_id", "=", mine]]):
        print(
            f"warning: {company['name']} keeps its books in {currency} and already has journal items, "
            "so its currency stays. A FreightSight organisation in EUR will refuse to push here; for "
            "the demo, create the database with --without-demo=all (docs/odoo-questions.md §5).",
            file=sys.stderr,
        )
        return
    # EUR ships inactive in a database created with a US company.
    euro = o.kw("res.currency", "search", [["name", "=", "EUR"]], context={"active_test": False})
    if not euro:
        raise SystemExit("this Odoo has no EUR currency record")
    o.kw("res.currency", "write", euro, {"active": True})
    o.kw("res.company", "write", [mine], {"currency_id": euro[0]})


def seed(o: Odoo) -> dict[str, Any]:
    missing = o.kw(
        "ir.module.module",
        "search_read",
        [["name", "in", ["purchase", "stock_landed_costs"]], ["state", "!=", "installed"]],
        fields=["name", "state"],
    )
    if missing:
        names = ", ".join(f"{m['name']} ({m['state']})" for m in missing)
        raise SystemExit(f"install these modules first: {names}")

    _books_in_euros(o)

    # 1. The category. `real_time` + `fifo` is the condition Odoo checks when a landed cost is
    #    validated; anything else and the push we are about to test would be refused later, in
    #    someone else's books rather than here.
    category = o.ensure(
        "product.category",
        [["name", "=", CATEGORY_NAME]],
        {"name": CATEGORY_NAME, "property_cost_method": "fifo", "property_valuation": "real_time"},
    )
    o.kw(
        "product.category",
        "write",
        [category],
        {"property_cost_method": "fifo", "property_valuation": "real_time"},
    )

    # 2. The goods. Storable, because a landed cost adjusts a stock valuation layer.
    product = o.ensure(
        "product.product",
        [["default_code", "=", SKU]],
        {
            "name": PRODUCT_NAME,
            "default_code": SKU,
            "type": "product",
            "categ_id": category,
            "purchase_ok": True,
            "standard_price": UNIT_PRICE,
            "list_price": UNIT_PRICE * 2,
        },
    )
    o.kw("product.product", "write", [product], {"categ_id": category})

    # 3. The cost carrier: a service product flagged `landed_cost_ok`. The connector picks the
    #    first one by id when the connection does not name one.
    freight = o.ensure(
        "product.product",
        [["default_code", "=", FREIGHT_SKU]],
        {
            "name": "FreightSight landed cost",
            "default_code": FREIGHT_SKU,
            "type": "service",
            "landed_cost_ok": True,
        },
    )

    vendor = o.ensure("res.partner", [["name", "=", VENDOR_NAME]], {"name": VENDOR_NAME, "supplier_rank": 1})

    # 4. The order. Reuse the one this script already made rather than piling up receipts: the
    #    push refuses a container whose orders have several receipts, and that is a different test.
    existing = o.kw(
        "purchase.order",
        "search_read",
        [["partner_id", "=", vendor], ["origin", "=", "freightsight-seed"]],
        fields=["name", "state", "picking_ids"],
        limit=1,
        order="id",
    )
    if existing:
        order_id, order = int(existing[0]["id"]), existing[0]
    else:
        uom = o.kw("product.product", "read", [product], fields=["uom_po_id"])[0]["uom_po_id"][0]
        order_id = int(
            o.kw(
                "purchase.order",
                "create",
                {
                    "partner_id": vendor,
                    "origin": "freightsight-seed",
                    "order_line": [
                        (
                            0,
                            0,
                            {
                                "product_id": product,
                                "name": PRODUCT_NAME,
                                "product_qty": QUANTITY,
                                "product_uom": uom,
                                "price_unit": UNIT_PRICE,
                            },
                        )
                    ],
                },
            )
        )
        order = o.kw("purchase.order", "read", [order_id], fields=["name", "state", "picking_ids"])[0]

    if order["state"] in ("draft", "sent"):
        o.kw("purchase.order", "button_confirm", [order_id])
        order = o.kw("purchase.order", "read", [order_id], fields=["name", "state", "picking_ids"])[0]

    # 5. The receipt. Confirming the order creates it; validating it is what makes a landed cost
    #    possible at all. In 17 the received amount is `quantity` and the line must be `picked`,
    #    otherwise button_validate answers with an "immediate transfer" wizard instead of a done
    #    picking — a dict where you expected a state.
    picking_id = int(order["picking_ids"][0])
    picking = o.kw("stock.picking", "read", [picking_id], fields=["name", "state"])[0]
    if picking["state"] != "done":
        moves = o.kw(
            "stock.move", "search_read", [["picking_id", "=", picking_id]], fields=["product_uom_qty"]
        )
        for move in moves:
            o.kw("stock.move", "write", [move["id"]], {"quantity": move["product_uom_qty"], "picked": True})
        result = o.kw("stock.picking", "button_validate", [picking_id])
        if isinstance(result, dict) and result.get("res_model"):
            raise SystemExit(
                f"Odoo answered button_validate with a {result['res_model']} wizard; "
                "the receipt was not validated"
            )
        picking = o.kw("stock.picking", "read", [picking_id], fields=["name", "state"])[0]

    journal = o.kw("account.journal", "search", [["type", "=", "general"]], limit=1, order="id")
    moves = o.kw(
        "stock.move",
        "search_read",
        [["picking_id", "=", picking_id]],
        fields=["product_id", "quantity", "purchase_line_id"],
    )
    return {
        "server_version": o.version,
        "category_id": category,
        "product_id": product,
        "landed_cost_product_id": freight,
        "purchase_order": {"id": order_id, "name": order["name"], "state": order["state"]},
        "picking": {"id": picking_id, "name": picking["name"], "state": picking["state"]},
        "moves": moves,
        "journal_id": journal[0] if journal else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--url", default="http://localhost:8169")
    parser.add_argument("--db", default="fstest")
    parser.add_argument("--login", default="admin")
    # A throwaway container's admin/admin, not a credential: never point this at a real Odoo.
    parser.add_argument("--password", default="admin")
    args = parser.parse_args()

    state = seed(Odoo(args.url, args.db, args.login, args.password))
    picking, order = state["picking"], state["purchase_order"]
    print(f"Odoo {state['server_version']} — {args.db} @ {args.url}")
    print(f"  category {state['category_id']} (fifo / real_time)")
    print(f"  product  {state['product_id']}  {SKU}  {PRODUCT_NAME}")
    print(f"  freight  {state['landed_cost_product_id']}  {FREIGHT_SKU} (landed_cost_ok)")
    print(f"  journal  {state['journal_id']}")
    print(f"  order    {order['id']}  {order['name']}  [{order['state']}]")
    print(f"  receipt  {picking['id']}  {picking['name']}  [{picking['state']}]")
    for move in state["moves"]:
        print(f"    move {move['id']}  {move['product_id'][1]}  qty {move['quantity']}")
    print()
    print("In FreightSight, build the container against this order:")
    print(f"  po_number = {order['name']}   sku = {SKU}   quantity = {QUANTITY:g}")
    print("  then add at least one actual cost, and call the landed-cost preview.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
