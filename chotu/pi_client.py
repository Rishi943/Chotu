"""Async HTTP client for the Chotu Pi bridge."""

import time
import httpx


class PiClient:
    """Wraps every Pi bridge endpoint. Returns envelope dicts, never raises."""

    def __init__(self, base_url: str):
        self.base_url = base_url
        self._default = httpx.AsyncClient(base_url=base_url, timeout=10.0)
        self._slow = httpx.AsyncClient(base_url=base_url, timeout=30.0)  # move/pose/set_legs can take 10-25s

    async def health(self) -> dict:
        return await self._get("/health", "health")

    async def move(self, direction: str, steps: int = 1, speed: int = 50) -> dict:
        return await self._post_slow("/move", "move", {
            "direction": direction,
            "steps": steps,
            "speed": speed,
        })

    async def pose(self, name: str) -> dict:
        return await self._post_slow("/pose", "pose", {"name": name})

    async def set_legs(self, legs: list, speed: int = 50) -> dict:
        return await self._post_slow("/set_legs", "set_legs", {"legs": legs, "speed": speed})

    async def speak(self, text: str) -> dict:
        return await self._post("/speak", "speak", {"text": text})

    async def get_distance(self) -> dict:
        return await self._get("/distance", "get_distance")

    async def capture(self) -> dict:
        return await self._post("/capture", "capture", {})

    async def get_battery(self) -> dict:
        return await self._get("/battery", "battery")

    # --- Internal helpers ---

    async def _get(self, path: str, tool: str) -> dict:
        try:
            r = await self._default.get(path)
            return r.json()
        except Exception as e:
            return self._error_envelope(tool, e)

    async def _post(self, path: str, tool: str, body: dict) -> dict:
        try:
            r = await self._default.post(path, json=body)
            return r.json()
        except Exception as e:
            return self._error_envelope(tool, e)

    async def _post_slow(self, path: str, tool: str, body: dict) -> dict:
        try:
            r = await self._slow.post(path, json=body)
            return r.json()
        except Exception as e:
            return self._error_envelope(tool, e)

    @staticmethod
    def _error_envelope(tool: str, error: Exception) -> dict:
        return {
            "ok": False,
            "tool": tool,
            "result": {},
            "duration_ms": 0,
            "timestamp": time.time(),
            "error": f"pi_unreachable: {error}",
        }
