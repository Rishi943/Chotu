"""Five tools. Short lists beat long ones for a small model.

`act` exists so the model never has to choose between `pose "push up"` (the
SunFounder preset that ends SEATED) and the real push-up trick. It picks a
name; this table picks the endpoint.
"""

# name the model says -> (bridge endpoint, name the bridge wants)
ACT_NAMES: dict[str, tuple[str, str]] = {
    "stand":      ("pose",  "stand"),
    "sit":        ("pose",  "sit"),
    "wave":       ("trick", "wave"),
    "push up":    ("trick", "pushup"),
    "twist":      ("trick", "twist"),
    "swimming":   ("trick", "swimming"),
    "handwork":   ("trick", "handwork"),
    "dance":      ("trick", "dance"),
    "look up":    ("pose",  "look up"),
    "look down":  ("pose",  "look down"),
    "look left":  ("pose",  "look left"),
    "look right": ("pose",  "look right"),
}

SENSE_KINDS: tuple[str, ...] = ("battery", "distance", "view")

# The OLED expressions the Pi's /face endpoint accepts. Chotu picks one per turn
# in the same reply as the action, so the face follows what he is saying instead
# of only flipping between thinking and idle.
FACES: tuple[str, ...] = (
    "idle", "speak_open", "speak_close", "playful", "judging", "embarrassed",
    "dissatisfied", "angry", "sad", "indifferent", "confused", "doubt",
    "surprised", "greeting", "wink", "sleeping", "magic", "cute", "thinking",
    "dead",
)

# How many actions one reply may queue. Not a model limit -- a power one: every
# item is another servo load, and the measured ceiling is +/-12 mm at speed 40
# on a 64 % pack.
MAX_SEQUENCE = 6

TOOL_SCHEMAS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "move",
            "description": "Walk. Use this to go somewhere or to turn on the spot.",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["forward", "backward", "turn left", "turn right"],
                    },
                    "steps": {"type": "integer", "minimum": 1, "maximum": 10},
                    "speed": {"type": "integer", "minimum": 20, "maximum": 90},
                },
                "required": ["direction"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "act",
            "description": "Do a named body action -- a pose or a trick.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "enum": sorted(ACT_NAMES)},
                    "reps": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 10,
                        "default": 1,
                        "description": (
                            "How many times to repeat a trick (e.g. push up). "
                            "Use it whenever a count is asked for. Poses cannot repeat."
                        ),
                    },
                },
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "sense",
            "description": (
                "Read one thing about yourself or the world. "
                "battery: charge left. distance: centimetres to whatever is "
                "in front. view: look through your camera."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "what": {"type": "string", "enum": list(SENSE_KINDS)},
                },
                "required": ["what"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "say",
            "description": "Speak out loud. Keep it to one or two short sentences.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read",
            "description": (
                "Read a file from docs/ when you need to know something that "
                "is not in your instructions. Do not guess instead of reading."
            ),
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    },
]
