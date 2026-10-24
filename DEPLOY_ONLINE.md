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

## 3) Deploy Frontend + Card Pictures on Vercel

This repo now includes:

- `vercel.json` for route rewrites (`/`, `/play/:room_id`, `/rules`, `/about`, etc.)
- `.vercelignore` to deploy only the web client and card picture folders

Deploy:

```bash
vercel --prod
```

Important:

- Vercel in this setup serves the web app + image assets only.
- The multiplayer Python server is still best hosted on an always-on backend (for example Render).
- If you use Vercel Git integration, remember the card folders are currently gitignored in this repo.
  - Easiest path for picture assets: deploy from local with `vercel --prod` (uses `.vercelignore`).
- To point the Vercel frontend at your backend API, open the app with `?api_base=...` once (it is saved in `localStorage`):
  - `https://your-vercel-site.vercel.app/?api_base=https://your-backend-host`

### Vercel env vars: the Partner With Us form

`api/partner-contact.js` is the only server-side code on Vercel. It mails the
Partner With Us enquiry straight to the inbox, so an enquiry no longer has to
wake the hibernating Render container to be sent.

It reads the SAME variable names `newsletter_email.py` reads, so there is
nothing to translate: copy the values Render already has into Vercel
(Settings → Environment Variables, Production + Preview), whichever set it has.

**Render is currently on SMTP** (`/api/partner/state` answers
`"transport":"SMTP (smtp.gmail.com)"`), so that is the set to copy:

| Variable | Required | Notes |
|---|---|---|
| `SMTP_HOST` | **Yes** | `smtp.gmail.com` |
| `SMTP_USERNAME` | **Yes** | the mailbox that sends |
| `SMTP_PASSWORD` | **Yes** | the Google App Password; spaces are stripped, so paste it as shown |
| `SMTP_PORT` | No | defaults to 587 (465 implies implicit TLS and is auto-detected) |
| `ADMIN_EMAIL` | No | where enquiries land, comma-separated. Render has **two**; Vercel defaults to `timothy.honey@beardedsealstudios.com` alone |
| `NEWSLETTER_FROM_EMAIL` | No | the From address, defaults to the same |

**⚠️ This is also the reason the form was never really sending.** Render blocks
outbound SMTP on ports 25 / 465 / 587, so `smtp.gmail.com` was unreachable from
that container whatever the credentials said (see `transport()` in
`newsletter_email.py` and §SMTP of NEWSLETTER_SETUP.md). A Vercel Function has
ordinary outbound TCP, so the same four values work from here. The newsletter
and the account emails still run on Render and still have that problem: an
HTTPS API key (`NEWSLETTER_API_KEY`, Brevo's free tier needs no domain) is the
fix for those, and this endpoint prefers it over SMTP the moment it is set.

Either way, check it from anywhere without sending anything:

```bash
curl -s https://currentsandcritters.com/api/partner-contact
# {"ok":true,"enabled":true,"transport":"SMTP (smtp.gmail.com)","inboxes":2,"from":"..."}
```

`enabled:false` means nothing is configured on this deployment: the form then
refuses honestly and the page falls back to a prefilled `mailto:` with
everything the sender typed still in it. Nothing is ever silently dropped, and
`node test_partner_contact_api.js` covers all of it offline, including the SMTP
conversation against a fake mail server.

## 4) Install As App (PWA)

- Open the deployed site in Chrome/Edge/Safari.
- Use "Install App" / "Add to Home Screen".
- The app uses:
  - `/manifest.webmanifest`
  - `/sw.js`
  - `/icon.svg`

## 5) Local Tunnel Failover (localhost.run)

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
