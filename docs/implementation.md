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
| `lmp.py` | the link-manager PDUs: 0x73, 0x74, accepted, not accepted, and both escape messages |
| `hci.py` | the vendor commands, the band command family and the mode-change event |
| `bands.py` | the radio's band plan, the frequency function, and the rules that pick a band |
| `codes.py` | the numbered values with the firmware's own names |
| `controller.py` | each end of the link-manager exchange, with the responder's four checks |
| `sink.py` | an earbud-shaped simulator |
| `source.py` | what a host sends, and the vendor commands its controller would need |
| `sim.py` | a whole negotiation with no hardware |
| `transport.py` | a real L2CAP channel, or a pair of queues |
| `serve.py` | the frames-in, frames-out half of answering a channel as the earbud |
| `describe.py` | one place for the wording of every decode |
| `capture.py` | finds this protocol's frames in a btsnoop capture, learning the channel from the L2CAP signalling |

## Running it

```sh
python3 -m pytest                  # the whole suite, no hardware, no dependencies
python3 tools/ulla_demo.py         # the whole setup, every byte, against the simulator
python3 tools/ulla_demo.py --reject busy    # and the same with the peer refusing
python3 tools/ulla_decode.py f6 17 e6       # decode one frame, PDU, command or event
python3 tools/capture_decode.py air.btsnoop # find this protocol's frames in a capture
python3 tools/ulla_sink.py                  # answer as an earbud on a real L2CAP channel
```

The last one is the sink half: it listens on PSM 0x1001 and answers with `openulla.sink`, so
a source implementation can be driven against it over real Bluetooth. It supplies the
internal events a real pair raises by itself, printing each one by name, and the last five of
those stand in for a controller and a DSP that are not there.

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
  HCI 0xFF79, mask 0xe6              79 ff 01 e6
  HCI 0xFF7A, the same mask          7a ff 01 e6
     that mask names bands (1, 2, 5, 6, 7)
  HCI 0xFE50, start the transition   50 fe 07 0b 00 00 00 01 03 00
5. controller to controller, on the existing ACL link
  LMP source -> sink, the feature exchange f6 0a 60 00 00 00
  LMP sink -> source, the feature exchange f6 0b 60 00 00 00
     the peer answers 0x00000060; band messages need bit 5 on both sides: True
  LMP source -> sink, the allowed-band mask f6 17 e6
  LMP source -> sink, the setup      e6 00 00 00 00 00 01 03 00
  LMP sink -> source, the setup      06 73
  vendor event 0xFF to the host      ff 07 00 0a 0b 00 01 00 ff
     handle 0x000b ULLA_ON SUCCESS NOT_APPLICABLE
6. the DSP's side, and the session is open
     manager now in SESSION_OPEN
7. the host starts the stream
  start stream, opcode 0x38          04 00 04 00 38 00
     manager now in STREAM_OPEN
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
value, and `tools/verify_claims.py` does it for you:

```sh
tools/fetch_firmware.sh
python3 tools/ftab.py firmware/*/AssetData/Firmware.acsw/firmware.uarp \
    extract acib payloads/acib.bin --payload FTAB
python3 tools/ftab.py firmware/*/AssetData/Firmware.acsw/firmware.uarp \
    extract rkof payloads/rkof.bin --payload FTAB
python3 tools/verify_claims.py --payloads payloads
```

```
  PASS  session transition table         48 rows over 15 states, events, both next states and handlers
  PASS  band plan                        8 records of 7 bytes at acib 0xf52e8
  PASS  HCI command table                11 handlers as published, keys ordered under the mask, 0xff75 absent behind 0x7f75
  PASS  PDU lengths                      accepted 2, not accepted 3, 0x74 7, 0x73 9, including the header byte
  PASS  LMP receive dispatch             table at acib 0x3b254, 5 opcodes checked
  PASS  escape space dispatch            0x7B: 39 entries, 12 refused; 0x7F: 34 entries, 8 refused
  PASS  extended PDU body builders       69 extended opcodes over both spaces, 19 with no builder
  PASS  mode-change event shape          event 0xff, 7 parameters, id 0x0a00
  PASS  mode, result and reason names    3 modes, 5 results, 12 reasons, 2 unnamed
  PASS  A2DP stream endpoints            vendor 0xFF, AAC 0x02 and SBC 0x00, lengths and bytes as published
10 of 10 claims hold
```

The transition-table line is the one worth looking at twice: it expands the library's 41 rows
into the 48 (state, event) pairs the firmware's own table holds and requires every event, both
next states and every handler address to match. Changing one byte of either image makes the
tool fail, which was checked by doing it.
