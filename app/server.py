import logging
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from app.models import DomainError, error_response, success_response
from app.service import AssistanceService

# Ensure all operational logs go strictly to stderr so stdout carries only MCP JSON-RPC frames.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("community_bridge_mcp")

mcp = FastMCP("community-bridge-assistance")
service = AssistanceService()


def _execute(fn: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    try:
        data = fn(*args, **kwargs)
        return success_response(data)
    except DomainError as exc:
        logger.warning("DomainError [%s]: %s", exc.code, exc.message)
        return error_response(exc)
    except Exception as exc:
        logger.exception("Unexpected internal error")
        return error_response(
            DomainError("VALIDATION_ERROR", f"Unexpected server error: {type(exc).__name__}")
        )


@mcp.tool(
    description=(
        "List participants belonging to the authenticated caller's nonprofit. "
        "Available to all staff roles."
    )
)
def list_participants() -> dict[str, Any]:
    return _execute(service.list_participants)


@mcp.tool(
    description=(
        "Retrieve funding allocations, committed amounts, paid amounts, and available balances "
        "in exact USD cents and formatted USD for the caller's nonprofit. "
        "Optionally filter by a specific fund_id."
    )
)
def get_funding_summary(fund_id: str | None = None) -> dict[str, Any]:
    return _execute(service.get_funding_summary, fund_id=fund_id)


@mcp.tool(
    description=(
        "List emergency assistance requests for the caller's nonprofit. "
        "Optionally filter by status ('Pending', 'Approved', 'Rejected', 'Paid') or participant_id."
    )
)
def list_requests(
    status: str | None = None,
    participant_id: str | None = None,
) -> dict[str, Any]:
    return _execute(service.list_requests, status=status, participant_id=participant_id)


@mcp.tool(
    description=(
        "Retrieve full details of a single emergency assistance request by request_id "
        "within the caller's nonprofit."
    )
)
def get_request(request_id: str) -> dict[str, Any]:
    return _execute(service.get_request, request_id=request_id)


@mcp.tool(
    description=(
        "Submit a new emergency assistance request in 'Pending' status for an active participant "
        "and fund in the caller's nonprofit. Does not reserve funds. Restricted to case_worker role."
    )
)
def create_request(
    participant_id: str,
    fund_id: str,
    amount_cents: int,
    purpose: str,
) -> dict[str, Any]:
    return _execute(
        service.create_request,
        participant_id=participant_id,
        fund_id=fund_id,
        amount_cents=amount_cents,
        purpose=purpose,
    )


@mcp.tool(
    description=(
        "Approve a 'Pending' assistance request and atomically reserve the full amount_cents "
        "against the selected fund if sufficient funds are available. "
        "Restricted to program_manager role."
    )
)
def approve_request(
    request_id: str,
    decision_note: str | None = None,
) -> dict[str, Any]:
    return _execute(
        service.approve_request,
        request_id=request_id,
        decision_note=decision_note,
    )


@mcp.tool(
    description=(
        "Reject a 'Pending' assistance request without modifying fund balances. "
        "Restricted to program_manager role."
    )
)
def reject_request(
    request_id: str,
    decision_note: str | None = None,
) -> dict[str, Any]:
    return _execute(
        service.reject_request,
        request_id=request_id,
        decision_note=decision_note,
    )


@mcp.tool(
    description=(
        "Record a simulated full-amount payment against an 'Approved' assistance request, "
        "moving amount_cents from committed to paid without changing available funds. "
        "Restricted to finance_staff role."
    )
)
def record_payment(
    request_id: str,
    payment_reference: str | None = None,
) -> dict[str, Any]:
    return _execute(
        service.record_payment,
        request_id=request_id,
        payment_reference=payment_reference,
    )


def main() -> None:
    logger.info("Starting Community Bridge MCP server over stdio...")
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
