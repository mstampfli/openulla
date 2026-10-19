# Measurements on a Linux host

Everything here was observed against real AirPods Pro 2 (USB-C) from a Linux laptop with an
Intel AX211 controller, BlueZ 5.82 and PipeWire 1.4, as an ordinary unprivileged user except
where a capture is mentioned. Device addresses and accessory identifiers are redacted; they
are not needed to reproduce anything.

Reproduce the protocol exchanges with `tools/aap_probe.py` (see `--help`).

## 1. The accessory protocol accepts a non-Apple host

Sent on L2CAP PSM 0x1001 from a plain socket:

```
00 00 04 00 01 00 02 00 00 00 00 00 00 00 00 00
```

Received:

```
01 00 04 00 00 00 01 00 03 00 00 00 00 00 00 00 00 00
```

Open reply, status 0, accessory version 1.3. The version pair in the reply is exactly the one
the firmware's registration writes, which is an independent live confirmation of a value that
had been read statically beforehand.

No authentication, no challenge and no shared secret were involved. Immediately afterwards the
earbuds volunteered a stream of their own service-4 reports, every one of them framed as
`04 00 04 00 <2-byte opcode> <payload>`, which confirms the frame layout of `protocol.md`
section 2 on the wire. Opcodes seen unsolicited: 0x02, 0x09, 0x17, 0x1d, 0x2b, 0x37, 0x4e,
0x53, 0x55, 0x63. Their contents include accessory descriptors and identifiers and are not
reproduced here.

## 2. A Linux host can start a ULLA session

Sent:

```
04 00 04 00 36 00 00
```

Received:

```
04 00 04 00 37 00 00 00 00 00
```

Opcode 0x37 with status 0. In the firmware that status value has exactly two possible origins,
and the one that produces 0 sits inside the handler for the state that waits for the local
audio path to quiesce. So the session machine really advanced from idle through host setup to
that state before answering. The other possible value, 0x1776, would have meant the earbud's
transport to its sibling was not ready.

## 3. The state machine, proven from the outside

In one channel, four commands, listening after each:

| sent | received |
|---|---|
| `04 00 04 00 36 00 00` start | `04 00 04 00 37 00 00 00 00 00` status 0 |
| `04 00 04 00 36 00 00` start again | nothing |
| `04 00 04 00 3c 00 00 00 00 00` stop | nothing |
| `04 00 04 00 36 00 00` start again | nothing |

Closing the channel and reconnecting makes a fresh start return status 0 again; confirmed
twice independently.

Each outcome is what the recovered description predicts, which is why this counts as evidence
rather than coincidence:

- The second start is ignored because a start is only acted on while the manager is idle, and
  the code path that ignores it sends no reply.
- The stop is ignored because the stop event has no row in the states the machine had reached,
  so the dispatcher finds nothing and leaves the state alone.
- Recovery on channel close happens because the disconnect path posts the disconnect event,
  which returns the machine to idle from the states this test reached. Those are the states
  before a session is open, and they are exactly the ones whose rows answer that event; from
  session open onwards the table answers the link-down event instead, so what resets a
  manager that got further is not established (`protocol.md` section 3.4).

The state change is therefore observable from outside the earbud, not merely inferred.

## 4. Nothing further happens without a real source

After a successful start, three independent observations agree that no further setup traffic
reaches a host:

1. The accessory channel stayed silent for 150 seconds. Exactly one frame arrived, the status.
2. An HCI capture across the whole run shows the earbud never touched the Bluetooth link: no
   supervision-timeout change, no request to leave power saving, no vendor event from it. The
   link was even allowed to enter sniff mode 40 seconds after the start. The only vendor event
   in the capture came from the host's own Intel controller during a later reconnection.
3. Subscribing to the report classes changes nothing, because those reports are statistics of
   a running link. The subscribe frame
   `04 00 04 00 17 00 00 00 20 00 04 00 00 00 00 00` was accepted without error and the
   session stayed healthy, but with no link established there is nothing to report.

This matches the recovered description exactly: the earbud is the responding side. It accepts,
moves to controller setup, and waits for a link-manager exchange that only a source's own
Bluetooth controller can emit.

## 5. Opening the service is what encrypts the link

A capture shows that connecting to PSM 0x1001 makes the host run authentication and then
enable encryption, reported as AES-CCM. Before the connection the link was up and unencrypted;
after it, encrypted. Worth knowing when interpreting link state during an experiment.

## 6. Observed side effect

In two runs the baseband link dropped roughly 30 seconds after the probe closed its channel,
and the host reconnected by itself about 1.6 seconds later. The likely explanation is an
ordinary idle disconnect, since no audio profile was connected in either run, whereas the link
stayed up in an earlier session when audio was in use. The cause was not isolated, so it is
recorded as an observation rather than a conclusion. Nothing was lost and no pairing was
affected.

## 7. Negative results worth not repeating

- **Stereo audio and the microphone are mutually exclusive on this hardware** (these earbuds
  with an ordinary Intel controller). Tested both orderings with the kernel's own link list as
  the witness. Acquire the audio transport first and the voice link never establishes, with
  repeated transport failures logged and a microphone capture of exact zeros. Establish the
  voice link first and acquiring the audio transport fails with an input/output error.
- **A profile name is not evidence of a link.** An early run looked like success because the
  host-side card profile said "headset" while an audio transport was active; the voice link
  was not up at all. `tools/bt_link_state.py` prints the kernel's own link handles so this
  cannot be misread.
- **Aggregates hide structure.** The same run's microphone recording averaged three live
  seconds against ten dead ones into a healthy-looking peak. A per-second view showed the
  voice link dying the moment the transport was acquired.
- **Link-manager traffic never reaches the host.** No amount of host-side capturing will show
  the setup PDUs of `protocol.md` section 5.

## 8. Only one host at a time, and the earbuds say so

Attempting to ask the earbuds a question while another host holds them does not work, and the
failure is theirs rather than the asking host's.

Setup, deliberately stripped to the minimum on the Linux side: reconnect, set the host audio
server's card profile to off, then disconnect every Bluetooth audio profile through BlueZ
(A2DP sink, both AVRCP roles, handsfree), leaving a bare encrypted baseband link held open
only by the accessory-protocol channel itself. Verified in that state: link up and encrypted,
no media transports, no audio card.

In that configuration the channel worked normally and took 33 frames from the earbuds. An
iPhone was then connected to the same earbuds. They reset the channel and tore down the link:

```
HCI Disconnect Complete, handle 256, status Success,
Reason: Remote Device Terminated due to Low Resources (0x14)
```

So the eviction is the earbuds' own decision and they report the reason. One observation of
that reason code, consistent with the owner's independent prior experience that the two
devices fight over the earbuds.

The consequence matters for anyone planning experiments: a second host cannot observe whether
a first host is using this link by asking the earbuds, because connecting the second host
evicts the first and resets the session. The test cannot distinguish its two outcomes, which
is worse than it being merely inconvenient. Capturing HCI on the driving host is the approach
that can work.

## 9. What our own controller asks for, and what it cannot ask for

Read back out of captures already taken, so no new run was needed. Every connection this host
makes to the earbuds carries the same packet-type request:

    HCI Command: Create Connection
      Packet type: 0xcc18
        DM1 may be used / DH1 may be used / DM3 may be used
        DH3 may be used / DM5 may be used / DH5 may be used

A second capture shows the link then settling on the enhanced-data-rate packet-type table with
five-slot packets, and the remote's standard feature pages coming back with nothing vendor
specific in them. So BlueZ on this Intel controller asks for the whole standard set and
nothing else; it makes no vendor-specific request of any kind.

The structural point matters more than the particular value. That field is 16 bits, of which
twelve are defined ACL packet types. BlueZ's own header (`/usr/include/bluetooth/hci.h`) gives
them as 2-DH1 0x0002, 3-DH1 0x0004, DM1 0x0008, DH1 0x0010, 2-DH3 0x0100, 3-DH3 0x0200,
DM3 0x0400, DH3 0x0800, 2-DH5 0x1000, 3-DH5 0x2000, DM5 0x4000, DH5 0x8000. The six
basic-rate bits mean "may be used" and the six enhanced-rate bits mean "may not be used", which
is how `btmon`'s own decode table words them. 0xcc18 is exactly the six basic-rate bits with
all six exclusion bits clear: the whole standard set, basic and enhanced rate alike.

The largest packet type that field can name is 3-DH5, at 3 Mbit/s. There is no bit for 4-DH5
or 8-DH5, so no standard host can ask for Apple's HDR modes over this interface, whatever its
controller implements. The Bluetooth Core Specification defines the field itself (Volume 4
Part E, the Create Connection command); the bit values above are quoted from BlueZ, which is
the stack that produced the measurement. This is consistent with the earbud firmware reaching
those modes through a vendor command and link-manager PDUs instead (`protocol.md` sections 6
and 5), and it is why a host-side packet-type request is not a route to this link.

## 10. What a controller that does not implement these commands answers

Measured 2026-10-19 on the same host (Intel AX211, BlueZ 5.82), with no connection open. Four
vendor commands sent with `hcitool cmd`, three of them in the range the earbud firmware uses
and one of the controller's own:

| sent | answer | status |
|---|---|---|
| OGF 0x3F, OCF 0x253 (0xFE53), no parameters | Command **Status**, event 0x0F | **0x0C** |
| OGF 0x3F, OCF 0x255 (0xFE55), four parameter bytes in range | Command **Status** | **0x0C** |
| OGF 0x3F, OCF 0x3FF (0xFFFF), an opcode nobody implements | Command **Status** | **0x0C** |
| OGF 0x3F, OCF 0x005, the controller's own read-version command | Command **Complete**, event 0x0E | 0x00, with its 13-byte answer |

Three things follow, and the third is the useful one.

**The parameters are never looked at.** 0xFE55 with a value inside the bound of
[protocol.md](protocol.md) 6.6 is refused exactly like the opcode with no parameters at all,
so this controller rejects by opcode before parsing anything.

**0x0C is this controller's blanket answer** for any unimplemented opcode in the vendor group,
and it answers its own vendor commands normally in the same breath, so nothing is wrong with
the path.

**So 0x0C alone proves nothing about ULLA.** It is the same code 6.6 documents for a session
command whose gate fails, which means a tester cannot read "command disallowed" as "implemented
and refused". Two things do discriminate:

- **the event**. This controller answers Command Status (0x0F); the earbud firmware answers
  these commands with Command Complete carrying its own status byte and, for two of them, a
  payload (6.6, 6.7).
- **0xFE55's bound**, which has three distinguishable outcomes rather than two: 0x0C for a
  controller that does not implement it, **0x11** for one that does and was handed a value
  outside the signed range -800 to +800, and success inside it.

Nothing was disturbed: the adapter stayed `UP RUNNING` and no connection existed before or
after. A single unknown vendor command is refused by the controller's own dispatcher, which is
what the first three rows show.

`tools/ulla_probe.py` is this measurement as a command. It sends the control first and then
every prediction, and prints what each answer would mean on a controller that implements them;
on this host it ends with "this controller implements none of them", which is the result above
and the baseline anything else is read against. Run on 2026-10-19 against the same adapter, it
reproduces all six rows and leaves the adapter `UP RUNNING`.

## 11. The mode byte is not validated, and the lite mode is accepted

Measured 2026-10-19 08:30 against real earbuds on firmware 9A348, with
`tools/aap_probe.py --device MAC --predict`. Three start-session commands, **each on its own
channel**, because a start is taken only while the manager is idle and closing the channel
resets it (2.4, section 3 above): mode 0 as the control, then mode 2 and mode 7.

| mode byte | what it is | status reply |
|---|---|---|
| 0 | the control, the documented path | **0x0000** |
| 2 | `ULLA_LITE_MODE_ON`, the third mode the firmware names | **0x0000** |
| 7 | outside the three names altogether | **0x0000** |

So the prediction of [protocol.md](protocol.md) section 6 holds: **nothing validates the mode
byte.** A host may ask for the lite mode, and a host may ask for a mode that does not exist;
both are answered exactly like mode 0, with the success status, and the manager advances the
same way. Any bound on that byte lives in whatever Apple's own source sends, not in the
earbud.

One methodological note, because it changed the measurement. The first run of this test
compared the **number of frames** that came back, and a real pair volunteers a stream of its
own status frames on the same channel - battery and state updates, opcode 0x0009 - so "one
frame arrived" says nothing about which one. The tool now finds the status reply by its opcode
and compares that; the table above is from the corrected run, and the frame-counting version
would have reported the same verdict for the wrong reason.

## 12. The advertised capabilities are the firmware's own table, byte for byte

Measured 2026-10-19 08:35 against real earbuds on 9A348, read out of BlueZ over D-Bus and
compared with the bytes [ulla.md](ulla.md) section 4.6 publishes from the firmware image. All
three endpoints the sink registers appear, and all three agree:

| endpoint | codec | advertised | the firmware's table |
|---|---|---|---|
| sep1 | 0x00 SBC | `3f ff 02 35` | the same |
| sep2 | 0x02 AAC | `c0 01 8c 83 e8 00` | the same |
| sep3 | 0xFF Apple vendor | `4c 00 00 00 01 80 00 80 00 8c 00 83 e8 00` | the same |

This is the cross-check those bytes had been waiting for. What was read out of the firmware at
the addresses 4.6 cites is exactly what the device puts on the air, which is the strongest
confirmation available for a table of this kind without a second implementation. The vendor
endpoint's **eight codec-specific bytes** are therefore confirmed as `00 80 00 8c 00 83 e8 00`;
what they *mean* is still open, and 4.6 says what can and cannot be read out of them.

`tools/bt_link_state.py MAC` prints this comparison as part of its output, so it is one command
and not a manual diff. The bytes it compares against live in `openulla/a2dp.py`, which is also
what `tools/verify_claims.py` checks the image against, so there is one copy of them rather
than two that could drift apart.
