"""
audio_pipeline.py — Captures Zoom audio and streams to Deepgram for transcription.

Flow:
    PulseAudio (virtual_speaker.monitor)
        → FFmpeg (raw PCM 16kHz mono)
        → Deepgram WebSocket
        → Transcript chunks
        → Your API (stored + broadcast via WebSocket)
"""

import asyncio
import json
import logging
import subprocess

import websockets

log = logging.getLogger("audio_pipeline")

DEEPGRAM_URL = (
    "wss://api.deepgram.com/v1/listen"
    "?model=nova-2"
    "&language=en"
    "&punctuate=true"
    "&diarize=true"          # Assigns speaker_0, speaker_1, etc.
    "&interim_results=true"  # Get partial transcripts for live feel
    "&endpointing=300"       # 300ms silence = end of utterance
    "&smart_format=true"
)


class AudioPipeline:
    def __init__(self, deepgram_api_key: str, interview_id: str, api):
        self.deepgram_api_key = deepgram_api_key
        self.interview_id     = interview_id
        self.api              = api
        self._ffmpeg_proc     = None

    # ── Main entry ─────────────────────────────────────────────────────────────

    async def run(self, stop_event: asyncio.Event):
        """
        Start FFmpeg and connect to Deepgram.
        Runs until stop_event is set (meeting ended).
        """
        log.info("Starting audio pipeline...")

        # Give PulseAudio a moment to fully initialize
        await asyncio.sleep(3)

        try:
            async with websockets.connect(
                DEEPGRAM_URL,
                extra_headers={"Authorization": f"Token {self.deepgram_api_key}"},
                ping_interval=20,
                ping_timeout=10,
            ) as ws:
                log.info("Connected to Deepgram ✅")

                self._ffmpeg_proc = self._start_ffmpeg()
                log.info("FFmpeg started — capturing audio ✅")

                # Run 3 coroutines in parallel:
                await asyncio.gather(
                    self._send_audio(ws, stop_event),       # FFmpeg → Deepgram
                    self._receive_transcripts(ws),           # Deepgram → Your API
                    self._keepalive(ws, stop_event),         # Prevent WS timeout
                    self._watch_stop(ws, stop_event),        # Close WS when done
                )

        except Exception as e:
            log.error(f"Audio pipeline error: {e}", exc_info=True)
        finally:
            self._kill_ffmpeg()

    # ── FFmpeg process ─────────────────────────────────────────────────────────

    def _start_ffmpeg(self) -> subprocess.Popen:
        """
        Start FFmpeg to capture from PulseAudio and output raw PCM.

        -f pulse                       → input from PulseAudio
        -i virtual_speaker.monitor     → capture what Chrome is playing (Zoom audio)
        -ar 16000                      → resample to 16kHz (Deepgram prefers this)
        -ac 1                          → mono audio
        -f s16le                       → raw PCM, 16-bit little-endian
        pipe:1                         → send to stdout (we read it in Python)
        """
        return subprocess.Popen(
            [
                "ffmpeg",
                "-f", "pulse",
                "-i", "virtual_speaker.monitor",
                "-ar", "16000",
                "-ac", "1",
                "-f", "s16le",
                "pipe:1",              # stdout
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,  # Suppress FFmpeg logs (very noisy)
        )

    # ── Coroutine 1: Send audio to Deepgram ───────────────────────────────────

    async def _send_audio(self, ws, stop_event: asyncio.Event):
        """
        Read audio chunks from FFmpeg stdout and send to Deepgram.
        Chunk size = 4096 bytes ≈ 128ms of audio at 16kHz mono 16-bit.
        """
        loop = asyncio.get_event_loop()

        while not stop_event.is_set():
            # Read from FFmpeg in a thread (blocking I/O)
            chunk = await loop.run_in_executor(
                None,
                self._ffmpeg_proc.stdout.read,
                4096
            )

            if not chunk:
                log.warning("FFmpeg stdout closed — audio capture stopped")
                break

            try:
                await ws.send(chunk)
            except websockets.ConnectionClosed:
                log.warning("Deepgram WebSocket closed")
                break

        # Tell Deepgram we're done sending audio
        try:
            await ws.send(json.dumps({"type": "CloseStream"}))
        except Exception:
            pass

    # ── Coroutine 2: Receive transcripts from Deepgram ────────────────────────

    async def _receive_transcripts(self, ws):
        """
        Receive transcript results from Deepgram and forward to your API.

        Deepgram sends:
        {
            "type": "Results",
            "channel": {
                "alternatives": [{
                    "transcript": "Tell me about yourself",
                    "words": [
                        { "word": "Tell", "speaker": 0, "start": 0.1, "end": 0.4 },
                        ...
                    ]
                }]
            },
            "is_final": true,
            "speech_final": true
        }
        """
        async for raw_message in ws:
            try:
                msg = json.loads(raw_message)

                if msg.get("type") != "Results":
                    continue

                alt = msg["channel"]["alternatives"][0]
                transcript = alt.get("transcript", "").strip()
                is_final   = msg.get("is_final", False)
                words      = alt.get("words", [])

                if not transcript:
                    continue

                # Extract speaker ID from the first word
                # Deepgram gives speaker as integer: 0, 1, 2...
                speaker_id = None
                if words:
                    speaker_id = words[0].get("speaker")

                log.info(
                    f"[{'FINAL' if is_final else 'interim'}] "
                    f"Speaker {speaker_id}: {transcript}"
                )

                # Send to your API
                await self.api.send_transcript(
                    text=transcript,
                    speaker_id=speaker_id,
                    words=words,
                    is_final=is_final,
                )

            except Exception as e:
                log.error(f"Error processing transcript: {e}")

    # ── Coroutine 3: Keepalive ─────────────────────────────────────────────────

    async def _keepalive(self, ws, stop_event: asyncio.Event):
        """
        Send a keepalive every 8 seconds.
        Without this, Deepgram closes the connection during long silences.
        """
        while not stop_event.is_set():
            await asyncio.sleep(8)
            try:
                await ws.send(json.dumps({"type": "KeepAlive"}))
            except Exception:
                break

    # ── Coroutine 4: Watch for stop ────────────────────────────────────────────

    async def _watch_stop(self, ws, stop_event: asyncio.Event):
        """Close the Deepgram WebSocket when the meeting ends."""
        await stop_event.wait()
        log.info("Meeting ended — closing Deepgram connection")
        try:
            await ws.close()
        except Exception:
            pass

    # ── Cleanup ────────────────────────────────────────────────────────────────

    def _kill_ffmpeg(self):
        if self._ffmpeg_proc:
            self._ffmpeg_proc.terminate()
            self._ffmpeg_proc.wait()
            log.info("FFmpeg stopped")

    async def stop(self):
        self._kill_ffmpeg()
