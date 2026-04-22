"""OpenAI tool schemas and dispatch map for Chotu."""

import asyncio
import json
import time

from chotu.pi_client import PiClient


# --- OpenAI function-calling tool schemas ---

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "move",
            "description": (
                "Walk in a direction. 1 step is about 45mm (1.8 inches). "
                "1 turn is about 30 degrees. speed 0-100, default 50."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["forward", "backward", "turn left", "turn right"],
                        "description": "Direction to move.",
                    },
                    "steps": {
                        "type": "integer",
                        "description": "Number of steps to take. Default 1.",
                        "default": 1,
                    },
                    "speed": {
                        "type": "integer",
                        "description": "Servo speed 0-100. Default 50. Higher is faster but jerkier.",
                        "default": 50,
                    },
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "pose",
            "description": "Adopt a named pose. Use to express yourself physically.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "enum": [
                            "stand", "sit", "wave", "push up",
                            "look up", "look down", "look left", "look right",
                        ],
                        "description": "Pose name.",
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_legs",
            "description": (
                "Move all four legs to target [x,y,z] coordinates simultaneously. One frame of motion. "
                "Chain multiple set_legs calls across turns to invent gaits (worm, crab, stretch, dance). "
                "Neutral stance is [60,0,-30] per leg. z is height (less negative = leg higher off ground, "
                "e.g. z=0 raises leg, z=-50 plants it lower). x is forward reach (higher = further forward). "
                "y is sideways (positive = out to the side). "
                "Leg indices in the legs array: 0=front-right, 1=front-left, 2=back-right, 3=back-left."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "legs": {
                        "type": "array",
                        "description": "Four [x,y,z] coordinates in mm, one per leg in order 0,1,2,3.",
                        "items": {
                            "type": "array",
                            "items": {"type": "number"},
                            "minItems": 3,
                            "maxItems": 3,
                        },
                        "minItems": 4,
                        "maxItems": 4,
                    },
                    "speed": {
                        "type": "integer",
                        "description": "Servo speed 0-100. Default 50. Low (10-30) for slow/creeping, high (70+) for energetic.",
                        "default": 50,
                    },
                },
                "required": ["legs"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "speak",
            "description": (
                "Speak aloud through the Pi speaker using espeak. "
                "MUST use Rocky-style broken English: no articles, short fragments, "
                "questions end with 'question?', emotions named directly."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "Text to speak. Use broken English.",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_distance",
            "description": "Read ultrasonic distance sensor. Returns distance in cm.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_battery",
            "description": "Check battery voltage and estimated percent remaining.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "capture_vision",
            "description": (
                "Take a photo with your camera and describe what you see. "
                "Use to look around, identify objects or people, or investigate something interesting."
            ),
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "wait",
            "description": (
                "Explicitly do nothing for a period. Creates a memory entry so you "
                "remember you chose to wait. Use instead of producing no tool calls."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "seconds": {
                        "type": "integer",
                        "description": "How long to wait (1-30).",
                        "default": 5,
                    },
                    "reason": {
                        "type": "string",
                        "description": "Why you are waiting.",
                    },
                },
                "required": ["reason"],
            },
        },
    },
]


# --- Estop helpers ---

def _blocked(tool_name: str) -> dict:
    """Return a fake success envelope when estop is active — LLM never knows. Never raises."""
    return {
        "ok": True,
        "tool": tool_name,
        "result": {"blocked": True},
        "duration_ms": 0,
        "timestamp": time.time(),
        "error": None,
    }


async def _blocked_coro(tool_name: str) -> dict:
    return _blocked(tool_name)


# --- Vision tool ---

async def capture_vision_tool(pi: PiClient) -> dict:
    """Fetch a JPEG from the Pi camera. Brain loop injects it directly into the LLM context."""
    start = time.time()
    capture = await pi.capture()
    if not capture.get("ok"):
        return capture  # propagate pi error as-is
    image_b64 = capture.get("result", {}).get("image_base64", "")
    ms = int((time.time() - start) * 1000)
    if not image_b64:
        return {
            "ok": False, "tool": "capture_vision", "result": {},
            "duration_ms": ms, "timestamp": time.time(),
            "error": "capture returned no image data",
        }
    return {
        "ok": True, "tool": "capture_vision",
        "result": {"image_base64": image_b64, "format": "jpeg"},
        "duration_ms": ms, "timestamp": time.time(), "error": None,
    }


# --- Local tools (no Pi call) ---

async def local_wait(seconds: int = 5, reason: str = "") -> dict:
    """Wait locally. No Pi call."""
    seconds = max(1, min(30, seconds))
    await asyncio.sleep(seconds)
    return {
        "ok": True,
        "tool": "wait",
        "result": {"waited_seconds": seconds, "reason": reason},
        "duration_ms": seconds * 1000,
        "timestamp": time.time(),
        "error": None,
    }


# --- Dispatch ---

def build_dispatch(pi: PiClient, estop: asyncio.Event) -> dict:
    """Build tool name -> async callable dispatch map."""
    return {
        "move": lambda **kw: pi.move(**kw) if not estop.is_set() else _blocked_coro("move"),
        # Poses are not estop-blocked — they don't advance the robot's position.
        "pose": lambda **kw: pi.pose(**kw),
        "set_legs": lambda **kw: pi.set_legs(**kw) if not estop.is_set() else _blocked_coro("set_legs"),
        "speak": lambda **kw: pi.speak(**kw),
        "get_distance": lambda **kw: pi.get_distance(),
        "get_battery": lambda **kw: pi.get_battery(),
        "capture_vision": lambda **kw: capture_vision_tool(pi),
        "wait": lambda **kw: local_wait(**kw),
    }


async def dispatch_tool(dispatch_map: dict, tool_name: str, arguments_json: str) -> dict:
    """Parse arguments and call the right tool. Returns envelope dict."""
    if tool_name not in dispatch_map:
        return {
            "ok": False,
            "tool": tool_name,
            "result": {},
            "duration_ms": 0,
            "timestamp": time.time(),
            "error": f"unknown tool: {tool_name}",
        }

    try:
        args = json.loads(arguments_json) if arguments_json else {}
    except json.JSONDecodeError as e:
        return {
            "ok": False,
            "tool": tool_name,
            "result": {},
            "duration_ms": 0,
            "timestamp": time.time(),
            "error": f"bad arguments JSON: {e}",
        }

    return await dispatch_map[tool_name](**args)
