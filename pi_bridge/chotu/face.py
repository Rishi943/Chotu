"""OLED face controller for Chotu (Pi-side only)."""

import logging
import threading
from pathlib import Path

try:
    from luma.core.interface.serial import i2c
    from luma.oled.device import sh1106
    _LUMA_AVAILABLE = True
except ImportError:
    _LUMA_AVAILABLE = False
    logging.warning("face: luma.oled not available — all face calls are no-ops")

try:
    from PIL import Image
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False

FACES_DIR = Path(__file__).parent / "faces"

_device = None
_lock = threading.Lock()
_cache: dict = {}
_current_face: str = ""
_speak_anim_stop = threading.Event()


def _load_face(path: Path):
    """Load PNG, flatten RGBA onto black, return mode-1 image."""
    img = Image.open(path)
    bg = Image.new("RGB", img.size, (0, 0, 0))
    if img.mode == "RGBA":
        bg.paste(img, mask=img.split()[3])
    else:
        bg.paste(img)
    return bg.convert("1")


def init() -> None:
    """Init SH1106 device and preload all face PNGs into memory."""
    global _device
    if not _LUMA_AVAILABLE or not _PIL_AVAILABLE:
        return
    try:
        serial = i2c(port=1, address=0x3C)
        _device = sh1106(serial)
        logging.info("face: OLED initialised (SH1106 @ 0x3C)")
    except Exception as e:
        logging.warning(f"face: OLED init failed: {e}")
        return

    if not FACES_DIR.exists():
        logging.warning(f"face: faces dir not found: {FACES_DIR}")
        return

    loaded = 0
    for png in FACES_DIR.glob("*.png"):
        try:
            _cache[png.stem] = _load_face(png)
            loaded += 1
        except Exception as e:
            logging.warning(f"face: failed to load {png.name}: {e}")
    logging.info(f"face: {loaded} faces preloaded from {FACES_DIR}")


def _display_face(name: str) -> bool:
    """Display a preloaded face. Must be called with _lock held or from _set_face_thread."""
    if _device is None or name not in _cache:
        return False
    try:
        _device.display(_cache[name])
        return True
    except Exception as e:
        logging.warning(f"face: display error ({name}): {e}")
        return False


def _set_face_thread(name: str) -> None:
    global _current_face
    with _lock:
        ok = _display_face(name)
    if ok:
        _current_face = name


def set_face(name: str) -> bool:
    """Set face expression. Non-blocking — runs in background thread. Returns False if unknown."""
    if _device is None:
        return False
    if name not in _cache:
        logging.warning(f"face: unknown expression '{name}'")
        return False
    t = threading.Thread(target=_set_face_thread, args=(name,), daemon=True)
    t.start()
    return True


def _speak_animation_thread() -> None:
    frames = ["speak_open", "speak_close"]
    i = 0
    while not _speak_anim_stop.is_set():
        frame = frames[i % 2]
        if frame in _cache:
            with _lock:
                _display_face(frame)
        i += 1
        _speak_anim_stop.wait(timeout=0.125)


def start_speak_animation() -> None:
    """Start alternating speak_open/speak_close at ~4Hz."""
    _speak_anim_stop.clear()
    t = threading.Thread(target=_speak_animation_thread, daemon=True)
    t.start()


def stop_speak_animation() -> None:
    """Stop speak animation and return to idle."""
    _speak_anim_stop.set()
    set_face("idle")


def clear() -> None:
    """Blank the OLED screen."""
    if _device is None:
        return
    try:
        with _lock:
            _device.clear()
    except Exception as e:
        logging.warning(f"face: clear error: {e}")


@property
def current_face() -> str:
    return _current_face
