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
