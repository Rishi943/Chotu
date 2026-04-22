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

def build_dispatch(pi: PiClient) -> dict:
    """Build tool name -> async callable dispatch map."""
    return {
        "move": lambda **kw: pi.move(**kw),
        "pose": lambda **kw: pi.pose(**kw),
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
