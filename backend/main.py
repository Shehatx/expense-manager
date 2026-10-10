from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from .db import connect, init_db

CATEGORIES = (
    "food",
    "transport",
    "shopping",
    "bills",
    "entertainment",
    "health",
    "travel",
    "other",
)
Account = Literal["cash", "credit"]
Action = Literal["add", "subtract", "set"]

app = FastAPI(title="Folio")
FRONTEND = __import__("pathlib").Path(__file__).resolve().parent.parent / "frontend"


def money(cents: int) -> float:
    return cents / 100


def to_cents(amount: float) -> int:
    try:
        quantized = Decimal(str(amount)).quantize(Decimal("0.01"))
    except InvalidOperation as exc:
        raise HTTPException(400, "Enter a valid amount") from exc
    cents = int(quantized * 100)
    if cents < 0 or cents > 100_000_000_00:
        raise HTTPException(400, "Amount is out of range")
    return cents


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _column(account: Account) -> str:
    return "cash_cents" if account == "cash" else "credit_cents"


class ExpenseIn(BaseModel):
    amount: float = Field(gt=0)
    category: str
    method: Account
    note: str = ""
    spent_on: str

    @field_validator("category")
    @classmethod
    def known_category(cls, value: str) -> str:
        if value not in CATEGORIES:
            raise ValueError("Unknown category")
        return value

    @field_validator("note")
    @classmethod
    def trim_note(cls, value: str) -> str:
        value = value.strip()
        if len(value) > 180:
            raise ValueError("Note is too long")
        return value

    @field_validator("spent_on")
    @classmethod
    def valid_day(cls, value: str) -> str:
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise ValueError("Use a YYYY-MM-DD date") from exc
        return value


class AdjustIn(BaseModel):
    account: Account
    action: Action
    amount: float = Field(ge=0)
    note: str = ""

    @field_validator("note")
    @classmethod
    def trim_note(cls, value: str) -> str:
        value = value.strip()
        if len(value) > 180:
            raise ValueError("Note is too long")
        return value


class TransferIn(BaseModel):
    source: Account
    amount: float = Field(gt=0)
    note: str = ""

    @field_validator("note")
    @classmethod
    def trim_note(cls, value: str) -> str:
        value = value.strip()
        if len(value) > 180:
            raise ValueError("Note is too long")
        return value


def apply_delta(db, account: Account, delta: int, kind: str, note: str) -> int:
    column = _column(account)
    current = db.execute(f"SELECT {column} FROM wallet WHERE id = 1").fetchone()[0]
    updated = current + delta
    if updated < 0:
        label = "cash" if account == "cash" else "credit card"
        raise HTTPException(400, f"Not enough {label} for that")
    db.execute(f"UPDATE wallet SET {column} = ? WHERE id = 1", (updated,))
    db.execute(
        """
        INSERT INTO movements (account, kind, delta_cents, balance_after_cents, note, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (account, kind, delta, updated, note, now_iso()),
    )
    return updated


def expense_dict(row) -> dict:
    return {
        "id": row["id"],
        "amount": money(row["amount_cents"]),
        "category": row["category"],
        "method": row["method"],
        "note": row["note"],
        "spent_on": row["spent_on"],
        "created_at": row["created_at"],
    }


@app.on_event("startup")
def startup() -> None:
    init_db()


@app.get("/api/summary")
def summary() -> dict:
    today = date.today()
    month_start = today.replace(day=1).isoformat()
    trend_start = (today - timedelta(days=13)).isoformat()
    with connect() as db:
        wallet = db.execute("SELECT cash_cents, credit_cents FROM wallet WHERE id = 1").fetchone()
        month_rows = db.execute(
            """
            SELECT method, COALESCE(SUM(amount_cents), 0) AS total
            FROM expenses
            WHERE spent_on >= ?
            GROUP BY method
            """,
            (month_start,),
        ).fetchall()
        categories = db.execute(
            """
            SELECT category, COALESCE(SUM(amount_cents), 0) AS total
            FROM expenses
            WHERE spent_on >= ?
            GROUP BY category
            ORDER BY total DESC
            """,
            (month_start,),
        ).fetchall()
        daily_rows = db.execute(
            """
            SELECT spent_on, COALESCE(SUM(amount_cents), 0) AS total
            FROM expenses
            WHERE spent_on >= ?
            GROUP BY spent_on
            """,
            (trend_start,),
        ).fetchall()
        movements = db.execute(
            """
            SELECT id, account, kind, delta_cents, balance_after_cents, note, created_at
            FROM movements
            ORDER BY id DESC
            LIMIT 12
            """
        ).fetchall()

    by_method = {row["method"]: row["total"] for row in month_rows}
    spent_by_day = {row["spent_on"]: row["total"] for row in daily_rows}
    days = []
    for offset in range(13, -1, -1):
        day = today - timedelta(days=offset)
        key = day.isoformat()
        days.append({"date": key, "total": money(spent_by_day.get(key, 0))})

    cash = wallet["cash_cents"]
    credit = wallet["credit_cents"]
    month_cash = by_method.get("cash", 0)
    month_credit = by_method.get("credit", 0)
    return {
        "cash": money(cash),
        "credit": money(credit),
        "total": money(cash + credit),
        "month_spent": money(month_cash + month_credit),
        "month_cash": money(month_cash),
        "month_credit": money(month_credit),
        "categories": [
            {"category": row["category"], "total": money(row["total"])} for row in categories
        ],
        "daily": days,
        "activity": [
            {
                "id": row["id"],
                "account": row["account"],
                "kind": row["kind"],
                "delta": money(row["delta_cents"]),
                "balance_after": money(row["balance_after_cents"]),
                "note": row["note"],
                "created_at": row["created_at"],
            }
            for row in movements
        ],
    }


@app.get("/api/expenses")
def list_expenses(category: str | None = None, method: str | None = None, q: str | None = None) -> list[dict]:
    clauses = []
    params: list[str] = []
    if category:
        if category not in CATEGORIES:
            raise HTTPException(400, "Unknown category")
        clauses.append("category = ?")
        params.append(category)
    if method:
        if method not in ("cash", "credit"):
            raise HTTPException(400, "Unknown payment method")
        clauses.append("method = ?")
        params.append(method)
    if q:
        clauses.append("LOWER(note) LIKE ?")
        params.append(f"%{q.strip().lower()}%")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with connect() as db:
        rows = db.execute(
            f"SELECT * FROM expenses {where} ORDER BY spent_on DESC, id DESC",
            params,
        ).fetchall()
    return [expense_dict(row) for row in rows]


@app.post("/api/expenses", status_code=201)
def create_expense(payload: ExpenseIn) -> dict:
    cents = to_cents(payload.amount)
    if cents <= 0:
        raise HTTPException(400, "Amount must be greater than zero")
    with connect() as db:
        apply_delta(db, payload.method, -cents, "expense", payload.note or payload.category)
        cursor = db.execute(
            """
            INSERT INTO expenses (amount_cents, category, method, note, spent_on, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (cents, payload.category, payload.method, payload.note, payload.spent_on, now_iso()),
        )
        row = db.execute("SELECT * FROM expenses WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return expense_dict(row)


@app.put("/api/expenses/{expense_id}")
def update_expense(expense_id: int, payload: ExpenseIn) -> dict:
    cents = to_cents(payload.amount)
    if cents <= 0:
        raise HTTPException(400, "Amount must be greater than zero")
    with connect() as db:
        existing = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        if existing is None:
            raise HTTPException(404, "Expense not found")
        apply_delta(db, existing["method"], existing["amount_cents"], "expense_reversal", "Edited expense")
        apply_delta(db, payload.method, -cents, "expense", payload.note or payload.category)
        db.execute(
            """
            UPDATE expenses
            SET amount_cents = ?, category = ?, method = ?, note = ?, spent_on = ?
            WHERE id = ?
            """,
            (cents, payload.category, payload.method, payload.note, payload.spent_on, expense_id),
        )
        row = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
    return expense_dict(row)


@app.delete("/api/expenses/{expense_id}")
def delete_expense(expense_id: int) -> dict:
    with connect() as db:
        existing = db.execute("SELECT * FROM expenses WHERE id = ?", (expense_id,)).fetchone()
        if existing is None:
            raise HTTPException(404, "Expense not found")
        apply_delta(db, existing["method"], existing["amount_cents"], "expense_reversal", existing["note"] or "Removed expense")
        db.execute("DELETE FROM expenses WHERE id = ?", (expense_id,))
    return {"ok": True}


@app.post("/api/wallet")
def adjust_wallet(payload: AdjustIn) -> dict:
    cents = to_cents(payload.amount)
    if payload.action != "set" and cents <= 0:
        raise HTTPException(400, "Amount must be greater than zero")
    with connect() as db:
        column = _column(payload.account)
        current = db.execute(f"SELECT {column} FROM wallet WHERE id = 1").fetchone()[0]
        if payload.action == "add":
            delta = cents
        elif payload.action == "subtract":
            delta = -cents
        else:
            delta = cents - current
        if delta != 0:
            apply_delta(db, payload.account, delta, payload.action, payload.note)
        wallet = db.execute("SELECT cash_cents, credit_cents FROM wallet WHERE id = 1").fetchone()
    return {"cash": money(wallet["cash_cents"]), "credit": money(wallet["credit_cents"])}


@app.post("/api/transfer")
def transfer(payload: TransferIn) -> dict:
    destination: Account = "credit" if payload.source == "cash" else "cash"
    cents = to_cents(payload.amount)
    if cents <= 0:
        raise HTTPException(400, "Amount must be greater than zero")
    note = payload.note or f"Transfer to {destination}"
    with connect() as db:
        apply_delta(db, payload.source, -cents, "transfer", note)
        apply_delta(db, destination, cents, "transfer", payload.note or f"Transfer from {payload.source}")
        wallet = db.execute("SELECT cash_cents, credit_cents FROM wallet WHERE id = 1").fetchone()
    return {"cash": money(wallet["cash_cents"]), "credit": money(wallet["credit_cents"])}


app.mount("/", StaticFiles(directory=FRONTEND, html=True), name="frontend")
