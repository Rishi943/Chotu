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

    async def move(self, direction: str, steps: int = 1, speed: int = 80) -> dict:
        return await self._post_slow("/move", "move", {
            "direction": direction,
            "steps": steps,
            "speed": speed,
        })

    async def pose(self, name: str, speed: int = 80) -> dict:
        return await self._post_slow("/pose", "pose", {"name": name, "speed": speed})

    async def set_legs(self, legs: list, speed: int = 80) -> dict:
        return await self._post_slow("/set_legs", "set_legs", {"legs": legs, "speed": speed})

    async def peek_over(self, lead: str, reach: str = "shallow",
                        pause_s: float = 1.5, speed: int = 60) -> dict:
        return await self._post_slow("/peek_over", "peek_over", {
            "lead": lead, "reach": reach, "pause_s": pause_s, "speed": speed,
        })

    async def play_sequence(self, frames: list, speed: int | None = None) -> dict:
        body: dict = {"frames": frames}
        if speed is not None:
            body["speed"] = speed
        return await self._post_slow("/play_sequence", "play_sequence", body)

    # Default was 80. A push-up drives all twelve servos together and is the
    # heaviest load the robot has; 40 is the only speed that whole-body load has
    # ever been proven to survive on battery (Rushi, 2026-08-07: "perfect it
    # works on 40 on battery"). At 80 it browned the Pi out on 2026-08-07 and
    # again on 2026-08-09, mid-conversation, forcing a reboot. Lowered 08-09.
    async def do_trick(self, name: str, speed: int = 40, reps: int = 1) -> dict:
        return await self._post_slow(
            "/trick", "trick", {"name": name, "speed": speed, "reps": reps},
        )

    async def speak(self, text: str) -> dict:
        return await self._post("/speak", "speak", {"text": text})

    async def play_wav(self, wav_bytes: bytes) -> dict:
        try:
            r = await self._slow.post(
                "/play_wav",
                content=wav_bytes,
                headers={"content-type": "application/octet-stream"},
            )
            return r.json()
        except Exception as e:
            return self._error_envelope("play_wav", e)

    async def get_distance(self) -> dict:
        return await self._get("/distance", "get_distance")

    async def capture(self, full: bool = False) -> dict:
        return await self._post("/capture", "capture", {"full": full})

    async def get_battery(self) -> dict:
        return await self._get("/battery", "battery")

    async def set_face(self, name: str) -> dict:
        return await self._post("/face", "face", {"name": name})

    async def get_perception(
        self,
        color: str | None = None,
        face: bool = False,
        human: bool = False,
    ) -> dict:
        body: dict = {}
        if color:
            body["color"] = color
        if face:
            body["face"] = True
        if human:
            body["human"] = True
        return await self._post("/perception", "perception", body)

    async def power(self, action: str) -> dict:
        """Ask the bridge to shut down or reboot the Pi. A shutdown that works
        drops the connection before the response arrives, so on this one call a
        connection error is treated as success rather than pi_unreachable."""
        try:
            r = await self._default.post(
                "/power", json={"action": action, "confirm": "chotu"}
            )
            return r.json()
        except Exception:
            return {
                "ok": True,
                "tool": "power",
                "result": {"action": action},
                "duration_ms": 0,
                "timestamp": time.time(),
                "error": None,
            }

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

    async def close(self) -> None:
        await self._default.aclose()
        await self._slow.aclose()

    def _error_envelope(self, tool: str, error: Exception) -> dict:
        return {
            "ok": False,
            "tool": tool,
            "result": {},
            "duration_ms": 0,
            "timestamp": time.time(),
            "error": f"pi_unreachable: {error}",
        }
