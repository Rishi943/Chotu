# habits/

PLAY-state skill prompts. Each habit is a directory containing a `HABIT.md` that defines its purpose, strategy, and exit conditions.

```
habits/
└── <name>/
    └── HABIT.md
```

Habits are loaded by `core/picker.py` when the picker selects PLAY. The picker passes the habit's prompt content to the LLM as an additional system message for the duration of the chunk loop.

`core/picker.py` and the first habit (`explore`) land in the session after this one. This directory is scaffolded ahead of time so the slot is fixed.
