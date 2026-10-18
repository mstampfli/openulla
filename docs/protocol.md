# The ULLA setup protocol, as recovered

Purpose: interoperability. This is what an independent implementation needs in order to speak
to AirPods Pro 2 (USB-C) the way Apple's own source does.

Every statement below is one of:

- **[fw 0xADDR]** read from the AirPods firmware, build 9A348. Addresses are byte offsets into
  the payload named with them, as extracted by `tools/ftab.py`: `acib` is the Bluetooth
  controller image, `rkof` the RTKitOS features image, both Cortex-M at base 0. The firmware
  itself is not redistributed and no disassembly of it appears here.
- **[measured]** observed on a Linux host (Intel AX211, BlueZ 5.82) against real earbuds.
- **[open]** not established. Nothing here is inferred or filled in by guesswork.

Numbers are hexadecimal when written `0x..`. Multi-byte integers are little-endian unless
stated otherwise.

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

Those reports are link statistics, 185 and 61 bytes respectively [fw rkof 0x25bb2, 0x25bf4,
collector around 0x25ad0], so they only carry content once a ULLA link is actually running.
Report bodies [fw rkof 0x27e90]: one byte of report type, then the manager's mode and role
bytes, then the data.

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

Acceptance is the standard link-manager accepted PDU carrying 0x73 [fw acib 0x3d19c, 2 bytes
on the wire]; rejection is the standard not-accepted PDU carrying 0x73 and one reason byte
[fw acib 0x3c934, 3 bytes].

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

Reason numbers, with the names the firmware's own tables give them [fw rkof 0x1dea6a and the
selector at 0x2777c]: 0 none, 1 secondary role not initiated, 2 invalid, 3 secondary
disallowed activity, 4 secondary transport not active, 5 secondary illegal connection count,
6 secondary invalid source connection, 0x81..0x87 the same list for the primary role plus
primary already connected and primary snoop disconnected, 0xaa lite-mode transport
disconnected, 0xff not applicable.

### 5.5 What the sink does after accepting

It copies the parsed fields into its own context, forwards an identical 0x73 to the second
link, and only sends the accepted PDU back once both links are ready [fw acib 0x677c8,
0x303a8, 0x302f8].

## 6. Controller interface: HCI vendor commands

The source's host starts the whole thing with vendor command **0xFE50** [fw acib table at
0xf1928, handler 0x1da40]. Parameters, after the standard 3-byte command header:

| offset | size | meaning |
|---|---|---|
| 0 | 2 | connection handle |
| 2 | 1 | copied into the setup request's last byte |
| 3 | 2 | the configuration word of the setup request |
| 5 | 1 | flags: bits 0-1 and bit 3 go into the setup request, bit 2 elsewhere |
| 6 | 1 | 0 starts the transition; non-zero selects another procedure |

The handler refuses with the standard codes 0x0C, 0x12 or 0x17 when a feature flag is clear,
a procedure is busy, or a parameter is out of range. On success it fills the transition
context, posts the link-manager procedure, and the state machine emits the 0x73 PDU.

Progress comes back as a vendor event [fw acib 0x10548, 0x11d34]: event code 0xFF, 7 bytes,
then the connection handle and three bytes carrying the mode, the result and the reason.
Mode values [fw rkof 0x1be190]: 0 ordinary ACL, 1 ULLA on, 2 lite mode on.
Result values [fw rkof 0x1be5bc]: 0 success, 1 error, 2 failed to establish, 3 setup rejected
by peer, 4 supervision timeout error.

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
instance, is absent, and the search lands on the unrelated key 0x7F75.

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
6.1 gates this send as well [fw acib 0x29916, byte at 0x10003290, bit 5, ANDed with a per-link
byte].

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

There is also a **second band PDU on the air: escape 0x7B, extended opcode 0x1C**. The same
module and the same feature gate as 6.2 walks the band indexes, skipping 3 and any band whose
range pair is zero, packs up to five (index, first, length) triples and sends them [fw acib
0x29964, the constants at 0x299d4]. So one PDU announces which bands are allowed and the other
announces the channel range within each. The receive side of 0x1C is index 27 of the same
table at 0x3bc14 and has not been read yet.

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
- It handles frames and subframes per version, with a dedicated routine for version 1.1
  [fw sbd1 0x9e070, 0xb21d4].
- In the controller, the audio stream module programs up to four DMA transfers per buffer from
  a packed descriptor: a buffer count, a length with a 1 KB boundary rule, and per-buffer
  flags [fw acib 0xdabcc].

**[open] The over-the-air packet format.** The air packet is produced by radio hardware
configured through registers; the PHY image contains no symbols or strings, and the register
semantics are not public. It is not recoverable from the firmware alone.

**[open] The codec.** The DSP image names no audio codec anywhere relevant to this path.

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

It cannot complete the link, because the next step is the link-manager exchange of section 5,
which must be emitted by the source's own Bluetooth controller. Commodity controllers neither
expose that interface nor implement the required radio modes, and their firmware is signed.
