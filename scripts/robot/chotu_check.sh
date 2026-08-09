#!/usr/bin/env bash
# Read-only health probe. Touches NOTHING that can move him, start the camera,
# or crash the bridge.
#
# Why this exists: on 2026-08-09 a probe of POST /perception crashed the bridge
# outright -- vilib's camera init raised
#   AttributeError: 'NoneType' object has no attribute 'size'
# inside picamera2, and the bridge stopped answering every endpoint, mid-session,
# while Rushi was talking to him. The camera-starting endpoints are the fragile
# ones and they are not diagnostics. Rushi: "add a better path for that dont want
# you crashing it again on the fly."
#
# SAFE, and all this script uses:
#   GET /health  GET /battery  GET /distance
#
# NEVER call these to "check something". They start vilib and can wedge or crash
# the bridge, and vilib LEAVES THE CAMERA ON afterwards, draining the pack:
#   POST /perception   POST /capture   GET /stream   POST /camera {"on": true}
# If a camera check is genuinely needed, say so and get an explicit go first --
# same rule as motion, audio and the lamp.
#
# Usage:  bash scripts/robot/chotu_check.sh
set -u

BRIDGE="${PALIV_BRIDGE:-http://192.168.0.190:7000}"   # by IP: chotu.local hangs
MODEL="${PALIV_MODEL_URL:-http://127.0.0.1:8099}"
PIPER="${PALIV_PIPER_URL:-http://127.0.0.1:8101}"
CONSOLE="${PALIV_CONSOLE_URL:-https://127.0.0.1:8888}"

ok()   { printf '  \033[32mup\033[0m    %s\n' "$1"; }
down() { printf '  \033[31mDOWN\033[0m  %s\n' "$1"; }

probe() {  # name url
    if curl -s -m 5 -o /dev/null "$2" 2>/dev/null; then ok "$1"; else down "$1"; fi
}

echo "chotu check -- read-only, nothing here moves him"
echo

# --- the bridge ------------------------------------------------------------
health=$(curl -s -m 5 "$BRIDGE/health" 2>/dev/null)
if [ -z "$health" ]; then
    down "bridge  $BRIDGE"
    echo
    echo "  He is off, or the bridge died. To bring it back:"
    echo "    bash scripts/robot/chotu-up.sh --no-stand     # --no-stand: he is on a table"
    echo "  If he is powered off, press the button on the pack first."
    exit 1
fi
ok "bridge  $BRIDGE"
echo "$health" | grep -q '"camera":true' \
    && printf '  \033[33mnote\033[0m  camera is ON -- it drains the pack. Turn it off:\n        curl -X POST %s/camera -H "content-type: application/json" -d '"'"'{"on": false}'"'"'\n' "$BRIDGE"

# --- the pack --------------------------------------------------------------
batt=$(curl -s -m 6 "$BRIDGE/battery" 2>/dev/null)
if [ -n "$batt" ]; then
    echo "$batt" | python -c '
import json, sys
d = json.load(sys.stdin).get("result", {})
v, p = d.get("voltage", 0), d.get("percent", 0)
warn = ""
# Measured 2026-08-07: a whole-body trick at speed 40 sags the pack ~760 mV and
# survives; at speed 80 it browned the Pi out twice, once at 66 %.
if v and v < 7.0:
    warn = "   <- low: a whole-body trick may brown him out"
print(f"  pack  {v:.2f} V  {p} %{warn}")
print("  note  charging flag reads False even ON the charger -- judge by the")
print("        voltage trend, not that field (2026-08-09)")
' 2>/dev/null || echo "  pack  $batt"
fi

# --- the ultrasonic --------------------------------------------------------
dist=$(curl -s -m 8 "$BRIDGE/distance" 2>/dev/null)
if [ -n "$dist" ]; then
    echo "$dist" | python -c '
import json, sys
d = json.load(sys.stdin).get("result", {})
cm, rel = d.get("cm", -1), d.get("reliable", False)
if cm is not None and cm > 0:
    print(f"  range {cm:.1f} cm  reliable={rel}")
else:
    print(f"  range no echo (cm={cm}) -- open space ahead, or the sensor is dead.")
    print("        Hold a hand ~20 cm in front and run this again to tell them apart.")
' 2>/dev/null || echo "  range $dist"
fi

echo
# --- the laptop side -------------------------------------------------------
probe "model   $MODEL"    "$MODEL/health"
probe "piper   $PIPER"    "$PIPER/health"
if curl -sk -m 5 -o /dev/null "$CONSOLE/" 2>/dev/null; then
    ok "console $CONSOLE"
else
    down "console $CONSOLE"
    echo "        start it with:"
    echo "        tail -f /dev/null | env PALIV_NO_LAUNCHER=1 PALIV_SPEAK_OUTPUT=pi \\"
    echo "          \"C:/Users/rushi/paliv-win-venv/Scripts/python.exe\" -u -c \\"
    echo "          \"import sys; sys.path.insert(0,'E:/AI/paliv'); import asyncio, core.brain as b; asyncio.run(b.main())\""
    echo "        (NOT 'python -m core.brain' -- the launcher re-execs into system Python)"
fi
