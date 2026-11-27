# Community Bridge — Emergency Assistance Funding MCP Server

A headless Model Context Protocol (`stdio`) server built in Python (`mcp` SDK, `Pydantic`, `SQLite`) that enables nonprofit staff to look up participants and grant funds, submit emergency assistance requests, approve or reject pending requests, and record simulated payments with strict tenant isolation, role-based access control (RBAC), and atomic budget protection.

---

## 1. Repository Structure
* `SPEC_INITIAL.md` — Frozen initial specification authored and committed prior to implementation.
* `SPEC.md` — Living specification (includes post-implementation revision log).
* `app/db.py` — SQLite connection factory (`WAL` mode, `busy_timeout`, `foreign_keys = ON`) and `STRICT` DDL schema.
* `app/seed.py` — Standalone repeatable database setup/reset CLI (`python -m app.seed --reset`).
* `app/models.py` — Pydantic input validators, domain error definitions, and exact cent-to-USD formatters.
* `app/service.py` — Core domain logic, `STAFF_ID` identity/role resolver, tenant scoping, and atomic transactions.
* `app/server.py` — MCP `stdio` server exposing 8 discoverable tools with `stderr`-only logging.
* `tests/test_service_and_mcp.py` — Automated `pytest` suite (unit, concurrency, and live MCP `stdio` E2E tests).
* `demo_client.py` — Reproducible MCP client demonstration script (no LLM required).
* `requirements.txt` — Pinned dependencies (`mcp==1.9.4`, `pydantic==2.11.5`, `pytest==8.3.5`).

---

## 2. Setup, Seed/Reset, Run & Test Commands

### Environment Setup
```bash
python3 -m venv .venv
source .venv/bin/activate        # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
