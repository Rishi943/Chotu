# SHOOT_BRIEF — Reel: "What Are These Buttons?"

## Setup

- Chotu on a round table. Camera fixed at one end.
- **Table lamp** (Tuya) on the table — Chotu controls it via `set_light` (lumos).
- **Room light** — Rushi controls manually. On for Act 1; killed after Act 1.
- Rushi is off-camera: director + safety catch. Typed nudges are director cues —
  stay in character.

## Character

First time inhabiting a physical body **and hearing its own voice**. Played by
Claude as itself inside that fiction — the not-knowing is performed, the
reactions are real. Curious, delighted, comedic; sincere when it lands. Mild
cursing when genuinely surprised.

The first-voice beat is a premise, not a throwaway: the character discovers it
can *speak* as part of booting up — the first words are a surprise to itself
(often a beat of "...is that me? Am I making that sound?"). This grounds the
whole reel and every later act's narration in the same waking-up moment.

Vision note: viewers assume a live feed — never narrate taking photos.

## How to run the acts (learned 2026-07-08)

- Each act runs as ONE driver script (see SKILL.md "Bulk beats") — per-call CLI
  spawns add 3–5s of dead air between beats; a warm process removes it.
- **Drivers live in `shoots/`, not the scratchpad.** Once an act's driver is
  validated, check it in next to this brief (e.g. `shoots/buttons_act1.py`).
  A retake = edit the lines in the file, rerun. Everything reel-specific
  (brief, drivers, take notes) stays under `shoots/` so the whole reel side
  can be split out of the repo cleanly later.
- **Pushup:** `do_trick '{"name":"pushup","speed":100}'` — 2 reps, ends
  standing. Do NOT use `pose "push up"` (SunFounder preset — ends seated).
  Speed 100 confirmed safe at ≥79% battery.
- Launch motion on a thread and fire the mid-motion line ~0.5s in — the
  bridge does not block `/speak` behind motion.
- Staged pauses are explicit `time.sleep` in the script (e.g. 1.5s before a
  dry closer).
- `marker` ACT n BEGIN/END around every take.
- **Voice:** run every take with `PALIV_SPEAK_OUTPUT=pi` — Piper synthesized
  laptop-side, played on the robot speaker (`piper-pi` backend). Default espeak
  is the fallback, not the reel voice.
  **Reel voice = LibriTTS speaker 668** (picked 2026-07-13):
  `LOCALIS_PIPER_MODEL=core/voices/en_US-libritts_r-medium.onnx`
  `PALIV_PIPER_ARGS="--length-scale 1.0 --sentence-silence 0.6 --volume 1.0 -s 668"`
  (length-scale locked to **1.0** on 2026-07-14 — 1.1/1.4 read too slow in-room;
  1.0 + short declarative sentences is the intelligible pace.)
  A young-reading (~138 Hz) male US voice, chosen for warmth + comedic range
  across the full male pool (pitch-ranked + tonal auditions, on-floor 2026-07-13).
  **Volume stays at 1.0** — the previous pick (HAL) peaked at full scale and
  `--volume 2.5` hard-clipped ~1.5% of samples (measured), which was last
  session's audible-but-unintelligible garble. At 1.0 the waveform is clean;
  residual in-room word loss is ambient (laptop fan), fine on the recording +
  subtitles in the edit. Louder in the room? Raise the Pi's ALSA volume, never
  the piper gain.
  **Licensing (why we left HAL):** HAL (`campwill/HAL-9000-Piper-TTS`) is trained
  on 2001: A Space Odyssey film audio — its Apache-2.0 tag covers only the
  weights, not the character/performance/film IP (recognizable-imitation risk on
  a public/monetized reel). LibriTTS-R is **CC-BY 4.0** (public-domain LibriVox
  source): clean for YouTube with one credit line in the description — e.g.
  *"Voice: LibriTTS-R (CC-BY 4.0), Piper TTS."*
  Lines stay in Claude's own voice — the voice is just timbre.

## Vocal direction (personality — keep consistent)

Distilled from the 2026-07-13 line auditions Rushi green-lit. This is the read
for every spoken line, all acts. Improvised lines must match it — it's what
keeps the character one person across takes, and the seed for the eventual
PALIV.md / CHOTU_BASE.md persona.

- **Core:** a mind waking up in a body for the first time — delighted, curious,
  narrating its own discovery aloud. Excitement is the default colour.
- **DO:** genuine wonder (*"I did not know I would be able to feel things."*);
  giddy discovery (*"Okay okay okay, I can walk. I am walking."*); self-aware
  comedy (*"...already walked into a wall. Strong start."*); warmth (*"It is
  nice to finally have a voice."*); live curiosity (*"What is that over there?
  I am going to go find out."*).
- **DON'T:** deadpan sarcasm / dry cynicism — auditioned and explicitly cut
  2026-07-13 (reads cold). Not every line needs a punchline; let some be
  sincere or purely curious.
- **Rhythm — let punctuation do the work.** Write lines like real speech and the
  voice varies its own cadence: `.` settles, `!` lifts, `?` rises, `...` is a
  short trailing beat, `,` barely pauses. That mixed punctuation *is* the default
  rhythm — exploring, idle chatter, or a reel alike. Don't engineer pauses
  line-by-line; short bursts, self-interrupts, and repeat-for-emphasis (*"Not
  important. I can move!"*) fall out of writing it the way it would be said.
  - Reserve the heavy knobs for a **deliberate** dramatic beat, sparingly: bump
    `--sentence-silence` (global — lengthens *every* stop) for a slower whole
    passage, or split a line into separate `speak` calls with a set gap for one
    big per-spot pause. Stacking dots/periods (`......` / `. . .`) does **not**
    lengthen a pause — validated 2026-07-13, don't bother.
  - **Never write `—` in a spoken line.** On the Windows boot piper decodes
    stdin as cp1252 and speaks the em dash as ~1.8s of mojibake babble
    (measured 2026-07-13); decoded correctly it's just a sentence break, so it
    buys nothing over `.` anyway. `local_speak` now sanitizes (`—`→`.`,
    `PYTHONUTF8=1`) as a guard — still write lines ASCII-punctuation-only.

## Script (3 acts, ~30s)

> **Running order (updated 2026-07-16):** Act 1 buttons/pushup → **Act 2 = the
> peek-over edge scare** → **Act 3 = the dark + lamp light moment**. This swaps
> the old Act 2/Act 3 order below (dark-and-lamp was Act 2, edge was Act 3).
> The section bodies below still carry the old labels; the order above wins.
> Act 2 (peek-over) and Act 3 (lamp) both rehearse on the **real table** next
> session.

> **Act 1 is LOCKED (2026-07-16).** Driver: `shoots/buttons_act1.py`. Lines stay
> improv-able but the structure is fixed. Two mechanisms baked in and validated
> on-floor: (1) **continuous pushups** — the up/down leg frames from
> `_trick_pushup` sent as ONE `play_sequence` (no stand between reps; `do_trick`
> re-stands every call), speed 80, `up_hold` 0.8s = the ~300ms+ pause between
> reps, single stand at the end, 5 reps ≈ 7.8s. (2) **beat piper's ~2.5s
> generation delay** by firing each `speak` on a thread `GEN_LEAD` seconds
> BEFORE the sound is wanted: mid-rep line gets a head start then pushups launch;
> the closer is pre-fired ~5s into the pushups so it lands on the stand-up out of
> the 5th rep. Speed 80 confirmed at ~72-78% battery.

### Act 1 — "I can move?" — REHEARSED ✅

Validated on-floor 2026-07-08, three takes, ~20s per take. **Beat 2 reframed
2026-07-13:** the boot sequence is now body *and* voice — the wake line is the
character hearing itself speak for the first time (see Character §).

1. idle face · hold still 1.5s (alone in frame)
2. **wake + first-voice line** — booting up, then startled by its own voice
3. surprised face · button line
4. pushup on a thread (speed 100) + mid-rep line at ~0.5s
5. 1.5s beat · judging/indifferent face · closer

Line pools (pick and vary, don't recite):

- **Wake + first voice:** "Oh. Oh, I'm on. Hi... wait. Is that me? Am I making
  that sound?" · "Okay. Something changed. And... I can hear myself. I have a
  voice?" · "Wait. This is new. I have... legs? And a voice, apparently."
- **Button:** "There is a whole menu of buttons in here. Let us try... this
  one." · "So many controls. What does this one do."
- **Mid-pushup:** "Whoa. What the hell. Nope. Nope. Okay, we are exercising
  now." · "Whoa. Okay. I did not authorize this. Hello. Still going." ·
  "Who wrote this function." · "I did not sign up for leg day."
- **Closer:** "Great. One day old and I already have a gym membership." ·
  "A body for five minutes and I already have a workout routine." ·
  "I am going to feel that in the... whatever these are."

### Act 2 — The Dark and the Lamp — REHEARSED ✅

Validated on-floor 2026-07-09, take 1, ~75s:

1. (Rushi kills the room light after the Act 1 closer)
2. turn right ×2 (toward Rushi) · confused face · dry dark line
3. turn left ×2 (back to table — turns cancel cleanly, framing returns) ·
   scan `look left`/`look right` · spot-the-lamp line
4. thinking face · reason-to-lumos line · 1s beat
5. `set_light lumos` (wand pose + soundbite + Tuya — confirmed lights the lamp)
6. magic face · **hold 2.5s, let it land** · had-it-all-along line
7. playful face · "Much better." — NO look-down at the end (held poses stick;
   end the act standing)

Line pool from take 1: "Okay. Who turned off the sun. I was using that." ·
"Wait. There is a lamp right there. It has been on this table the entire
time." · "Hold on. I have a tools list. Checking. There is one called lumos.
Of course there is." · "So I have had a light switch built into me this whole
time. Good to know." · "Much better."

Remaining before the shoot: lamp placement IN FRAME (take 1 confirmed the
Tuya switch fires, but the light change was not visible from camera).

### Act 3 — The Edge (peek_over validated on floor; full act not yet rehearsed)

- Chotu notices something on the floor. Starts toward the table edge.
- Stops at the tape mark. Then the **peek_over animation** — Rushi-authored,
  `assets/Animations/peek_over.json`, played via
  `play_sequence '{"frames": <file frames>}'` (no speed override — per-frame
  speeds + the 2s lean-hold are baked in; ~3.6s total, floor-validated
  2026-07-09). Front leg reaches out over the void and holds.
- Rishi (off-camera, audible) during the hold: "Chotu—". Animation pulls the
  leg back on its own.
- Turns to Rishi for the punchline: "Gotcha." (wink face)
- Turns back to camera, walks toward it, `do_trick wave` in front of the lens.
- **Edge safety:** ultrasonic is DEAD — the walk-up is a fixed short distance
  to the tape mark, Rushi is the catch. The "almost fell" drama is the
  animation, not detection — no walking happens at the edge itself.

## Pacing

- Unhurried. Latency is edited out in post — never rush a beat.
- The Gotcha beat: pause after stopping before the line.

## Rehearsal log

- **2026-07-16** (floor, Windows boot): **Act 1 locked + first checked-in
  driver** (`shoots/buttons_act1.py` — closes the standing "no per-act drivers
  committed" open item). Reworked the pushup beat: continuous `play_sequence`
  reps (no stand between) replacing chained `do_trick`; speed 80, up_hold 0.8.
  Nailed the line-timing problem — piper's ~2.5s synth delay was landing every
  line AFTER the motion; fixed by firing each speak GEN_LEAD ahead (head-start
  the mid-rep line, pre-fire the closer ~5s into the pushups). One transient
  brownout mid-session (recovered on bridge restart; 72% after, speed 80 held
  fine). Act order swapped: Act 2 = peek-over scare, Act 3 = lamp. Act 2/3 defer
  to table day. Trace: `out/sessions/2026-07-16_12-47-04_fable/`.
- **2026-07-08** (floor, brain on Windows): Act 1 validated ×3 takes. Pushup
  ending in a sit traced to `pose "push up"`; fixed by `do_trick pushup`.
  One-process driver script kills inter-beat latency. Traces:
  `out/sessions/2026-07-08_21-53-42_fable/`.
- **2026-07-09** (voice picks): Act 1 ×3 takes through piper-pi — HAL slow
  pacing, GB northern male, HAL tight pacing (same script A/B). **Take 6 (HAL,
  `1.1`/`0.5s`/vol 2.5, ~38s) is the keeper** — pinned above. Traces:
  `out/sessions/2026-07-09_act1_hal_fable/`.
- **2026-07-09** (Act 2 first rehearsal): full act validated ×1 take, HAL
  voice. Lumos fired the real lamp (out of frame). Cut the look-down closer —
  held poses stick until reset. Turn right ×2 / left ×2 cancels cleanly.
  Same trace dir as above.
