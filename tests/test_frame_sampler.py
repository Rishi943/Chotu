"""FrameSampler: connects to Pi /stream, decodes MJPEG, keeps deque of last N
JPEGs, pushes each new frame to the active backend. Sampling rate ~1 FPS."""

from core.frame_sampler import FrameSampler


class FakeBackend:
    def __init__(self):
        self.frames: list[tuple[bytes, float]] = []

    async def send_frame(self, jpeg_bytes: bytes, ts: float) -> None:
        self.frames.append((jpeg_bytes, ts))


async def test_latest_returns_none_when_empty():
    backend = FakeBackend()
    s = FrameSampler(backend=backend, stream_url="http://x/stream", buffer_size=3, sample_hz=1.0)
    assert s.latest() is None


async def test_buffer_caps_at_size():
    backend = FakeBackend()
    s = FrameSampler(backend=backend, stream_url="http://x/stream", buffer_size=3, sample_hz=1.0)
    for i in range(5):
        await s._on_frame(f"frame{i}".encode())
    buf = list(s._buffer)
    assert len(buf) == 3
    assert buf[-1] == b"frame4"
    assert buf[0] == b"frame2"


async def test_frame_pushed_to_backend():
    backend = FakeBackend()
    s = FrameSampler(backend=backend, stream_url="http://x/stream", buffer_size=3, sample_hz=1.0)
    await s._on_frame(b"hello")
    assert len(backend.frames) == 1
    assert backend.frames[0][0] == b"hello"
    assert isinstance(backend.frames[0][1], float)


async def test_sample_rate_throttles_backend_pushes():
    """Internal stream may be 10 FPS but backend only gets 1 FPS."""
    backend = FakeBackend()
    s = FrameSampler(backend=backend, stream_url="http://x/stream", buffer_size=3, sample_hz=2.0)
    for i in range(5):
        await s._on_frame(f"f{i}".encode())
    assert len(s._buffer) == 3
    # First frame always passes, subsequent within 1/sample_hz s do not
    assert len(backend.frames) == 1
