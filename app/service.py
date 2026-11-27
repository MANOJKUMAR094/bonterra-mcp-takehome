import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.db import get_connection
from app.models import (
    CreateRequestInput,
    DecisionInput,
    DomainError,
    PaymentInput,
    RoleType,
    StaffContext,
    format_usd,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _serialize_fund(row: Any) -> dict[str, Any]:
    allocation = int(row["allocation_cents"])
    committed = int(row["committed_cents"])
    paid = int(row["paid_cents"])
    available = allocation - committed - paid
    return {
        "id": row["id"],
        "nonprofit_id": row["nonprofit_id"],
        "name": row["name"],
        "allocation_cents": allocation,
        "committed_cents": committed,
        "paid_cents": paid,
        "available_cents": available,
        "allocation_usd": format_usd(allocation),
        "committed_usd": format_usd(committed),
        "paid_usd": format_usd(paid),
        "available_usd": format_usd(available),
    }


def _serialize_request(row: Any) -> dict[str, Any]:
    amt = int(row["amount_cents"])
    return {
        "id": row["id"],
        "nonprofit_id": row["nonprofit_id"],
        "participant_id": row["participant_id"],
        "fund_id": row["fund_id"],
        "amount_cents": amt,
        "amount_usd": format_usd(amt),
        "purpose": row["purpose"],
        "status": row["status"],
        "created_by_staff_id": row["created_by_staff_id"],
        "reviewed_by_staff_id": row["reviewed_by_staff_id"],
        "paid_by_staff_id": row["paid_by_staff_id"],
        "decision_note": row["decision_note"],
        "payment_reference": row["payment_reference"],
        "created_at": row["created_at"],
        "reviewed_at": row["reviewed_at"],
        "paid_at": row["paid_at"],
    }


class AssistanceService:
    def __init__(self, db_path: Path | str | None = None):
        self.db_path = db_path

    def _resolve_caller(self, conn: Any, staff_id_override: str | None = None) -> StaffContext:
        staff_id = (
            staff_id_override
            if staff_id_override is not None
            else os.environ.get("STAFF_ID", "")
        ).strip()
        if not staff_id:
            raise DomainError(
                "UNAUTHORIZED",
                "Missing caller identity. Configure STAFF_ID in the server launcher environment.",
            )
        row = conn.execute(
            "SELECT id, nonprofit_id, name, role FROM staff WHERE id = ?;",
            (staff_id,),
        ).fetchone()
        if row is None:
            raise DomainError(
                "UNAUTHORIZED",
                f"Unknown staff identity '{staff_id}'.",
            )
        return StaffContext(
            id=row["id"],
            nonprofit_id=row["nonprofit_id"],
            name=row["name"],
            role=row["role"],
        )

    @staticmethod
    def _require_role(caller: StaffContext, allowed_roles: set[RoleType]) -> None:
        if caller.role not in allowed_roles:
            raise DomainError(
                "FORBIDDEN",
                f"Role '{caller.role}' is not permitted to perform this action.",
                {"required_roles": sorted(allowed_roles), "caller_role": caller.role},
            )

    def list_participants(self, staff_id: str | None = None) -> dict[str, Any]:
        conn = get_connection(self.db_path)
        try:
            caller = self._resolve_caller(conn, staff_id)
            rows = conn.execute(
                """
                SELECT id, nonprofit_id, full_name, status
                FROM participants
                WHERE nonprofit_id = ?
                ORDER BY id ASC;
                """,
                (caller.nonprofit_id,),
            ).fetchall()
            return {
                "nonprofit_id": caller.nonprofit_id,
                "participants": [dict(r) for r in rows],
            }
        finally:
            conn.close()

    def get_funding_summary(
        self, fund_id: str | None = None, staff_id: str | None = None
    ) -> dict[str, Any]:
        conn = get_connection(self.db_path)
        try:
            caller = self._resolve_caller(conn, staff_id)
            if fund_id is not None:
                cleaned_id = fund_id.strip()
                if not cleaned_id:
                    raise DomainError("VALIDATION_ERROR", "fund_id cannot be blank if provided.")
                rows = conn.execute(
                    """
                    SELECT id, nonprofit_id, name, allocation_cents, committed_cents, paid_cents
                    FROM funds
                    WHERE nonprofit_id = ? AND id = ?;
                    """,
                    (caller.nonprofit_id, cleaned_id),
                ).fetchall()
                if not rows:
                    raise DomainError("NOT_FOUND", f"Fund '{cleaned_id}' was not found.")
            else:
                rows = conn.execute(
                    """
                    SELECT id, nonprofit_id, name, allocation_cents, committed_cents, paid_cents
                    FROM funds
                    WHERE nonprofit_id = ?
                    ORDER BY id ASC;
                    """,
                    (caller.nonprofit_id,),
                ).fetchall()

            funds = [_serialize_fund(r) for r in rows]
            total_alloc = sum(f["allocation_cents"] for f in funds)
            total_comm = sum(f["committed_cents"] for f in funds)
            total_paid = sum(f["paid_cents"] for f in funds)
            total_avail = total_alloc - total_comm - total_paid

            return {
                "nonprofit_id": caller.nonprofit_id,
                "funds": funds,
                "totals": {
                    "allocation_cents": total_alloc,
                    "committed_cents": total_comm,
                    "paid_cents": total_paid,
                    "available_cents": total_avail,
                    "allocation_usd": format_usd(total_alloc),
                    "committed_usd": format_usd(total_comm),
                    "paid_usd": format_usd(total_paid),
                    "available_usd": format_usd(total_avail),
                },
            }
        finally:
            conn.close()

    def list_requests(
        self,
        status: str | None = None,
        participant_id: str | None = None,
        staff_id: str | None = None,
    ) -> dict[str, Any]:
        conn = get_connection(self.db_path)
        try:
            caller = self._resolve_caller(conn, staff_id)
            clauses = ["nonprofit_id = ?"]
            params: list[Any] = [caller.nonprofit_id]

            if status is not None:
                if status not in ("Pending", "Approved", "Rejected", "Paid"):
                    raise DomainError(
                        "VALIDATION_ERROR",
                        "status must be one of: Pending, Approved, Rejected, Paid.",
                    )
                clauses.append("status = ?")
                params.append(status)

            if participant_id is not None:
                cleaned_pid = participant_id.strip()
                if not cleaned_pid:
                    raise DomainError("VALIDATION_ERROR", "participant_id cannot be blank.")
                clauses.append("participant_id = ?")
                params.append(cleaned_pid)

            sql = f"""
                SELECT *
                FROM assistance_requests
                WHERE {' AND '.join(clauses)}
                ORDER BY created_at ASC, id ASC;
            """
            rows = conn.execute(sql, params).fetchall()
            return {
                "nonprofit_id": caller.nonprofit_id,
                "requests": [_serialize_request(r) for r in rows],
            }
        finally:
            conn.close()

    def get_request(self, request_id: str, staff_id: str | None = None) -> dict[str, Any]:
        cleaned_id = (request_id or "").strip()
        if not cleaned_id:
            raise DomainError("VALIDATION_ERROR", "request_id is required.")

        conn = get_connection(self.db_path)
        try:
            caller = self._resolve_caller(conn, staff_id)
            row = conn.execute(
                """
                SELECT *
                FROM assistance_requests
                WHERE id = ? AND nonprofit_id = ?;
                """,
                (cleaned_id, caller.nonprofit_id),
            ).fetchone()
            if row is None:
                raise DomainError("NOT_FOUND", f"Assistance request '{cleaned_id}' was not found.")
            return {"request": _serialize_request(row)}
        finally:
            conn.close()

    def create_request(
        self,
        participant_id: str,
        fund_id: str,
        amount_cents: int,
        purpose: str,
        staff_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            payload = CreateRequestInput(
                participant_id=participant_id,
                fund_id=fund_id,
                amount_cents=amount_cents,
                purpose=purpose,
            )
        except ValidationError as exc:
            raise DomainError("VALIDATION_ERROR", exc.errors()[0]["msg"]) from exc

        conn = get_connection(self.db_path)
        try:
            caller = self._resolve_caller(conn, staff_id)
            self._require_role(caller, {"case_worker"})

            conn.execute("BEGIN IMMEDIATE;")
            participant = conn.execute(
                """
                SELECT id, status
                FROM participants
                WHERE id = ? AND nonprofit_id = ?;
                """,
                (payload.participant_id, caller.nonprofit_id),
            ).fetchone()
            if participant is None:
                raise DomainError(
                    "NOT_FOUND",
                    f"Participant '{payload.participant_id}' was not found in your nonprofit.",
                )
            if participant["status"] != "active":
                raise DomainError(
                    "VALIDATION_ERROR",
                    f"Participant '{payload.participant_id}' is inactive and cannot receive new requests.",
                )

            fund = conn.execute(
                """
                SELECT id
                FROM funds
                WHERE id = ? AND nonprofit_id = ?;
                """,
                (payload.fund_id, caller.nonprofit_id),
            ).fetchone()
            if fund is None:
                raise DomainError(
                    "NOT_FOUND",
                    f"Fund '{payload.fund_id}' was not found in your nonprofit.",
                )

            req_id = f"req_{uuid.uuid4().hex[:12]}"
            created_at = _utc_now()
            conn.execute(
                """
                INSERT INTO assistance_requests (
                    id, nonprofit_id, participant_id, fund_id, amount_cents, purpose,
                    status, created_by_staff_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, 'Pending', ?, ?);
                """,
                (
                    req_id,
                    caller.nonprofit_id,
                    payload.participant_id,
                    payload.fund_id,
                    payload.amount_cents,
                    payload.purpose,
                    caller.id,
                    created_at,
                ),
            )
            created_row = conn.execute(
                "SELECT * FROM assistance_requests WHERE id = ?;", (req_id,)
            ).fetchone()
            conn.execute("COMMIT;")
            return {"request": _serialize_request(created_row)}
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
        finally:
            conn.close()

    def approve_request(
        self,
        request_id: str,
        decision_note: str | None = None,
        staff_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            payload = DecisionInput(request_id=request_id, decision_note=decision_note)
        except ValidationError as exc:
            raise DomainError("VALIDATION_ERROR", exc.errors()[0]["msg"]) from exc

        conn = get_connection(self.db_path)
        try:
            caller = self._resolve_caller(conn, staff_id)
            self._require_role(caller, {"program_manager"})

            conn.execute("BEGIN IMMEDIATE;")
            req = conn.execute(
                """
                SELECT *
                FROM assistance_requests
                WHERE id = ? AND nonprofit_id = ?;
                """,
                (payload.request_id, caller.nonprofit_id),
            ).fetchone()
            if req is None:
                raise DomainError(
                    "NOT_FOUND", f"Assistance request '{payload.request_id}' was not found."
                )

            if req["status"] != "Pending":
                raise DomainError(
                    "INVALID_STATE_TRANSITION",
                    f"Cannot approve request in '{req['status']}' state; only 'Pending' requests can be approved.",
                    {"current_status": req["status"]},
                )

            amount_cents = int(req["amount_cents"])
            fund_id = req["fund_id"]

            # Atomic balance guard: prevents overspending under concurrent approvals.
            cursor = conn.execute(
                """
                UPDATE funds
                SET committed_cents = committed_cents + ?
                WHERE id = ?
                  AND nonprofit_id = ?
                  AND (allocation_cents - committed_cents - paid_cents) >= ?;
                """,
                (amount_cents, fund_id, caller.nonprofit_id, amount_cents),
            )
            if cursor.rowcount != 1:
                fund_row = conn.execute(
                    "SELECT allocation_cents, committed_cents, paid_cents FROM funds WHERE id = ?;",
                    (fund_id,),
                ).fetchone()
                avail = (
                    int(fund_row["allocation_cents"])
                    - int(fund_row["committed_cents"])
                    - int(fund_row["paid_cents"])
                )
                raise DomainError(
                    "INSUFFICIENT_FUNDS",
                    f"Insufficient available funds in '{fund_id}' ({format_usd(avail)} available, {format_usd(amount_cents)} required).",
                    {
                        "fund_id": fund_id,
                        "available_cents": avail,
                        "requested_cents": amount_cents,
                    },
                )

            reviewed_at = _utc_now()
            conn.execute(
                """
                UPDATE assistance_requests
                SET status = 'Approved',
                    reviewed_by_staff_id = ?,
                    decision_note = ?,
                    reviewed_at = ?
                WHERE id = ? AND status = 'Pending';
                """,
                (caller.id, payload.decision_note, reviewed_at, payload.request_id),
            )

            updated_req = conn.execute(
                "SELECT * FROM assistance_requests WHERE id = ?;", (payload.request_id,)
            ).fetchone()
            updated_fund = conn.execute(
                "SELECT * FROM funds WHERE id = ?;", (fund_id,)
            ).fetchone()
            conn.execute("COMMIT;")

            return {
                "request": _serialize_request(updated_req),
                "fund": _serialize_fund(updated_fund),
            }
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
        finally:
            conn.close()

    def reject_request(
        self,
        request_id: str,
        decision_note: str | None = None,
        staff_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            payload = DecisionInput(request_id=request_id, decision_note=decision_note)
        except ValidationError as exc:
            raise DomainError("VALIDATION_ERROR", exc.errors()[0]["msg"]) from exc

        conn = get_connection(self.db_path)
        try:
            caller = self._resolve_caller(conn, staff_id)
            self._require_role(caller, {"program_manager"})

            conn.execute("BEGIN IMMEDIATE;")
            req = conn.execute(
                """
                SELECT *
                FROM assistance_requests
                WHERE id = ? AND nonprofit_id = ?;
                """,
                (payload.request_id, caller.nonprofit_id),
            ).fetchone()
            if req is None:
                raise DomainError(
                    "NOT_FOUND", f"Assistance request '{payload.request_id}' was not found."
                )

            if req["status"] != "Pending":
                raise DomainError(
                    "INVALID_STATE_TRANSITION",
                    f"Cannot reject request in '{req['status']}' state; only 'Pending' requests can be rejected.",
                    {"current_status": req["status"]},
                )

            reviewed_at = _utc_now()
            conn.execute(
                """
                UPDATE assistance_requests
                SET status = 'Rejected',
                    reviewed_by_staff_id = ?,
                    decision_note = ?,
                    reviewed_at = ?
                WHERE id = ? AND status = 'Pending';
                """,
                (caller.id, payload.decision_note, reviewed_at, payload.request_id),
            )
            updated_req = conn.execute(
                "SELECT * FROM assistance_requests WHERE id = ?;", (payload.request_id,)
            ).fetchone()
            conn.execute("COMMIT;")
            return {"request": _serialize_request(updated_req)}
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
        finally:
            conn.close()

    def record_payment(
        self,
        request_id: str,
        payment_reference: str | None = None,
        staff_id: str | None = None,
    ) -> dict[str, Any]:
        try:
            payload = PaymentInput(request_id=request_id, payment_reference=payment_reference)
        except ValidationError as exc:
            raise DomainError("VALIDATION_ERROR", exc.errors()[0]["msg"]) from exc

        conn = get_connection(self.db_path)
        try:
            caller = self._resolve_caller(conn, staff_id)
            self._require_role(caller, {"finance_staff"})

            conn.execute("BEGIN IMMEDIATE;")
            req = conn.execute(
                """
                SELECT *
                FROM assistance_requests
                WHERE id = ? AND nonprofit_id = ?;
                """,
                (payload.request_id, caller.nonprofit_id),
            ).fetchone()
            if req is None:
                raise DomainError(
                    "NOT_FOUND", f"Assistance request '{payload.request_id}' was not found."
                )

            if req["status"] != "Approved":
                raise DomainError(
                    "INVALID_STATE_TRANSITION",
                    f"Cannot record payment for request in '{req['status']}' state; only 'Approved' requests can be paid.",
                    {"current_status": req["status"]},
                )

            amount_cents = int(req["amount_cents"])
            fund_id = req["fund_id"]

            cursor = conn.execute(
                """
                UPDATE funds
                SET committed_cents = committed_cents - ?,
                    paid_cents = paid_cents + ?
                WHERE id = ?
                  AND nonprofit_id = ?
                  AND committed_cents >= ?;
                """,
                (amount_cents, amount_cents, fund_id, caller.nonprofit_id, amount_cents),
            )
            if cursor.rowcount != 1:
                raise DomainError(
                    "INVALID_STATE_TRANSITION",
                    "Fund committed balance invariant failed during payment recording.",
                )

            paid_at = _utc_now()
            conn.execute(
                """
                UPDATE assistance_requests
                SET status = 'Paid',
                    paid_by_staff_id = ?,
                    payment_reference = ?,
                    paid_at = ?
                WHERE id = ? AND status = 'Approved';
                """,
                (caller.id, payload.payment_reference, paid_at, payload.request_id),
            )

            updated_req = conn.execute(
                "SELECT * FROM assistance_requests WHERE id = ?;", (payload.request_id,)
            ).fetchone()
            updated_fund = conn.execute(
                "SELECT * FROM funds WHERE id = ?;", (fund_id,)
            ).fetchone()
            conn.execute("COMMIT;")

            return {
                "request": _serialize_request(updated_req),
                "fund": _serialize_fund(updated_fund),
            }
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
        finally:
            conn.close()
