
import json
import os
import re
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo

with open("orders_clean.json", encoding="utf-8") as f:
    ORDERS = json.load(f)

TZ = ZoneInfo("Asia/Kolkata")

MONTHS = {
    "january": 1, "february": 2, "march": 3,
    "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9,
    "october": 10, "november": 11, "december": 12,
}

PRODUCTS = [
    "Air Fryer", "Blender", "Grinder", "Juicer",
    "Kettle", "Mixer", "Rice Cooker", "Toaster"
]

REGIONS = ["North", "South", "East", "West", "Central"]

RANKING_WORDS = [
    "most", "highest", "top", "lowest", "maximum",
    "minimum", "best", "worst", "least"
]


def business_date(order):
    value = order["created_at"].replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)

    return dt.astimezone(TZ).date()


def usd(order):
    return Decimal(str(order["amount_usd"]))


def money(value):
    return float(
        value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    )


def answer_question(question):
    q = question.lower()
    q = re.sub(r"\s+", " ", q).strip()

    # Determine status. Paid orders are the default.
    if "all statuses" in q or "all orders" in q:
        statuses = {"paid", "refunded", "void"}
    elif "refund" in q:
        statuses = {"refunded"}
    elif any(word in q for word in ["void", "cancelled", "canceled"]):
        statuses = {"void"}
    else:
        statuses = {"paid"}

    orders = [
        o for o in ORDERS
        if o.get("status", "").lower() in statuses
    ]

    # Filter by business month and year.
    month = next(
        (number for name, number in MONTHS.items() if name in q),
        None
    )

    year_match = re.search(r"\b(2025|2026|2027)\b", q)
    year = int(year_match.group(1)) if year_match else None

    if month is not None:
        orders = [
            o for o in orders
            if business_date(o).month == month
        ]

    if year is not None:
        orders = [
            o for o in orders
            if business_date(o).year == year
        ]

    # Filter by region.
    region = next(
        (
            r for r in REGIONS
            if re.search(r"\b" + re.escape(r.lower()) + r"\b", q)
        ),
        None
    )

    if region:
        orders = [
            o for o in orders
            if o.get("region", "").lower() == region.lower()
        ]

    # Filter by an explicitly named product.
    product = next(
        (p for p in PRODUCTS if p.lower() in q),
        None
    )

    if product:
        orders = [
            o for o in orders
            if o.get("product", "").lower() == product.lower()
        ]

    # Filter by a customer ID if one is mentioned.
    customer_match = re.search(r"\bc\d+\b", q, re.IGNORECASE)

    if customer_match:
        customer_id = customer_match.group(0).upper()
        orders = [
            o for o in orders
            if str(o.get("customer", "")).upper() == customer_id
        ]

     # Count distinct customers before ranking or counting.
    if any(phrase in q for phrase in [
        "distinct customer", "distinct customers",
        "unique customer", "unique customers",
        "different customer", "different customers"
    ]):
        return len({
            o["customer"] for o in orders
            if o.get("customer") is not None
        })

    # Identify ranking questions. "At least" must not trigger ranking.
    ranking = (
        any(word in q for word in [
            "most", "highest", "top", "lowest",
            "maximum", "minimum", "best", "worst"
        ])
        or ("least" in q and "at least" not in q)
    )

    if ranking:
        # Determine what is being ranked.
        if "product" in q:
            group_key = "product"
        elif "customer" in q:
            group_key = "customer"
        elif "region" in q:
            group_key = "region"
        else:
            group_key = None

        if group_key:
            groups = {}

            for order in orders:
                name = order.get(group_key)
                if name is None:
                    continue

                if name not in groups:
                    groups[name] = {
                        "revenue": Decimal("0"),
                        "count": 0
                    }

                groups[name]["revenue"] += usd(order)
                groups[name]["count"] += 1

            if groups:
                # Use order counts when the question asks for orders.
                count_metric = any(
                    phrase in q for phrase in [
                        "most orders", "fewest orders",
                        "least orders", "number of orders",
                        "order count", "most popular",
                        "most frequently"
                    ]
                )

                metric = "count" if count_metric else "revenue"

                ascending = (
                    any(word in q for word in [
                        "lowest", "minimum", "worst"
                    ])
                    or ("least" in q and "at least" not in q)
                    or ("fewest" in q)
                )

                ranked = sorted(
                    groups.items(),
                    key=lambda item: (
                        item[1][metric],
                        str(item[0]).lower()
                    ),
                    reverse=not ascending
                )

                return ranked[0][0]

    # Calculate average after all applicable filters.
    if any(word in q for word in ["average", "mean"]):
        if not orders:
            return 0

        total = sum(
            (usd(o) for o in orders),
            Decimal("0")
        )

        return money(total / Decimal(len(orders)))

    # Count questions.
    count_phrases = [
        "how many", "number of", "count",
        "quantity of", "how much many"
    ]

    if any(phrase in q for phrase in count_phrases):
        return len(orders)

    # Revenue and monetary total questions.
    revenue_phrases = [
        "revenue", "sales", "earned", "amount",
        "total", "sum", "how much", "how many dollars",
        "worth", "value in usd", "value in us dollars"
    ]

    if any(phrase in q for phrase in revenue_phrases):
        total = sum(
            (usd(o) for o in orders),
            Decimal("0")
        )
        return money(total)

    # General order-count questions.
    if "orders" in q or "order" in q:
        return len(orders)

    return {
        "error": "Unsupported question",
        "question": question
    }


class Handler(BaseHTTPRequestHandler):
    def send_json(self, status, data):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.send_json(200, {
            "status": "ok",
            "service": "Ledger Agent",
            "orders_loaded": len(ORDERS)
        })

    def do_POST(self):
        if self.path.rstrip("/") not in ("", "/answer"):
            self.send_json(404, {"error": "Not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
            data = json.loads(
                self.rfile.read(length).decode("utf-8")
            )

            question = data.get("question", "")

            if not isinstance(question, str) or not question.strip():
                self.send_json(
                    400, {"error": "A question is required"}
                )
                return

            result = answer_question(question)
            self.send_json(200, {"answer": result})

        except Exception as exc:
            self.send_json(500, {"error": str(exc)})

    def log_message(self, format, *args):
        print(format % args)


if __name__ == "__main__":
    print("Ledger Agent starting on port 8000...")
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", 8000))), Handler).serve_forever()



