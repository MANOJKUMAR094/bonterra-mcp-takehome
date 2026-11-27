"""
Reproducible MCP Client Demonstration Script (No LLM required).
Spawns the Community Bridge MCP server over stdio, discovers tools,
executes the full lifecycle across fixture staff roles, and prints final balances.
"""

import asyncio
import json
import os
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.seed import reset_and_seed

PROJECT_ROOT = Path(__file__).resolve().parent
DEMO_DB_PATH = PROJECT_ROOT / "demo_assistance.db"


async def invoke_as_staff(staff_id: str, tool_name: str, arguments: dict | None = None) -> dict:
    env = {
        **os.environ,
        "STAFF_ID": staff_id,
        "ASSISTANCE_DB_PATH": str(DEMO_DB_PATH),
        "PYTHONPATH": str(PROJECT_ROOT),
    }
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "app.server"],
        env=env,
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            if tool_name == "__list_tools__":
                tools_resp = await session.list_tools()
                return {"tools": [t.name for t in tools_resp.tools]}
            res = await session.call_tool(tool_name, arguments=arguments or {})
            return json.loads(res.content[0].text)


async def main() -> None:
    print("=== 1. Resetting & Seeding Demo Database ===")
    reset_and_seed(DEMO_DB_PATH)
    print(f"Seeded database at: {DEMO_DB_PATH}\n")

    print("=== 2. Discovering MCP Tools (as Case Worker cw_cb_1) ===")
    discovered = await invoke_as_staff("cw_cb_1", "__list_tools__")
    print(json.dumps(discovered, indent=2), "\n")

    print("=== 3. Looking Up Participants & Initial Funding (cw_cb_1) ===")
    participants = await invoke_as_staff("cw_cb_1", "list_participants")
    print("Participants:", json.dumps(participants, indent=2))
    initial_funds = await invoke_as_staff("cw_cb_1", "get_funding_summary", {"fund_id": "fund_cb_housing"})
    print("Initial Housing Fund:", json.dumps(initial_funds, indent=2), "\n")

    print("=== 4. Creating $300.00 Assistance Request (cw_cb_1) ===")
    created = await invoke_as_staff(
        "cw_cb_1",
        "create_request",
        {
            "participant_id": "part_cb_1",
            "fund_id": "fund_cb_housing",
            "amount_cents": 30_000,
            "purpose": "Emergency rent payment to prevent eviction",
        },
    )
    print(json.dumps(created, indent=2), "\n")
    req_id = created["data"]["request"]["id"]

    print("=== 5. Verifying RBAC Guard: Case Worker Attempting Approval (cw_cb_1) ===")
    forbidden_attempt = await invoke_as_staff("cw_cb_1", "approve_request", {"request_id": req_id})
    print(json.dumps(forbidden_attempt, indent=2), "\n")

    print("=== 6. Approving Request (Program Manager pm_cb_1) ===")
    approved = await invoke_as_staff(
        "pm_cb_1",
        "approve_request",
        {"request_id": req_id, "decision_note": "Approved per emergency housing guidelines"},
    )
    print(json.dumps(approved, indent=2), "\n")

    print("=== 7. Recording Simulated Payment (Finance Staff fin_cb_1) ===")
    paid = await invoke_as_staff(
        "fin_cb_1",
        "record_payment",
        {"request_id": req_id, "payment_reference": "SIM-ACH-20261126-01"},
    )
    print(json.dumps(paid, indent=2), "\n")

    print("=== 8. Retrieving Final Funding Summary (fin_cb_1) ===")
    final_summary = await invoke_as_staff("fin_cb_1", "get_funding_summary")
    print(json.dumps(final_summary, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
