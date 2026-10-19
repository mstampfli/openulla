# The implementation

`openulla/` is an independent implementation of the setup protocol described in
[protocol.md](protocol.md). It speaks every layer that was recovered, and it stops exactly
where the recovery stops. It needs nothing but Python 3.11 and the standard library.

The rule the code obeys is the rule the documentation obeys: **every constant, every length
and every refusal cites the firmware address or the measurement that proves it**, in the
module docstring and beside the value. Where something is not established, the code does not
fill it in; it says so, and the tests say so too.

## What is in it

| module | what it is |
|---|---|
| `aap.py` | the host interface: AAP service 4 on L2CAP PSM 0x1001, the open exchange, the commands of protocol.md 2.4, the status reply and the 0x17 wrapper |
| `session.py` | the earbud's session manager: 17 states, 40 events, all 41 rows of the firmware's transition table, and the machine that walks them |
| `sibling.py` | the earbud-to-earbud messages, including the reversed address order |
| `lmp.py` | the link-manager PDUs: 0x73, 0x74, accepted, not accepted, and the escape messages, including Apple's packet-type-table request, its power control pair, the halfword pair and the counted band block |
| `hci.py` | the vendor commands, the band command family and the mode-change event |
| `bands.py` | the radio's band plan, the frequency function, and the rules that pick a band |
| `codes.py` | the numbered values with the firmware's own names, including the firmware's own spelling of each |
| `errors.py` | two exceptions: malformed, and refused with the code the firmware would send |
| `controller.py` | each end of the link-manager exchange: the responder's four checks, and the procedure below driving when each PDU goes out |
| `procedure.py` | the controller's own procedures, one per role: which event each state expects, what it sends, and which result the host is told |
| `sink.py` | an earbud-shaped simulator |
| `source.py` | what a host sends, and the vendor commands its controller would need |
| `sim.py` | a whole negotiation with no hardware |
| `transport.py` | a real L2CAP channel, or a pair of queues |
| `serve.py` | the frames-in, frames-out half of answering a channel as the earbud |
| `octets.py` | reading and writing this protocol's integers, and the one place that says they are little-endian |
| `describe.py` | one place for the wording of every decode, over all five layers |
| `capture.py` | finds this protocol's frames in a btsnoop capture, learning the channel from the L2CAP signalling |
| `a2dp.py` | the three stream endpoints the earbuds register, in one copy, so the verifier and the link tool compare the same bytes |
| `features.py` | the specification's LMP feature bit assignments, so a set bit nobody defines can be spotted rather than argued about |
| `packets.py` | the controller's eighteen-entry ACL packet-type table, including the six entries only Apple's third table can select, and the chooser that picks among them |
| `avdtp.py` | enough AVDTP signalling to ask an endpoint what it will accept: the header, the capability list, Discover, GetCapabilities and SetConfiguration, and the reject that names which category failed |

That is every module. Three of them hold data rather than protocol, and each exists so that two
consumers read one copy: `a2dp.py` is checked against the firmware by `tools/verify_claims.py`
and against the air by `tools/bt_link_state.py`, `features.py` is read by that same tool and by
the tests behind [measurements.md](measurements.md) section 13, and `packets.py` is read by
`lmp.py` for the bound on the packet-table request and by the verifier, which reads all three of
the firmware's parallel arrays back out of an image.

## Running it

Three gates run on every push, on Python 3.11, 3.12 and 3.13: the suite, `ruff check` and
`mypy`. The mypy configuration turns on `disallow_any_expr`, not only `strict`, because
`strict` lets `Any` in from any untyped boundary and `struct.unpack` is one of those; that is
why `openulla/octets.py` exists and why nothing in the package reads bytes through `struct`.

The suite covers **98% of the library's statements**, measured rather than claimed
(`coverage run --source=openulla -m pytest`). Of the remainder, the largest part is the real
L2CAP socket code in `transport.py`, which needs a kernel and a peer rather than a test; the
three state machines, the vendor commands and every decode are at 100%, including every branch
that refuses something.

```sh
python3 -m pytest                  # the whole suite, no hardware, no dependencies
python3 tools/ulla_demo.py         # the whole setup, every byte, against the simulator
python3 tools/ulla_demo.py --reject busy    # and the same with the peer refusing
python3 tools/ulla_decode.py f6 17 e6       # decode one frame, PDU, command or event
python3 tools/ulla_interop.py --check       # interop/ still matches the library
python3 tools/capture_decode.py air.btsnoop # find this protocol's frames in a capture
python3 tools/ulla_sink.py                  # answer as an earbud on a real L2CAP channel
```

The last one is the sink half: it listens on PSM 0x1001 and answers with `openulla.sink`, so
a source implementation can be driven against it over real Bluetooth. It supplies the
internal events a real pair raises by itself, printing each one by name, and the last five of
those stand in for a controller and a DSP that are not there.

There is also a corpus for somebody who is not writing Python at all. `interop/` holds four
generated files: every message as bytes with this package's own decode of it, the session
manager's table one row per (state, event), the three controller procedures one row per
accepted event, and every named number with the firmware's own spelling of it.
`tools/ulla_interop.py --check` fails if any of them and the library have drifted apart, and
`tests/test_interop.py` runs that check, requires each layer's decoder to recognise its own
vectors, and requires no two vectors of one layer to decode to the same words. An
implementation in another language can encode each message and compare bytes, walk the same
transitions, and name the same codes.

The decoder is the other way to use the library: give it hex and it names the fields, says
whether an earbud of this build would dispatch or refuse an extended PDU, and turns an
allowed-band mask into the bands and frequencies it means.

```
$ python3 tools/ulla_decode.py f6 17 e6
3 bytes: f6 17 e6

LMP PDU: opcode 0x7b, transaction id 0, extended opcode 0x17
  an earbud of build 9A348 has this one dispatched
  allowed-band mask 0xe6
    band 1: 5150-5250 MHz, 5 channels
    band 2: 5735-5855 MHz, 6 channels
    band 5: 6045-6165 MHz, 6 channels
    band 6: 6165-6285 MHz, 6 channels
    band 7: 6285-6425 MHz, 7 channels
```

Against real earbuds, `tools/aap_probe.py` is the one that leaves the machine; everything
else is offline.

## What one run looks like

```
1. the host opens AAP service 4 on L2CAP PSM 0x1001
  open request                       00 00 04 00 01 00 02 00 00 00 00 00 00 00 00 00
     accessory version 1.3, status 0
2. the host asks for a session with itself
  start session, opcode 0x36         04 00 04 00 36 00 00
  the bud asks its sibling           05 01 66 55 44 33 22 11 00
     status reply 0x0000, manager now in WAIT_AUDIO_QUIESCE
3. the host subscribes to the report classes
  wrapper 0x17, class 0x00200000     04 00 04 00 17 00 00 00 20 00 04 00 00 00 00 00
4. the local audio path quiesces, and the controller is told to change mode
  HCI 0xFF77, band commands on       77 ff 01 01
  HCI 0xFDF3, the mode procedure on  f3 fd 01 01
  HCI 0xFF79, mask 0xe6              79 ff 01 e6
  HCI 0xFF7A, the same mask          7a ff 01 e6
     that mask names bands (1, 2, 5, 6, 7)
  HCI 0xFE50, start the transition   50 fe 07 0b 00 00 00 01 03 00
5. controller to controller, on the existing ACL link
  LMP source -> sink, the feature exchange f6 0a 60 00 00 00
  LMP sink -> source, the feature exchange f6 0b 60 00 00 00
     the peer answers 0x00000060; band messages need bit 5 on both sides: True
  LMP source -> sink, the allowed-band mask f6 17 e6
  LMP source -> sink, the setup request 0x73 e6 00 00 00 00 00 01 03 00
  LMP sink -> source, accepted, for 0x73 06 73
  LMP source -> sink, the configuration 0x74 e8 00 00 00 00 00 00
     between those two the procedure waits for an event no earbud posts,
     so this simulator supplies that one step: docs/protocol.md 5.9
  vendor event 0xFF to the host      ff 07 00 0a 0b 00 01 00 ff
     handle 0x000b ULLA_ON SUCCESS NOT_APPLICABLE
6. the DSP's side, and the session is open
     manager now in SESSION_OPEN
7. the host starts the stream
  start stream, opcode 0x38          04 00 04 00 38 00
     manager now in STREAM_OPEN
8. the controller side of the stream, which is section 7.1
  HCI 0xFE57, the DMA address        57 fe 04 00 40 04 10
  HCI 0xFE52, start the stream       52 fe 00
  HCI 0xFE53, read the descriptor    53 fe 00
  HCI 0xFE58, stop the stream        58 fe 04 00 00 00 00
     the start refuses without that address: the firmware asserts on it
9. the host stops the stream, and both controllers run the teardown
  stop stream, opcode 0x3A           04 00 04 00 3a 00
     manager now in SESSION_OPEN
  vendor event 0xFF to the host      ff 07 00 0a 0b 00 00 00 ff
     handle 0x000b BT_ACL_ACTIVE SUCCESS NOT_APPLICABLE
```

The first three steps are the ones a Linux host has really done against real earbuds; the
bytes above are the same bytes [measurements.md](measurements.md) records. From step 4 on the
simulator stands in for hardware, because the vendor commands and the link-manager PDUs have
to come from a Bluetooth controller that implements them.

## What the simulator does and does not do

`sink.Sink` answers the way the earbud answers wherever that is proven: the session belongs
to the channel it arrives on and there are three such channels, the open reply carries
accessory version 1.3, a start command is taken only while the manager is idle and ignored in
silence otherwise, an earbud whose sibling transport is down answers status 0x1776 and stays
idle, status 0 arrives once the manager reaches the state that waits for the local audio path,
and the report subscription tells both the sibling and the local controller.

It also sends no report afterwards, which is not an omission: no report is produced in this
build of the firmware either, and protocol.md 2.6 shows that from both ends. A source that
subscribes and waits will wait here exactly as long as it would against real earbuds.

It does not invent what was not recovered. A real earbud raises its own internal events as its
DSP, its sibling and its controller get there; here those events are fed in explicitly, so a
test states the order it wants rather than trusting a guess. The handlers' internals are not
modelled: the machine takes a row's first branch unless a caller says otherwise, which is what
the firmware's 0x1770 return value means. There is no audio path at all.

## What it cannot do, and why

The setup ends with the two controllers exchanging link-manager PDUs on their existing ACL
link, and then the radio changes mode. Both halves are below the host interface:

- A commodity Bluetooth controller does not expose a way to emit a vendor link-manager PDU,
  and its firmware is signed, so the exchange of protocol.md section 5 cannot be driven from
  a Linux host. `source.ControllerCommands` builds the vendor commands anyway, byte for byte,
  for hardware that does expose them and for comparing against a capture.
- The radio modes themselves are baseband, so they live in controller silicon. The standard
  HCI packet-type mask has no bit for them, which is measured, not assumed:
  [measurements.md](measurements.md) section 9.

## Getting a capture the reader can read

On Linux, `btmon -w air.btsnoop` writes the layout `tools/capture_decode.py` was tested
against, and anything else that writes btsnoop with the HCI UART datalink works too.

On macOS, Apple's PacketLogger writes its own `.pklg` container, which this reader does not
read: that layout has not been checked against a real file here, and this project does not
ship code for a layout it has not seen. Converting the file first (Wireshark opens `.pklg`)
or capturing on the Linux side of the same link both avoid the question.

## Checking the constants yourself

Every address in the docstrings is a byte offset into a payload of Apple's own published
firmware, build 9A348, which `tools/fetch_firmware.sh` fetches and `tools/ftab.py` splits.
No firmware and no disassembly is redistributed here, so the addresses are the way to check a
value, and `tools/verify_claims.py` does it for you.

**The whole path was walked from nothing on 2026-10-19**, which is the only way to know it
still works: Apple's catalog served build 9A348 as `A3048.zip`, 11,455,085 bytes, SHA-256
beginning `a04ff3f0d74e47e4`, the same asset [ulla.md](ulla.md) section 1 names. The
`firmware.uarp` inside it, SHA-256 beginning `78171265810972c3`, is byte for byte the file
every address in this repository was read from, and the five payloads extracted from that
download pass **34 of 34 claims**. If your own download gives those two prefixes, you have the
same bytes; if it does not, Apple has published a new build and the tool will tell you which
claims moved.

```sh
tools/fetch_firmware.sh
python3 tools/ftab.py firmware/*/AssetData/Firmware.acsw/firmware.uarp \
    extract acib payloads/acib.bin --payload FTAB
python3 tools/ftab.py firmware/*/AssetData/Firmware.acsw/firmware.uarp \
    extract rkof payloads/rkof.bin --payload FTAB
python3 tools/ftab.py firmware/*/AssetData/Firmware.acsw/firmware.uarp \
    extract sbd1 payloads/sbd1.bin --payload FTAB
python3 tools/ftab.py firmware/*/AssetData/Firmware.acsw/firmware.uarp \
    extract phyb payloads/phyb.bin --payload FTAB
python3 tools/ftab.py firmware/*/AssetData/Firmware.acsw/firmware.uarp \
    payload CTAB payloads/ctab.bin
python3 tools/verify_claims.py --payloads payloads

# Optional, and only for the question of whether anything was looked for in the wrong place.
# No claim here rests on these, and tools/payload_survey.py scans whatever is in the directory
# [ulla.md 4.7]. CTAB and NERF are SuperBinary payloads rather than FTAB entries, so they need
# the other subcommand. `ftab.py list` prints "size=%8d", so the size is field 4 and not field
# 3: filtering on $3 silently matches every line, which is how a zero-byte sbd2.bin got into an
# earlier run of this.
U=firmware/*/AssetData/Firmware.acsw/firmware.uarp
python3 tools/ftab.py $U list --payload FTAB | awk '$4 != 0 {print $1}' | while read tag; do
    python3 tools/ftab.py $U extract "$tag" "payloads/$tag.bin" --payload FTAB
done
python3 tools/ftab.py $U payload NERF payloads/NERF.bin
python3 tools/ftab.py $U payload CTAB payloads/ctab.bin
python3 tools/payload_survey.py --payloads payloads

# R1FW is a third container, and it holds a second rkos and sbd1 that no claim here has read:
python3 tools/ftab.py $U list --payload R1FW
```

```
  PASS  session transition table         48 rows over 15 states, events, both next states and handlers
  PASS  band plan                        8 records of 7 bytes at acib 0xf52e8
  PASS  HCI command table                12 handlers as published, keys ordered under the mask, 0xff75 absent behind 0x7f75
  PASS  PDU lengths                      accepted 2, not accepted 3, 0x74 7, 0x73 9, including the header byte
  PASS  LMP receive dispatch             table at acib 0x3b254, 5 opcodes checked
  PASS  escape space dispatch            0x7B: 39 entries, 12 refused; 0x7F: 34 entries, 8 refused
  PASS  extended PDU body builders       69 extended opcodes over both spaces, 19 with no builder, the entry's own fields as published
  PASS  mode-change event shape          event 0xff, 7 parameters, id 0x0a00
  PASS  mode, result and reason names    3 modes, 5 results, 12 reasons, 2 unnamed
  PASS  name table bounds                3 modes then 'ULLA_MGR_EVENT_UNKNOWN', 5 results then a null
  PASS  property getter table            10 ids in the table at acib 0xf7d60, 7 behind property 0x20d
  PASS  audio DMA descriptor             count at 3+3, length at 6+10, flags at 16+4, descriptor at 0x521c01f0, windows to 0x1002ffff and 0x521cffff
  PASS  ULLA session commands            11 handlers as published, 0xFE16 bounded to 125 to 10000
  PASS  feature bit commands             0xFF77 sets bit 5, 0xFDF3 adds 0x56 over 0xa9, both at 0x10003290
  PASS  procedure state tables           8 sink, 7 third and 9 source states, acib 0xf2628 to 0xf2664
  PASS  A2DP stream endpoints            vendor 0xFF, AAC 0x02 and SBC 0x00, lengths and bytes as published
  PASS  report listeners                 3 types: 0x0a08 56 bytes, 0xe910 185 bytes, 0xe911 61 bytes
  PASS  session command gates            5 commands through one gate, 5 ids on the link-up paths, 3 role refusals of 0x0C, 0xFE55 bounded to +/-800
  PASS  report producers                 0x121d2: id 0xe910, 185 payload bytes, blocks at +0x45 and +0x85 copied 0x80 at a time; 0x11e24 stamps 0xa08 over 9 + 4 words and one more
  PASS  report request path              class 0x8c of 0x5e reaches 0x5544c, id 0x8c00 built at 2 sites
  PASS  DSP ULLA node                    the node's version at +0x2bc, 3 property ids, 'processBufferUllav11', error 0xe00002e7
  PASS  procedure scheduler              89 ids from 0xfcd0, 40 arms, 0x1a -> 0x296a0
  PASS  ctab is the table again          'rkosftab' at 0x3d, 20 entries in table order, no block magic
  PASS  no standard road to a new rate   packet-type table bounded to 0 or 1 with error 0x1e, feature pages bounded below 3 and filled with eight zeros above, pages at 0x10003288 and 0x100033a4
  PASS  the packet-type table            18 entries over four arrays with the slot counts agreeing, all twelve specified payloads present, 6 more at mask bits 16 to 21, Apple's request bounded to 2, 0xFDEE writes 0x3f0000, and three more arrays: payloads and slot classes in rate-class order, both groups slower first
  PASS  each table's legal entries       three constants 0xffc0ccf9 0xffc033ef 0xffff00e9 reproduce all 54 membership answers, and only table 2's permits bits 16 to 21
  PASS  the permission partition         four immediates: Apple's two partition the six exactly, the standard branch's two are the EDR rate pairs, and the bit-4 gate keeps 1021 bytes there against 1288 here, so the shape inverts
  PASS  shared host commands             7 of their twelve present and 5 absent, 0xFD02 mode 1 -> 2586 bytes, otherwise 339, landing at 0x1000b494+0x718 whose default is 2586, over 9 records of 0xc4
  PASS  payload header width             ten-bit length at bit 3 under mask 0xe007, twelve-bit under 0x8007, the wide builder tests table 2 and mask 0x3f0000
  PASS  the five paired procedures       5 senders choose between the two forms on bits [2, 5], the three power answers are one two-bit code five times, 9 pair immediates hold, 10 sequence gates, the TLV reply opens with type 0 carrying 0x5b
  PASS  prose-only citations             48 prose citations hold their instruction
  PASS  citations are instructions       775 acib citations, none of them a literal-pool word, with 1 allowed as data
  PASS  the radio PHY image              a 48-word table ending at the info block with 41 live entries, 11 reserved slots on one handler and SVCall/PendSV on another, vector 15 a tick counter, msr of MSP, PSP and BASEPRI, '[BTP]: (version:' its own name, and of 25 populated IRQs 13 bare bx lr, 9 trampolines, 3 bodies
  PASS  the codec question               2 codec names in the DSP each reached from its own site, 6 codec commands beside A2DP and HFP, and the link's own configuration path as 5 names of one module with its DSP proxy
34 of 34 claims hold
```

The transition-table line is the one worth looking at twice: it expands the library's 41 rows
into the 48 (state, event) pairs the firmware's own table holds and requires every event, both
next states and every handler address to match. Changing one byte of either image makes the
tool fail, which was checked by doing it.
