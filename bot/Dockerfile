# ─────────────────────────────────────────────
# Stage 1: Base system with all dependencies
# ─────────────────────────────────────────────
FROM ubuntu:22.04

# Prevent interactive prompts during apt installs
ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONUNBUFFERED=1

# ── 1. System packages ─────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y \
    # Virtual display (fake screen for Chrome)
    xvfb \
    x11-utils \
    \
    # Virtual audio (fake mic/speaker)
    pulseaudio \
    pulseaudio-utils \
    alsa-utils \
    \
    # Media capture & processing
    ffmpeg \
    gstreamer1.0-tools \
    gstreamer1.0-plugins-base \
    gstreamer1.0-plugins-good \
    \
    # Chromium browser
    chromium-browser \
    chromium-browser-l10n \
    \
    # Python
    python3.11 \
    python3.11-dev \
    python3-pip \
    \
    # Process manager (runs Xvfb + PulseAudio + bot together)
    supervisor \
    \
    # Utilities
    curl \
    wget \
    dbus \
    dbus-x11 \
    && rm -rf /var/lib/apt/lists/*

# ── 2. Python dependencies ─────────────────────────────────────────────────────
COPY requirements.txt /app/requirements.txt
RUN pip3 install --no-cache-dir -r /app/requirements.txt

# Install Playwright and its bundled Chromium
# We'll use system Chromium for better PulseAudio integration
RUN pip3 install playwright==1.44.0
RUN playwright install chromium
RUN playwright install-deps chromium

# ── 3. PulseAudio config ───────────────────────────────────────────────────────
# Default PulseAudio config kills itself when idle.
# We override it to stay alive forever.
COPY config/pulse-client.conf /etc/pulse/client.conf
COPY config/pulse-default.pa  /etc/pulse/default.pa

# ── 4. Supervisor config ───────────────────────────────────────────────────────
# Supervisor is PID 1. It starts and monitors:
#   - Xvfb      (virtual screen)
#   - PulseAudio (virtual audio)
#   - bot.py    (your Playwright script)
COPY config/supervisord.conf /etc/supervisor/conf.d/bot.conf

# ── 5. Bot script ──────────────────────────────────────────────────────────────
COPY src/ /app/src/

WORKDIR /app

# Environment variables (overridden at ECS task level)
ENV DISPLAY=:99
ENV PULSE_SERVER=unix:/tmp/pulse/native
ENV PYTHONPATH=/app

# Supervisor starts everything
CMD ["/usr/bin/supervisord", "-n", "-c", "/etc/supervisor/supervisord.conf"]
