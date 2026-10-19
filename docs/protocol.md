# The ULLA setup protocol, as recovered

Purpose: interoperability. This is what an independent implementation needs in order to speak
to AirPods Pro 2 (USB-C) the way Apple's own source does. One such implementation is in this
repository: `openulla/`, described in [implementation.md](implementation.md), encodes and
decodes everything below and is checked against it by `python3 -m pytest`.

Every statement below is one of:

- **[fw 0xADDR]** read from the AirPods firmware, build 9A348. Addresses are byte offsets into
  the payload named with them, as extracted by `tools/ftab.py`: `acib` is the Bluetooth
  controller image, `rkof` the RTKitOS features image, both Cortex-M at base 0. The firmware
  itself is not redistributed and no disassembly of it appears here.
- **[measured]** observed on a Linux host (Intel AX211, BlueZ 5.82) against real earbuds.
- **[open]** not established. Nothing here is inferred or filled in by guesswork.

Numbers are hexadecimal when written `0x..`. Multi-byte integers are little-endian unless
stated otherwise.

Most of what follows can be re-checked mechanically. `tools/verify_claims.py` reads these
offsets in a firmware image on your own machine and prints one line per claim: the session
manager's whole transition table, the band plan, the command table with every handler
address, the PDU lengths, both dispatch tables, the event's shape, the reason names and the
stream endpoints. It fails on a single changed byte, and it needs no firmware from us.

## 1. Layer map

```
  host (phone, headset, computer)
    |  Apple accessory protocol (AAP) service 4, L2CAP PSM 0x1001   <- section 2
  earbud session manager                                            <- section 3
    |  AAP service 0x10, to the other earbud                        <- section 4
  other earbud
  ---
  earbud session manager -> its own Bluetooth controller, HCI vendor commands  <- section 6
  controller <-> controller, link-manager PDUs on the existing ACL link        <- section 5
  then: the radio changes mode and carries audio                     <- section 7
```

The setup rides entirely on an ordinary, already-connected Bluetooth link. Only the last step
moves to the other radio.

## 2. The host interface: AAP service 4

### 2.1 Framing

AAP runs on L2CAP PSM 0x1001 [fw rkof 0x40e78]. Every frame:

| offset | size | meaning |
|---|---|---|
| 0 | 2 | frame type |
| 2 | 2 | service id |
| 4 | .. | body |

Frame types [fw rkof 0x405f6]: 0 open request, 1 open reply, 2 and 3 control, 4 data.
The service used for ULLA is **4** [fw rkof 0x8af9e]. Type 4 frames are handed to the
service's data handler with the 4-byte header removed [fw rkof 0x40664..0x4068c].

### 2.2 Opening the service

The open request is 16 bytes; shorter frames are rejected [fw rkof 0x40600]:

| offset | size | meaning |
|---|---|---|
| 0 | 2 | frame type 0 |
| 2 | 2 | service id 4 |
| 4 | 2 | protocol version, major |
| 6 | 2 | protocol version, minor |
| 8 | 8 | zero in the request we send |

[fw rkof 0x4073a..0x4078a]. The accessory replies with a 14-byte body [fw rkof 0x40884,
0x3f778]:

| offset | size | meaning |
|---|---|---|
| 0 | 1 | status, 0 on success |
| 1 | 1 | pad |
| 2 | 2 | accessory version, major |
| 4 | 2 | accessory version, minor |
| 6 | 8 | reserved |

Rejection codes seen in the firmware: 0x72 frame too short, 0x67 service already in that
state, 0x68 no free session slot, 0x85 a session for this channel exists already
[fw rkof 0x40600, 0x406b4, 0x407ae, 0x406ea].

**No authentication is involved.** There is no challenge, no key and no shared secret anywhere
in this exchange. [measured] A plain Linux host sending
`00 00 04 00 01 00 02 00 00 00 00 00 00 00 00 00` receives
`01 00 04 00 00 00 01 00 03 00 00 00 00 00 00 00 00 00`: status 0 and accessory version 1.3.

### 2.3 Which device the session is for

The accessory keeps up to three service-4 sessions. Each records the **peer's Bluetooth
address, taken from the L2CAP connection itself**, not from any message
[fw rkof 0x6ca6c, 0x4075a]. A later start command therefore always refers to the sender.
That matters for an implementation: there is no address field to fill in, and no way to ask
for a session on someone else's behalf through this command.

### 2.4 Commands

The body of a type-4 frame starts with a 2-byte opcode [fw rkof 0x6cef4], and the handler
dispatches on it [fw rkof 0x6cf48, table at 0x6cf4c]. The ULLA ones:

| opcode | body after the opcode | effect |
|---|---|---|
| 0x36 | 1 byte, mode | start a session with the sender. Accepted only while the manager is idle; otherwise ignored silently [fw rkof 0x6d602, 0x27a58] |
| 0x38 | none | start the audio stream [fw rkof 0x6d55a] |
| 0x3a | none | stop the audio stream [fw rkof 0x6dae4] |
| 0x3c | 4 bytes | stop the session [fw rkof 0x6d396] |
| 0x3f | 4 bytes | abort response [fw rkof 0x6daaa] |
| 0x43 | 1 + 4 + 4 bytes | configure statistics [fw rkof 0x6d2e0] |
| 0x17 | see 2.6 | wrapper used in both directions |

**What the mode byte of 0x36 actually does**, since that one byte is the only choice a host
makes. The manager stores it and never validates it [fw rkof 0x27a58, which has no range
check, and measured: 0, 1 and 2 all answer status 0]. After that it is read in eight places,
and they fall into three groups:

- three read **only its low bit** [fw rkof 0x2609e, 0x264f4, 0x26900, each masking with 1], so
  mode 2 behaves there exactly like mode 0;
- two compare **the whole value against 1 and 0** to choose the vendor command's parameters
  [fw rkof 0x271c4, 0x271da], which is section 6.0's table;
- two pass it **on unchanged**, into the sibling's start-session request and into the vendor
  command's last parameter [fw rkof 0x27466, 0x27238, 0x272c8], and one saves the previous
  value beside it when the session is reset [fw rkof 0x27602].

So the session manager has no separate lite-mode path of its own: it carries the byte to the
controller and to its sibling, and the distinction lives below it, in the controller's
procedure and in the DSP, which has its own lite receive client (section 7).

For scale, and because it matters when a host is deciding what to answer: that table covers
opcodes 1 to 0x64, of which **61 have a handler of their own and 39 share one path that
ignores them** [fw rkof table 0x6cf4c, the shared target 0x6d218]. Sending and accepting are
not the same set. Of the ten opcodes these earbuds were measured sending unsolicited
([measurements.md](measurements.md) section 2), 0x09, 0x4E, 0x53 and 0x63 do have inbound
handlers, 0x17 is the wrapper and is taken before the table [fw rkof 0x6cefa], and 0x02, 0x1D,
0x2B, 0x55 and the status reply 0x37 have none, so an implementation should not expect to be
able to send back everything it receives.

### 2.5 The status reply

A start command is answered with opcode 0x37 and a 6-byte body [fw rkof 0x6e9cc]:

| offset | size | meaning |
|---|---|---|
| 0 | 2 | opcode 0x37 |
| 2 | 4 | status |

Only two status values exist in this build: 0, sent once the manager has reached the stage
where it waits for its local audio path to quiesce, and 0x1776, sent when the earbud's
transport to its sibling is not ready [fw rkof 0x271f8 and 0x2751c, the only two callers].

### 2.6 The wrapper, opcode 0x17

Used for grouped payloads in both directions [fw rkof 0x6cefa receiving, 0x6e198 sending]:

| offset | size | meaning |
|---|---|---|
| 0 | 2 | opcode 0x17 |
| 2 | 4 | class |
| 6 | 2 | payload length |
| 8 | .. | payload |

The class field is 4 bytes when the session's negotiated version pair is above 0x00010001 and
2 bytes otherwise [fw rkof 0x6cf1a, 0x6e0ae]. Sending this wrapper with class **0x00200000**
subscribes the host to the ULLA report streams, tells the sibling earbud, and tells the local
controller to begin reporting [fw rkof 0x6d116, 0x27f60].

Those reports are link statistics, and they only carry content once a ULLA link is actually
running. Two notification ids feed them, each with a size the worker validates: **0xE910 with
185 bytes and 0xE911 with 61** [fw rkof 0x25baa and 0x25bec compare against 0xB9 and 0x3D;
the collector is around 0x25ad0].

The report the host receives is [fw rkof 0x27e90, which zeroes a 185-byte buffer first]:

| offset | size | meaning |
|---|---|---|
| 0 | 1 | report type |
| 1 | 1 | the manager's field at its offset 0 |
| 2 | 1 | the manager's field at its offset 0x20 |
| 3 | .. | the data, and the wrapper's length is this plus three |

There are **three report types, 0, 1 and 2**, each with its own entry into that builder, and
each taking a descriptor whose halfword at +0 is the data length and whose word at +4 is the
data [fw rkof 0x27f42, 0x27f38 and 0x27e84, which pass 0, 1 and 2 respectively]. What each
type carries is not established.

The same report goes to the **sibling** instead when no host session is open, with a four-byte
header: a constant 4, then the type, then those same two bytes, sent as message 12 of section
4 [fw rkof 0x27ef4..0x27f26]. What the manager's two fields mean was not established, but both behave like small selectors
rather than counters: the first is written once in the whole manager, to **2 or 3** when a
session starts, depending on whether the sibling transport's own field at +0x18 is zero
[fw rkof 0x27532..0x2753c], and the second is read elsewhere through a test against 1 that
maps it to 1 or 2 [fw rkof 0x27d9a..0x27da2]. Neither is the manager's current state, which is
a third field, at its offset 0xC [fw rkof 0x25f32, the dispatcher's own index].

## 3. The session manager

### 3.1 How it is driven

A table-driven machine [fw rkof 0x25f1c]. For the current state it searches that state's rows
for the incoming event, calls the row's handler, and takes one of two next states depending on
whether the handler returned 0x1770. A next state of 0x10 means "stay". An event with no row
in the current state is ignored and the state is unchanged.

The state and event numbers below are the firmware's own: the dispatcher indexes its name
tables by the numeric value, so the numbering is read from code rather than assumed
[fw rkof 0x25f26, 0x25f34].

### 3.2 States

| # | state |
|---|---|
| 0 | init |
| 1 | idle |
| 2 | host setup |
| 3 | waiting for the sibling's session response |
| 4 | waiting for the local audio path to quiesce |
| 5 | controller setup |
| 6 | DSP setup |
| 7 | session open |
| 8 | stream host setup |
| 9 | stream open |
| 10 | waiting for audio stopped |
| 11 | stream host teardown |
| 12 | waiting for the sibling's stop response |
| 13 | controller teardown |
| 14 | host teardown |
| 15 | waiting for abort response |
| 16 | "no change" marker |

### 3.3 Events

0 unknown, 1 init, 2 reset, 3 session start request, 4 host session stop request, 5 peer
session stop request, 6 peer session start response, 7 peer session stop response, 8 DSP
session ready, 9 sibling audio configured, 10 sibling audio deconfigured, 11 peer DSP config
complete, 12 app setup complete, 13 DSP setup complete, 14 peer session stopped, 15 host
stream start request, 16 host stream stop request, 17 peer stream start request, 18 peer
stream stop request, 19 peer stream start response, 20 peer stream stop response, 21 stream
ready from controller, 22 stream stop response from controller, 23 stream start response from
DSP, 24 stream stop response from DSP, 25 stream ready from DSP, 26 stream host setup complete,
27 supervision timeout updated, 28 unsniff completed, 29 ULLA link up, 30 ULLA link down,
31 got data path configs, 32..34 snoop mode update/enabled/disabled, 35 local flow off done,
36 peer abort request, 37 peer abort response, 38 disconnect, 39 abort.

Three of those come from outside the manager, and the manager's own init handler registers the
callbacks that raise them, by id [fw rkof 0x27620 calling 0x1accae with six ids]:

- **29 and 30 are the same thing, told apart by one byte.** The vendor mode-change event of
  section 6 is registered as id 0x0A00, and its handler keeps the event's mode byte in the
  manager and then posts **29 when that byte is non-zero and 30 when it is zero**
  [fw rkof 0x2774c, whose tail at 0x27802..0x27810 stores the mode and chooses between 0x1D
  and 0x1E]. So "ULLA link up" and "ULLA link down" are one event from the controller, read by
  its mode: mode 1 or 2 is up, mode 0 is back to ordinary ACL. That handler is also where the
  mode, result and reason names of section 6 come from.
- **27 comes from a completion matching the session's own handle**: the callback for id 0x382
  compares its argument against the handle the manager stored when the session started and
  posts 27 only if they are the same [fw rkof 0x27710..0x27722, the handle at manager+0x44
  stored at 0x2754e].
- The completion of the command that starts everything is **ignored**: the callback registered
  for id 0x250, which is 0xFE50's own command id, is a bare return [fw rkof 0x27898]. The
  outcome arrives as the event, not as the completion.

Which events are raised at all is only partly answerable from the image. Scanning every call
of the two posting functions for a constant event number finds 2, 8, 9, 11, 12, 13, 17, 21,
22, 24, 25, 26, 27, 29 and 30 (the one site above), 34, 35, 37, 38 and 39 [fw rkof, calls of
0x26158 and 0x1adf1c]. A dozen further call sites compute the number instead, so the rest of
the list is neither confirmed nor excluded by that scan.

### 3.4 Transitions

Index table [fw rkof 0x1be53c], rows [fw rkof 0x1be23c onwards]. "ok" is the next state when
the handler returns 0x1770, "else" the other one, and "-" means stay.

| state | event | ok | else | handler |
|---|---|---|---|---|
| 0 init | 1 init | idle | idle | 0x27620 |
| 1 idle | 3 session start | host setup | - | 0x274f0 |
| 1 idle | 2 reset | - | - | 0x275d4 |
| 2 host setup | 12 app setup complete | - | quiesce | 0x27374 |
| 2 host setup | 9 sibling audio configured | - | - | 0x273d4 |
| 2 host setup | 27 supervision timeout updated | wait session response | quiesce | 0x27438 |
| 2 host setup | 38 disconnect, 39 abort | idle | host teardown | 0x26308 |
| 3 wait session response | 6 peer session start response | quiesce | - | 0x27330 |
| 3 | 38, 39 | idle | host teardown | 0x26308 |
| 4 quiesce | 35 local flow off done | controller setup | controller teardown | 0x27198 |
| 4 | 38, 39 | idle | host teardown | 0x26308 |
| 5 controller setup | 29 ULLA link up | DSP setup | - | 0x270b4 |
| 5 | 30 link down, 38 disconnect | idle | host teardown | 0x26308 |
| 6 DSP setup | 8 DSP session ready | - | - | 0x26dc4 |
| 6 | 11 peer DSP config complete | - | - | 0x26e48 |
| 6 | 13 DSP setup complete | session open | - | 0x26eac |
| 6 | 30, 38 | idle | host teardown | 0x26308 |
| 7 session open | 15 host stream start, 17 peer stream start | stream host setup | - | 0x26c78 |
| 7 | 5 peer session stop | wait stop response | quiesce | 0x26d24 |
| 7 | 4 host session stop | wait stop response | - | 0x26d24 |
| 7 | 30 | idle | host teardown | 0x26308 |
| 8 stream host setup | 25 stream ready from DSP | - | - | 0x268cc |
| 8 | 21 stream ready from controller | - | - | 0x26974 |
| 8 | 19 peer stream start response | - | - | 0x26a1c |
| 8 | 26 stream host setup complete | stream open | - | 0x26a88 |
| 8 | 30 | idle | host teardown | 0x26308 |
| 9 stream open | 4 host session stop, 16 host stream stop | wait audio stopped | - | 0x26828 |
| 9 | 18 peer stream stop | stream host teardown | - | 0x2682c |
| 9 | 30 | idle | host teardown | 0x26308 |
| 10 wait audio stopped | 20 peer stream stop response | session open | - | 0x26824 |
| 10 | 30 | idle | host teardown | 0x26308 |
| 11 stream host teardown | 22 stream stop response from controller | - | - | 0x26560 |
| 11 | 24 stream stop response from DSP | session open | - | 0x2660c |
| 11 | 20 peer stream stop response | session open | - | 0x2671c |
| 11 | 30 | idle | host teardown | 0x26308 |
| 12 wait stop response | 7 peer session stop response | quiesce | - | 0x264b0 |
| 12 | 30 | idle | host teardown | 0x26308 |
| 13 controller teardown | 30 | idle | host teardown | 0x26308 |
| 14 host teardown | 27 supervision timeout updated | idle | - | 0x260c8 |
| 14 | 14 peer session stopped | idle | - | 0x260e0 |
| 14 | 38 disconnect | idle | idle | 0x260e4 |
| 15 wait abort response | no rows in this build | | | |

Two things that table says by what it leaves out. **The disconnect event is only answered in
the states before a session is open**: host setup, waiting for the sibling, waiting for the
audio path, controller setup, DSP setup and host teardown. From session open onwards it is the
**link-down event** that tears things down, and it is answered in every state from controller
setup to controller teardown. So once a session is open the manager is not driven by the
host's channel going away; something else has to raise the link down. And state 15, waiting
for an abort response, has no rows at all in this build, which is why nothing can leave it.

### 3.5 Starting a session, in detail

On event 3 in idle [fw rkof 0x274f0]: if the sibling transport is not in an active mode the
manager answers the host 0x1776 and stays idle. Otherwise it stores the 6-byte peer address,
resolves it to an existing ACL connection (failing fatally if there is none), records that
connection's handle, resolves the sibling's address to its own handle, and moves to host setup.
So the source must already have an ordinary Bluetooth connection to the earbud before asking.

### 3.6 The order it all happens in

The transition table of 3.4 says what each event does; this is the sequence those events
arrive in, and the one place where the manager actually commands its controller.

| step | what happens | where |
|---|---|---|
| 1 | the host opens service 4 and sends opcode 0x36 | section 2 |
| 2 | the manager stores the peer's address and the mode byte and leaves idle | [fw rkof 0x274f0, 0x27a58] |
| 3 | it asks its sibling, with the address reversed and the same mode byte | [fw rkof 0x27466], section 4 |
| 4 | the app setup and the supervision timeout settle, events 12 and 27 | 3.4 |
| 5 | the sibling answers, event 6, and the manager waits for the local audio path | 3.4 |
| 6 | the local audio path stops, event 35, and **this handler is the one that commands the controller** | [fw rkof 0x27198] |
| 7 | the controller reports the link up, event 29, and the DSP is configured | 3.4, and section 6's vendor event |
| 8 | the DSP's events 8, 11 and 13 arrive and the session is open | 3.4 |
| 9 | the host asks for the stream with 0x38, and events 25, 21, 19 and 26 open it | 3.4 |

Step 6 is worth the detail. That handler finishes a timer and then dispatches on one field of
the manager, with four values and a byte table [fw rkof 0x271b2, table 0x271c0, fatal for 0 or
above 4 at 0x272ba]:

| value | what it sends |
|---|---|
| 1 | the vendor command 0xFE50, with the parameters of section 6.0 [fw rkof 0x271c4..0x272e4] |
| 2 | the vendor command 0xFE51 [fw rkof 0x27234] |
| 3 | the host's status reply with 0, or else the sibling's start-session response [fw rkof 0x271e2, 0x271f8, 0x272b4] |
| 4 | the sibling's stop-session response [fw rkof 0x271fe] |

So the mode change is asked for **after** the local audio path has quiesced, not when the host
first asks, and the status reply the host sees is sent from the same state. In this build
nothing stores the value 1 in that field, which is why the earbud is the responder of the
link-manager exchange rather than its initiator.

## 4. Earbud to earbud

The manager talks to the sibling as client 5 of a transport carried over AAP service 0x10
[fw rkof 0x27692, 0x29e0e, 0x2a624]. Frame:

| offset | size | meaning |
|---|---|---|
| 0 | 1 | client id, 5 for the session manager |
| 1 | 1 | message id |
| 2 | .. | body |

Messages, every sender located [fw rkof calls to 0x2aee0]:

| id | message | body |
|---|---|---|
| 1 | start session request | 6 address bytes, most significant first, then the mode byte |
| 2 | start stream request | empty |
| 3 | stop session request | empty |
| 4 | stop stream request | empty |
| 5 | start session response | empty |
| 6 | start stream response | empty |
| 7 | stop session response | empty |
| 8 | stop stream response | empty |
| 9 | DSP config complete | empty |
| 10 | session stopped | empty |
| 11 | source disconnected | empty |
| 12 | statistics | the report buffer described in 2.6 |
| 13 | configure statistics | 1 + 4 + 4 bytes, as received from the host |

Every sender first checks that the sibling transport is in an active mode and connected.
The receiving side parses message 1 by reversing those six address bytes into the order the
Bluetooth stack expects [fw rkof 0x30978, 0x2b37a], which is the mirror of how the sender
packs them, so the two paths corroborate each other.

## 5. Controller to controller: the link-manager PDUs

Two vendor link-manager PDUs carry the setup. They travel on the existing ACL link between
the two controllers and **never cross the host interface**, so an ordinary host capture cannot
show them [measured: nothing of the sort appears in an HCI trace].

Every PDU begins with one byte holding the transaction id in bit 0 and the opcode in bits 1
to 7 [fw acib 0x3a634].

### 5.1 Opcode 0x73, the setup request

Total 9 bytes [fw acib 0x3aa6e]:

| offset | size | meaning |
|---|---|---|
| 0 | 1 | header: transaction id and opcode 0x73 |
| 1 | 4 | a value the source reads from its own radio hardware at runtime |
| 5 | 2 | a configuration word supplied by the source's host |
| 7 | 1 | two flag bits from the host's request |
| 8 | 1 | one further flag bit from the host's request |

Which of the host's parameters becomes which of those bytes is proven all the way through. The
command's handler fills a small structure, that structure's bytes 4 to 0x13 are copied into
the transition context [fw acib 0x68344, a 16-byte copy from its +4], and the sender reads the
context to build the PDU [fw acib 0x68588]:

| PDU field | comes from | where |
|---|---|---|
| the 32-bit field at offset 1 | the radio, not the host: a hardware read with retries | [fw acib 0xdc9c0, context +4] |
| the configuration word at 5 | parameters 3 and 4 of the command | [fw acib 0x1daf2, context +8] |
| the flag byte at 7 | bits 0 and 1 of parameter 5 | [fw acib 0x1db04, context +0xA] |
| the flag byte at 8 | bit 3 of parameter 5, as a whole byte of 0 or 1 | [fw acib 0x1dafe, context +0xC] |
| the byte past the length | parameter 2 | [fw acib 0x1daea, context +0x10] |
| nothing on the air | bit 2 of parameter 5 | [fw acib 0x1daf8, context +0x18] |

That last row is as far as the bit is traced. Scanning the procedure for word reads of the
context's +0x18 finds two: the copy itself [fw acib 0x68368] and the state-2 handler below,
which puts the value, with a dozen other context fields, into the request block it builds for
the layer underneath, at that block's +0x50 [fw acib 0x68700]. Whether any of the three calls
that block is handed to puts it on the air is not traced here, so this note stops at the block.

The receiving side reads the same fields from the same offsets [fw acib 0x675f4], so the
layout is confirmed by the sending and receiving code independently. The 4-byte value comes
from a hardware read with a validity check and up to ten retries [fw acib 0xdc9c0], so it is
not a firmware constant and cannot be predicted statically. **The accept decision never reads
any of these fields** (section 5.4), so an implementation does not need to understand them in
order to be accepted; it needs them only to make the radios agree.

The total of 9 is the length the controller puts on the air: the per-opcode serializer writes
the body and reports a length byte, which its caller stores as the packet's length
[fw acib 0x3aa6e sets 9; the caller at 0x3ac8a reads it back and stores it at the packet's
offset 2]. That length includes the header byte, which three controls settle: the accepted PDU
writes one body byte and reports 2, the not-accepted PDU writes two and reports 3, and 0x74
writes six and reports 7. Across all 46 opcodes that carry a length constant, 44 report
exactly one more than the bytes they write.

**One caution for an implementation.** 0x73 is one of the two that do not. Its case writes a
*fifth* field, a third flag byte, at the body offset just past the declared length, and the
receiver reads that offset too [fw acib 0x675f4 reads the body at +0, +4, +6, +7 and +8, and
0x63488 hands it the body pointer directly]. So the byte the firmware takes for that field
comes from whatever follows the PDU in its receive buffer. Send nine bytes, fill the two flag
bytes at offsets 7 and 8, and do not expect a third flag byte to arrive. On the sending side
that byte is real enough: the source's own sender fills it from its transition context, at the
context's offset 0x10 [fw acib 0x685cc..0x685d2 and section 5.5].

### 5.2 Opcodes 0x74, 0x75 and 0x76, the messages after the setup

**0x74, the configuration.** Total 7 bytes [fw acib 0x3a832]:

| offset | size | meaning |
|---|---|---|
| 0 | 1 | header: transaction id and opcode 0x74 |
| 1 | 4 | configuration value, read by id from the controller's parameter table |
| 5 | 2 | configuration value, read by id from the controller's parameter table |

In the build examined, the two parameter getters for these ids are stubs that return nothing
[fw acib 0xcf9ac, 0xcf9ae], so this firmware contributes zeroes for both. The receiver checks
nothing of its own: it reads the word and the halfword and hands both, with the procedure
object, to the slot at +0xC8 of the module block [fw acib 0x6352c]. That slot is the one 5.9
and 5.10 map, so **receiving a 0x74 is what posts event 3** to a responder's procedure, and
the gap in the source's set is the same slot holding a function that only traces.

One detail for a decoder: that receiver reads the halfword **signed** [fw acib 0x63530,
`ldrsh`], while 0x75's reads it unsigned [fw acib 0x487ac, `ldrh`]. The same two bytes
therefore reach the layer above as a negative number in one message and a large positive one
in the other, once the top bit is set.

**0x75 has the same seven bytes.** It shares 0x74's case in the sender, so its layout is
identical [fw acib 0x3a832 serves both opcodes], but its receiver is a different function and
it does check [fw acib 0x3bb8a, handler 0x487a0]:

- the halfword must be **at most 7499** (0x1D4B), and above that the message is dropped;
- the session must be active: the state byte of the session object is 2 and a flag of it is
  non-zero [fw acib 0x2f550];
- the link's own role byte must be zero, so only one end may send it [fw acib 0x2f566];
- the link type must resolve [fw acib 0x2f540].

**0x76 is the header alone,** one byte with no body at all: its case in the sender leaves the
length at the 1 the switch starts with [fw acib 0x3aaaa, the common exit]. Its receiver tests
the first two of those conditions [fw acib 0x3b9de].

All three of those accessors read one object, the controller's ULLA session
[fw acib 0x100027cc, with the link at its +0xC], which is why these three are messages for a
session that already exists while 0x73 is the one that creates it: 0x73's own checks look at
different state entirely (5.4). What the values mean is open.

**Which of them this firmware sends.** Two sweeps agree on an asymmetry worth knowing before
implementing either end. Every call to the two serializer entries, 86 of them, has its opcode
read off: 0x73 goes out at acib 0x685de (the source's first state) and 0x30410 (the sink
forwarding to its sibling), 0x74 at 0x6866c, 0x76 at 0x2f5c4, and nothing sends **0x75**. Ten
of the 86 sites take their opcode from a register rather than a nearby constant; four of those
are generic senders whose only callers pass 0x1F, 0x20, 0x23 and 0x24, and none of the ten has
0x75 anywhere in the 48 bytes before its call. The second sweep comes at it from the other
side: the image has ten instructions that put the constant 0x75 into a register, and not one of
them is followed by a serializer call within 80 bytes. So this build receives 0x75 and never
sends it, which is exactly what its receiver's role test implies: the other end sends it, and
an implementation of a source has to.

**How 0x76 is sent** is the same two-conditions pattern as the rest of this firmware. The
sender itself is one small function that takes a link [fw acib 0x2f5c4]. The session object's
byte at +0xE1 is a two-bit accumulator, and two functions set bits in it: one sends 0x76 to
the peer once both bits are in and clears the byte, the other does the equivalent work locally
instead [fw acib 0x4873c and 0x48878, both reaching 3 before they act]. Which of the two runs
is decided by two role bytes of the ULLA state block, and on one path the sender is called
directly on the link that block names [fw acib 0x486d0, the link from its +0x1A]. The two
bits come from two sites each [fw acib 0x2f5b8 and 0x2f5be with bit 2, 0x486c4 and 0x486dc
with bit 1].

**How much of the ordinary opcode space this protocol uses**, as far as a sweep can say:
walking every one of the 126 entries of the receive dispatch table and following each handler
plus one level of its calls, the only handlers that reach that session object or its accessors
are 0x75's and 0x76's. So the family on the air is 0x73, 0x74, 0x75 and 0x76 among the
ordinary opcodes, plus the extended messages of 5.6, and nothing else in that space touches
the session. The sweep follows straight-line code two levels deep, so it is evidence rather
than proof: a handler that reaches the session through a function pointer or a third call
would not show up in it.

### 5.3 Accept and reject

Acceptance is the standard link-manager accepted PDU, opcode 3, carrying 0x73 [fw acib
0x3d19c, which passes 3 to the send builder and stores the one body byte; 2 bytes on the
wire]; rejection is the standard not-accepted PDU, opcode 4, carrying 0x73 and one reason byte
[fw acib 0x3c934, which passes 4 and stores the pair; 3 bytes].

On the way in, the two answers are reached through the receive parser's own dispatch: a
halfword table at acib 0x3b254, indexed by opcode minus one, with targets relative to
0x3b23e. Opcode 3 lands on 0x3b696, opcode 4 on 0x3b71c, 0x73 on 0x3bb1e (the function that
reads the fields) and 0x74 on 0x3b704. Anyone repeating that decode should know the table
begins at 0x3b254 and not 0x3b256, because the instruction that forms its address word-aligns
the program counter; being one entry out makes 0x73 and 0x74 look swapped.

### 5.4 What the receiver actually checks

Four checks, in order, each with its own reason [fw acib 0x675f4]:

1. a feature-enabled byte [fw acib 0x62074]
2. an object-ready call through the session's function table [fw acib 0x62030]
3. a busy flag [fw acib 0x62154]
4. link state, role and link count [fw acib 0x620c8]: the link must be in the expected state
   with a particular flag clear, and depending on role either the sibling's link must also be
   valid or the link must be the only one.

**None of them performs any cryptographic check**: no key comparison, no nonce, no challenge.
The parser's per-opcode attribute table is state gating only, with no encryption bit, and the
entry for 0x73 imposes no state requirement at all [fw acib 0xf1f58, 0x3b0aa].

Each check has its own reason. The receiver is handed a byte to answer in, sets **bit 7 of it
before any check runs** [fw acib 0x67608], and writes the failing check's number into its low
nibble [fw acib 0x67654, which clears the nibble and adds the number]:

| check | what it reads | nibble when it fails | on the air |
|---|---|---|---|
| 1 | a feature-enabled byte [fw acib 0x62074] | 1 | 0x81 |
| 2 | object ready, through the session's function table [fw acib 0x62030] | 2 | 0x82 |
| 3 | a busy flag [fw acib 0x62154] | 3 | 0x83 |
| 4 | link state, role and link count [fw acib 0x620c8] | 4, 5 or 6 | 0x84, 0x85, 0x86 |

Check 4 returns its reason directly: 6 when the link is absent, or its state byte is not 0xC,
or a flag at its offset 0x185 is set; otherwise 4 or 5 from the role and connection-count
tests [fw acib 0x62102 onwards]. Zero means it passed.

The caller then tests that byte's low nibble: zero and it takes the accept path, otherwise it
sends the not-accepted PDU with **the whole byte** as the reason [fw acib 0x63492 shifts the
byte left by 28 and branches on zero, 0x6349a and 0x6349e pass it to the sender at 0x3c934].
So the reason an earbud puts on the air for a refused setup is 0x81 to 0x86, and those are
exactly the names the host-side table gives the primary role.

Reason numbers, with the names the firmware's own tables give them [names at fw rkof
0x1dea6a, selected by the two byte tables at rkof 0x2777c and 0x2778c and the two direct
comparisons at 0x27798 and 0x2779c]:

| value | name |
|---|---|
| 0 | no specific reason |
| 1 | secondary role not initiated |
| 3 | secondary disallowed activity |
| 4 | secondary transport not in active mode |
| 5 | secondary illegal number of connections |
| 6 | secondary invalid source connection |
| 0x81 | primary role not initiated |
| 0x82 | primary ULLA already connected |
| 0x83 | primary disallowed activity |
| 0x84 | primary transport not in active mode |
| 0x85 | primary illegal number of connections |
| 0x86 | primary invalid source connection |
| 0x87 | primary snoop disconnected |
| 0xaa | lite-mode transport disconnected |
| 0xff | not applicable |

The two halves of that list are the same six failures twice, once per role, and the 0x80 bit
is the role of the device doing the refusing. An earbud facing a source is the primary one, so
the checks above produce the 0x81 to 0x86 half; the bare 1 to 6 are the same failures reported
by a secondary.

**2 is missing from the secondary half on purpose.** Its slot in the name table points at the
same "invalid reason" text as any unknown value, while the primary half is contiguous and its
extra member at 0x82 is "already connected", which cannot happen to a secondary. Read together
with the checks, that is also what check 2 means: the object is not ready because a link
already exists.

### 5.5 What each side does with the request

**The source.** The controller implements this role too, and the chain from the host's command
to the bytes on the air is complete:

1. 0xFE50 with its last parameter zero fills the transition context [fw acib 0x1dace..0x1db0a].
2. The procedure's first state builds the PDU from that context and sends it
   [fw acib 0x68588, the send at 0x685de]: the word at the context's +4 becomes the PDU's
   32-bit field, the halfword at +8 its configuration word, and the bytes at +0xA, the low
   byte of the word at +0xC and the byte at +0x10 become its three flag bytes, of which the
   last is the one that falls outside the declared length (5.1). The transaction id comes from
   the link's own byte at +0xE, which is where every sender in this family takes it from.
3. When the peer accepts, the next state sends 0x74 with the two values its parameter table
   returns [fw acib 0x685f8, the send at 0x6866c].

So the source role is in the controller, byte for byte, and what is missing in this build is a
host that asks for it, which is the point 3.6 makes about the field that would. One step of it
is missing too, though, and 5.9 says exactly which: the procedure's third state waits for an
event that nothing in this image posts.

**The sink.** It copies the parsed fields into its own context, forwards an identical 0x73 to
the second link [fw acib 0x303a8, the send at 0x30410], and only sends the accepted PDU back
once both links are ready [fw acib 0x677c8, 0x302f8].

### 5.6 The two escape spaces, and which messages exist in them

Extended PDUs live behind two escape opcodes, 0x7B and 0x7F, numbered independently. Each has
its own dispatch table, reached from the main table's case for that opcode after a bound
check, and each has one stub that every unimplemented extended opcode shares with the
out-of-range case:

| escape | entries | extended opcodes | table | reject stub |
|---|---|---|---|---|
| 0x7B | 39 | 0x01 to 0x27 | acib 0x3bc14, from 0x3bc04 | 0x3c2c6 |
| 0x7F | 34 | 0x01 to 0x22 | acib 0x3b3bc, from 0x3b372 | 0x3bf52 |

What each space implements in this build:

| escape | implemented | refused, on the shared stub |
|---|---|---|
| 0x7B | 0x01, 0x02, 0x0A to 0x11, 0x14 to 0x1C, 0x1F to 0x23, 0x25, 0x26, 0x27 | 0x03 to 0x09, 0x12, 0x13, 0x1D, 0x1E, 0x24 |
| 0x7F | 0x01 to 0x07, 0x0B to 0x0D, 0x10, 0x11, 0x15 to 0x22 | 0x08, 0x09, 0x0A, 0x0E, 0x0F, 0x12, 0x13, 0x14 |

Extended opcode 0x02 of both spaces shares one handler, so the two spaces are not entirely
separate. The two messages this document decodes are 0x7B/0x17 (section 6.2) and 0x7B/0x1C
(section 6.5).

Which of them this build **sends** is a different list, found by scanning every call of the
send builder for the constants it passes [fw acib 0x3ad7c, the extended opcode in its third
argument]: in the 0x7B space 0x01, 0x0A to 0x0F, 0x17, 0x18, 0x1B, 0x1C, 0x24, 0x26, 0x27 and
0x28; in the 0x7F space 0x03, 0x04, 0x0B, 0x0C, 0x0D, 0x10, 0x11, 0x15, 0x16, 0x17, 0x19,
0x21, 0x22, 0x23 and 0x26. Three senders compute their extended opcode at runtime and one
function sends two different messages on two paths, so that list is a floor, not a ceiling.
One of those three settles it: it takes the escape opcode, the extended opcode and the
transaction flag from three bytes of an array it is handed, so it can send anything at all
[fw acib 0x4aa8c to 0x4aa92]. Any list read off the call sites is what can certainly be sent,
never what cannot. For the ordinary opcodes the same question does have a clean answer, and
5.2 gives it.

**Two of those are messages the earbuds send and will not accept.** 0x7B/0x24 is one of the
twelve on the reject stub, and 0x7B/0x28 is past the end of the 39-entry table altogether. So
the other end of a ULLA link implements extended opcodes these earbuds do not, which is the
first thing in this firmware that says the peer is a larger implementation than the earbud.

### 5.7 How long each extended message is, in both spaces

The bodies are not assembled by the senders. A sender fills a field block whose first sixteen
bytes are a transmit descriptor, and names an extended opcode; one builder then turns the
block into a body [fw acib 0x3a620, reached with the descriptor's byte at +2 zero]. Extended
opcodes 1 and 2 are handled in its prologue, and each escape space has its own sub-table after
that: 0x7B's is indexed by the extended opcode minus 0x0A [fw acib 0x3a9ac, table 0x3a9ba, 31
entries for 0x0A to 0x28] and 0x7F's by the extended opcode minus 3 [fw acib 0x3a7be, table
0x3a7cc, 38 entries for 0x03 to 0x28]. So the transmit length of every extended message is
readable in one place. Below, "block+N" is the sender's field block, body offsets follow the
header and the extended opcode, and the total includes both of those:

| extended opcode | body | total |
|---|---|---|
| **either space**, 0x01 | two bytes, block+0x10 and +0x11 | 4 |
| **either space**, 0x02 | three bytes, block+0x10 to +0x12 | 5 |
| **0x7B** 0x0A, 0x0B | one word from block+0x10 | 6 |
| 0x7B 0x0C, 0x0D, 0x0E, 0x17 | one byte from block+0x10 | 3 |
| 0x7B 0x0F | two bytes, block+0x10 and +0x11 | 4 |
| 0x7B 0x10, 0x11, 0x20, 0x21 | one halfword from block+0x10 | 4 |
| 0x7B 0x15, 0x16, 0x24 | N bytes from block+0x11, where N is block+0x10 | N + 2 |
| 0x7B 0x18, 0x27 | fifteen bytes from block+0x10 | 17 |
| 0x7B 0x19 | byte, halfword, halfword, byte | 8 |
| 0x7B 0x1A | one byte, then ten bytes from block+0x11 | 13 |
| 0x7B 0x1B | one byte from block+0x10 | 3 |
| 0x7B 0x1C | 3N bytes from block+0x11, where N is block+0x10 | 3N + 2 |
| 0x7B 0x1F, 0x28 | byte, halfword, halfword, byte, halfword, byte, byte, halfword | 14 |
| 0x7B 0x26 | one word and one halfword | 8 |
| **0x7F** 0x03, 0x04 | two bytes, then two words read through a pointer in the block | 12 |
| 0x7F 0x05 | byte, word, halfword, byte, byte, word | 15 |
| 0x7F 0x06, 0x0B, 0x1E, 0x1F, 0x20, 0x28 | one byte from block+0x10 | 3 |
| 0x7F 0x07 | halfword, byte, byte | 6 |
| 0x7F 0x0C, 0x26 | two bytes, six bytes, two halfwords, two bytes | 16 |
| 0x7F 0x0D, 0x27 | two bytes, block+0x10 and +0x11 | 4 |
| 0x7F 0x10 | byte, halfword, halfword | 7 |
| 0x7F 0x11 | ten bytes from block+0x16 | 12 |
| 0x7F 0x15, 0x16 | byte, halfword, word | 9 |
| 0x7F 0x17, 0x18, 0x1B, 0x1C, 0x1D, 0x21, 0x22 | empty | 2 |
| 0x7F 0x19, 0x1A | three bytes | 5 |
| 0x7B 0x03 to 0x09, 0x12 to 0x14, 0x1D, 0x1E, 0x22, 0x23, 0x25 | no builder at all | - |
| 0x7F 0x08 to 0x0A, 0x0E, 0x0F, 0x12 to 0x14, 0x23 to 0x25 | no builder at all | - |

The two spaces' ranges differ by direction, which is worth noticing before comparing the
tables: 0x7F can be built up to extended opcode 0x28 but accepted only up to 0x22, so its
0x26, 0x27 and 0x28 are build-only in the same way 0x7B's 0x24 and 0x28 are.

Put against the receive side of 5.6 that sorts every extended opcode of the 0x7B space into
three groups: 0x03 to 0x09, 0x12, 0x13, 0x1D and 0x1E can be neither built nor accepted;
0x14, 0x22, 0x23 and 0x25 can be accepted but not built; and 0x24 and 0x28 can be built but
not accepted, which is the asymmetry above.

Two rows are the messages this document decodes in full, and this table is how they were
checked: 0x7B/0x17's one byte is the allowed-band mask of section 6.2, and 0x7B/0x1C's 3N
bytes are the band ranges of section 6.5, whose count the length carries. A third pair, with
its two fields and what the receiver does with them, is traced end to end in 5.11.

### 5.8 The feature exchange, which gates everything else

Two of the messages above are a request and its reply, and together they are what decides
whether any of the rest is ever sent.

**0x7B/0x0A** carries one word: the sender's own feature word, loaded from the device's feature
cell [fw acib 0x29410 and 0x2948a load 0x10003290; the body is the word at the block's +0x10,
builder acib 0x3abc8]. **0x7B/0x0B** is the same message the other way round. The receiving
side stores the word it was given against the link and answers with its own
[fw acib 0x3c0a8 stores the body word in its block, then 0x29470 copies it to the link's
offset 0x47 and sends the reply].

That per-link copy is the "per-link byte" the gates in section 6.2 refer to. Every ULLA
feature in this firmware is gated on **the AND of the two words**, a byte at a time:

| feature | bit | bytes ANDed |
|---|---|---|
| the band command family and the band PDUs | 5 | the device's own byte and the link's 0x47 [fw acib 0x29916] |
| 0xFE50's non-zero path, the mode procedure | 6 | the same pair, or the two bytes three further on, 0x10003293 against the link's 0x4A [fw acib 0x1db5e] |
| which escape space one reply goes in (5.11) | 2, then 1, then 6 of the fourth bytes | the first bytes for 2 and 1, then 0x10003293 against the link's 0x4A again [fw acib 0x29204, the second test at 0x293ac] |

So the device's feature word lives at 0x10003290 to 0x10003293 and the peer's at the link's
0x47 to 0x4A, and **an implementation that never answers the exchange has every feature gated
off**, because the AND reads its word as zero. Answering it is the first thing a source has to
do. (That address is the base of a larger structure, not a word of its own: the ULLA state
block sections 5.10 and 5.2 use is the same structure at +0xC.)

**Where its own bits come from**, which decides what a controller advertises:

| bits | written by | how |
|---|---|---|
| byte 0, bit 5 | vendor command 0xFF77 | one parameter byte: non-zero sets it, zero clears it [fw acib 0x192f4 to 0x19306] |
| byte 0, bits 1, 2, 4 and 6 | vendor command **0xFDF3** | the same shape: the byte is masked to 0xA9 and this group, 0x56, is added when the parameter is non-zero [fw acib 0x16b20, table index 162 of 6] |
| byte 0, bit 0 | vendor command 0xFFA1 | [fw acib handler 0x18048, the store at 0x18066] |
| byte 0, bits 1, 3 and 4 | vendor command 0xFDEF | each bit on its own condition [fw acib handler 0x16a3c, the store at 0x16aa4] |
| byte 0, bit 7 | vendor command 0xFE8A | [fw acib handler 0x174bc, the store at 0x174cc] |
| byte 1, bits 2 and 4; byte 3, bits 6 and 7 | the controller itself | one function sets them, after another has zeroed all four bytes [fw acib 0x2965c, the clear at 0x2964c] |

Two of those rows matter for anyone driving this from a host. The band family needs 0xFF77,
and **the mode procedure needs 0xFDF3**: bit 6 is in the 0x56 group, so without that command
the controller's own half of 0xFE50's gate is zero and the non-zero path is refused (6, and
the gate at acib 0x1db5e). 0xFF78 says the same thing from the other side by refusing outright
while bit 5 is clear [fw acib 0x19348].

The earbud sets its own bit 5 the same way, through its manager's queued sender: a one-byte
call that sends command id 0x377 [fw rkof 0x249b4, the send at 0x249d0], reached from the
diagnostic CLI's `control_ubo` and from a configuration path that enables it when bit 5 of a
configuration word is set [fw rkof 0x663fc and 0x8a3fc]. Nothing in either image sends
0xFDF3, and that fits what bit 6 gates: only 0xFE50's non-zero path, which this build's own
manager never takes (3.6 and 6). So the earbud enables what it uses and leaves the rest, and a
source has to enable the rest itself.

The 0xA9 and 0x56 masks are complementary, so that
byte really is two groups of four bits, and the identity check below reduces an advertised copy
to the first group. The three other opcodes in that table were resolved the same way as
0xFDF3, by finding the handler the store belongs to and reading its key [fw acib handler table
0xf1230, keys 0xf1928]; what their bits gate is not traced, and only bits 1, 2, 5 and 6 have
known uses so far.

One quirk, for anyone comparing against a capture: when a per-link identity check matches (a
halfword of 0xF, a byte of at most 10, and a ten-byte comparison at the link's 0x4E), the low
byte of the word the earbud advertises is reduced to its bits 0, 3, 5 and 7
[fw acib 0x3684e deciding it, 0x2942c and 0x294a8 applying the mask 0xA9]. Bit 5, the band
family, survives that reduction; bits 1 and 2, the two in the table above that steer the reply
of 5.11, do not. The mask is applied to the word the earbud advertises, so it is the peer's
view of those two bits that goes to zero, and with it the peer's reason to answer in the 0x7B
space. What the remaining bits mean is open.

### 5.9 The source's own procedure, state by state

Everything above describes single messages. The controller runs them as a procedure, and that
procedure is a state machine of its own, separate from the session manager of section 3 and a
layer below it. It is the thing that actually puts 0x73 and 0x74 on the air.

**How it is driven** [fw acib 0x6829c]. The dispatcher takes a procedure object, an event
number and one argument. It reads the current state from the object's **+0x24**, refuses to
run inside itself using bit 0 of the object's **+0x23**, calls the handler that the table at
**acib 0xf2664** holds for that state, and writes the handler's return value back as the new
state. The table is nine words long, so there are nine states, and a handler that does not
expect an event returns its own number, which is how an unexpected event becomes "stay here".

The object is one per module, not one per link: the installer writes its address into the
module's own block at 0x1000df68 **+0xA4** [fw acib 0x6218c], so a source runs one setup at a
time. The initialiser copies the host's parameters into it, keeps only bits 5 to 7 of the flag
byte and zeroes the rest [fw acib 0x68344].

| state | handler | events it expects | what it does | next |
|---|---|---|---|---|
| 0 | acib 0x68588 | 0 | sends the 0x73 setup request and claims the link's procedure slot with the number 5 | 1 |
| 1 | acib 0x685f8 | 1, 2 | sets flag bit 1 on event 1, and once that bit and the link's own test both hold, sends 0x74 | 2 |
| 2 | acib 0x6869c | 3 | builds the 0x74-byte request block from the context and hands it down | 3 |
| 3 | acib 0x68788 | 4 | reports to the host: mode 1 and success, or mode 0 and result 1 | 4 or 0 |
| 4 | acib 0x687fc | 5 | the link is up and this is where it waits; on event 5 the teardown begins and a callback is registered | 5 |
| 5 | acib 0x6886c | 2, 6 | sets flag bit 4 on event 2, and once bits 2, 3 and 4 are all set registers two more callbacks | 7 |
| 6 | acib 0x688f4 | any | nothing at all: two instructions returning 0 | 0 |
| 7 | acib 0x688f8 | 7, 8 | one call back into the module | 8 |
| 8 | acib 0x68924 | 7, 8, 9 | on 9 finishes the teardown and reports mode 0 with the success result | 8 or 0 |

**The second half is the teardown**, and that is not a reading of the numbers but of a string.
Three strings in the image name parts of these machines [fw acib 0xf89ca, 0xf8a09, 0xf8a4c]:

```
ULLA_TRANS: ERROR- lm_ulla_trans_sec_handle_setup_lmp_accepted
ULLA_TRANS: ERROR- lm_ulla_trans_sec_handle_setup_lmp_not_accepted
ULLA_TRANS: ERROR- lm_ulla_trans_source_ulla_teardown_init_handle_lmp_not_accepted
```

So the firmware calls each of these a **transaction**, calls this role **source** and the
third one of 5.10 **sec**, and has a phase named **ulla_teardown_init**. The third string is
the assert at acib 0x68504; the function beside it, acib 0x68514, is the handler for the
*accepted* answer in that phase, it requires the link to be in mode 0x36, and it is the one
that posts event 6. The entry that starts at state 4 is the entry that puts the link in mode
0x36, and what state 8 reports is mode 0 with the success result. Three independent things,
all saying the same: states 4 to 8 take ULLA down again.

Those three are the only names to be had, and it is worth saying why. Everything else these
handlers log is a number: the trace call takes a 32-bit id, passes its top byte as a level and
the id itself into a queue, and never touches a string [fw acib 0x74240, 0x74256, 0x74270, all
into 0x7443c]. The dictionary that would turn those ids into text is not used here. There *is*
a 132-entry table of format strings in this payload [fw acib 0xf5064 to 0xf5274], and it holds
ULLA entries, among them "ULLA: tried transmitting packet of size %d, maximal size is %d" and
"Obsolete ULLA version 0x%04X, local version 0x%04X" - but nothing in the image references
that table or those two strings, so neither can be attached to a call site. The three names
above come from the other logging path, the one that takes a string pointer and does not
return [fw acib 0x6bf30]. So: asserts are named, traces are numbered, and this document does
not guess at the numbers.

The flag byte at +0x23 is the whole of the machine's memory besides the state: bit 0 is the
re-entry guard, bit 1 is "the peer accepted", bits 2 and 3 are the two paths of the teardown's
accepted answer, and bit 4 is its link condition. Bits 5 to 7 survive a new run and are not
traced here.

**The events**, each one at the site that posts it:

| # | posted by | what has happened |
|---|---|---|
| 0 | acib 0x6842c, from 0x66da0 | the source entry point: the procedure is starting |
| 1 | acib 0x3c6ec -> 0x61fcc -> 0x684dc | an accepted PDU naming opcode 0x73 arrived |
| 2 | acib 0x628f8 -> 0x68424 | the link's own state byte read 3 and was set to 4 |
| 3 | acib 0x66db0 | a call inside the module that owns the procedure |
| 4 | acib 0x619a4 -> 0x684e4 | that module passes a {byte, word} pair |
| 5 | acib 0x68450, from 0x66d72 | the teardown entry, which starts the machine at state 4 |
| 6 | acib 0x68514, posted at 0x68578 | the teardown's accepted answer, both paths of it |
| 7 | acib 0x66fe8 | the first callback state 5 registered |
| 8 | acib 0x67028 | the second callback state 5 registered |
| 9 | acib 0x684fc, slot +0xD8 | the teardown's last step |

So the machine has two entry points, one per half. The first sets the state to 0 and posts
event 0, after putting the link into mode 0x35 [fw acib 0x6842c]. The second sets the state to
**4** and posts event 5, with the link in mode 0x36 and a callback registered, and state 4 is
also where the setup's own last state leaves it, so a link that is up sits there until the
teardown starts [fw acib 0x68450, and 0x687a2 arriving at 4 on success]. Both
are called from the same module, which keeps its own phase number at 0x1000df68 +0x84. What
that module is for beyond driving this exchange is not traced, so it is not named here.

**A refusal is not an event.** When a not-accepted PDU names opcode 0x73, the receive handler
calls a thunk that passes the PDU body and the report goes straight to the host as mode 0,
result 3 and the peer's own reason byte, releasing the link's procedure slot that state 0
claimed; the procedure's state is not touched [fw acib 0x3c7de -> 0x61ff0 -> 0x6848c]. The
slot is a number at the link's +0x82, taken only while it reads zero and released only by the
number that took it, which is how a second setup on the same link is kept out
[fw acib 0x2515c taking it, 0x25304 releasing it]. That path also
proves the reason byte's shape from the other side: its trace splits the byte into the low
nibble and bit 7 [fw acib 0x684be and 0x684c4], which is exactly the 0x81 to 0x86 encoding of
section 5.4. The accepted PDU reaches event 1 through the mirror image of the same dispatch,
a switch on the accepted opcode [fw acib 0x3c6dc for 0x71, 0x72 and 0x73].

**Which result the host is told** is decided in four different places, and together they
account for the whole list of section 6:

| result | when | where |
|---|---|---|
| 0 success | mode 1, after event 4 in state 3 | fw acib 0x687a2 |
| 0 success | mode 0, after the teardown in state 8 | fw acib 0x6896a, with the object's +0x1C as the reason |
| 1 error | event 4 in state 3 when the module's check does not return 1 | fw acib 0x687c4 |
| 2 failed to be established | the link went away while it was in mode 0x35 | fw acib 0x683c2 |
| 3 setup rejected by peer | the not-accepted PDU above | fw acib 0x6848c |
| 4 supervision timeout | the link went away in any other mode | fw acib 0x683fe |

**The role table.** The event posters above are not called directly; they are installed into
the module's block by a role-dependent installer, which chooses one of three sets of the same
slots by a byte at 0x1000df69 [fw acib 0x6218c, the source set at 0x621ac, a second at 0x62238
and a third at 0x622d0]. The slots line up, which is the useful part: the source's slot +0xE0
posts event 1 where the second set has acib 0x676fc, slot +0xE4 is the refusal report where
the second set has 0x67714, and slot +0xD4 posts event 4 where it has 0x677b0, next to the
setup-request receiver of section 5.5. So both roles are the same machine with different
handlers, not two unrelated implementations.

**Four more parameters** ride along in the context at +0x14 and +0x15 to +0x17. The
initialiser leaves them as 3, 8, 8, 8 [fw acib 0x6832c]; the module installs a setter for them
at its own +0x10C [fw acib 0x68300, installed at 0x6246c]; and state 2 copies them into the
request block at +0x6C and +0x6D to +0x6F [fw acib 0x6871a onwards]. What the four mean is
not traced.

**One event has no poster.** Event 3, the one state 2 waits for, is posted by exactly one
function, acib 0x66db0: no branch in the image reaches it and no word in the image equals its
address, so nothing in this build calls it. The sink posts its own event 3 from the slot at
+0xC8, which is where a received 0x74 goes (5.2), and in the source's set that slot holds a
function that writes a trace line and returns [fw acib 0x66e10] - consistent, since a source
sends 0x74 rather than receiving one, and its event 3 was meant to come from its own module
instead. Every other event of this machine has a reachable poster, so the gap is one
step wide: a source built from this firmware gets as far as sending 0x74 and stops there, and
an implementation of the source role has to drive that step itself. This is the step 5.5 was
missing when it said the chain was complete.

One more limit worth stating plainly: the request block that state 2 builds is
only known by its offsets, because what the three calls it is handed to do with it was not
followed [fw acib 0x68748 to 0x68762]; the one field traced into it on purpose is bit 2 of the
host's flag byte, at the block's +0x50 (5.1).

`openulla/procedure.py` is this table, and `tests/test_procedure.py` walks it.

### 5.10 The sink runs the same kind of machine, and there is a third

The source's machine is not a special case. The firmware holds **three** of them, one per
role, each with its own dispatcher, its own table and its own state field in the procedure
object:

| role | dispatcher | table | states | state field | re-entry guard |
|---|---|---|---|---|---|
| source | acib 0x6829c | acib 0xf2664 | 9 | +0x24 | bit 0 of +0x23 |
| sink | acib 0x67318 | acib 0xf2628 | 8 | +0x1F | the byte at +0x1C |
| third | acib 0x67ba4 | acib 0xf2648 | 7 | +0x1E | the byte at +0x1C |

The three tables are one consecutive run of words in that order, and the run ends with a zero
after the source's ninth entry. The state fields overlap, so only one role can be live in an
object at a time, which is how the installer treats them: it writes one of three sets of
callbacks into the module block and nothing else, choosing on the byte at 0x1000df69 and, for
the two responder sets, on a second byte and one test [fw acib 0x6218c, the source set at
0x621ac, the sink's at 0x62238 and the third at 0x622d0].

**The slots and the event numbers line up between the roles.** That is what makes them the
same machine: slot +0xE0 posts event 1 in both sets, +0xEC event 2, +0xC8 event 3, +0xD4
event 4, +0xF0 event 5, +0xF4 event 6, +0xF8 event 7, and +0xD8 the last one, which is 8 for
the sink and 9 for the source [fw acib 0x67706, 0x67584, 0x677aa, 0x677ba, 0x675a8, 0x675e0,
0x675e8, 0x675f0 against the source's list in 5.9].

The sink's eight states:

| state | handler | events it expects | what it does | next |
|---|---|---|---|---|
| 0 | acib 0x677c8 | 0 | with the module's second role byte at 1, claims the link's slot and keeps the request; otherwise hands it to the second link; if that fails, refuses | 2, 1 or 0 |
| 1 | acib 0x67858 | 1, 2 | event 1 claims the slot on both links; once its flag and both link tests pass, sends the accepted PDU | 2 or 1 |
| 2 | acib 0x678f8 | 2, 3 | event 2 sends the accepted PDU, event 3 builds the request block and hands it down | 2 or 3 |
| 3 | acib 0x679f8 | 4 | reports to the host, mode 1 and success or mode 0 and result 1 | 0 or 3 |
| 4 | acib 0x67a6c | 5 | the teardown begins: three calls into its module | 5 |
| 5 | acib 0x67aa0 | 6 | registers a callback | 6 |
| 6 | acib 0x67af0 | 7 | one call back into the module | 7 |
| 7 | acib 0x67b18 | 8 | finishes the teardown and reports mode 0 with the success result | 0 |

Its second half is the teardown too, for the same reasons as the source's: its second entry
puts the link in mode 0x36 and its last state reports mode 0 with the success result
[fw acib 0x675ac, 0x67b5a].

Two differences from the source are worth the ink. The sink's state 3 goes back to **0** when
the mode is on, where the source waits at 4: a responder has nothing left to drive, so its
teardown is started entirely by that second entry [fw acib 0x67a20 returning 0, 0x675ac
entering at 4]. And the sink keeps its one flag as a whole byte at +0x1D rather than as a bit
[fw acib 0x67880].

Its first entry point is the receive path: the handler for an incoming 0x73 runs the
feature-enabled test and then the four checks of 5.4, and on acceptance calls the entry that
puts the link in mode 0x35, sets the state to 0 and posts event 0
[fw acib 0x63448, the checks at 0x675f4, the entry at 0x67588]. The caller tests only the low
nibble of the byte the checks return and sends the whole byte, which is the other half of the
0x81 to 0x86 encoding of 5.4 [fw acib 0x63492 onwards].

**A refusal that is not in the reason list.** When the sink cannot hand the request to its
second link, and the link can still carry a reply at all, it refuses with a reason byte of
**0x24**, the standard "LMP PDU not allowed" code, not one of the ULLA reasons
[fw acib 0x6781c to 0x67848]. The firmware's own reason formatter has no name for 0x24, so it
prints the invalid-reason text for a byte the firmware itself sends. There is one more such
site: when the role test before the four checks fails, the refusal is sent with 0x81, which
is in the list [fw acib 0x634ce]. So a decoder has to pass an unknown reason byte through
rather than treat it as malformed, which is what `openulla.lmp` does.

Both answers go out through two senders worth naming, since they are the whole of the reply
side: **accepted** is acib 0x3d1c6 and **not accepted** acib 0x3c934, which writes the
rejected opcode and the reason into the body and sends it with opcode 4; the hold until both
links are ready goes through acib 0x302f8, which picks one of the two by its verdict argument
and is gated on two bytes of the ULLA state block at 0x1000329c [fw acib 0x30300, 0x30306].

**The third role is the sink's machine with the waiting state taken out.** Its seven handlers
do the same work in the same order, with every event number after the first one lower:

| state | handler | events it expects | what it does | next |
|---|---|---|---|---|
| 0 | acib 0x67fac | 0 | claims the link's slot and moves on, answering nobody and forwarding nothing | 1 |
| 1 | acib 0x67fec | 1, 2 | event 1 sends the accepted PDU, event 2 builds the request block and hands it to the same three calls as the sink's | 1 or 2 |
| 2 | acib 0x68104 | 3 | reports to the host, mode 1 and success or mode 0 and result 1 | 0 or 2 |
| 3 | acib 0x68184 | 4 | three calls into its module | 4 |
| 4 | acib 0x681b4 | 5 | registers a callback | 5 |
| 5 | acib 0x681f8 | 6 | one call back into the module | 6 |
| 6 | acib 0x6821c | 7 | tears the link down and reports mode 0 with the success result | 0 |

Its entry points match the sink's one for one, one number lower: the first puts the link in
mode 0x35, sets the state to 0 and posts event 0 [fw acib 0x67dd8], and the second puts it in
mode 0x36 with a callback, sets the state to 3, posts event 4 and keeps its third argument as
the reason byte the teardown will report [fw acib 0x67dfc, reported at 0x6825c].

Two details of its own. Its host events carry the handle the ULLA state block holds at its
+0x1C, not its own link's [fw acib 0x68162 against the sink's 0x67a4c]. And the
receive path picks it for a request arriving on a link other than that stored one, ignoring a
second request on the stored link itself [fw acib 0x634e4] - so this earbud can hold two
responder procedures at once, on two different links, in two different machines.

**The firmware's name for this role is "sec".** Two of the three strings quoted in 5.9 are the
asserts in this set's slots for an accepted and a not-accepted answer to a setup request
[fw acib 0x67f60 and 0x67f70, installed at the slots +0xE0 and +0xE4 by 0x622de and 0x622e4].
An assert in both means this role never sends 0x73, so it can never be answered, which fits a
first state that answers nobody. The same two slots in the source's set are its event-1 poster
and its refusal reporter, so the slot map has firmware names for two of its entries:
`handle_setup_lmp_accepted` at +0xE0 and `handle_setup_lmp_not_accepted` at +0xE4.

`openulla/procedure.py` holds the sink's table beside the source's, and `openulla.controller`
drives it rather than keeping a second copy of the same rules: the four checks decide, and
everything about when each PDU goes out comes from the machine.

### 5.11 One extended pair, traced end to end

Section 5.6 lists what exists in the two escape spaces and 5.7 how long each message is, and
both stop short of what the messages are for, because naming them would be interpretation.
One pair can be taken further than that without guessing, because the answer is built in
front of us. It is also the clearest example of the 0x11 offset between the two spaces being
used for something.

**0x7B/0x0E is a request, and the handler that receives it answers.** [fw acib 0x2919c]

1. The byte at the received buffer's +0x10 is a selector, and the handler switches on 0, 1
   and 2 [fw acib 0x291b0].
2. Each branch asks the controller the same thing with a different parameter: property 0xB9
   about this link, with 0, 1 or 4 [fw acib 0x5b55e, 0x5b550 and 0x5b56c, all through 0x5b622
   and the property call 0x79454].
3. The answer picks one of exactly three two-byte pairs for the reply: **0x15 and 0x05**,
   **0x2A and 0x0A**, or **0x3F and 0x0F**, which are the same two numbers at one, two and
   three times [fw acib 0x291c4, 0x291d2 and 0x291de, each storing it at the block's +0x10].
4. A second property, 0xA7, returning zero forces the middle pair [fw acib 0x291f2 to
   0x29200].
5. **Which space the reply goes in is a feature decision.** Bit 2 of the AND of the two first
   feature bytes sends it as 0x7B/0x0F; failing that, bit 1 of the same AND or bit 6 of the
   AND of the two fourth bytes does the same; otherwise the identical message goes out as
   0x7F/0x20 [fw acib 0x29204 to 0x2922c, the second test at 0x293ac, the send at 0x3ad7c].

**And the reply's own receiver reads those two bytes back.** [fw acib 0x29318]

- The link's byte at +0x17E has to read 2, or the message goes to a different handler
  entirely [fw acib 0x29324, the other one at 0x29244].
- The second body byte is bounded at 15, so it is a four-bit code, and two masks classify it:
  the values 0, 3, 12 and 15 set one flag on the link and 2, 8 and 10 set another
  [fw acib 0x29332 to 0x29372, the masks 0x9009 and 0x504, the flags at the link's +0x177 and
  +0x176]. The three codes this firmware itself would send, 5, 10 and 15, fall in the second
  class, neither class and the first.
- Then both bytes go to one call with a 1 [fw acib 0x29380], and the link is poked once more
  [fw acib 0x3f618] before a property write with id 0xA6.

The transmit side agrees with that layout: 5.7's builder for 0x7B/0x0F writes exactly two
bytes, from the block's +0x10 and +0x11, which is where step 3 put them. For 0x0E this
firmware's own builder writes one byte, so the request it would send is shorter than the one
it reads a selector out of at +0x10; the directions of these messages are not symmetric, which
5.7 already says in general.

What the numbers mean stays open. A value of 21, 42 or 63 with a code of 5, 10 or 15, chosen
per link by a controller property, is a 1:2:3 family and nothing in these payloads names it.
It is the most concrete lead left for anyone with a capture: those six bytes are recognisable
on sight.

**The same three bits gate one more message.** The function that sends 0x7B/0x10, whose body
is one halfword (5.7), opens with the identical test: bit 2 of the AND of the two first
feature bytes, or failing that bit 1 of the same AND or bit 6 of the AND of the fourth bytes
[fw acib 0x296ac to 0x296ce]. On top of that it wants a per-link halfword at +0x4C to read
either 0x4C or 0x0F and a byte at +0x3E to be set [fw acib 0x296d0 to 0x296e0]; otherwise it
does one call and returns. When it does go on, it writes 0x2F to the link's +0x7D and 1 to its
+0x80, and sends the message only if bit 5 or bit 7 of that same AND is set, having called one
function in either case and a second one when bit 5 is [fw acib 0x296f2 to 0x29774]. So those
bits are not a one-off for the pair above: they gate a small group of messages, which is one
more reason the feature exchange is where an implementation starts.

## 6. Controller interface: HCI vendor commands

The source's host starts the whole thing with vendor command **0xFE50** [fw acib table at
0xf1928, handler 0x1da40]. Parameters, after the standard 3-byte command header:

| offset | size | meaning |
|---|---|---|
| 0 | 2 | connection handle |
| 2 | 1 | the setup request's third flag byte, the one past its declared length (5.1) |
| 3 | 2 | the configuration word of the setup request |
| 5 | 1 | see below: a bitmask on one path, a value 0 to 4 on the other |
| 6 | 1 | the mode: 0 takes the transition path, non-zero the procedure path |

The last parameter chooses between two paths, and it is the mode byte of the host's own start
command that ends up there, which section 6.0 traces [fw acib 0x1daa2].

**With it zero**, the handler fills the transition context and the state machine emits the
0x73 PDU. Parameter 5 is a bitmask here: bits 0 and 1 and bit 3 go into the setup request and
bit 2 is used elsewhere [fw acib 0x1dace..0x1db0a].

**With it non-zero**, a different procedure runs [fw acib 0x1daa6], and parameter 5 is not a
bitmask but a small value: **5 or above is refused with 0x12**, and 4 is the one value that
takes a shorter route [fw acib 0x1db7e, 0x1db92]. That path is gated on a different feature
bit from the band family's: bit 6 of the device's feature byte ANDed with the peer's, or else
bit 6 of the byte three further on ANDed with the peer's, refusing with 0x0C
[fw acib 0x1db5e..0x1db7c; the band family uses bit 5 of the same pair, and section 5.8 says
where the peer's word comes from]. On
success it allocates a request, stores parameter 5 in it with four constants and the handle,
and hands it to the radio layer [fw acib 0x1dba4..0x1dbd8].

Either way the handler refuses with the standard codes 0x0C, 0x12 or 0x17 when a feature flag
is clear, a parameter is out of range, or a procedure is busy.

Progress comes back as a vendor event [fw acib 0x10548, 0x11d34], nine bytes in all:

| offset | size | value |
|---|---|---|
| 0 | 1 | event code 0xFF |
| 1 | 1 | parameter length, 7 |
| 2 | 2 | event id 0x0A00 |
| 4 | 2 | connection handle |
| 6 | 1 | mode |
| 7 | 1 | result |
| 8 | 1 | reason |

The event is built field by field: the word 0x0a0007ff is written first, then the handle as a
halfword, then the three bytes [fw acib 0x11d4a..0x11d52]. The result reaches its byte through
the allocator, which moves the third argument into the register the store uses [fw acib
0x129ea]. The thirteen callers of the wrapper confirm the order, for instance (handle, 0, 1,
0xff) at acib 0x67a56 and (handle, 0, 4, 0xff) at 0x6841c. A second vendor event uses the same
shape with five parameters and event id 0x0A09 [fw acib 0x11d74]; what it reports is open.

Mode values [fw rkof 0x1be190]: 0 ordinary ACL, 1 ULLA on, 2 lite mode on.
Result values [fw rkof 0x1be5bc]: 0 success, 1 error, 2 failed to establish, 3 setup rejected
by peer, 4 supervision timeout error.

Both lists are exactly that long, three names and five, and the formatter that prints them
indexes each list by the value with no bound check at all [fw rkof 0x2774c, the two loads at
0x27760 and 0x27766]. So a mode of 3 or a result of 5 does not print "invalid": it prints the
name that happens to sit next to the list, which for the mode list is the first of the event
names. The reason byte is the one of the three that is checked, through the branch table of
section 5.4. It is worth knowing which way round that is before trusting a log.

### 6.0 What a source actually puts in those seven bytes

The earbud's own session manager contains the source side of this command, and it fills the
parameters with constants, so an implementation does not have to invent them. The manager's
command queue dispatches on one field, and the entry that sends this command builds
[fw rkof 0x272c0, sent at 0x272e4 through the queued sender 0x23a60 with the command id 0x250
and the length 7]:

| offset | size | value the manager uses |
|---|---|---|
| 0 | 2 | the connection handle it recorded for the peer |
| 2 | 1 | 1 |
| 3 | 2 | 0x0096 |
| 5 | 1 | 4 when its stored mode field is 1, otherwise 0xF |
| 6 | 1 | that stored mode field |

The flags byte follows from one comparison [fw rkof 0x271c4 onwards]: field equal to 1 takes
the branch that sets 4, field zero takes the one that sets 0xF, and any other value logs and
also takes 0xF. Split as the handler splits it, that means the setup PDU's flag byte is 0 with
bit 2 set elsewhere for a field of 1, and 0b11 with bit 3 also set for 0 or anything else.

That stored field is the mode byte of the host's own start command, opcode 0x36 of section
2.4. The chain is proven: the command's one byte reaches the manager, which stores it at its
offset 0x29 together with the peer address taken from the session [fw rkof 0x27a58, whose
arguments arrive in two registers through the outlined shim at 0x1abde8], and from there it is
both the last byte of the sibling start-session request [fw rkof 0x27466] and the last
parameter of this command [fw rkof 0x272c8]. A second command, **0xFE51**, carries it too:
handle, a zero byte, then that field, four parameter bytes in all [fw rkof 0x27234].

Reading the two together says which combinations the controller accepts:

| mode | parameter 5 | parameter 6 | what the handler does |
|---|---|---|---|
| 0 | 0xF | 0 | the transition path, where 0xF is the bitmask bits 0, 1, 2 and 3 |
| 1 | 4 | 1 | the procedure path, where 4 is its one special value |
| 2 | 0xF | 2 | the procedure path, where 0xF is 5 or above, so **it is refused with 0x12** |

The third row is the firmware's own code, not a transcription error: for a lite-mode request
the manager would build a command its controller refuses. The earbud never takes this path in
this build, because nothing stores the value that reaches it, so it is dormant source-role
code rather than a live contradiction. For an implementation it means one thing: do not copy
that flag byte blindly for a non-zero mode.

### 6.1 Which bands are allowed: command 0xFF79

The bands a device will use are carried as a **one-byte mask**, and the mask is built from an
8-entry byte table, one entry per band, where **bit N is set exactly when entry N is not
0xFF** [fw rkof 0x97fa4; entries 0-3 via 0x1b351c, entries 4-7 read at +4..+7]. A slot holding
0xFF means that band is unavailable.

The host side sends the mask to its own controller as vendor command **0xFF79**, one parameter
byte [fw rkof 0x24b04, command id 0x379 through the queued sender 0x23a60]. Every caller
immediately sends the same byte again as id **0x37A** [fw rkof 0x249e0], so the two always
travel as a pair. The controller's handler is found through the same binary-searched command
table as 0xFE50 [fw acib dispatcher 0x18964, keys 0xf1928, handlers 0xf1230, 446 entries
compared under the mask 0x3FFF]: 0xFF79 resolves to the handler at acib 0x193f4, 0xFF7A to
0x19528, 0xFF7C to 0x19704, 0xFFBD to 0x1926c. A caution for anyone repeating the lookup:
because the comparison is masked, a hit must be checked against the full key. 0xFF75, for
instance, is absent, and the search lands on the unrelated key 0x7F75. The aliasing cuts both
ways, so a key that does not look like its opcode can still be the right entry: the key for
0xFF77 is stored as 0xBF77, two high bits apart, and its handler is the one that command
reaches. The handler table ends exactly where the key table begins, 446 words at 0xf1230
against 446 halfwords at 0xf1928, and the keys are strictly increasing once masked, which is
what makes the search valid.

The handler reads the mask at the parameter offset and gates it on a feature bit [fw acib
0x19430]. With the bit clear it completes with the standard code 0x0C. With the bit set, **a
mask of 0 is refused and any mask with bit 3 set is refused**, both with 0x11. Otherwise it
posts an internal event and then walks every active link, comparing that link's own band byte
against the new mask, and the in-use mask against its complement [fw acib 0x1947a..0x1949e].

The mask is also recomputed on demand: a feature-image module named `drvcomm` [fw rkof vtable
0x1fb8c0, name at 0x1fb8de] takes a message whose body is exactly 8 bytes [fw rkof handler
0xc37ec], derives the mask from it and sends both commands.

### 6.2 Allowed bands on the air

A peer's mask travels in an **extended link-manager PDU: escape opcode 0x7B, extended opcode
0x17, one payload byte**, the sender's own mask.

The transmit side proves the pairing. The controller loads its own allowed-band byte and sends
it with the escape opcode and the extended opcode as literal constants [fw acib 0x2992a:
device state 0x10002d68, own mask at +0x1b2, extended opcode 0x17, length 1, escape opcode
0x7B, then the send builder 0x3ad7c]. The same feature bit that gates the command of section
6.1 gates this send as well [fw acib 0x29916, byte at 0x10003290, bit 5, ANDed with the byte
at the link's 0x47, which is the peer's feature word from the exchange of section 5.8]. So a
peer that has not answered that exchange is never sent this PDU.

The receive side matches: the parser is ordinary LMP, with the opcode in the first byte shifted
right by one, the transaction id in its low bit, and the extended opcode in the following byte,
taken only for opcodes 0x7B and above [fw acib 0x3af66, 0x3af6a, 0x3afa4, 0x3af9c..0x3afb4].
The body length of 2 is checked [fw acib 0x3b558], the extended opcode selects the handler [fw
acib dispatch 0x3bc04, table 0x3bc14, index 22], and the payload byte is read as the mask [fw
acib 0x3d55a, then 0x29ab4].

So both ends announce their mask with the same PDU, and each applies the other's only through
the intersection described below.

The two escape spaces are numbered independently: extended opcode 0x17 also exists behind
escape 0x7F as an unrelated message [fw acib 0x262e6]. For two messages this controller emits
in both spaces, the extended opcodes differ by 0x11 (0x7F/0x1F against 0x7B/0x0E, and 0x7F/0x20
against 0x7B/0x0F) [fw acib 0x28fa8, 0x2906a, 0x29236], but that is two observations, not a
rule.

What arrives is not adopted blindly. The received mask is ANDed with the device's own
allowed-band byte and ignored if the two do not intersect; only then is it stored as that
link's band byte [fw acib 0x45360, called only from 0x29af2 inside the PDU handler 0x29ab4].

What each bit means is section 6.3.

### 6.3 What each band bit means

The bit positions of the mask are a band index, 0 to 7, and the same index space is used by
five commands. 0xFF7C takes it as a plain index to set a link's current band, testing it
against the device's own mask by shifting the mask right by the index [fw acib 0x19832] and
storing it at the link's band-index byte, with 8 meaning none [fw acib 0x197f4, 0x1984c].
0xFF78 reads it back, turning a band byte into an index with a count-leading-zeros and a
subtraction from 31 [fw acib 0x193a0]. 0xFFB3, which the firmware's own test command
`set_test_band_and_range` sends [fw rkof 0x668b6, command id 0x3B3, three bytes], rejects
index 3 before doing anything else [fw acib 0x18d10] and then indexes a band table. 0xFF8E
takes up to eight (index, first channel, length) triples and validates each against the same
table [fw acib 0x19f9c].

That table is a constant in the controller image: **8 records of 7 bytes** at acib 0xf52e8,
indexed by the band index [fw acib 0x407ec, the record stride computed as index*8 - index].
Its fields are a start frequency in MHz (2 bytes), a band width in MHz, a channel count, a
channel bandwidth in MHz, a channel spacing in MHz, and the band's first channel number in a
single global numbering. The one function that uses it as a plan computes a channel's
frequency as `(channel - first) * spacing + (start - correction)`, where the correction comes
from a RAM twin of the table, and returns the channel bandwidth alongside [fw acib 0x407dc,
its only caller 0x40abc, RAM table 0x10000640 with 6-byte records whose +2 is the usable
channel count].

Decoded, that is the whole band plan:

| bit | start | width | channels | channel bw | spacing | first channel | span | band |
|---|---|---|---|---|---|---|---|---|
| 0 | 2401 | 82 | 13 | 22 | 5 | 0 | 2401-2483 | 2.4 GHz ISM |
| 1 | 5150 | 100 | 5 | 20 | 20 | 13 | 5150-5250 | U-NII-1 |
| 2 | 5735 | 120 | 6 | 20 | 20 | 18 | 5735-5855 | U-NII-3 |
| 3 | all bytes zero | | | | | | | refused everywhere |
| 4 | 5945 | 100 | 5 | 20 | 20 | 24 | 5945-6045 | U-NII-5, lowest quarter |
| 5 | 6045 | 120 | 6 | 20 | 20 | 29 | 6045-6165 | U-NII-5 |
| 6 | 6165 | 120 | 6 | 20 | 20 | 35 | 6165-6285 | U-NII-5 |
| 7 | 6285 | 140 | 7 | 20 | 20 | 41 | 6285-6425 | U-NII-5, highest quarter |

All frequencies in MHz. Four internal checks hold, each of which a wrong field assignment
would break: `width = (channels - 1) * spacing + channel bandwidth` in all seven defined
records; each 6 GHz band's start plus its width is the next band's start, with the last
ending at 6425, the U-NII-5 upper edge; the first-channel numbers form one contiguous
numbering, 0, 13, 18, (none), 24, 29, 35, 41, each the previous band's first plus its channel
count, ending at 47 for 48 channels in all; and the spans contain the extents Apple's own FCC
exhibits report for these earbuds, 5157-5245 in band 1 and 6108-6420 inside bands 5 to 7.
Band 4 lies entirely outside what these earbuds are granted, so the table is the radio
family's plan and the regulatory selection narrows it, which is what the country-code commands
of section 6.4 are for.

**Band 3 is empty**, which is why every path refuses it: its record is seven zero bytes, and
three separate places reject or skip the index [fw acib 0x18d10, the 0xFF79 gate, and the walk
at 0x2999c]. The gap between band 2's top and band 4's start is where U-NII-4 lies, so the
slot looks reserved for it, but the record is empty and nothing further is claimed.

**Band 0 is a fallback.** A link's usable mask is the intersection of the two devices' masks,
and the firmware then clears bit 0 unless the intersection is exactly that bit, in two places
[fw acib 0x47014, 0x4702e]. So the 2.4 GHz band is used only when it is the only band both
ends allow.

### 6.4 The rest of the band command family

| Command | Parameters | Handler, and what it checks |
|---|---|---|
| 0xFF77 | 1 byte | the switch for the whole family: sets or clears bit 5 of the feature byte that gates every band command [fw acib 0x192dc] |
| 0xFF78 | 2 bytes, a handle | returns a link's current band as an index [fw acib 0x19324] |
| 0xFF7C | handle and 1 byte | sets a link's band, index 0-7 or 8 for none; 0x11 if out of range, 0x0C if the gate is clear [fw acib 0x19704] |
| 0xFF8E | count, then 3 bytes each | per-band channel ranges; each index must be below 8, each range inside that band's channel count [fw acib 0x19f9c] |
| 0xFF92 | count 1-8, then that many bytes | one country code per band: **each entry is either below 8 or exactly 0xFF**; 0x11 otherwise [fw acib 0x1e074] |
| 0xFD31 | 1 byte | one country code for the device, **0 to 7**; 0x12 otherwise [fw acib 0x1e0de] |
| 0xFFB3 | 3 bytes | test band and range; index 3 refused, the range checked against the band's channel count [fw acib 0x18d04] |
| 0xFDF3 | 1 byte | not a band command, but its sibling: the same shape sets the other four bits of that same feature byte, bit 6 among them, which is the gate on 0xFE50's mode procedure (5.8) [fw acib 0x16b20] |

0xFF92 is what the earbud's own side sends when it recomputes the table [fw rkof 0x248f8,
command id 0x392, eight bytes], and it closes the loop on the mask: a country code is 0 to 7,
there is one per band, and 0xFF means that band is unavailable, which is exactly the
convention the mask builder of section 6.1 reads. Before building the mask, the earbud clamps
each of the eight slots against a per-band byte of its own state [fw rkof 0x97f20], so the
mask that goes out is the intersection of what the region allows with what the device is
configured for.

### 6.5 The second band PDU: escape 0x7B, extended opcode 0x1C

Where 0x17 says which bands are allowed, 0x1C says which channels within them. The whole PDU:

| offset | size | meaning |
|---|---|---|
| 0 | 1 | header: transaction id and escape opcode 0x7B |
| 1 | 1 | extended opcode, 0x1C |
| 2 | 3 each | one triple per band: band index, first channel, length |

**There is no count field on the wire.** Both ends keep the count in their own field block and
read it off the length instead: the sender copies three times its stored count from the block
and reports a total of 3n + 2 [fw acib 0x3abda], and the receiver copies the triples into the
same place in its own block and writes back `(PDU length - 2) / 3` as the count
[fw acib 0x3bfc2]. The two are mirror images, and the length is the count.

The sender walks the band indexes behind the same feature gate as 6.2, skips index 3 and any
band whose range pair is zero, and packs **at most five** triples [fw acib 0x29964, escape and
extended opcode as literals at 0x299d4]; the receiver refuses a count above five
[fw acib 0x29b38, bound at 0x29b62]. It then stores each pair into a per-link array of eight
two-byte entries, indexed by the band, at the link's offset 0x49F
[fw acib 0x29b8c..0x29b9a]. The division truncates, so a PDU whose length is not 2 plus a
multiple of three keeps the triples it has and loses the remainder rather than being refused.

The same (index, first, length) shape is what command 0xFF8E takes from the host, and the
range it validates against is the band's channel count from section 6.3, so all three places
agree on what a range means.

Where this message sits among the rest of its escape space, and which extended opcodes that
space implements at all, is section 5.6.

### 6.6 The other vendor commands that reach the ULLA session

0xFE50 is not the only command on this interface, and the rest can be found mechanically
rather than guessed at. The ULLA session object has one accessor [fw acib 0x2f52c], the same
one 0x75's and 0x76's receivers use (5.2); taking every call to it in the image and asking
which command handler it falls inside gives **eleven commands**, nine of them not mentioned
anywhere else in this document:

| command | handler | parameters its handler reads | the earbud's own manager sends it |
|---|---|---|---|
| 0xFE16 | acib 0x1ebf4 | a byte and a word, bounded below | yes, five bytes: 1, then a word of its own state [fw rkof 0x27c08] |
| 0xFE4C | acib 0x1d6cc | a halfword [fw acib 0x1d6d6] | no |
| 0xFE4E | acib 0x1e7b8 | a byte, 0 or 1, and three more bytes when it is 1 | no |
| 0xFE50 | acib 0x1da40 | the seven bytes of section 6 | yes, the mode change of section 6 |
| 0xFE52 | acib 0x1dd88 | none at all | yes, with none [fw rkof 0x26940] |
| 0xFE53 | acib 0x1de62 | none at all | no |
| 0xFE55 | acib 0x1dde4 | a word [fw acib 0x1de08] | no |
| 0xFE57 | acib 0x1d900 | a word [fw acib 0x1d904] | no |
| 0xFE58 | acib 0x1d952 | none that its handler reads | yes, four zero bytes [fw rkof 0x26866] |
| 0xFE7B | acib 0x1d44e | a byte [fw acib 0x1d45e] | no |
| 0xFF79 | acib 0x193f4 | the mask byte of 6.1 | yes, the allowed-band mask of 6.1 |

Each parameter column is what the handler loads from the command buffer's +0xB onwards, which
is where this interface's parameters start (6 and 6.1 use the same offset). 0xFE58 is worth
the note: its handler reads none, although the manager sends four zero bytes with it.

**Five of them share one shape**, which is the useful part. 0xFE52, 0xFE53, 0xFE55, 0xFE57 and
0xFE58 all begin with the same gate chain [fw acib 0x1dd88, 0x1de62, 0x1dde4, 0x1d900,
0x1d952, all through 0x1dfe0]:

1. a device predicate [fw acib 0x62044]. When it holds, the handler takes a path of its own
   that does not involve the session object at all.
2. otherwise the session has to be **active**: the state byte 2 with a flag set, which is
   exactly the check 0x75's receiver makes [fw acib 0x2f550]. If it is not, the command
   completes with **0x0C**, command disallowed.
3. then the link's **role byte** decides which of two branches runs [fw acib 0x2f566], the
   same test again from 5.2.

What each one does on that path is a different call into the session module, and most of them
are one property of the session object:

| command | on the session path | the status when a gate fails |
|---|---|---|
| 0xFE4C | one call with the halfword [fw acib 0x48938] | **2** when the session is not active, 0x0C on the role branch |
| 0xFE4E | its own pair of handlers, by the first parameter [fw acib 0x1e688, 0x1e88c] | - |
| 0xFE52 | property **0x1B0** or **0x1B2**, the role deciding which [fw acib 0x4883c, 0x4870e] | 0x0C |
| 0xFE53 | **reads** property **0x1AF** and reports it to the host [fw acib 0x48856 into 0x11f04] | 0x0C |
| 0xFE55 | writes property id **2** with the word, and answers **0x11** if that write fails [fw acib 0x79448 at 0x1de0e] | 2, or 0x0C |
| 0xFE57 | property **0x1B1** with the word [fw acib 0x486f8] | 0x0C |
| 0xFE58 | property **0x1B3**, then reports to the host [fw acib 0x48722 into 0x11e8c] | 0x0C |
| 0xFE7B | property **0x1AB** with the byte, and no role branch at all [fw acib 0x1d47c] | - |

So four properties, 0x1B0 to 0x1B3, are driven by three of these commands, one command reads a
fifth and one writes a sixth; and the refusals are not uniform, which matters for a host: 2
(no connection), 0x0C (command disallowed) and 0x11 all appear. This is a family of commands
for a session that already exists, gated on the session being active, which is the same
division 5.2 found on the air: 0x73 creates, and 0x74 to 0x76 are for a session that is there.

0xFE4E is the one whose parameters change shape: its first byte has to be 0 or 1, and when it
is 1 the handler reads a halfword and a byte after it [fw acib 0x1e7ce to 0x1e810], so the
command is one byte long or four.

**0xFE16 is the one whose parameters are fully readable**, and it is worth the detail because
its bound is a measurement rather than a guess. It takes a byte and a 32-bit value, which is
exactly the five bytes the earbud's manager sends. The value has to be **at least 0x7D (125)
and at most 0x2710 (10000)**, and outside that the command completes with **0x11**; then the
same device predicate decides between two handlers, with the session-active check and 0x0C on
the other path [fw acib 0x1ebfc to 0x1ec4a, the two handlers 0x1ebb4 and 0x1eb0a, which pass
both parameters into the link layer at 0x5ed34 and 0x5ed54]. A single value bounded to
125 to 10000 looks like a time or an interval, but nothing in these payloads says in what
unit, so this document records the bound and not a meaning.

What the individual commands mean is not traced, and naming them would be interpretation. The
table is published because it bounds the host interface: an implementation that answers these
eleven, with 0x0C where the gates fail, has covered what a host can ask about an ULLA session
in this build. `openulla.hci.ULLA_SESSION_COMMANDS` holds the list.

## 7. The audio layer, and what is not known

Once the mode change succeeds the radio carries the audio. The parts that are established:

- The two Apple modes are pi/4 DQPSK and ride in DH5-shaped, five-slot packets at 4 and
  8 Mbit/s, named 4-DH5 and 8-DH5 in Bluetooth's own packet naming. Source: the narrowband
  test report for FCC ID BCGA3240, an Apple MacBook Air, which labels its measurements
  "pi/4 DQPSK (4-DH5)" and "pi/4 DQPSK (8-DH5)". That tells you the packet family and the
  symbol rate class, not the bit layout.

- The earbud's DSP firmware contains the ULLA receive clients, a normal one and a lite one,
  and an encode node that selects behaviour by a version byte, 0x10 for 1.0 and 0x11 for 1.1
  [fw sbd1 0x9d334; the DSP image is AArch64].
- That version is a field of the node, at its offset 0x2bc, and it is exposed on the node's
  property interface: property id 0x259 reads it, and id 0x25a copies out a 56-byte block
  assembled from ten other fields of the same node [fw sbd1 0x9d0dc the getter's switch,
  0x9d0fc..0x9d14c the copy]. Every access to the version field in the image is a read, so it
  arrives from outside the DSP.
- It handles frames and subframes per version, with a dedicated routine for version 1.1
  [fw sbd1 0x9e070, 0xb21d4], and the per-frame path branches on the same version before
  looking at a per-link mask and a frame counter [fw sbd1 0x9e4f8].
- In the controller, the audio stream module programs up to four DMA transfers per buffer from
  a packed descriptor: a buffer count, a length with a 1 KB boundary rule, and per-buffer
  flags [fw acib 0xdabcc].

**[open] The over-the-air packet format.** The air packet is produced by radio hardware
configured through registers; the PHY image contains no symbols or strings, and the register
semantics are not public. It is not recoverable from the firmware alone.

**[open] The codec, the sample rate, the frame size and the bit depth, with a reason.** None
of them is a constant in the DSP image, and that was checked rather than assumed: no
sample-rate value (48000, 44100, 96000, 32000, 24000, 16000, 8000) appears as an immediate or
as a data word anywhere in the audio-node region of that image; the DSP's own log texts
resolve to addresses outside the payload, in the separately shipped text table; and the DSP
data payloads that ship beside the code are filter coefficients, RIFF blobs and a manifest of
per-blob hashes, not a graph description that names formats. So the audio format is
configuration the node is handed at runtime, which makes it a measurement question, not a
reading question. The DSP image names no audio codec anywhere relevant to this path either.

**[open] Whether the audio payload is encrypted.** The setup adds no encryption of its own and
establishes no key material (section 5.4), but whether the radio keeps applying the ordinary
link cipher after the mode change is a hardware question these files cannot answer.

**[open] The maximum ULLA packet size.** The firmware carries a message about it, but only
through a numbered trace mechanism whose text table ships separately.

## 8. What an implementation can do today

With the above, a host that is already paired with the earbuds can [measured]:

- open AAP service 4 and negotiate versions,
- ask the earbuds to start a ULLA session with itself and receive the success status,
- subscribe to the report classes,
- observe that the session machine advances and refuses a second start until it is reset.

All four of those are what `openulla/` does, and `tools/ulla_demo.py` prints every byte of
one run of them against a simulated earbud.

It cannot complete the link, because the next step is the link-manager exchange of section 5,
which must be emitted by the source's own Bluetooth controller. Commodity controllers neither
expose that interface nor implement the required radio modes, and their firmware is signed.

### 8.1 The order to implement it in

For anyone writing another implementation, this is the sequence that the sections above
support, with the part each one proves. Nothing below is advice; each step is a layout or a
rule that was read from the firmware or measured.

1. **Be paired and connected.** The manager resolves the peer's address to an existing ACL
   connection and fails fatally if there is none (3.5), so an ordinary Bluetooth connection
   comes first.
2. **Open AAP service 4 on PSM 0x1001** with the 16-byte open request, and read the version
   pair back out of the reply; it decides the wrapper's class width (2.2, 2.6).
3. **Send opcode 0x36 with one mode byte.** It is taken only while the manager is idle, and
   otherwise ignored in silence (2.4). The session is the one the L2CAP channel identifies, so
   there is no address to fill in (2.3).
4. **Expect the status reply,** 0 once the manager reaches the state that waits for its local
   audio path, or 0x1776 if its sibling transport is not ready (2.5, 3.6).
5. **Subscribe with the 0x17 wrapper and class 0x00200000** if you want the reports, and parse
   their three-byte header (2.6).
6. On the controller side, if your controller lets you: **answer the feature exchange** first,
   because every band message and the mode procedure are gated on the AND of the two words
   (5.8), and **set your own half of that AND** with 0xFF77 for the band family and 0xFDF3 for
   the mode procedure, which is what those two commands do (5.8). Then announce your allowed
   bands with the pair of commands and, if you use them, the per-band channel ranges
   (6.1 to 6.5).
7. **Emit the setup request** and be ready for the four checks and their reasons (5.1, 5.4),
   or to receive one and answer it; the accept decision reads none of the PDU's own fields.
8. **Report the outcome to your host** as the nine-byte vendor event, and expect the manager
   to move on the link-up event (6, 3.6). Which result you report is not a choice: the
   firmware has a different one for a local failure, a peer's refusal and a link that went
   away in each of the two phases (5.9).
9. **Keep the procedure's own state** if you want to behave like the firmware rather than
   merely interoperate: it answers an unexpected event by staying where it is, waits for two
   independent conditions before sending 0x74, and three before it goes on (5.9).
10. **Do not expect to send back everything you receive**: a third of service 4's opcodes have
    no inbound handler at all, the status reply among them (2.4).

Steps 1 to 5 are what a Linux host can do today, and `openulla/` does them. Steps 6 to 8 need
a controller that exposes its link-manager interface, which is the wall this project is at.
