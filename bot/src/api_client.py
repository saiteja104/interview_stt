"""
api_client.py — HTTP client that sends data from the bot back to your FastAPI.

The bot runs inside ECS (isolated container).
Your FastAPI runs separately (ECS Fargate or elsewhere).
They communicate over HTTP.
"""

import logging
from datetime import datetime, timezone

import httpx

log = logging.getLogger("api_client")


class ApiClient:
    def __init__(self, base_url: str, interview_id: str):
        self.base_url     = base_url.rstrip("/")
        self.interview_id = interview_id

        # Internal endpoints — not exposed to customers
        self._status_url     = f"{self.base_url}/internal/interviews/{interview_id}/status"
        self._transcript_url = f"{self.base_url}/internal/interviews/{interview_id}/transcript"
        self._speaker_url    = f"{self.base_url}/internal/interviews/{interview_id}/speaker"

    async def update_status(self, status: str, error: str | None = None):
        """Tell the API what the bot is currently doing."""
        payload = {"status": status}
        if error:
            payload["error"] = error

        await self._post(self._status_url, payload)
        log.info(f"Status updated: {status}")

    async def send_transcript(
        self,
        text: str,
        speaker_id: int | None,
        words: list,
        is_final: bool,
    ):
        """Send a transcript chunk to the API."""
        payload = {
            "text": text,
            "speaker_id": speaker_id,
            "words": words,
            "is_final": is_final,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        await self._post(self._transcript_url, payload)

    async def send_speaker_event(self, name: str, timestamp_ms: int):
        """Send a speaker change event (from DOM scraping) to the API."""
        payload = {
            "name": name,
            "timestamp_ms": timestamp_ms,
        }
        await self._post(self._speaker_url, payload)

    async def _post(self, url: str, payload: dict):
        """POST with retry logic — network hiccups shouldn't crash the bot."""
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    response = await client.post(url, json=payload)
                    response.raise_for_status()
                    return
            except Exception as e:
                if attempt == 2:
                    log.error(f"Failed to POST to {url} after 3 attempts: {e}")
                else:
                    log.warning(f"POST failed (attempt {attempt + 1}): {e} — retrying")
                    import asyncio
                    await asyncio.sleep(1 * (attempt + 1))
