# Technical Specification: Emergency Assistance Funding MCP Server
**Document Version:** 1.0 (Initial Specification — Pre-Implementation)  
**Project:** Community Bridge — Headless Emergency Assistance Funding Service  

---

## 1. Requirements

### 1.1 Users & Roles
Every authenticated caller resolves server-side to a `StaffMember` belonging to exactly one `Nonprofit` and holding one of three roles:
* **Case Worker (`case_worker`):** Submits emergency assistance requests for existing participants within their nonprofit; views participants, funds, requests, and funding summaries.
* **Program Manager (`program_manager`):** Approves or rejects `Pending` assistance requests within their nonprofit; views participants, funds, requests, and funding summaries.
* **Finance Staff (`finance_staff`):** Records simulated full-amount payments against `Approved` assistance requests within their nonprofit; views participants, funds, requests, and funding summaries.

### 1.2 Core Workflows
1. **Discovery & Lookup:** Staff list participants, funds (with live financial balances), and assistance requests scoped strictly to their nonprofit.
2. **Request Submission:** Case workers create an assistance request referencing an existing participant and fund in their nonprofit, a positive USD amount (in integer cents), and a non-empty purpose. Request starts in `Pending`; fund balances do not change.
3. **Request Review:** Program managers approve or reject a `Pending` request in their nonprofit. Approval atomically verifies sufficient available funds and reserves the full amount (`committed_cents += amount_cents`). Rejection transitions the request to `Rejected` without altering fund balances.
4. **Payment Recording:** Finance staff record a simulated payment for an `Approved` request in their nonprofit. Payment atomically transitions the request to `Paid`, reduces `committed_cents` by `amount_cents`, and increases `paid_cents` by `amount_cents` (`available_cents` remains unchanged).

### 1.3 Functional & Non-Functional Requirements
* **REQ-01 (Identity Resolution):** The server MUST resolve caller identity from a server/launcher environment variable (`STAFF_ID`), mapping it to a trusted server-side `StaffMember` record (role + `nonprofit_id`). Missing or unknown `STAFF_ID` MUST reject all tool calls with `UNAUTHORIZED`.
* **REQ-02 (Zero-Trust Tool Inputs):** Tool arguments, prompt text, or stored record text MUST NOT override or elevate caller identity, role, or `nonprofit_id`.
* **REQ-03 (Tenant Isolation):** All read and write operations MUST strictly enforce `nonprofit_id == caller.nonprofit_id`. Cross-nonprofit access attempts MUST fail with `NOT_FOUND` (avoiding cross-tenant ID enumeration) and leave state unchanged.
* **REQ-04 (Role-Based Access Control):**
  * Read tools (`list_participants`, `list_funds`, `get_funding_summary`, `list_requests`, `get_request`): allowed for all 3 roles.
  * `create_request`: allowed ONLY for `case_worker`.
  * `approve_request`, `reject_request`: allowed ONLY for `program_manager`.
  * `record_payment`: allowed ONLY for `finance_staff`.
  * Unauthorized role calls MUST fail with `FORBIDDEN` and leave state unchanged.
* **REQ-05 (Request Creation & Immutability):** Requests are created in `Pending` state for an existing participant and fund in the caller's nonprofit, with `amount_cents > 0` and non-empty `purpose`. Submitting a request MUST NOT reserve funds. Once created, request fields (`participant_id`, `fund_id`, `amount_cents`, `purpose`) CANNOT be edited.
* **REQ-06 (State Machine & Permitted Transitions):** Only three transitions are permitted:
  * `Pending` → `Approved`
  * `Pending` → `Rejected`
  * `Approved` → `Paid`
  Any other transition (including duplicate `approve`, duplicate `reject`, duplicate `pay`, `Rejected` → `Approved`, `Pending` → `Paid`) MUST fail with `INVALID_STATE_TRANSITION` and leave state unchanged.
* **REQ-07 (Exact Cent-Level USD Arithmetic):** All monetary values MUST be stored, calculated, and validated as exact 64-bit signed integers representing USD cents (`amount_cents`). Floating-point arithmetic MUST NOT be used.
* **REQ-08 (Budget Reservation & Payment Accounting):** For every fund:
  * `available_cents = allocation_cents - committed_cents - paid_cents`
  * Approval: requires `available_cents >= request.amount_cents`; atomically sets `committed_cents += request.amount_cents`.
  * Rejection: leaves `allocation_cents`, `committed_cents`, and `paid_cents` unchanged.
  * Payment: atomically sets `committed_cents -= request.amount_cents` and `paid_cents += request.amount_cents` (`available_cents` does not change).
* **REQ-09 (Concurrency & Overspend Prevention):** Approval + fund balance check + reservation MUST execute inside an atomic database transaction with write serialization (`BEGIN IMMEDIATE`) and a conditional balance guard (`available_cents >= amount_cents`) so competing approvals can never cause `committed_cents + paid_cents > allocation_cents`.
* **REQ-10 (Input Validation & Structured Errors):** All tool inputs MUST be validated against strict schemas (rejecting non-positive amounts, blank strings, unknown IDs). All tools MUST return structured JSON responses with explicit machine-readable error codes on failure.
* **REQ-11 (Persistent Local Storage & Repeatable Seed/Reset):** Application state MUST persist in a local SQLite database. A standalone CLI command (`python -m app.seed --reset`) separate from server startup MUST deterministically reset and seed two nonprofits with fixture staff, participants, and funds.
* **REQ-12 (MCP Stdio Protocol & Clean Logging):** The server MUST expose discoverable tools over `stdio` transport and write all operational/error logs strictly to `stderr` so `stdout` carries only valid MCP JSON-RPC frames.

### 1.4 Goals, Non-Goals & Assumptions
* **Goals:** High financial integrity, deterministic concurrency protection, strict tenant/role isolation, clean MCP tool contracts, full requirement-to-test traceability.
* **Non-Goals (Out of Scope):** UI/frontend, real payment processors, live OAuth/OIDC server implementation, cloud hosting, LLM/agent orchestration, MCP resources/prompts, participant onboarding, grant intake, partial payments, cancellations, refunds, or external payment failures.
* **Assumptions:**
  1. Single currency (USD) expressed in integer cents (`100` = `$1.00`).
  2. One MCP server process runs per launcher session configured with a single `STAFF_ID` environment variable.
  3. Cross-nonprofit resource IDs return `NOT_FOUND` rather than `FORBIDDEN` so callers cannot probe whether an ID exists in another tenant.
  4. Duplicate approval or payment calls return `INVALID_STATE_TRANSITION` (explicit rejection rather than silent no-op idempotency) so callers know the transition was not applied a second time.

---

## 2. Business Behavior

### 2.1 State Machine
```text
             ┌──────────────┐
             │   Pending    │
             └──────┬───────┘
                    │
         ┌──────────┴──────────┐
         ▼                     ▼
  ┌─────────────┐       ┌─────────────┐
  │  Approved   │       │  Rejected   │ (Terminal)
  └──────┬──────┘       └─────────────┘
         │
         ▼
  ┌─────────────┐
  │    Paid     │ (Terminal)
  └─────────────┘

---
## 3. Specification Revision Log
* **v1.0 (Initial):** Saved prior to implementation in `SPEC_INITIAL.md`.
* **v1.1 (Post-Implementation Review):** No changes to business rules, state transitions, permissions, or monetary invariants were required during implementation. Clarified in implementation that `demo_client.py` writes to an isolated `demo_assistance.db` file (configurable via `ASSISTANCE_DB_PATH`) so running the demo does not overwrite a developer's default `assistance.db`.
