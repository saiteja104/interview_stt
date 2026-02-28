#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# debug_container.sh — Useful commands for debugging the bot container
# ─────────────────────────────────────────────────────────────────────────────

CONTAINER_NAME="zoom-bot-test"

echo "=== Debugging container: $CONTAINER_NAME ==="
echo ""

# 1. Get a shell inside the running container
echo "To get a shell inside the container:"
echo "  docker exec -it $CONTAINER_NAME bash"
echo ""

# 2. Check all processes are running
echo "To check Xvfb + PulseAudio + bot are running:"
echo "  docker exec $CONTAINER_NAME ps aux"
echo ""

# 3. Check PulseAudio is working
echo "To check PulseAudio sinks:"
echo "  docker exec $CONTAINER_NAME pactl list sinks short"
echo ""

# 4. Check what audio is being captured
echo "To verify FFmpeg is capturing audio (you should see bitrate > 0):"
echo "  docker exec $CONTAINER_NAME bash -c 'ffmpeg -f pulse -i virtual_speaker.monitor -t 5 /tmp/test.wav 2>&1'"
echo ""

# 5. Take a screenshot of what Chrome sees
echo "To take a screenshot of what Chrome sees:"
echo "  docker exec $CONTAINER_NAME bash -c 'DISPLAY=:99 import -window root /tmp/screenshot.png'"
echo "  docker cp $CONTAINER_NAME:/tmp/screenshot.png ./screenshot.png"
echo ""

# 6. Watch live logs
echo "To watch live bot logs:"
echo "  docker logs -f $CONTAINER_NAME"
echo ""

# 7. Supervisor status
echo "To check supervisor process status:"
echo "  docker exec $CONTAINER_NAME supervisorctl status"
echo ""

# Run the ps command automatically if container is running
if docker ps -q -f name=$CONTAINER_NAME | grep -q .; then
    echo "=== Current process list ==="
    docker exec $CONTAINER_NAME ps aux
    echo ""
    echo "=== Supervisor status ==="
    docker exec $CONTAINER_NAME supervisorctl status 2>/dev/null || echo "(supervisorctl not available)"
    echo ""
    echo "=== PulseAudio sinks ==="
    docker exec $CONTAINER_NAME pactl list sinks short 2>/dev/null || echo "(PulseAudio not running)"
else
    echo "Container '$CONTAINER_NAME' is not running."
    echo "Start it with: ./scripts/test_local.sh"
fi
