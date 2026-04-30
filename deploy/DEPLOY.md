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

## 3) Render / Fly.io (sketch)

- **Two web services** from the same repo and Docker image.
- **API:** start command `uvicorn main:app --host 0.0.0.0 --port 8000`.
- **UI:** start command `streamlit run ui.py --server.port 8501 --server.address 0.0.0.0` and env `MERIDIAN_API_URL=https://<api-host>`.
- Add **health check** path `/health` on the API service.

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
