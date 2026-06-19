# Dev setup & run commands

- Laptop venv: `.venv/` at project root.
- Pi venv: `~/chotu-bridge/.venv` (created with `--system-site-packages`).
- Pi access: SSH only. Hostname `chotu.local` via mDNS, fallback to IP in `.env`.
- Start llama-server: `llama-server -m <model.gguf> --mmproj <mmproj.gguf> --port 8080 -ngl 99 -c 32768 --parallel 1`
- Start Pi bridge: `ssh chotu@chotu.local 'sudo ~/chotu-bridge/.venv/bin/python3 ~/chotu-bridge/server.py'`
- Start brain: `source .venv/bin/activate && python3 -m core.brain`
- Voice input: `PALIV_VOICE=1 python3 -m core.brain`
- Push-to-talk GUI: `PALIV_PTT=1 python3 -m core.brain` (independent of `PALIV_VOICE`; shows `🎤|∞` pill in browser)
- Debug logging: `PALIV_DEBUG=1`
- Mute audio: `PALIV_MUTE=1`
- Offline dry-run: `python -m scripts.dry_run "walk forward"` (real LLM, faked Pi)
- Animation studio: `python -m scripts.animation_studio` (pose/animation editor on :8899, proxies to the Pi; exports frames JSON for `add-chotu-tool`)
