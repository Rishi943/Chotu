"""Spell implementations — wand pose + optional soundbite + Tuya local control."""

import asyncio
import os
import time



def _tuya_device():
    import tinytuya
    d = tinytuya.BulbDevice(
        os.getenv("TUYA_DEVICE_ID", ""),
        os.getenv("TUYA_LIGHT_IP", ""),
        os.getenv("TUYA_LOCAL_KEY", ""),
    )
    d.set_version(float(os.getenv("TUYA_VERSION", "3.3")))
    return d


async def _tuya_on() -> bool:
    try:
        await asyncio.to_thread(lambda: _tuya_device().turn_on())
        return True
    except Exception as e:
        print(f"  [spells] tuya error: {e}")
        return False


async def _tuya_off() -> bool:
    try:
        await asyncio.to_thread(lambda: _tuya_device().turn_off())
        return True
    except Exception as e:
        print(f"  [spells] tuya error: {e}")
        return False


async def _tuya_colour(r: int, g: int, b: int) -> bool:
    try:
        await asyncio.to_thread(lambda: _tuya_device().set_colour(r, g, b))
        return True
    except Exception as e:
        print(f"  [spells] tuya error: {e}")
        return False


_SOUND_ENV = {"lumos": "SPELL_LUMOS_SOUND", "nox": "SPELL_NOX_SOUND", "avada_kedavra": "SPELL_AVADA_SOUND"}

async def _play_soundbite(spell: str) -> None:
    path = os.getenv(_SOUND_ENV.get(spell, ""), "")
    if not path or not os.path.exists(path):
        return
    try:
        import wave
        import numpy as np
        import sounddevice as sd
        from core.tools import _get_tts_lock
        with wave.open(path, "rb") as wf:
            rate = wf.getframerate()
            ch = wf.getnchannels()
            audio = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16)
            if ch > 1:
                audio = audio.reshape(-1, ch)
        async with _get_tts_lock():
            sd.stop()
            sd.play(audio, samplerate=rate)
            await asyncio.to_thread(sd.wait)
    except Exception as e:
        print(f"  [spells] soundbite error: {e}")


async def _wand_pose(pi) -> None:
    try:
        await pi.do_trick("wand")
    except Exception:
        pass  # Pi unreachable — skip pose, proceed to spell


def _envelope(spell: str, ok: bool, start: float) -> dict:
    return {
        "ok": ok,
        "tool": "cast_spell",
        "result": {"spell": spell},
        "duration_ms": int((time.time() - start) * 1000),
        "timestamp": time.time(),
        "error": None if ok else "tuya call failed",
    }


async def cast_lumos(pi) -> dict:
    start = time.time()
    await _wand_pose(pi)
    await _play_soundbite("lumos")
    ok = await _tuya_on()
    return _envelope("lumos", ok, start)


async def cast_nox(pi) -> dict:
    start = time.time()
    await _wand_pose(pi)
    await _play_soundbite("nox")
    ok = await _tuya_off()
    return _envelope("nox", ok, start)


async def cast_avada_kedavra(pi) -> dict:
    start = time.time()
    await _wand_pose(pi)
    await _play_soundbite("avada_kedavra")
    ok_flash = await _tuya_colour(0, 255, 0)
    await asyncio.sleep(0.3)
    ok_off = await _tuya_off()
    return _envelope("avada_kedavra", ok_flash and ok_off, start)


async def cast_spell(pi, name: str) -> dict:
    dispatch = {
        "lumos":         cast_lumos,
        "nox":           cast_nox,
        "avada_kedavra": cast_avada_kedavra,
    }
    if name not in dispatch:
        return {
            "ok": False, "tool": "cast_spell", "result": {},
            "duration_ms": 0, "timestamp": time.time(),
            "error": f"unknown spell: {name}",
        }
    return await dispatch[name](pi)
