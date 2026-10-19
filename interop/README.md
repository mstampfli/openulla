# Interop vectors

`vectors.txt` is every message this project documents, as bytes, so an implementation in
another language can check itself without reading Python.

One vector per line, tab separated:

| column | what it is |
|---|---|
| layer | `aap`, `sibling`, `lmp`, `hci-command` or `hci-event` |
| name | unique within its layer; the two together address one vector |
| section | the section of [../docs/protocol.md](../docs/protocol.md) that defines the message |
| hex | the bytes, space separated |
| decode | what this project's own decoder makes of those bytes, joined with `; ` |

Two ways to use it. **Encode** the same message in your implementation and compare the bytes:
they must match exactly, because every byte here comes from the library, which cites the
firmware address or the measurement behind it. **Decode** the bytes and compare your field
names against the last column, which is the reference wording.

The file is generated, so it cannot drift from the code:

```sh
python3 ../tools/ulla_vectors.py --check    # fails if the file and the library disagree
python3 ../tools/ulla_vectors.py --write    # regenerate after an intended change
```

`tests/test_vectors.py` runs that check, requires each layer's decoder to recognise its own
vectors, and requires no two vectors of one layer to decode to the same words.

What is **not** here: anything the project has not recovered. There are no audio frames,
because the format is not established (docs/protocol.md section 7), and no capture: a capture
would carry link keys and other people's addresses.
