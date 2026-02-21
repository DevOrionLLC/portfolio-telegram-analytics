# Tailscale setup (recommended)

Install:
curl -fsSL https://tailscale.com/install.sh | sh

Bring up:
sudo tailscale up

Check:
tailscale status

Access private admin surfaces (examples):
- SSH over tailnet:
  ssh <user>@<tailscale-ip>
- SSH tunnel:
  ssh -L 18789:127.0.0.1:18789 <user>@<tailscale-ip>
  then open http://127.0.0.1:18789 in your browser.

If you use OpenClaw's built-in Tailscale Serve/Funnel modes, follow OpenClaw docs.