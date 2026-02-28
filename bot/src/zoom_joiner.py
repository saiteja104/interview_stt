"""
zoom_joiner.py — Controls Chrome via Playwright to join a Zoom meeting.

Flow:
  1. Launch Chrome (non-headless, uses Xvfb virtual display)
  2. Navigate to Zoom web client URL
  3. Enter bot name → click Join
  4. Handle waiting room
  5. Once inside, inject speaker-watcher script
  6. Poll for meeting end
  7. Signal stop_event so audio pipeline shuts down too
"""

import asyncio
import logging
import re

from playwright.async_api import async_playwright, Page, Browser, BrowserContext

log = logging.getLogger("zoom_joiner")


# How long to wait for the host to admit the bot from waiting room (seconds)
WAITING_ROOM_TIMEOUT = 300  # 5 minutes


class ZoomJoiner:
    def __init__(self, bot_name: str, api):
        self.bot_name = bot_name
        self.api      = api
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page:    Page | None    = None

    # ── Main entry ─────────────────────────────────────────────────────────────

    async def run(self, meeting_url: str, stop_event: asyncio.Event):
        async with async_playwright() as pw:
            await self._launch_browser(pw)
            await self._join_meeting(meeting_url)
            await self._wait_for_meeting_end(stop_event)

    # ── Step 1: Launch Chrome ──────────────────────────────────────────────────

    async def _launch_browser(self, pw):
        log.info("Launching Chrome...")

        self._browser = await pw.chromium.launch(
            # IMPORTANT: headless=False — use Xvfb instead
            # Zoom detects and blocks true headless mode
            headless=False,

            args=[
                # Use our Xvfb virtual display
                "--display=:99",

                # Security/sandbox (required in Docker)
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",       # Use /tmp instead of /dev/shm

                # Auto-accept mic/camera permission popups
                "--use-fake-ui-for-media-stream",

                # Use PulseAudio for audio I/O
                "--alsa-output-device=pulse",
                "--alsa-input-device=pulse",

                # Hide that this is an automated browser
                "--disable-blink-features=AutomationControlled",

                # Performance
                "--disable-gpu",
                "--disable-extensions",
                "--window-size=1280,720",

                # Disable notifications (Zoom tries to send them)
                "--disable-notifications",
            ]
        )

        self._context = await self._browser.new_context(
            # Pre-grant mic + camera so Zoom doesn't prompt
            permissions=["microphone", "camera"],

            # Look like a real Linux Chrome user
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/125.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 720},
        )

        # Spoof automation-detection properties
        await self._context.add_init_script("""
            // Hide webdriver flag
            Object.defineProperty(navigator, 'webdriver', { get: () => false });

            // Fake plugins (real browsers have these)
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { name: 'Chrome PDF Plugin' },
                    { name: 'Chrome PDF Viewer' },
                    { name: 'Native Client' },
                ]
            });

            // Fake language
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
        """)

        self._page = await self._context.new_page()
        log.info("Chrome launched ✅")

    # ── Step 2: Join the Meeting ───────────────────────────────────────────────

    async def _join_meeting(self, meeting_url: str):
        log.info(f"Navigating to meeting: {meeting_url}")

        # Convert standard Zoom URL to web client URL
        # zoom.us/j/123456?pwd=xxx → zoom.us/wc/join/123456?pwd=xxx
        web_client_url = self._to_web_client_url(meeting_url)
        log.info(f"Web client URL: {web_client_url}")

        await self._page.goto(web_client_url, wait_until="networkidle", timeout=30000)

        # Take a debug screenshot
        await self._screenshot("01_page_loaded")

        # ── Enter name ─────────────────────────────────────────────────────────
        # Zoom shows a "Your Name" input before joining
        try:
            name_input = await self._page.wait_for_selector(
                'input[placeholder="Your Name"], input#inputname',
                timeout=10000
            )
            await name_input.fill(self.bot_name)
            log.info(f"Entered bot name: {self.bot_name}")
        except Exception:
            log.warning("Name input not found — may have been skipped")

        await self._screenshot("02_name_entered")

        # ── Click Join ─────────────────────────────────────────────────────────
        # Try multiple selectors — Zoom's UI changes often
        join_selectors = [
            'button.preview-join-button',
            'button:has-text("Join")',
            '#joinBtn',
            'button[class*="join"]',
        ]
        joined = False
        for selector in join_selectors:
            try:
                btn = await self._page.wait_for_selector(selector, timeout=5000)
                await btn.click()
                joined = True
                log.info(f"Clicked join button [{selector}]")
                break
            except Exception:
                continue

        if not joined:
            raise RuntimeError("Could not find Join button — Zoom UI may have changed")

        await self._screenshot("03_join_clicked")

        # ── Handle audio dialog ────────────────────────────────────────────────
        # Zoom sometimes shows "Join with Computer Audio" dialog
        await asyncio.sleep(2)
        try:
            audio_btn = await self._page.wait_for_selector(
                'button:has-text("Join Audio"), button:has-text("Join with Computer Audio")',
                timeout=6000
            )
            await audio_btn.click()
            log.info("Joined with computer audio")
        except Exception:
            log.info("No audio dialog — skipped")

        # ── Wait for waiting room or direct entry ──────────────────────────────
        await self._handle_waiting_room()

    async def _handle_waiting_room(self):
        """
        Wait until the bot is admitted into the meeting.
        Zoom can put the bot in a waiting room — a human host must click 'Admit'.
        """
        log.info("Checking for waiting room...")
        await self.api.update_status("waiting_room")

        start = asyncio.get_event_loop().time()

        while True:
            elapsed = asyncio.get_event_loop().time() - start

            if elapsed > WAITING_ROOM_TIMEOUT:
                raise TimeoutError(
                    f"Bot was not admitted within {WAITING_ROOM_TIMEOUT}s"
                )

            # Check if we're inside the meeting
            in_meeting = await self._page.query_selector(
                '.meeting-app, #wc-container-left, .video-avatar__avatar'
            )
            if in_meeting:
                log.info("Bot is now inside the meeting ✅")
                await self._screenshot("04_in_meeting")
                await self.api.update_status("active")
                return

            # Check if still in waiting room
            waiting = await self._page.query_selector(
                '.waiting-room-container, [class*="waiting-room"]'
            )
            if waiting:
                log.info(f"In waiting room... ({int(elapsed)}s elapsed)")
            else:
                # Not in waiting room, not in meeting — might be loading
                log.info("Transitioning...")

            await asyncio.sleep(3)

    # ── Step 3: Watch for Speaker Changes ────────────────────────────────────

    async def _inject_speaker_watcher(self):
        """
        Inject JavaScript into the Zoom page that watches for
        speaker changes (highlighted participant tile).

        Stores speaker events in window.__speakerEvents so we can
        poll them from Python.
        """
        await self._page.evaluate("""
            window.__speakerEvents = [];

            const observer = new MutationObserver(() => {
                // Zoom highlights the active speaker's tile with a specific class
                // These selectors may need updating if Zoom changes their UI
                const selectors = [
                    '.speaker-active-container .participants-item__display-name',
                    '.video-avatar__avatar--active .video-avatar__avatar-name',
                    '[class*="active-speaker"] [class*="display-name"]',
                ];

                for (const sel of selectors) {
                    const el = document.querySelector(sel);
                    if (el) {
                        const name = el.textContent.trim();
                        if (name) {
                            window.__speakerEvents.push({
                                name: name,
                                timestamp: Date.now(),
                            });
                        }
                        break;
                    }
                }
            });

            observer.observe(document.body, {
                childList: true,
                subtree: true,
                attributes: true,
                attributeFilter: ['class']
            });

            console.log('Speaker watcher injected ✅');
        """)
        log.info("Speaker watcher injected")

    # ── Step 4: Wait for Meeting End ───────────────────────────────────────────

    async def _wait_for_meeting_end(self, stop_event: asyncio.Event):
        """
        Keep the bot alive until the meeting ends.
        Periodically polls for:
          - Speaker changes (forwards to API)
          - Meeting end dialog
        """
        await self._inject_speaker_watcher()
        log.info("Bot is live in the meeting. Waiting for it to end...")

        while not stop_event.is_set():

            # ── Check for meeting end ──────────────────────────────────────────
            ended = await self._page.query_selector(
                '[class*="meeting-ended"], '
                ':has-text("This meeting has been ended"), '
                '.ReactModal__Content:has-text("ended")'
            )
            if ended:
                log.info("Meeting ended — host ended the call")
                stop_event.set()
                break

            # ── Collect speaker events ─────────────────────────────────────────
            events = await self._page.evaluate(
                "window.__speakerEvents.splice(0)"  # Drain the array
            )
            for event in events:
                await self.api.send_speaker_event(
                    name=event["name"],
                    timestamp_ms=event["timestamp"],
                )

            await asyncio.sleep(1)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _to_web_client_url(self, meeting_url: str) -> str:
        """
        Convert any Zoom URL format to the web client URL.

        zoom.us/j/123456789?pwd=xxx
            → zoom.us/wc/join/123456789?pwd=xxx
        """
        # Extract meeting ID
        match = re.search(r'/j/(\d+)', meeting_url)
        if not match:
            raise ValueError(f"Cannot extract meeting ID from URL: {meeting_url}")

        meeting_id = match.group(1)

        # Preserve password parameter if present
        pwd_match = re.search(r'pwd=([^&]+)', meeting_url)
        pwd_param = f"?pwd={pwd_match.group(1)}" if pwd_match else ""

        return f"https://zoom.us/wc/join/{meeting_id}{pwd_param}"

    async def _screenshot(self, name: str):
        """Save a debug screenshot."""
        try:
            path = f"/tmp/bot_screenshot_{name}.png"
            await self._page.screenshot(path=path)
            log.debug(f"Screenshot saved: {path}")
        except Exception:
            pass  # Non-critical

    async def close(self):
        if self._browser:
            await self._browser.close()
