import asyncio
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.models import DomainError
from app.seed import reset_and_seed
from app.service import AssistanceService


@pytest.fixture()
def seeded_db(tmp_path: Path) -> Path:
    db_file = tmp_path / "test_assistance.db"
    reset_and_seed(db_file)
    return db_file


@pytest.fixture()
def svc(seeded_db: Path) -> AssistanceService:
    return AssistanceService(seeded_db)


def test_happy_path_lifecycle_and_budget_math(svc: AssistanceService) -> None:
    # Initial fund balance: $10,000.00 (1,000,000 cents)
    initial = svc.get_funding_summary(fund_id="fund_cb_housing", staff_id="cw_cb_1")["funds"][0]
    assert initial["allocation_cents"] == 1_000_000
    assert initial["committed_cents"] == 0
    assert initial["paid_cents"] == 0
    assert initial["available_cents"] == 1_000_000

    # 1. Case worker creates Pending request for $300.00 (30,000 cents) -> no reservation yet
    created = svc.create_request(
        participant_id="part_cb_1",
        fund_id="fund_cb_housing",
        amount_cents=30_000,
        purpose="Emergency rent assistance",
        staff_id="cw_cb_1",
    )["request"]
    assert created["status"] == "Pending"
    assert created["amount_cents"] == 30_000
    assert created["amount_usd"] == "$300.00"

    after_create = svc.get_funding_summary(fund_id="fund_cb_housing", staff_id="cw_cb_1")["funds"][0]
    assert after_create["available_cents"] == 1_000_000
    assert after_create["committed_cents"] == 0

    # 2. Program manager approves -> $300.00 committed, $9,700.00 available
    approved = svc.approve_request(
        request_id=created["id"],
        decision_note="Verified lease notice",
        staff_id="pm_cb_1",
    )
    assert approved["request"]["status"] == "Approved"
    assert approved["fund"]["committed_cents"] == 30_000
    assert approved["fund"]["paid_cents"] == 0
    assert approved["fund"]["available_cents"] == 970_000

    # 3. Finance staff records payment -> $300.00 paid, $0 committed, $9,700.00 available
    paid = svc.record_payment(
        request_id=created["id"],
        payment_reference="ACH-2026-991",
        staff_id="fin_cb_1",
    )
    assert paid["request"]["status"] == "Paid"
    assert paid["fund"]["committed_cents"] == 0
    assert paid["fund"]["paid_cents"] == 30_000
    assert paid["fund"]["available_cents"] == 970_000


def test_reject_pending_request_leaves_funding_unchanged(svc: AssistanceService) -> None:
    before = svc.get_funding_summary(fund_id="fund_cb_utilities", staff_id="pm_cb_1")["funds"][0]
    rejected = svc.reject_request(
        request_id="req_cb_seed_1",
        decision_note="Missing utility statement",
        staff_id="pm_cb_1",
    )["request"]
    assert rejected["status"] == "Rejected"

    after = svc.get_funding_summary(fund_id="fund_cb_utilities", staff_id="pm_cb_1")["funds"][0]
    assert after == before


def test_insufficient_funding_blocks_approval_and_preserves_state(svc: AssistanceService) -> None:
    # fund_cb_utilities has $2,500.00 (250,000 cents)
    big_req = svc.create_request(
        participant_id="part_cb_1",
        fund_id="fund_cb_utilities",
        amount_cents=250_001,
        purpose="Exceeds fund by 1 cent",
        staff_id="cw_cb_1",
    )["request"]

    with pytest.raises(DomainError) as exc:
        svc.approve_request(request_id=big_req["id"], staff_id="pm_cb_1")
    assert exc.value.code == "INSUFFICIENT_FUNDS"

    fetched = svc.get_request(request_id=big_req["id"], staff_id="pm_cb_1")["request"]
    assert fetched["status"] == "Pending"
    fund = svc.get_funding_summary(fund_id="fund_cb_utilities", staff_id="pm_cb_1")["funds"][0]
    assert fund["committed_cents"] == 0
    assert fund["available_cents"] == 250_000


def test_repeated_approvals_and_payment_retries_fail_cleanly(svc: AssistanceService) -> None:
    # Approve once -> succeeds
    svc.approve_request(request_id="req_cb_seed_1", staff_id="pm_cb_1")

    # Approve second time -> fails with INVALID_STATE_TRANSITION, committed stays 15,000
    with pytest.raises(DomainError) as exc_app:
        svc.approve_request(request_id="req_cb_seed_1", staff_id="pm_cb_1")
    assert exc_app.value.code == "INVALID_STATE_TRANSITION"

    fund_after_dup_app = svc.get_funding_summary(fund_id="fund_cb_utilities", staff_id="pm_cb_1")["funds"][0]
    assert fund_after_dup_app["committed_cents"] == 15_000

    # Pay once -> succeeds
    svc.record_payment(request_id="req_cb_seed_1", staff_id="fin_cb_1")

    # Pay second time -> fails with INVALID_STATE_TRANSITION, paid stays 15,000
    with pytest.raises(DomainError) as exc_pay:
        svc.record_payment(request_id="req_cb_seed_1", staff_id="fin_cb_1")
    assert exc_pay.value.code == "INVALID_STATE_TRANSITION"

    fund_after_dup_pay = svc.get_funding_summary(fund_id="fund_cb_utilities", staff_id="fin_cb_1")["funds"][0]
    assert fund_after_dup_pay["paid_cents"] == 15_000
    assert fund_after_dup_pay["committed_cents"] == 0


def test_role_restrictions_enforced(svc: AssistanceService) -> None:
    # Case worker cannot approve, reject, or pay
    for action in (
        lambda: svc.approve_request("req_cb_seed_1", staff_id="cw_cb_1"),
        lambda: svc.reject_request("req_cb_seed_1", staff_id="cw_cb_1"),
        lambda: svc.record_payment("req_cb_seed_1", staff_id="cw_cb_1"),
    ):
        with pytest.raises(DomainError) as exc:
            action()
        assert exc.value.code == "FORBIDDEN"

    # Program manager cannot create or pay
    with pytest.raises(DomainError) as exc_pm_create:
        svc.create_request("part_cb_1", "fund_cb_housing", 10_000, "Rent", staff_id="pm_cb_1")
    assert exc_pm_create.value.code == "FORBIDDEN"

    svc.approve_request("req_cb_seed_1", staff_id="pm_cb_1")
    with pytest.raises(DomainError) as exc_pm_pay:
        svc.record_payment("req_cb_seed_1", staff_id="pm_cb_1")
    assert exc_pm_pay.value.code == "FORBIDDEN"

    # Finance staff cannot create or approve
    with pytest.raises(DomainError) as exc_fin_create:
        svc.create_request("part_cb_1", "fund_cb_housing", 10_000, "Rent", staff_id="fin_cb_1")
    assert exc_fin_create.value.code == "FORBIDDEN"

    with pytest.raises(DomainError) as exc_fin_app:
        svc.approve_request("req_cb_seed_1", staff_id="fin_cb_1")
    assert exc_fin_app.value.code == "FORBIDDEN"


def test_cross_nonprofit_isolation(svc: AssistanceService) -> None:
    # Hope Foundation staff cannot read or approve Community Bridge request
    with pytest.raises(DomainError) as exc_get:
        svc.get_request("req_cb_seed_1", staff_id="pm_hf_1")
    assert exc_get.value.code == "NOT_FOUND"

    with pytest.raises(DomainError) as exc_app:
        svc.approve_request("req_cb_seed_1", staff_id="pm_hf_1")
    assert exc_app.value.code == "NOT_FOUND"

    # Hope Foundation case worker cannot submit against Community Bridge participant or fund
    with pytest.raises(DomainError) as exc_cross_part:
        svc.create_request("part_cb_1", "fund_hf_general", 5_000, "Cross tenant", staff_id="cw_hf_1")
    assert exc_cross_part.value.code == "NOT_FOUND"


def test_missing_unknown_identity_and_invalid_inputs(svc: AssistanceService) -> None:
    # Missing identity
    with pytest.raises(DomainError) as exc_missing:
        svc.list_participants(staff_id="")
    assert exc_missing.value.code == "UNAUTHORIZED"

    # Unknown identity
    with pytest.raises(DomainError) as exc_unknown:
        svc.list_participants(staff_id="hacker_999")
    assert exc_unknown.value.code == "UNAUTHORIZED"

    # Invalid inputs: zero, negative, float, bool, blank purpose, inactive participant
    bad_calls = [
        lambda: svc.create_request("part_cb_1", "fund_cb_housing", 0, "Rent", staff_id="cw_cb_1"),
        lambda: svc.create_request("part_cb_1", "fund_cb_housing", -500, "Rent", staff_id="cw_cb_1"),
        lambda: svc.create_request("part_cb_1", "fund_cb_housing", 12.50, "Rent", staff_id="cw_cb_1"),  # type: ignore[arg-type]
        lambda: svc.create_request("part_cb_1", "fund_cb_housing", True, "Rent", staff_id="cw_cb_1"),  # type: ignore[arg-type]
        lambda: svc.create_request("part_cb_1", "fund_cb_housing", 1000, "   ", staff_id="cw_cb_1"),
        lambda: svc.create_request("part_cb_inactive", "fund_cb_housing", 1000, "Rent", staff_id="cw_cb_1"),
    ]
    for call in bad_calls:
        with pytest.raises(DomainError) as exc:
            call()
        assert exc.value.code == "VALIDATION_ERROR"


def test_concurrent_competing_approvals_cannot_overspend(svc: AssistanceService) -> None:
    # fund_cb_utilities has $2,500.00 (250,000 cents). Create two $1,800.00 (180,000 cents) requests.
    r1 = svc.create_request("part_cb_1", "fund_cb_utilities", 180_000, "Req 1", staff_id="cw_cb_1")["request"]["id"]
    r2 = svc.create_request("part_cb_2", "fund_cb_utilities", 180_000, "Req 2", staff_id="cw_cb_1")["request"]["id"]

    def attempt_approve(req_id: str) -> str:
        try:
            svc.approve_request(req_id, staff_id="pm_cb_1")
            return "APPROVED"
        except DomainError as err:
            return err.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(attempt_approve, [r1, r2]))

    assert sorted(outcomes) == ["APPROVED", "INSUFFICIENT_FUNDS"]

    fund = svc.get_funding_summary(fund_id="fund_cb_utilities", staff_id="pm_cb_1")["funds"][0]
    assert fund["committed_cents"] == 180_000
    assert fund["available_cents"] == 70_000


def test_end_to_end_mcp_stdio_flow(seeded_db: Path) -> None:
    async def _run_mcp_e2e() -> None:
        project_root = str(Path(__file__).resolve().parent.parent)

        async def call_tool(staff_id: str, tool_name: str, arguments: dict) -> dict:
            env = {
                **os.environ,
                "STAFF_ID": staff_id,
                "ASSISTANCE_DB_PATH": str(seeded_db),
                "PYTHONPATH": project_root,
            }
            server_params = StdioServerParameters(
                command=sys.executable,
                args=["-m", "app.server"],
                env=env,
            )
            async with stdio_client(server_params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    tools_resp = await session.list_tools()
                    tool_names = {t.name for t in tools_resp.tools}
                    assert "create_request" in tool_names
                    assert "approve_request" in tool_names
                    assert "record_payment" in tool_names

                    result = await session.call_tool(tool_name, arguments=arguments)
                    assert result.content
                    return json.loads(result.content[0].text)

        # 1. Create via Case Worker
        created = await call_tool(
            "cw_cb_1",
            "create_request",
            {
                "participant_id": "part_cb_1",
                "fund_id": "fund_cb_housing",
                "amount_cents": 30_000,
                "purpose": "E2E MCP test rent",
            },
        )
        assert created["ok"] is True
        req_id = created["data"]["request"]["id"]

        # 2. Approve via Program Manager
        approved = await call_tool(
            "pm_cb_1",
            "approve_request",
            {"request_id": req_id, "decision_note": "Approved over MCP"},
        )
        assert approved["ok"] is True
        assert approved["data"]["request"]["status"] == "Approved"

        # 3. Record Payment via Finance Staff
        paid = await call_tool(
            "fin_cb_1",
            "record_payment",
            {"request_id": req_id, "payment_reference": "E2E-PAY-001"},
        )
        assert paid["ok"] is True
        assert paid["data"]["request"]["status"] == "Paid"

        # 4. Verify final summary
        summary = await call_tool(
            "fin_cb_1",
            "get_funding_summary",
            {"fund_id": "fund_cb_housing"},
        )
        assert summary["ok"] is True
        fund = summary["data"]["funds"][0]
        assert fund["paid_cents"] == 30_000
        assert fund["committed_cents"] == 0
        assert fund["available_cents"] == 970_000

    asyncio.run(_run_mcp_e2e())
