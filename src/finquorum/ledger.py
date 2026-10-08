"""Read-only ledger queries: spending by category, budget vs actual,
transaction search, and recurring-charge detection."""

import sqlite3
from collections import defaultdict
from datetime import datetime


def get_or_create_category(conn: sqlite3.Connection, name: str) -> int:
    row = conn.execute("SELECT id FROM categories WHERE name = ?", (name,)).fetchone()
    if row:
        return row["id"]
    cur = conn.execute("INSERT INTO categories (name) VALUES (?)", (name,))
    conn.commit()
    return cur.lastrowid


def spending_by_category(conn: sqlite3.Connection, year_month: str | None = None) -> list[dict]:
    """Sum of transaction amounts per category, optionally filtered to a
    single 'YYYY-MM' month. Only counts spending (negative amounts) and
    excludes transfers."""
    sql = """
        SELECT COALESCE(c.name, 'Uncategorized') AS category,
               SUM(t.amount_cents) AS total_cents,
               COUNT(*) AS tx_count
        FROM transactions t
        LEFT JOIN categories c ON c.id = t.category_id
        WHERE t.is_transfer = 0 AND t.split_parent_id IS NULL
    """
    params: list = []
    if year_month:
        sql += " AND substr(t.posted_date, 1, 7) = ?"
        params.append(year_month)
    sql += " GROUP BY category ORDER BY total_cents ASC"
    rows = conn.execute(sql, params).fetchall()
    return [
        {"category": r["category"], "total_cents": r["total_cents"], "tx_count": r["tx_count"]}
        for r in rows
    ]


def budget_vs_actual(conn: sqlite3.Connection, year_month: str) -> list[dict]:
    """Budgeted vs actual spend per category for a given 'YYYY-MM' month."""
    budgets = conn.execute(
        """SELECT c.name AS category, b.amount_cents AS budget_cents
           FROM budgets b JOIN categories c ON c.id = b.category_id
           WHERE b.year_month = ?""",
        (year_month,),
    ).fetchall()
    actuals = {row["category"]: row["total_cents"] for row in spending_by_category(conn, year_month)}

    result = []
    for b in budgets:
        actual = actuals.get(b["category"], 0)
        result.append(
            {
                "category": b["category"],
                "budget_cents": b["budget_cents"],
                "actual_cents": actual,
                # spending is negative; "over" means |actual| > budget
                "remaining_cents": b["budget_cents"] + actual,
            }
        )
    return result


def search_transactions(
    conn: sqlite3.Connection,
    query: str | None = None,
    category: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    limit: int = 200,
) -> list[dict]:
    sql = """
        SELECT t.id, t.posted_date, t.payee, t.amount_cents, t.memo,
               COALESCE(c.name, 'Uncategorized') AS category, t.is_transfer
        FROM transactions t
        LEFT JOIN categories c ON c.id = t.category_id
        WHERE 1=1
    """
    params: list = []
    if query:
        sql += " AND (t.payee LIKE ? OR t.memo LIKE ?)"
        params.extend([f"%{query}%", f"%{query}%"])
    if category:
        sql += " AND c.name = ?"
        params.append(category)
    if start_date:
        sql += " AND t.posted_date >= ?"
        params.append(start_date)
    if end_date:
        sql += " AND t.posted_date <= ?"
        params.append(end_date)
    sql += " ORDER BY t.posted_date DESC LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def _parse_date(d: str) -> datetime:
    return datetime.strptime(d, "%Y-%m-%d")


def detect_recurring_charges(
    conn: sqlite3.Connection,
    amount_tolerance_pct: float = 0.05,
    min_occurrences: int = 3,
    cadence_days: tuple[int, int] = (25, 35),
) -> list[dict]:
    """Simple heuristic: group transactions by payee, then within a payee
    group transactions whose amounts are within amount_tolerance_pct of each
    other. If >= min_occurrences such transactions have gaps between
    consecutive occurrences all within cadence_days (default ~monthly),
    flag it as a recurring charge."""
    rows = conn.execute(
        "SELECT payee, posted_date, amount_cents FROM transactions "
        "WHERE is_transfer = 0 AND split_parent_id IS NULL ORDER BY payee, posted_date"
    ).fetchall()

    by_payee = defaultdict(list)
    for r in rows:
        by_payee[r["payee"]].append((r["posted_date"], r["amount_cents"]))

    recurring = []
    for payee, txs in by_payee.items():
        # cluster by similar amount
        clusters: list[list[tuple[str, int]]] = []
        for date, amount in txs:
            placed = False
            for cluster in clusters:
                ref_amount = cluster[0][1]
                if ref_amount == 0:
                    continue
                if abs(amount - ref_amount) / abs(ref_amount) <= amount_tolerance_pct:
                    cluster.append((date, amount))
                    placed = True
                    break
            if not placed:
                clusters.append([(date, amount)])

        for cluster in clusters:
            if len(cluster) < min_occurrences:
                continue
            dates = sorted(_parse_date(d) for d, _ in cluster)
            gaps = [(dates[i + 1] - dates[i]).days for i in range(len(dates) - 1)]
            if all(cadence_days[0] <= g <= cadence_days[1] for g in gaps):
                avg_amount = sum(a for _, a in cluster) / len(cluster)
                recurring.append(
                    {
                        "payee": payee,
                        "occurrences": len(cluster),
                        "avg_amount_cents": round(avg_amount),
                        "first_date": dates[0].strftime("%Y-%m-%d"),
                        "last_date": dates[-1].strftime("%Y-%m-%d"),
                        "avg_gap_days": round(sum(gaps) / len(gaps)),
                    }
                )
    return recurring
