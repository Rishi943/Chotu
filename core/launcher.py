"""Pre-launch interactive config screen for `python -m core.brain`.

Renders an arrow-key terminal screen (stdlib termios/tty only) letting the user
pick a model/provider preset and toggle mute/debug/voice/PTT/persona, then writes
the choices into os.environ. Must run BEFORE any env-reading core.* import so the
selections win over .env (which loads with override=False)."""

import os
import sys
from dataclasses import dataclass
from pathlib import Path

PRESETS = [
    {"label": "Gemma", "provider": "local", "model": "gemma-4-E4B-it-qat-UD-Q4_K_XL.gguf",
     "tag": "local", "spawn_llama": True, "mmproj": "gemma_mmproj-BF16.gguf",
     "extra": ["--swa-full", "--reasoning-budget", "-1"]},
    {"label": "Qwen", "provider": "local", "model": "Qwen3.5-4B-Q4_K_M.gguf",
     "tag": "local", "spawn_llama": True, "mmproj": "mmproj-BF16.gguf", "extra": []},
    {"label": "Qwen cloud", "provider": "local", "model": "qwen3.5-flash",
     "tag": "cloud — DashScope, spends tokens", "spawn_llama": False, "mmproj": None, "extra": None},
    {"label": "Claude", "provider": "claude", "model": "claude-sonnet-4-6",
     "tag": "cloud — spends tokens", "spawn_llama": False, "mmproj": None, "extra": None},
]


def llama_args(preset: dict, models_dir: Path) -> list[str]:
    """Build the llama-server argv for a local preset. Mirrors the repo's Launch flags."""
    return [
        "llama-server",
        "-m", str(models_dir / preset["model"]),
        "--mmproj", str(models_dir / preset["mmproj"]),
        "--port", "8080", "-ngl", "99", "-c", "16384", "--parallel", "1",
        "--image-max-tokens", "140", "--temp", "0.7", "--top-p", "0.95", "--top-k", "64",
        *preset["extra"],
    ]

# Toggle field name -> env var (checkbox toggles only).
TOGGLES = {"mute": "PALIV_MUTE", "debug": "PALIV_DEBUG", "stats": "PALIV_SHOW_STATS"}

N_ROWS = 11         # 4 presets + persona + input + mute/debug/stats + pi-bridge + Start
START_ROW = 10
PIBRIDGE_ROW = 9
PERSONA_ROW = 4
INPUT_ROW = 5
PRESET_ROWS = (0, 1, 2, 3)
TOGGLE_BY_ROW = {6: "mute", 7: "debug", 8: "stats"}
_INPUT_CYCLE = ["text", "voice", "ptt"]
_PI_CYCLE = ["running", "start", "offline"]


@dataclass
class LauncherState:
    preset_idx: int = 1            # default highlight: Qwen
    mute: bool = False
    debug: bool = False
    stats: bool = False
    input_mode: str = "text"       # text | voice | ptt
    pi_mode: str = "running"       # running | start | offline
    persona: str = "base"          # "base" or "reel"
    focus: int = 0                 # index into the focusable row list

    @classmethod
    def seed_from_env(cls, env: dict[str, str]) -> "LauncherState":
        provider = env.get("PALIV_LLM_PROVIDER", "local")
        model = env.get("PALIV_BRAIN_MODEL", "")
        if provider == "claude":
            preset_idx = 3
        elif model == "qwen3.5-flash":
            preset_idx = 2
        elif "gemma" in model.lower():
            preset_idx = 0
        else:
            preset_idx = 1  # default: Qwen
        if env.get("PALIV_PTT") == "1":
            input_mode = "ptt"
        elif env.get("PALIV_VOICE") == "1":
            input_mode = "voice"
        else:
            input_mode = "text"
        return cls(
            preset_idx=preset_idx,
            mute=env.get("PALIV_MUTE") == "1",
            debug=env.get("PALIV_DEBUG") == "1",
            stats=env.get("PALIV_SHOW_STATS") == "1",
            input_mode=input_mode,
            pi_mode="running",
            persona="reel" if env.get("PALIV_PERSONA") == "reel" else "base",
        )

    def apply_key(self, key: str) -> tuple[str, "LauncherState"]:
        if key == "QUIT":
            return ("quit", self)
        if key == "DOWN":
            self.focus = (self.focus + 1) % N_ROWS
            return ("continue", self)
        if key == "UP":
            self.focus = (self.focus - 1) % N_ROWS
            return ("continue", self)
        if key == "SELECT":
            if self.focus == START_ROW:
                return ("start", self)
            if self.focus in PRESET_ROWS:
                self.preset_idx = self.focus
            elif self.focus in TOGGLE_BY_ROW:
                name = TOGGLE_BY_ROW[self.focus]
                setattr(self, name, not getattr(self, name))
            elif self.focus == INPUT_ROW:
                self.input_mode = _INPUT_CYCLE[(_INPUT_CYCLE.index(self.input_mode) + 1) % 3]
            elif self.focus == PIBRIDGE_ROW:
                self.pi_mode = _PI_CYCLE[(_PI_CYCLE.index(self.pi_mode) + 1) % 3]
            elif self.focus == PERSONA_ROW:
                self.persona = "reel" if self.persona == "base" else "base"
            return ("continue", self)
        return ("continue", self)   # unknown key: no-op

    def to_env(self) -> dict[str, str]:
        preset = PRESETS[self.preset_idx]
        env = {
            "PALIV_LLM_PROVIDER": preset["provider"],
            "PALIV_BRAIN_MODEL": preset["model"],
            "PALIV_PERSONA": "reel" if self.persona == "reel" else "",
            "PALIV_VOICE": "1" if self.input_mode == "voice" else "0",
            "PALIV_PTT": "1" if self.input_mode == "ptt" else "0",
        }
        for field_name, var in TOGGLES.items():
            env[var] = "1" if getattr(self, field_name) else "0"
        if preset["spawn_llama"]:
            env["PALIV_BRAIN_URL"] = "http://127.0.0.1:8080/v1"
            env["PALIV_BRAIN_KEY"] = ""
        return env

    def render(self) -> str:
        def cur(row): return "›" if self.focus == row else " "
        lines = ["  Chotu brain — launch config        (↑/↓ move · space toggle · enter start · q quit)",
                 "", "  Model:"]
        for i, p in enumerate(PRESETS):
            mark = "•" if self.preset_idx == i else " "
            lines.append(f"  {cur(i)} ({mark}) {p['label']}  ({p['tag']})")
        lines.append("")
        lines.append(f"  {cur(PERSONA_ROW)} Persona: {self.persona} ▸")
        lines.append(f"  {cur(INPUT_ROW)} Input: {self.input_mode} ▸")
        lines.append("")
        for row, name, label in [(6, "mute", "Mute"), (7, "debug", "Debug"), (8, "stats", "Stats")]:
            box = "✓" if getattr(self, name) else " "
            lines.append(f"  {cur(row)} [{box}] {label}")
        lines.append(f"  {cur(PIBRIDGE_ROW)} Pi bridge: {self.pi_mode} ▸")
        lines.append("")
        lines.append(f"  {cur(START_ROW)} Start ▶")
        return "\n".join(lines)


def _read_key() -> str:
    """Block for one keystroke (raw mode already active). Map to a logical key."""
    ch = sys.stdin.read(1)
    if ch == "\x1b":                       # ESC — maybe an arrow sequence
        seq = sys.stdin.read(2)
        if seq == "[A":
            return "UP"
        if seq == "[B":
            return "DOWN"
        return "QUIT"                      # bare ESC quits
    if ch == " ":
        return "SELECT"
    if ch in ("\r", "\n"):
        return "ENTER_OR_SELECT"
    if ch in ("q", "Q"):
        return "QUIT"
    if ch == "\x03":                       # Ctrl-C
        return "QUIT"
    return ch


def run_launcher() -> None:
    """Interactive pre-launch config screen. Mutates os.environ in place.
    No-op when stdin is not a TTY or PALIV_NO_LAUNCHER=1."""
    if os.getenv("PALIV_NO_LAUNCHER") == "1" or not sys.stdin.isatty():
        return

    import termios
    import tty

    state = LauncherState.seed_from_env(dict(os.environ))
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        while True:
            sys.stdout.write("\x1b[2J\x1b[H")        # clear + home
            sys.stdout.write(state.render().replace("\n", "\r\n"))
            sys.stdout.flush()
            key = _read_key()
            # Both Space and Enter act as SELECT in the model; only Enter on the
            # Start row starts. apply_key treats "SELECT" uniformly, so normalise.
            logical = "SELECT" if key in ("SELECT", "ENTER_OR_SELECT") else key
            action, state = state.apply_key(logical)
            if action == "start":
                break
            if action == "quit":
                termios.tcsetattr(fd, termios.TCSADRAIN, old)
                sys.stdout.write("\r\n")
                sys.exit(0)
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
        sys.stdout.write("\r\n")
        sys.stdout.flush()

    for var, val in state.to_env().items():
        os.environ[var] = val
