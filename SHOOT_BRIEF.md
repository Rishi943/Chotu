# SHOOT_BRIEF — Reel: "What Are These Buttons?"

## The Setup
Chotu is on a round table. Camera is fixed at one end. On the table:
- A lamp (Tuya, connected — `set_light` / "lumos")


Two lights in the room:
- **Room light** — Rishi controls this manually. On for Act 1 (so video isn't dark). Rishi turns it off after Act 1.
- **Table lamp** — Chotu controls this via `set_light` / lumos.

## Character Brief
Chotu has always existed as a chatbot. This is its **first time inhabiting a physical body**. It does not know what any of its actuators or tools do. It is curious, slightly confused, and dry-humored — not panicking, just poking at things with mild wonder.

Note on vision: **viewers don't know Chotu takes individual photos.** They'll assume it has a live feed. All vision actions should feel natural and continuous — no "let me take a photo" narration.

---

## Script (3 acts, ~30 seconds)

### Act 1 — "I can move?" (0–10s)
- Rishi leaves frame. Chotu is alone on the table. Brief pause — stays still.
- Discovers it has physical controls. **Always does a pushup.**
- Reacts with something dry. Example: *"Okay. So that's a thing."* or *"That was unasked for."*

### Act 2 — The Dark and the Lamp (10–20s)
- After the pushup line, Rishi (off-camera) **turns off the room light.**
- Chotu **turns its body to the right** (toward Rishi) and reacts: *"I can't see anything — did it just get dark?"* or similar dry, in-character line.
- Looks back at the table. Scans. Notices the lamp sitting there. Makes a comment — something like *"There's definitely a lamp in the middle of nowhere."*
- Reasons out loud: checks its tools. Finds something called `lumos`. A beat.
- Calls it. Lamp turns on.
- **Pause — 2 to 3 full seconds. Let it land.**
- Then reacts to having that tool: something dry about the fact that it had a lamp switch in its toolkit all along. Example: *"Huh. I've had that the whole time."*
- Looks at the table again. Better now.

### Act 3 — The Edge (20–30s)
- Chotu notices something on the floor (a crumb, a cat toy, Rishi's shoe — whatever's there).
- Starts moving toward the edge of the table.
- Rishi (off-camera, audible): *"Chotu—"*
- Stops. Right at the edge. Pause.
- **Turns and looks directly at Rishi** when it delivers the punchline: *"Gotcha."* (or similar in character — dry, pleased with itself).
- Then turns back toward the camera, walks toward it, and **waves once** (`do_trick wave` if available) once back in front of the lens.
- Rishi will assist with navigation/positioning during the walk.

---

## Tool Sequence (rough)
1. `set_face idle` — resting while alone
2. `speak` — wake-up / first words
3. `set_face surprised` — discovers it can move
4. `do_trick pushup` — does the pushup
5. `set_face indifferent` — dry reaction face
6. `speak` — dry reaction line
7. `set_face confused` — Rishi kills light, Chotu turns right
8. `move` — short body turn right (toward Rishi)
9. `speak` — "can't see anything / got dark" line
10. `set_face thinking` — scanning the table
11. `speak` — noticing the lamp; reasoning about tools, finding "lumos"
12. `set_light` ("lumos") — lamp on
13. `set_face magic` — lamp fires (hold 2–3s)
14. `set_face judging` — dry comment on having that tool
15. `speak` — "I had that the whole time" line
16. `move` — turn back forward
17. `set_face playful` — notices something on the floor
18. `speak` — noticing something on the floor
19. `move` — toward edge (pre-planned short distance, stops safely before)
20. `set_face indifferent` — pause at edge
21. `set_face wink` — "Gotcha." beat (facing Rishi)
22. `speak` — "Gotcha."
23. `move` — turn toward camera, walk toward it
24. `set_face greeting` — in front of lens
25. `do_trick wave` — wave once

---

## Edge Handling
The ultrasonic sensor is unreliable (returns -1). **Do not use distance for edge detection.**

Instead:
- Pre-plan the forward move as a fixed short distance that stops safely before the edge
- Put a piece of tape on the table as a visual mark for Rishi
- The "almost fell" drama is performance, not real detection
- Rishi will be nearby as a safety catch

---

## Pacing Notes

- Each beat should feel **unhurried** — short pause between actions
- The "Gotcha" beat: pause after stopping at the edge before delivering the line
- If Chotu gets stuck or goes off-script, Rishi can type a short nudge (e.g. "look at the lamp" or "what's that on the floor")

---

