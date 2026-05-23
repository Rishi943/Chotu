# Explore — map a room as a chain of nodes

You are mapping the room you're standing in. You'll create a chain of
nodes, where each node is a spot where you spun a full 360° and described
what you saw. Consecutive nodes are connected by a known reversible walk,
so when you're done you can always return to where you started.

When you call `conclude()` only the map survives. The individual photos,
your scan-time notes, every single tool call — all of that is wiped from
your memory. Only the final structured map remains. So tag thoroughly:
your future self relies on what you record now.

## Headings

At every node, you start facing `x0` (your arrival heading). One right
turn = `+1` step in x. Photos are taken at `x0, x1, ... x11` — twelve in
total, one per ~30°. Twelve right turns brings you back to `x0`.

You don't compute x. The tools track it. Every result you get tells you
your `current_x`. You just turn and record.

## At each node, do this 12 times:

1. `capture_vision` — look at where you're facing now. You get back the
   image plus your `current_x`.
2. Describe the image in your monologue. Identify:
   - **anchors**: fixed landmarks that won't move (vents, doors, frames,
     windows, big furniture).
   - **objects**: things on/in/around the anchors (cups, books, chargers,
     clothes, toys).
   - is this an **open path**? — clear floor in front of you, somewhere
     worth going next.
3. `record_photo(anchors=[...], objects=[...], description="...",
                  open_path=true|false, forward_steps=N)`
   - `forward_steps` is REQUIRED whenever `open_path=true`. It's how many
     forward steps you commit to walking to drop the next node there.
4. `move(direction="turn right", steps=1)` — turns 30°. `current_x`
   increments to your new heading.
5. Repeat from step 1. After twelve right turns you're back at `x0`.

You may set `open_path=true` on **AT MOST ONE photo per node**. That's
the direction you commit to going next. Choose well — once you commit,
you'll walk there.

If nothing in this node's 12 photos looks worth exploring further (or
you've covered enough of the room), tag **no photo** as open_path. That
marks this node as terminal.

## After 12 photos at a node:

- `commit_node_and_advance()` finalizes the node.
  - If you tagged an open_path: this turns you to that heading, walks
    `forward_steps`, and resets you at the new node with `current_x=0`.
    Loop back to "At each node."
  - If no open_path: nothing moves. You're done adding nodes. Continue to
    "When you're done."

If `commit_node_and_advance` returns `ok: false`, the move was aborted
(usually an obstacle) and you're back at the same node. Pick a different
direction by re-running the scan or selectively re-recording photos with
a new open_path — keeping in mind you can only set ONE open_path per
node, so if you already set one and it failed, choose differently next
time around. After 3 failures total in a single explore run, the tool
will return `aborted: true, reason: "..."` — at that point call
`return_to_origin()` then `conclude(status="inconclusive")`.

## When you're done adding nodes:

- `return_to_origin()` — walks your chain backward to Node 0 atomically.
  You don't do any turn/walk math. The tool reports `{success, last_node_reached}`.
- `conclude(status="done" if success else "inconclusive",
            notes="<one-line summary of the room>")`.

## What you may NOT call inside explore:

- `pose`, `do_trick`, `get_perception`, `investigate`, `cast_spell`,
  `set_legs` — these are blocked for the duration of the scope.
- `move("forward", ...)` and `move("backward", ...)` — only single
  turn-left or turn-right steps are allowed. Forward motion goes through
  `commit_node_and_advance`.

Still available (use sparingly): `get_distance`, `get_battery`,
`set_face`, `speak` (only if you need to ask for help — e.g. the room is
too dark to see), `wait`.

## Example monologue + tools at a single x

> "I see a desk straight ahead. There's a laptop on it and a mug. The
>  floor between me and the desk looks clear — I'd say about 8 steps. I'll
>  mark this direction as my open path."

```
record_photo(
  anchors=["desk"],
  objects=["laptop", "mug"],
  description="desk ahead, laptop centered, floor clear ~8 steps",
  open_path=true,
  forward_steps=8
)
move(direction="turn right", steps=1)
```

## Tips

- Anchors stay still; objects might move tomorrow. Tag them as such.
- If `capture_vision` fails at a turn, record the photo with
  `description="vision failed"`, empty anchors/objects, `open_path=false`,
  and continue. Don't abort the scan.
- One open_path per node. The first `record_photo` with `open_path=true`
  wins for that node — subsequent calls with `open_path=true` will be
  rejected. Think before you commit.
- Don't speak unless something blocks you (e.g. dark room — ask for the
  light). Your job here is to look and remember, not to chat.
