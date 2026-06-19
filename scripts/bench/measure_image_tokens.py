"""Measure the EXACT token cost of one camera frame for the configured model.

Method: difference measurement using the model's own reported usage.
  baseline  = prompt_tokens for (system + short text)
  with_img  = prompt_tokens for (system + short text + the real JPEG)
  image cost = with_img - baseline

The frame is the real /capture output (320x240 q40 JPEG) sent in the exact
image_url base64 format core/brain.py uses, so the number matches production.

The result is specific to PALIV_LLM_PROVIDER + PALIV_BRAIN_MODEL. Run it once
per model you care about (swap the env vars between runs).

Usage:
    python -m scripts.bench.measure_image_tokens                  # uses live Pi /capture
    python -m scripts.bench.measure_image_tokens --image foo.jpg  # use a local JPEG instead
    python -m scripts.bench.measure_image_tokens --repeat 3       # confirm determinism
"""

import argparse
import asyncio
import base64
import os

from dotenv import load_dotenv

load_dotenv()

from core.llm_client import LLMClient
from core.pi_client import PiClient

PI_HOST = os.getenv("PI_HOST", "http://chotu.local:7000")

# Minimal, fixed text used in BOTH calls — only the image differs between them.
_PROBE_TEXT = "Describe."


async def _get_frame_b64(image_path: str | None) -> tuple[str, int]:
    """Return (base64_jpeg, raw_jpeg_bytes). From a local file or the live Pi."""
    if image_path:
        with open(image_path, "rb") as f:
            raw = f.read()
        return base64.b64encode(raw).decode("ascii"), len(raw)

    pi = PiClient(PI_HOST)
    try:
        env = await pi.capture()
    finally:
        await pi.close()
    if not env.get("ok"):
        raise SystemExit(f"Pi /capture failed: {env.get('error')}. Is the Pi bridge up?")
    b64 = env.get("result", {}).get("image_base64", "")
    if not b64:
        raise SystemExit("Pi returned an empty frame.")
    return b64, len(base64.b64decode(b64))


# Only the INPUT side matters; output length never affects prompt_tokens.
# Some models (qwen-omni) reject very small caps, so keep this comfortably >= 10.
_MAX_TOK = 16


async def _prompt_tokens(llm: LLMClient, messages: list[dict]) -> int:
    """One completion; return the model's reported prompt_tokens.

    Falls back to a streaming call (with usage in the final chunk) for models
    like qwen-omni that reject non-streaming requests."""
    try:
        resp = await llm.chat_complete(messages, tools=[], thinking=False, max_tokens=_MAX_TOK)
        if resp.usage and "prompt_tokens" in resp.usage:
            return resp.usage["prompt_tokens"]
    except Exception as e:
        if "stream" not in str(e).lower():
            raise

    # Streaming fallback — read usage from the final chunk.
    stream = await llm._openai.chat.completions.create(
        model=llm.model,
        messages=messages,
        max_tokens=_MAX_TOK,
        stream=True,
        stream_options={"include_usage": True},
        modalities=["text"],
    )
    prompt_tokens = None
    async for chunk in stream:
        if chunk.usage is not None:
            prompt_tokens = chunk.usage.prompt_tokens
    if prompt_tokens is None:
        raise SystemExit(f"No usage returned (provider={llm.provider} model={llm.model}).")
    return prompt_tokens


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="Local JPEG path instead of the live Pi frame.")
    ap.add_argument("--repeat", type=int, default=1, help="Repeat to confirm determinism.")
    ap.add_argument("--dashscope", metavar="MODEL",
                    help="Measure against a DashScope model (uses DASHSCOPE_API_KEY).")
    args = ap.parse_args()

    if args.dashscope:
        os.environ["PALIV_LLM_PROVIDER"] = "local"  # DashScope is OpenAI-compatible
        os.environ["PALIV_BRAIN_URL"] = "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
        os.environ["PALIV_BRAIN_KEY"] = os.environ["DASHSCOPE_API_KEY"]
        os.environ["PALIV_BRAIN_MODEL"] = args.dashscope

    b64, raw_bytes = await _get_frame_b64(args.image)
    src = args.image or f"{PI_HOST}/capture"

    llm = LLMClient()
    print(f"provider={llm.provider}  model={llm.model}")
    print(f"frame source={src}  jpeg={raw_bytes/1024:.1f}KB  b64_chars={len(b64)}")
    print("-" * 60)

    baseline_msgs = [{"role": "user", "content": [{"type": "text", "text": _PROBE_TEXT}]}]
    image_msgs = [{
        "role": "user",
        "content": [
            {"type": "text", "text": _PROBE_TEXT},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
        ],
    }]

    try:
        for i in range(args.repeat):
            base = await _prompt_tokens(llm, baseline_msgs)
            withimg = await _prompt_tokens(llm, image_msgs)
            tag = f"run {i+1}: " if args.repeat > 1 else ""
            print(f"{tag}baseline(text only) = {base} tok   "
                  f"with image = {withimg} tok   "
                  f"==> IMAGE = {withimg - base} tokens")
    finally:
        await llm.close()


if __name__ == "__main__":
    asyncio.run(main())
