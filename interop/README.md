# Interop files

Four generated files, so an implementation in another language can check itself without
reading Python. Every one of them comes from the library, which cites the firmware address or
the measurement behind each value.

| file | what it holds |
|---|---|
| `vectors.txt` | every message this project documents, as bytes |
| `session.txt` | the earbud's session manager, one row per (state, event) |
| `procedures.txt` | the three procedures a controller runs, one row per accepted event |
| `codes.txt` | every named number, with the firmware's own spelling where there is one |

All four are tab separated, ASCII, with a commented header naming the columns. Lines starting
with `#` are comments.

**vectors.txt** carries the layer, a name, the section of
[../docs/protocol.md](../docs/protocol.md) that defines the message, the bytes, and what this
project's own decoder makes of them. The layer and the name together address one vector. Two
ways to use it: encode the same message and compare bytes, which must match exactly, or decode
the bytes and compare your field names against the last column.

**session.txt** is the table the earbud walks: for each state and event, the state it moves to
when the handler succeeds, the state it moves to otherwise, and the firmware address of the
handler. `NO_CHANGE` means it stays where it is.

**procedures.txt** is the controller side, per role: the state, the event, the next state, what
the firmware does, the refusal it puts on the air if any, the mode-change event the host is
told if any, and the handler's address. An event that does not appear for a state is one that
state ignores.

**codes.txt** is every mode, result, reason, status and error this protocol uses, with the
firmware's own spelling of it where the image carries one.

The files are generated, so they cannot drift from the code:

```sh
python3 ../tools/ulla_interop.py --check    # fails if a file and the library disagree
python3 ../tools/ulla_interop.py --write    # regenerate after an intended change
```

`tests/test_interop.py` runs that check, requires each layer's decoder to recognise its own
vectors, and requires no two vectors of one layer to decode to the same words.

What is **not** here: anything the project has not recovered. There are no audio frames,
because the format is not established (docs/protocol.md section 7), and no capture: a capture
would carry link keys and other people's addresses.
