#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# test_local.sh — Build and run the bot container locally
#
# Usage:
#   ./scripts/test_local.sh
#
# What it does:
#   1. Builds the Docker image
#   2. Runs it with a test Zoom meeting
#   3. Shows live logs so you can see what's happening
# ─────────────────────────────────────────────────────────────────────────────

set -e

# ── Config — edit these ────────────────────────────────────────────────────────
MEETING_URL="https://zoom.us/j/YOUR_MEETING_ID?pwd=YOUR_PASSWORD"
DEEPGRAM_API_KEY="your_deepgram_key_here"
API_BASE_URL="http://host.docker.internal:8000"   # Points to your local FastAPI
# ──────────────────────────────────────────────────────────────────────────────

IMAGE_NAME="zoom-bot:local"
CONTAINER_NAME="zoom-bot-test"

echo "🔨 Building Docker image..."
docker build -t $IMAGE_NAME ./bot/

echo ""
echo "🚀 Starting bot container..."
echo "   Meeting: $MEETING_URL"
echo "   API:     $API_BASE_URL"
echo ""

# Remove old container if exists
docker rm -f $CONTAINER_NAME 2>/dev/null || true

docker run \
    --name $CONTAINER_NAME \
    --rm \
    \
    # Environment variables (would come from ECS task in production)
    -e MEETING_URL="$MEETING_URL" \
    -e INTERVIEW_ID="test-interview-001" \
    -e BOT_NAME="Test Bot" \
    -e API_BASE_URL="$API_BASE_URL" \
    -e DEEPGRAM_API_KEY="$DEEPGRAM_API_KEY" \
    \
    # Required capabilities for PulseAudio + Xvfb
    --cap-add=SYS_ADMIN \
    --security-opt seccomp=unconfined \
    \
    # Resource limits (mirrors ECS task definition)
    --memory="2g" \
    --cpus="1.0" \
    \
    # Mount for debug screenshots
    -v /tmp/bot-screenshots:/tmp \
    \
    $IMAGE_NAME

echo ""
echo "✅ Container exited"
echo "📸 Screenshots saved to /tmp/bot-screenshots/"
