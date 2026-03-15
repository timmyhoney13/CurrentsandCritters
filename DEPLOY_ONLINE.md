# Fish Game Online Deployment (No Tunnel)

This project now supports an installable web app (PWA) and Docker deployment.

## 1) Run Locally With Docker

```bash
docker build -t fish-game .
docker run --rm -p 10000:10000 \
  -e PORT=10000 \
  -e FISH_CREATE_KEY="replace-with-your-own-key" \
  fish-game
```

Then open:

- `http://localhost:10000/`
- Health check: `http://localhost:10000/api/health`

## 2) Deploy Online (Example: Render Blueprint)

`render.yaml` is already configured for always-on hosting with persistent room state:

- Docker web service
- Health check at `/api/health`
- Persistent disk mounted at `/var/data/fish-room-state`
- `FISH_ROOM_STATE_DIR` pointed to that disk path
- Required secret `FISH_CREATE_KEY` (set in Render UI)

Deploy steps:

1. Put this project in a Git repository and push it to GitHub.
2. In Render, create a new Blueprint deploy and select that repo.
3. When prompted for env vars, set:
   - `FISH_CREATE_KEY` = your private host key string
   - `PUBLIC_BASE_URL` = your Render URL (optional, can set after first deploy)
4. Wait for build and health check to pass.
5. Open your Render URL and create/join rooms there (no tunnel needed).

## 3) Install As App (PWA)

- Open the deployed site in Chrome/Edge/Safari.
- Use "Install App" / "Add to Home Screen".
- The app uses:
  - `/manifest.webmanifest`
  - `/sw.js`
  - `/icon.svg`

## 4) Local Tunnel Failover (localhost.run)

When you must use localhost.run, run a small tunnel pool so the client can auto-failover to another live URL if one tunnel drops:

```bash
python3 multiplayer/tunnel_pool.py \
  --key-path multiplayer/.localhostrun_ed25519 \
  --key-path multiplayer/.localhostrun_ed25519_b \
  --target-host 127.0.0.1 \
  --target-port 8777 \
  --count 2 \
  --output multiplayer/public_links.json
```

The server reads `multiplayer/public_links.json` and publishes these backup links in state payloads (`public_links`) and `/api/public-links`.

## Notes

- Free hosting plans vary and can change over time.
- For best multiplayer stability, prefer an always-on host (no local tunnel).
- Running games are checkpointed to disk and auto-restored after server restart when `FISH_ROOM_STATE_DIR` persists across deploys.
