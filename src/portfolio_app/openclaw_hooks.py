from __future__ import annotations

import logging
import requests

from .config import settings

log = logging.getLogger("openclaw")


def _enabled() -> bool:
    return bool(settings.OPENCLAW_HOOKS_URL and settings.OPENCLAW_HOOKS_TOKEN)


def post_wake(text: str, mode: str = "now") -> None:
    if not _enabled():
        return
    url = settings.OPENCLAW_HOOKS_URL.rstrip("/") + "/wake"
    try:
        requests.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.OPENCLAW_HOOKS_TOKEN}",
                "Content-Type": "application/json",
            },
            json={"text": text, "mode": mode},
            timeout=10,
        ).raise_for_status()
    except Exception as e:
        log.warning("OpenClaw wake failed: %s", e)


def post_agent(message: str) -> None:
    """
    Optional: trigger an OpenClaw agent run via /hooks/agent. :contentReference[oaicite:2]{index=2}
    Useful if you want OpenClaw to handle narration/summary, but keep our Python as source of truth.
    """
    if not _enabled():
        return
    url = settings.OPENCLAW_HOOKS_URL.rstrip("/") + "/agent"
    payload = {
        "message": message,
        "name": "Portfolio",
        "agentId": settings.OPENCLAW_HOOKS_AGENTID,
        "deliver": bool(settings.OPENCLAW_HOOKS_DELIVER),
        "channel": settings.OPENCLAW_HOOKS_CHANNEL,
    }
    if settings.OPENCLAW_HOOKS_TO:
        payload["to"] = settings.OPENCLAW_HOOKS_TO

    try:
        requests.post(
            url,
            headers={
                "Authorization": f"Bearer {settings.OPENCLAW_HOOKS_TOKEN}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=10,
        ).raise_for_status()
    except Exception as e:
        log.warning("OpenClaw agent hook failed: %s", e)