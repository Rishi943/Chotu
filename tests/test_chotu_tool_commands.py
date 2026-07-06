import asyncio
import pytest
import scripts.robot.chotu_tool as ct


class FakePi:
    async def set_legs(self, **kw): return {"ok": True, "tool": "set_legs", "result": kw}
    async def peek_over(self, **kw): return {"ok": True, "tool": "peek_over", "result": kw}
    async def health(self): return {"ok": True, "tool": "health", "result": {"status": "ok"}}
    async def play_sequence(self, **kw): return {"ok": True, "tool": "play_sequence", "result": kw}
    async def close(self): pass


@pytest.mark.asyncio
async def test_new_commands_route(monkeypatch):
    monkeypatch.setattr(ct, "PiClient", lambda host: FakePi(), raising=False)
    # _run imports PiClient locally from core.pi_client — patch there instead:
    import core.pi_client
    monkeypatch.setattr(core.pi_client, "PiClient", lambda host: FakePi())
    for cmd, args in [
        ("set_legs", {"legs": [[0, 0, -70]] * 4}),
        ("peek_over", {"lead": "left"}),
        ("health", {}),
        ("play_sequence", {"frames": [{"legs": [[0, 0, -70]] * 4}]}),
    ]:
        res = await ct._run(cmd, args)
        assert res["ok"] is True
