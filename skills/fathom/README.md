# Fathom Skill

Automatically summarizes Fathom meeting transcripts and delivers them to you via Telegram.

## How it works

1. Fathom sends a webhook when a meeting recording is ready
2. The agent verifies the signature, saves the transcript, and triggers this skill
3. Claude summarizes the transcript into 2-4 bullet points and sends them to you

---

## Setup

### 1. Cloudflare Tunnel

The agent needs a public URL. Set up a Cloudflare tunnel pointing to `localhost:8443`:

1. Install cloudflared: `brew install cloudflared`
2. Log in: `cloudflared login`
3. Create a tunnel: `cloudflared tunnel create chorgi`
4. Route a domain: `cloudflared tunnel route dns chorgi fathom.chorgi.me`
5. Get the tunnel token from the Cloudflare dashboard:
   - Zero Trust → Networks → Tunnels → your tunnel → Configure → Install connector
   - Copy the `--token` value
6. Add to `.personal/secrets.env`:
   ```
   CLOUDFLARED_TOKEN=<your_token>
   ```

The watchdog will start and keep cloudflared running automatically.

### 2. Webhook Secret

Generate a secret token for the webhook URL:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(16))"
```

Add to `.personal/secrets.env`:
```
WEBHOOK_SECRET=<your_token>
WEBHOOK_PORT=8443
```

### 3. Fathom Webhook Secret

1. Go to [Fathom](https://fathom.video) → Settings → Integrations → Webhooks
2. Create a new webhook
3. Set the destination URL to:
   ```
   https://fathom.chorgi.me/<WEBHOOK_SECRET>/fathom
   ```
4. Copy the signing secret Fathom provides
5. Add to `.personal/secrets.env`:
   ```
   FATHOM_WEBHOOK_SECRET=whsec_<your_fathom_signing_secret>
   ```

### 4. Restart the agent

```bash
# The watchdog will restart automatically, or:
python agent/main.py
```

---

## Verification

Check the health endpoint:
```
https://fathom.chorgi.me/<WEBHOOK_SECRET>/health
```

Should return `200 OK`.

Send a test webhook from Fathom's dashboard and watch the logs:
```bash
tail -f ~/.chorgi_v1_watchdog.log | grep -i fathom
```

Transcripts are saved to `skills/fathom/workspace/YYYY-MM-DD_<title>.txt`.
