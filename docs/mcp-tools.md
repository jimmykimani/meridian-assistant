# Meridian MCP — Phase 1 discovery

**Server**: `https://order-mcp-74afyau24q-uc.a.run.app/mcp`  
**Transport**: Streamable HTTP (JSON-RPC over POST)  
**Observed behavior** (2026-04-30):

- `initialize` returns **application/json** with `protocolVersion` `2024-11-05`.
- `tools/list` succeeds as a **standalone** POST (same headers as below) and returns **application/json** (no SSE in this sample). Cold responses can take **25–40s** (Cloud Run cold start).
- Server info from `initialize`: name `order-mcp`, version `1.22.0`.

**Request headers used during discovery**:

- `Content-Type: application/json`
- `Accept: application/json, text/event-stream`

---

## Tool inventory (authoritative names)

The assessment brief mentioned generic names (`authenticate_customer`, `get_products`, …). The **live server** exposes these tools — the agent **must** use these names and schemas.

| Tool | Purpose (summary) | Auth recommendation |
|------|-------------------|---------------------|
| `list_products` | Browse/filter catalog by `category`, `is_active` | **Public** |
| `get_product` | Details + stock for a **SKU** | **Public** |
| `search_products` | Keyword search | **Public** |
| `verify_customer_pin` | **Email + 4-digit PIN** → identity | **Login step** — call before account tools |
| `get_customer` | Profile by **customer_id** (UUID) | **Authenticated only** |
| `list_orders` | Orders; filter by `customer_id`, `status` | **Authenticated only** — scope to verified `customer_id` |
| `get_order` | Order detail by **order_id** (UUID) | **Authenticated only** — agent should confirm order belongs to customer when possible |
| `create_order` | New order: `customer_id` + `items[]` (`sku`, `quantity`, `unit_price`, `currency`) | **Authenticated only** |

---

## Schemas (from `tools/list`)

### `list_products`

- **Parameters**: `category` (string \| null), `is_active` (boolean \| null), both optional.
- **Returns**: MCP content describes formatted string, one product per line.

### `get_product`

- **Parameters**: `sku` (string, required), e.g. `COM-0001`.
- **Returns**: Formatted product details.

### `search_products`

- **Parameters**: `query` (string, required).
- **Returns**: Same style as `list_products`.

### `get_customer`

- **Parameters**: `customer_id` (string, required), UUID.
- **Returns**: Formatted customer details.

### `verify_customer_pin`

- **Parameters**: `email` (string), `pin` (string) — both required.
- **Returns**: Formatted customer details if verified; errors if not found or PIN wrong.
- **Chatbot note**: Do not echo PIN; parse response server-side for `customer_id` if present in text for downstream tools.

### `list_orders`

- **Parameters**: `customer_id` (string \| null), `status` (string \| null) — optional filters.
- **Returns**: Formatted order list.
- **Statuses** (from description): `draft` \| `submitted` \| `approved` \| `fulfilled` \| `cancelled`.

### `get_order`

- **Parameters**: `order_id` (string, required), UUID.
- **Returns**: Order with line items.

### `create_order`

- **Parameters**:
  - `customer_id` (string, required)
  - `items` (array of objects): each item includes at least `sku`, `quantity` (&gt; 0), `unit_price` (decimal **string**), `currency` (string, default USD per description).
- **Returns**: Order confirmation string; inventory decremented atomically; order `submitted` / payment `pending` per description.

---

## User story mapping (Meridian brief)

| Business ask | Tools to use |
|--------------|----------------|
| Check availability / browse | `search_products`, `list_products`, `get_product` |
| Authenticate returning customer | `verify_customer_pin` (email + PIN) |
| Order history | After auth: `list_orders` with verified `customer_id`; `get_order` for detail |
| Place order | After auth: `create_order` with verified `customer_id`; confirm SKU, qty, price, address flow in agent before call |
| Account / profile | After auth: `get_customer` if needed |

---

## Implementation notes for Phase 2+

1. **No hardcoded tool names from the old brief** — wire the agent from **`tools/list`** at runtime; classify tools for auth gating using the table above.
2. **Latency**: budget generous HTTP timeouts and UX “loading” for first request after idle.
3. **403 / allowlist**: If deployment hits `403`, the MCP host may only allow certain egress IPs or regions — document the allowlist rules from the backend team for your real deployment target.
4. **`notifications/initialized`**: Not required for `tools/list` in this probe; if `tools/call` fails without session, add MCP session flow per spec.

---

## Repeat discovery

From repo root:

```bash
python3 scripts/discover_mcp.py
```

Prints server metadata and a JSON summary of tools (requires network).

---

## Phase 2 — HTTP client (`mcp_client.py`)

Production-oriented async client (httpx): `initialize`, `list_tools` (cached), `call_tool`, JSON or SSE bodies, retries with backoff, circuit breaker, 403 allowlist messaging.

**Tests**: `pytest tests/test_mcp_client.py -m "not integration"` (fast). Live MCP: `pytest tests/test_mcp_client.py -m integration`.

---

## Phase 3 — Agent (`agent.py`, `session_manager.py`, `settings.py`)

- **Groq** (Llama via OpenAI-compatible API) with **function calling**; tools built from live **`tools/list`** (via `schema_utils`).
- **Session** state: serialized chat history (OpenAI-style messages) + authenticated `customer_id` after `verify_customer_pin`.
- **Auth gating**: sensitive tools (`get_customer`, `list_orders`, `get_order`, `create_order`) require an authenticated session; `customer_id` is **injected** for order/account tools.
- **Structured reply**: `{ "message", "requires_auth", "tool_used", "confidence" }`.
- **Video 2 talk track**: [video2_midpoint_script.md](video2_midpoint_script.md).

---

## Phase 4 — UI + deployment (no Hugging Face)

Implemented: **`main.py`** (FastAPI: `/health`, `/sessions/new`, `/sessions/reset`, `/chat`) and **`ui.py`** (Streamlit). Run locally with **uvicorn** + **streamlit** (see [README.md](../README.md)), or **`docker compose up`**.

Deploy targets: **GCP Cloud Run**, **VM + Docker**, **local + tunnel** — store `GROQ_API_KEY` in the host’s secret manager, not in git.

---

## Deploy guide

See **[deploy/DEPLOY.md](../deploy/DEPLOY.md)** (Cloud Run, Compose on a VM, checklist). Restart API after key changes: `./scripts/restart_backend.sh`.
