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

The same report goes to the **sibling** instead when no host session is open, with a four-byte
header: a constant 4, then the type, then those same two bytes, sent as message 12 of section
4 [fw rkof 0x27ef4..0x27f26]. What the manager's two fields mean was not established; its
current state is a different field, at its offset 0xC [fw rkof 0x25f32, the dispatcher's own
index].

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
bytes at offsets 7 and 8, and do not expect a third flag byte to arrive.

### 5.2 Opcode 0x74, the configuration

Total 7 bytes [fw acib 0x3a832]:

| offset | size | meaning |
|---|---|---|
| 0 | 1 | header: transaction id and opcode 0x74 |
| 1 | 4 | configuration value, read by id from the controller's parameter table |
| 5 | 2 | configuration value, read by id from the controller's parameter table |

In the build examined, the two parameter getters for these ids are stubs that return nothing
[fw acib 0xcf9ac, 0xcf9ae], so this firmware contributes zeroes for both. The receiver passes
the two values straight to its session object with no checks of its own [fw acib 0x6352c].

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

Each check has its own reason, and the receiver keeps it in the low nibble of the link's state
byte [fw acib 0x67654]:

| check | what it reads | reason when it fails |
|---|---|---|
| 1 | a feature-enabled byte [fw acib 0x62074] | 1 |
| 2 | object ready, through the session's function table [fw acib 0x62030] | 2 |
| 3 | a busy flag [fw acib 0x62154] | 3 |
| 4 | link state, role and link count [fw acib 0x620c8] | 4, 5 or 6 |

Check 4 returns its reason directly: 6 when the link is absent, or its state byte is not 0xC,
or a flag at its offset 0x185 is set; otherwise 4 or 5 from the role and connection-count
tests [fw acib 0x62102 onwards]. Zero means it passed.

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

**2 is missing from that list on purpose.** The secondary numbering skips it: its slot in the
table points at the same "invalid reason" text as any unknown value, while the primary list is
contiguous and its extra member at 0x82 is "already connected", which cannot happen to a
secondary. And 2 is exactly what check 2 above produces, so the value does travel; it is the
host-side name table that has no name for it.

Values 1 to 6 are what this receiver puts on the air. The 0x81 to 0x87 names belong to the
primary role's own failures; where the 0x80 bit is set was not established, and a scan of the
controller image for those immediates returns 38 hits that cannot discriminate.

### 5.5 What the sink does after accepting

It copies the parsed fields into its own context, forwards an identical 0x73 to the second
link, and only sends the accepted PDU back once both links are ready [fw acib 0x677c8,
0x303a8, 0x302f8].

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

Put against the receive side of 5.6 that sorts every extended opcode of the 0x7B space into
three groups: 0x03 to 0x09, 0x12, 0x13, 0x1D and 0x1E can be neither built nor accepted;
0x14, 0x22, 0x23 and 0x25 can be accepted but not built; and 0x24 and 0x28 can be built but
not accepted, which is the asymmetry above.

Two rows are the messages this document decodes in full, and this table is how they were
checked: 0x7B/0x17's one byte is the allowed-band mask of section 6.2, and 0x7B/0x1C's 3N
bytes are the band ranges of section 6.5, whose count the length carries.

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

So the device's feature word lives at 0x10003290 to 0x10003293 and the peer's at the link's
0x47 to 0x4A, and **an implementation that never answers the exchange has every feature gated
off**, because the AND reads its word as zero. Answering it is the first thing a source has to
do.

One quirk, for anyone comparing against a capture: when a per-link identity check matches (a
halfword of 0xF, a byte of at most 10, and a ten-byte comparison at the link's 0x4E), the low
byte of the word the earbud advertises is reduced to its bits 0, 3, 5 and 7
[fw acib 0x3684e deciding it, 0x2942c and 0x294a8 applying the mask 0xA9]. Bit 5, the band
family, survives that reduction. What the other bits of the word mean is open.

## 6. Controller interface: HCI vendor commands

The source's host starts the whole thing with vendor command **0xFE50** [fw acib table at
0xf1928, handler 0x1da40]. Parameters, after the standard 3-byte command header:

| offset | size | meaning |
|---|---|---|
| 0 | 2 | connection handle |
| 2 | 1 | copied into the setup request's last byte |
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
   (5.8). Then announce your allowed bands with the pair of commands and, if you use them, the
   per-band channel ranges (6.1 to 6.5).
7. **Emit the setup request** and be ready for the four checks and their reasons (5.1, 5.4),
   or to receive one and answer it; the accept decision reads none of the PDU's own fields.
8. **Report the outcome to your host** as the nine-byte vendor event, and expect the manager
   to move on the link-up event (6, 3.6).
9. **Do not expect to send back everything you receive**: a third of service 4's opcodes have
   no inbound handler at all, the status reply among them (2.4).

Steps 1 to 5 are what a Linux host can do today, and `openulla/` does them. Steps 6 to 8 need
a controller that exposes its link-manager interface, which is the wall this project is at.
