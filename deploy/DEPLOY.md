# Deploy Meridian Support (API + Streamlit UI)

This project is **not** tied to Hugging Face Spaces. Typical paths: **Docker Compose** on a VM, **Google Cloud Run** (two services), or **Render/Fly.io** with two processes.

**Secrets:** Never commit `.env`. Use each platform’s **secret manager** for `GROQ_API_KEY` (and optional `MCP_SERVER_URL`).

---

## 1) Docker Compose (fastest “live URL” on a VM)

On a host with Docker:

```bash
git clone <your-repo-url> meridian-chatbot && cd meridian-chatbot
cp .env.example .env   # then edit .env — set GROQ_API_KEY
docker compose up --build -d
```

- API: `http://<server-ip>:8000`
- UI: `http://<server-ip>:8501`

**CORS:** Set `CORS_ORIGINS` in `.env` to your real UI origin, e.g. `http://YOUR_IP:8501` or `https://ui.yourdomain.com`.

**HTTPS:** Put **Caddy** or **nginx** in front with TLS; forward to 8501 (UI) and 8000 (API) or expose only UI and keep API on private network.

---

## 2) Google Cloud Run (two services)

Build and push one image (same Dockerfile for both services; override **command** for UI).

### Build & push (Artifact Registry example)

```bash
export PROJECT_ID=your-gcp-project
export REGION=us-central1
export REPO=meridian
export IMAGE=${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO}/meridian-chatbot:latest

gcloud auth configure-docker ${REGION}-docker.pkg.dev
docker build -t "${IMAGE}" .
docker push "${IMAGE}"
```

### API service

```bash
gcloud run deploy meridian-api \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --port 8000 \
  --allow-unauthenticated \
  --set-secrets "GROQ_API_KEY=groq-api-key:latest" \
  --set-env-vars "LOG_LEVEL=INFO"
```

Create secret `groq-api-key` in Secret Manager (Groq API key as secret **value**).

Note the API URL, e.g. `https://meridian-api-xxxxx-uc.a.run.app`.

### UI service

Set Streamlit to call the API URL and open CORS on the API for the UI origin.

```bash
export API_URL=https://meridian-api-xxxxx-uc.a.run.app

gcloud run deploy meridian-ui \
  --image "${IMAGE}" \
  --region "${REGION}" \
  --port 8501 \
  --allow-unauthenticated \
  --command streamlit \
  --args run,ui.py,--server.port=8501,--server.address=0.0.0.0,--browser.gatherUsageStats=false \
  --set-env-vars "MERIDIAN_API_URL=${API_URL}" \
  --set-secrets "GROQ_API_KEY=groq-api-key:latest"
```

Then update **API** service env:

```bash
gcloud run services update meridian-api \
  --region "${REGION}" \
  --set-env-vars "CORS_ORIGINS=https://meridian-ui-yyyyy-uc.a.run.app"
```

(Use your real UI Cloud Run URL.)

### MCP allowlisting

If MCP returns **403** from Cloud Run egress, ask the backend team to **allowlist** Cloud Run’s outbound IPs or use a **VPC connector** / approved egress path per their policy.

---

## 3) Render (API + Streamlit — two Web Services)

Render gives **one URL per Web Service**. If you only created **one** service running `uvicorn`, opening that URL shows the API JSON at `/` — **that is expected**. The Streamlit app is a **second** process and needs a **second** Web Service (same Git repo).

### A) API service (you may already have this)

- **Root directory:** repo root (or leave default).
- **Runtime:** Python, or Docker using this repo’s `Dockerfile` (override start if the image defaults to port 8000 — Render still expects the app to listen on **`$PORT`**).
- **Build:** `pip install -r requirements.txt`
- **Start:** `uvicorn main:app --host 0.0.0.0 --port $PORT`  
  (Render sets **`PORT`**; do not hard-code `8000` unless your dashboard explicitly maps it.)
- **Health check path:** `/health`
- **Environment:** set `GROQ_API_KEY` (and optional vars from the README). After the UI exists, set **`CORS_ORIGINS`** to a comma-separated list that includes **your Streamlit service’s public `https://…onrender.com` URL** (and localhost if you still test locally). Example:  
  `https://meridian-support-ui.onrender.com,http://localhost:8501,http://127.0.0.1:8501`

Without the UI origin in `CORS_ORIGINS`, the browser will block chat requests from the Streamlit tab.

### B) Streamlit UI service (add this to get a real UI URL)

1. **Dashboard → New → Web Service**, same repository.
2. **Build:** `pip install -r requirements.txt`
3. **Start command:**

   ```bash
   streamlit run ui.py --server.port=$PORT --server.address=0.0.0.0 --browser.gatherUsageStats=false
   ```

4. **Environment variables:**
   - **`MERIDIAN_API_URL`** = your API’s public base URL, e.g. `https://meridian-assistant.onrender.com` (no trailing slash).
   - The UI talks to the API over HTTP only; it does **not** need `GROQ_API_KEY` unless you change the app to call Groq from the browser (this project does not).

5. Deploy, copy the new **`https://…onrender.com`** URL — **open that** in the browser for the chat UI.

6. Go back to the **API** service → **Environment** → set **`CORS_ORIGINS`** to include the Streamlit URL → **save** (API will redeploy).

### Fly.io (sketch)

Same idea as Render: **two apps** (or two processes with explicit routing), API on `uvicorn` with `$PORT`, UI on Streamlit with `MERIDIAN_API_URL` pointing at the API, and `CORS_ORIGINS` on the API matching the UI origin.

### C) Streamlit Community Cloud (UI only)

Host **only** the Streamlit app on [Streamlit Community Cloud](https://streamlit.io/cloud); keep the FastAPI API on Render, Cloud Run, etc. The UI calls the API with **server-side** `httpx`, so you normally **do not** need to add Streamlit’s URL to API `CORS_ORIGINS`.

1. Sign in at [streamlit.io/cloud](https://streamlit.io/cloud) with **GitHub**.
2. **Create app** (or **New app**) → pick this **repository** and branch (e.g. `main`).
3. **Main file path:** `ui.py` (not the default `streamlit_app.py`).
4. **Python version:** 3.10+ (match your local setup if prompted).
5. Open **App settings → Secrets** and add TOML, for example:

   ```toml
   MERIDIAN_API_URL = "https://meridian-assistant.onrender.com"
   ```

   Optional: `MERIDIAN_CHAT_TIMEOUT = "180"` (seconds).

6. **Deploy.** After the build, Streamlit gives you a public app URL; pushes to the connected branch redeploy automatically.

`ui.py` reads `MERIDIAN_API_URL` from the environment **or** from `st.secrets` so local `.env` / Docker and Community Cloud both work.

---

## 4) Production checklist

- [ ] `GROQ_API_KEY` from secrets only  
- [ ] `CORS_ORIGINS` matches real UI origin(s)  
- [ ] `MERIDIAN_API_URL` on UI points to public API URL  
- [ ] MCP connectivity verified from deploy region (403 / latency)  
- [ ] Replace in-memory sessions with Redis (future) if you need multi-instance APIs  

---

## 5) Health checks

- API: `GET /health` → `{"status":"ok"}`  
- Identity: `GET /` → `"service":"meridian-support-api"`  

Use these for load balancer or Cloud Run startup probes.
