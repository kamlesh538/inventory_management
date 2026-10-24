"""A complete vehicle sale: invoice, loan, insurance and payments, saved together or not at all."""

import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from jbhonda.core.errors import BusinessError
from jbhonda.core.money import format_inr
from jbhonda.core.sales import PartLine, balance_due, record_payment, sell_vehicle
from jbhonda.db import NOW, transaction

POLICY_TYPES = ("COMPREHENSIVE", "THIRD_PARTY", "BUNDLED_1OD_5TP")


@dataclass(frozen=True)
class PaymentIn:
    mode: str
    amount: int
    reference_no: str | None = None


@dataclass(frozen=True)
class LoanIn:
    financer_id: int
    loan_amount: int
    tenure_months: int | None = None
    emi_amount: int | None = None
    interest_rate_bp: int | None = None
    loan_account_no: str | None = None
    disbursed: bool = False  # True when the bank has already paid the dealer


@dataclass(frozen=True)
class InsuranceIn:
    insurer_id: int
    policy_number: str
    policy_type: str
    premium: int
    start_date: date
    end_date: date
    idv: int | None = None
    nominee_name: str | None = None
    nominee_relation: str | None = None


def complete_vehicle_sale(
    conn: sqlite3.Connection, *, customer_id: int, vehicle_id: int, sale_price: int | None = None,
    discount: int = 0, accessories: Sequence[PartLine] = (), payments: Sequence[PaymentIn] = (),
    loan: LoanIn | None = None, insurance: InsuranceIn | None = None, booking_id: int | None = None,
    on: date | None = None, notes: str | None = None,
) -> int:
    """Returns the invoice id. Payments, loan and advance together may not exceed the bill."""
    on = on or date.today()
    with transaction(conn):
        hypothecated_to = None
        if loan is not None:
            financer = conn.execute("SELECT * FROM financer WHERE id = ?", (loan.financer_id,)).fetchone()
            if financer is None:
                raise BusinessError("Choose the financer.")
            hypothecated_to = f"{financer['name']} {financer['branch']}".strip()

        invoice_id = sell_vehicle(
            conn, customer_id=customer_id, vehicle_id=vehicle_id, sale_price=sale_price, discount=discount,
            accessories=accessories, on=on, booking_id=booking_id, hypothecated_to=hypothecated_to, notes=notes,
        )

        due = balance_due(conn, invoice_id)  # after any booking advance
        paying_now = sum(p.amount for p in payments)
        expected = paying_now + (loan.loan_amount if loan else 0)
        if expected > due:
            raise BusinessError(
                f"Payments{' and loan' if loan else ''} ({format_inr(expected)}) are more than "
                f"the amount due ({format_inr(due)})."
            )

        loan_id = None
        if loan is not None:
            loan_id = _create_loan(conn, invoice_id, customer_id, loan, down_payment=paying_now, on=on)
            if loan.disbursed:
                record_payment(conn, invoice_id=invoice_id, amount=loan.loan_amount, mode="FINANCE",
                               payment_type="LOAN_DISBURSEMENT", loan_id=loan_id, on=on,
                               reference_no=loan.loan_account_no)

        if loan is not None:
            payment_type = "DOWN_PAYMENT"
        elif paying_now == due:
            payment_type = "FULL_PAYMENT"
        else:
            payment_type = "DOWN_PAYMENT"
        for p in payments:
            if p.mode == "FINANCE":
                raise BusinessError("Record finance under the loan section, not as a payment.")
            record_payment(conn, invoice_id=invoice_id, amount=p.amount, mode=p.mode, payment_type=payment_type,
                           reference_no=p.reference_no, on=on)

        if insurance is not None:
            add_insurance_policy(conn, vehicle_id=vehicle_id, invoice_id=invoice_id, policy=insurance)
        return invoice_id


def _create_loan(
    conn: sqlite3.Connection, invoice_id: int, customer_id: int, loan: LoanIn, *, down_payment: int, on: date,
) -> int:
    if loan.loan_amount <= 0:
        raise BusinessError("Enter the loan amount.")
    if loan.tenure_months is not None and loan.tenure_months <= 0:
        raise BusinessError("Loan tenure must be at least one month.")
    if loan.emi_amount is not None and loan.emi_amount < 0:
        raise BusinessError("EMI cannot be negative.")
    return conn.execute(
        "INSERT INTO loan (invoice_id, customer_id, financer_id, loan_account_no, loan_amount, down_payment,"
        " interest_rate_bp, tenure_months, emi_amount, sanction_date, disbursement_date, status)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            invoice_id, customer_id, loan.financer_id, (loan.loan_account_no or "").strip().upper() or None,
            loan.loan_amount, down_payment, loan.interest_rate_bp, loan.tenure_months, loan.emi_amount,
            on.isoformat(), on.isoformat() if loan.disbursed else None,
            "DISBURSED" if loan.disbursed else "SANCTIONED",
        ),
    ).lastrowid


def record_loan_disbursement(
    conn: sqlite3.Connection, loan_id: int, *, on: date | None = None, reference_no: str | None = None,
) -> str:
    """The bank has paid the dealer: record the money against the invoice. Returns the receipt number."""
    on = on or date.today()
    with transaction(conn):
        loan = conn.execute("SELECT * FROM loan WHERE id = ?", (loan_id,)).fetchone()
        if loan is None:
            raise BusinessError("Loan not found.")
        if loan["status"] == "DISBURSED":
            raise BusinessError("This loan is already marked as received.")
        if loan["status"] == "REJECTED":
            raise BusinessError("This loan was rejected.")
        receipt = record_payment(
            conn, invoice_id=loan["invoice_id"], amount=loan["loan_amount"], mode="FINANCE",
            payment_type="LOAN_DISBURSEMENT", loan_id=loan_id, on=on,
            reference_no=reference_no or loan["loan_account_no"],
        )
        conn.execute(
            f"UPDATE loan SET status = 'DISBURSED', disbursement_date = ?, updated_at = {NOW} WHERE id = ?",
            (on.isoformat(), loan_id),
        )
        return receipt


def add_insurance_policy(
    conn: sqlite3.Connection, *, vehicle_id: int, invoice_id: int | None, policy: InsuranceIn,
) -> int:
    if not policy.policy_number.strip():
        raise BusinessError("Enter the insurance policy number.")
    if policy.policy_type not in POLICY_TYPES:
        raise BusinessError("Choose the policy type.")
    if policy.premium < 0 or (policy.idv is not None and policy.idv < 0):
        raise BusinessError("Premium and IDV cannot be negative.")
    if policy.end_date < policy.start_date:
        raise BusinessError("Policy end date is before its start date.")
    with transaction(conn):
        if conn.execute("SELECT 1 FROM insurer WHERE id = ?", (policy.insurer_id,)).fetchone() is None:
            raise BusinessError("Choose the insurance company.")
        if conn.execute(
            "SELECT 1 FROM insurance_policy WHERE insurer_id = ? AND policy_number = ?",
            (policy.insurer_id, policy.policy_number.strip().upper()),
        ).fetchone():
            raise BusinessError(f"Policy {policy.policy_number.strip().upper()} is already recorded.")
        return conn.execute(
            "INSERT INTO insurance_policy (vehicle_id, invoice_id, insurer_id, policy_number, policy_type, premium,"
            " idv, start_date, end_date, nominee_name, nominee_relation) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                vehicle_id, invoice_id, policy.insurer_id, policy.policy_number.strip().upper(), policy.policy_type,
                policy.premium, policy.idv, policy.start_date.isoformat(), policy.end_date.isoformat(),
                (policy.nominee_name or "").strip() or None, (policy.nominee_relation or "").strip() or None,
            ),
        ).lastrowid
