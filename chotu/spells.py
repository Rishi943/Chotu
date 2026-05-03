"""Spell implementations — wand pose + optional soundbite + HA REST call."""

import asyncio
import os
import time

import httpx

_WAND_POSE = [[80, 0, 20], [60, 0, -30], [60, 0, -30], [60, 0, -30]]  # FR raised
_NEUTRAL   = [[60, 0, -30] for _ in range(4)]


async def _ha_call(service: str, data: dict) -> bool:
    token    = os.getenv("HA_TOKEN", "")
    base_url = os.getenv("HA_BASE_URL", "http://127.0.0.1:8123")
    headers  = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    url      = f"{base_url}/api/services/light/{service}"
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.post(url, json=data, headers=headers)
            return r.status_code < 300
    except Exception as e:
        print(f"  [spells] HA call failed: {e}")
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
        from chotu.tools import _get_tts_lock
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
        await pi.set_legs(_WAND_POSE, speed=40)
        await asyncio.sleep(0.5)
        await pi.set_legs(_NEUTRAL, speed=40)
    except Exception:
        pass  # Pi unreachable — skip pose, proceed to spell


def _envelope(spell: str, ok: bool, start: float) -> dict:
    return {
        "ok": ok,
        "tool": "cast_spell",
        "result": {"spell": spell},
        "duration_ms": int((time.time() - start) * 1000),
        "timestamp": time.time(),
        "error": None if ok else "HA call failed",
    }


async def cast_lumos(pi) -> dict:
    start = time.time()
    entity = os.getenv("HA_LIGHT_ENTITY", "light.rishi_room_light")
    await _wand_pose(pi)
    await _play_soundbite("lumos")
    ok = await _ha_call("turn_on", {"entity_id": entity})
    return _envelope("lumos", ok, start)


async def cast_nox(pi) -> dict:
    start = time.time()
    entity = os.getenv("HA_LIGHT_ENTITY", "light.rishi_room_light")
    await _wand_pose(pi)
    await _play_soundbite("nox")
    ok = await _ha_call("turn_off", {"entity_id": entity})
    return _envelope("nox", ok, start)


async def cast_avada_kedavra(pi) -> dict:
    start = time.time()
    entity = os.getenv("HA_LIGHT_ENTITY", "light.rishi_room_light")
    await _wand_pose(pi)
    await _play_soundbite("avada_kedavra")
    ok_flash = await _ha_call("turn_on", {"entity_id": entity, "rgb_color": [0, 255, 0], "brightness": 255})
    await asyncio.sleep(0.3)
    ok_off = await _ha_call("turn_off", {"entity_id": entity})
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
