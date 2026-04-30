# Video 3 — Final presentation script (3–10 minutes)

Use this as a **run-of-show**: what to say, what to type, what to show in the repo, and what to admit. Keep two terminals running (**uvicorn** on 8000, **streamlit** on 8501) and `.env` with a valid `GROQ_API_KEY` before you record.

---

## Part A — Live demo (what to type, in order)

**Setup (5 seconds on camera):** “I have the API on port 8000 and the Streamlit UI on 8501; the UI talks to the API only—no secrets in the browser bundle beyond what’s normal for a web app.”

### Scenario 1 — Anonymous product help (no sign-in)

**Goal:** Show frictionless browse / search like real retail chat.

1. Open **http://127.0.0.1:8501** (do **not** open the sidebar verify form yet).
2. Type exactly (or paste):

   > What wireless keyboards do you have? Search the catalog and mention two options with price if you can.

3. **Expected:** Assistant uses MCP (`search_products` and/or `get_product` / `list_products`). You may see **Details** → `tool_used` in the JSON. First response after idle can take **30–60 seconds** (MCP cold start)—say that out loud while the spinner runs.

4. Optional follow-up (still no sign-in):

   > Do you have any monitors in stock?

### Scenario 2 — Sensitive ask without verification (auth gate)

**Goal:** Show that orders/account are blocked until verify; UX nudges the user.

5. Type:

   > Show me my last three orders.

6. **Expected:** Model explains they need account verification; **Details** may show `requires_auth: true` or a tool blocked with `authentication_required`. If the UI shows the blue **Next step** banner, point at it—that’s intentional.

### Scenario 3 — Verify, then orders

**Goal:** Same flow as Amazon/bank: unlock only what’s sensitive.

7. Open sidebar → **Account — orders & purchases**.
8. Paste from [test-customers.md](test-customers.md) (say “assessment test data”):

   - **Email:** `donaldgarcia@example.net`  
   - **PIN:** `7912`  

9. Click **Verify account**. Wait for MCP (can be slow first time).

10. **Expected:** “Verified…” message; sidebar shows masked email.

11. Type:

    > List my recent orders.

12. **Expected:** `list_orders` (or equivalent) with **server-injected** `customer_id`—you can mention you never asked the user to paste a UUID.

### Scenario 4 — Graceful failure (optional, 30 seconds)

13. Wrong PIN once (optional): wrong PIN → generic “could not verify” style message—**good** (no “PIN incorrect” leakage if MCP is conservative).

**If Groq returns 429:** Say: “That’s rate limits; in production we’d add backoff, caching, and reserved capacity.” Don’t burn time fixing on camera.

---

## Part B — Code & architecture walkthrough (what to show and say)

**Switch to IDE** (or split screen). Walk top-down in this order—about **2–4 minutes**.

### 1. End-to-end data flow (one sentence + diagram)

> “Browser runs Streamlit; Streamlit calls our **FastAPI** JSON API; FastAPI holds **in-memory sessions** and runs the **agent**; the agent calls **Groq (Llama)** with tools discovered from **MCP**; only the MCP client talks to Meridian’s order service.”

```text
Streamlit (ui.py)  →  HTTP  →  FastAPI (main.py)
                                    │
                    session + auth  │  MeridianAgent (agent.py)
                                    │        │
                                    │        ├→ Groq / Llama (tool calls)
                                    │        └→ MCPClient → order MCP (Cloud Run)
```

### 2. `mcp_client.py` (30–45 seconds)

- **Why it exists:** One place for JSON-RPC over Streamable HTTP, **SSE vs JSON** bodies, retries, **circuit breaker**, clear **403** messaging.
- **Line to say:** “Production services don’t get to leak raw stack traces to the UI—we normalize failures into something the model or user can handle.”

### 3. `agent.py` (45–60 seconds)

- **Dynamic tools:** Tool definitions come from **`tools/list`** at runtime, converted for the LLM (`schema_utils.py` handles gnarly JSON Schema).
- **Auth gating in code, not only in the prompt:** `SENSITIVE_TOOLS` require `authenticated_customer_id`; otherwise return a structured `authentication_required` payload so the model explains verification—matches the VP’s “trust but verify” story.
- **Inject `customer_id`:** For `list_orders` / `create_order` / `get_customer`, the server injects the verified ID so the model can’t accidentally pass someone else’s ID.
- **System prompt:** Anonymous catalog help first; sensitive flows only after verify—**matches the product decision** you shipped in the UI.

### 4. `main.py` (30 seconds)

- **Routes:** `/chat` (anonymous OK), `/auth/verify` (+ aliases `/login`, `/verify`), `/sessions/.../status`, `/sessions/clear-chat` vs reset/logout.
- **Why:** “API layer lets us swap Streamlit for a mobile app or partner integration later without rewriting the brain.”

### 5. `ui.py` (20–30 seconds)

- Chat-first; **Account** expander for verify—reduces drop-off for shoppers who only need specs.

### 6. Tests (15 seconds)

- Point at `tests/`—MCP parsing, circuit breaker, API health, auth utils. “We’re not pretending this is throwaway—there’s a safety net for the parts that break in real life.”

---

## Part C — Honest assessment (use almost verbatim; shows maturity)

### What works well

- **Clear separation:** UI / API / agent / MCP client—easy for another engineer to review or extend.
- **Defense in depth:** Prompt says “verify for sensitive work”; **code** blocks sensitive MCP tools without a session customer ID; PIN path goes through MCP, not the LLM’s memory as source of truth.
- **Cost-aware stack:** Fast hosted Llama + tool calling; MCP is the system of record so we’re not hallucinating inventory.
- **Operational realism:** Long timeouts and UI copy for cold MCP; structured API responses for productization.

### Limitations (say these plainly—reviewers respect it)

- **In-memory sessions:** Restart the API and everyone’s chat/auth state is gone—production needs Redis or a session store.
- **Order detail by ID:** `get_order` is gated on “logged in” but not fully re-validated against “does this order belong to this customer” at the MCP layer in our prototype—**call out** that a full solution would enforce ownership server-side.
- **LLM variability:** The model might still try a sensitive tool before explaining verify—we mitigate with tool errors and UI `requires_auth`, not a formal proof.
- **Quota / rate limits:** Groq can **429** under burst; MCP can be slow or **403** from allowlisting—documented, not hidden.
- **Prompt injection:** Basic heuristics only—not a replacement for enterprise input filtering and monitoring.

### What I’d improve with more time

- **Persistent sessions** + optional Redis cache for MCP `list_tools` results.
- **Stricter order ownership checks** (or MCP enhancements) and audit logs for every tool call (who, which session, latency, outcome—**without** logging PINs).
- **Automated E2E tests** against a stub MCP for CI; contract tests on JSON schemas returned by tools.
- **Observability:** OpenTelemetry traces from UI → API → MCP → LLM for demo debugging and SLOs.
- **Optional SSO** later—PIN flow stays for the assessment, but real Meridian might add IdP.

---

## Closing line (10 seconds)

> “This prototype proves the integration pattern: **LLM + MCP + gated auth** is viable for Meridian support, with a clear path to hardening. The open questions are session persistence, quota, and stricter ownership checks—not whether the architecture hangs together.”

---

## Pre-record checklist

- [ ] `uvicorn main:app --host 127.0.0.1 --port 8000` running from repo root  
- [ ] `streamlit run ui.py --server.port 8501` running  
- [ ] `curl http://127.0.0.1:8000/` shows `meridian-support-api`  
- [ ] `.env` has `GROQ_API_KEY` (and optional `GROQ_MODEL`)  
- [ ] Test customer row ready to paste: `donaldgarcia@example.net` / `7912`  
- [ ] Close unrelated tabs; **Do not** show real personal API keys on screen  

Good luck with the recording.
