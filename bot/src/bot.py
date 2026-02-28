"""
bot.py — Main entrypoint for the Zoom bot.

This script runs inside the Docker container and:
1. Reads config from environment variables (set by ECS task)
2. Starts the Playwright browser → joins the Zoom meeting
3. Starts FFmpeg → streams audio to Deepgram
4. Sends transcripts back to your API
5. Exits cleanly when the meeting ends

Environment variables (injected by ECS):
    MEETING_URL     - The Zoom meeting URL to join
    INTERVIEW_ID    - Your internal interview ID
    BOT_NAME        - Display name shown in the meeting
    API_BASE_URL    - Your FastAPI backend URL
    DEEPGRAM_API_KEY
"""

import asyncio
import logging
import os
import signal
import sys

from zoom_joiner import ZoomJoiner
from audio_pipeline import AudioPipeline
from api_client import ApiClient

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
log = logging.getLogger("bot")

# ── Config from environment ────────────────────────────────────────────────────
MEETING_URL      = os.environ["MEETING_URL"]
INTERVIEW_ID     = os.environ["INTERVIEW_ID"]
BOT_NAME         = os.environ.get("BOT_NAME", "Interview Assistant")
API_BASE_URL     = os.environ["API_BASE_URL"]
DEEPGRAM_API_KEY = os.environ["DEEPGRAM_API_KEY"]


async def main():
    log.info(f"Bot starting | interview={INTERVIEW_ID} | meeting={MEETING_URL}")

    api     = ApiClient(base_url=API_BASE_URL, interview_id=INTERVIEW_ID)
    joiner  = ZoomJoiner(bot_name=BOT_NAME, api=api)
    audio   = AudioPipeline(
        deepgram_api_key=DEEPGRAM_API_KEY,
        interview_id=INTERVIEW_ID,
        api=api,
    )

    # Signal handler — clean up if container is stopped mid-meeting
    def handle_shutdown(sig, frame):
        log.info("Shutdown signal received — cleaning up")
        asyncio.create_task(shutdown(joiner, audio, api))

    signal.signal(signal.SIGTERM, handle_shutdown)
    signal.signal(signal.SIGINT, handle_shutdown)

    try:
        # Notify API that bot is starting
        await api.update_status("joining")

        # ── Run browser + audio pipeline in parallel ───────────────────────────
        #
        #   joiner.run()  → opens Chrome, joins Zoom, watches for meeting end
        #   audio.run()   → captures PulseAudio → streams to Deepgram
        #
        # Both run at the same time. When joiner detects meeting end,
        # it sets a shared stop_event that stops audio too.
        #
        stop_event = asyncio.Event()

        await asyncio.gather(
            joiner.run(meeting_url=MEETING_URL, stop_event=stop_event),
            audio.run(stop_event=stop_event),
        )

    except Exception as e:
        log.error(f"Bot crashed: {e}", exc_info=True)
        await api.update_status("failed", error=str(e))
        sys.exit(1)

    finally:
        log.info("Bot finished — meeting ended")
        await api.update_status("completed")


async def shutdown(joiner, audio, api):
    """Graceful shutdown — called on SIGTERM."""
    await joiner.close()
    await audio.stop()
    await api.update_status("completed")


if __name__ == "__main__":
    asyncio.run(main())
