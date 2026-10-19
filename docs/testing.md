# What this project needs tested, and by whom

Most of what the AirPods' own firmware can answer has been answered: the setup protocol is
documented field by field in [protocol.md](protocol.md), an implementation of it is in
`openulla/`, and `tools/verify_claims.py` re-derives thirty-two groups of claims from the
firmware image itself. Some firmware questions are still open and protocol.md marks each one
`[open]` where it arises. What is left beyond those needs hardware this project does not have.

Two different kinds of hardware answer two different questions, and it is worth being precise
about which:

| if you have | you can answer | you cannot answer |
|---|---|---|
| an Apple host that drives the link (Vision Pro, possibly a Mac) | whether anything other than a Vision Pro drives it, with what parameters, and whether "HRB" is this protocol | nothing about non-Apple sources |
| a Nexus 5 or a CYW920735Q60EVB-01, the two parts that can inject these opcodes (section 2) | whether the **negotiation** completes from non-Apple silicon, and what the earbuds answer when it does not | whether audio follows: the HDR radio modes are not in commodity silicon, so the mode change cannot succeed there |
| a software radio with 20 MHz of bandwidth reaching 5.2 and 5.8 GHz | what the air format actually is, out of the one packet in four that lands in its window | anything about the host or controller interfaces |
| no hardware at all | whether this project's own bytes and tables are right | anything new about the device |

A result that contradicts what is written here is as valuable as one that confirms it, and a
refusal that does not arrive is as informative as one that does. Nothing below is a request for
a success.

## 1. Apple hardware: capture the link being driven

**The question.** Does any host other than a Vision Pro drive this link, and is the "HRB" mode
macOS logs the same thing as ULLA? Two contributors have narrowed this without settling it
(ulla.md section 8): an Apple N1 Mac sends Apple's HDR packets to these earbuds and asks for
the 5 GHz mode repeatedly without ever getting it, while an older Broadcom Mac shows **none** of
the eighteen vendor opcodes documented here in an established session.

**What to do.** Capture HCI traffic **across a connection setup or a provoked band switch**,
not during a steady stream. That is the one piece of method that matters: the command which
starts a mode change is sent once, at the change, so a capture of an already-running session can
hold tens of thousands of records and none of them. Start the capture, then connect the earbuds.

- On macOS, Apple's PacketLogger writes `.pklg`; Wireshark converts it to btsnoop, which
  `tools/capture_decode.py` reads directly and prints this protocol's frames out of.
- On a Vision Pro or an iPhone, Apple's own Bluetooth logging profile plus a sysdiagnose is the
  equivalent route. A Vision Pro capture is the single most valuable contribution this project
  could receive, because that device is known to use the link.

**What to look for, in order.** The feature exchange first (protocol.md 5.8): if a host drives
this link at all, that is its first message, and the capture then holds the feature words both
ends advertise. Then the vendor command's parameters against the ones the earbuds' own code
sends (6.0). Then the band mask and the per-band channel ranges (6.2, 6.5).

**What each outcome means.** If the band-change command is **0xFE50** with the seven-byte
parameter shape of section 6 (a connection handle, a flag byte, a two-byte configuration word,
a flag byte, a mode byte) then HRB is this protocol and the question is settled. If it is a
different vendor opcode, the question is settled the other way, which is just as useful.

**What not to do.** Do not try to answer this by asking the earbuds from a second host while an
Apple device holds them. That was measured here and it cannot work: the earbuds evict the second
host with "Remote Device Terminated due to Low Resources", and by the time the asking host is
connected the first one is gone and the session has reset, so the test cannot tell its two
outcomes apart ([measurements.md](measurements.md) section 8).

## 2. A patchable controller: drive the negotiation

**The question.** Can the link-manager exchange of protocol.md section 5 be driven from
non-Apple silicon, and what do the earbuds answer when it is? The exchange needs **no key and no
challenge of any kind**, which is established from the firmware, and it is why this is worth
trying at all.

Be clear about the ceiling before you start: commodity controllers do not implement Apple's HDR
radio modes, so even a negotiation the earbuds accept cannot be followed by audio. What such a
test settles is whether the *protocol* is reachable from other hardware, and which of the five
documented refusals the earbuds choose when it is not.

**Start with the cheap end, which needs no link and no audio:**

```sh
sudo python3 tools/ulla_probe.py          # the four predictions, with their own control
```

It sends an opcode nobody implements first, learns your controller's blanket refusal, and only
then reads the real answers against it. That control is not optional: an Intel AX211 answers
**0x0C to every unimplemented vendor opcode**, which is the same code section 6.6 documents for
a session command whose gate fails, so "it refused with 0x0C" is not evidence of anything by
itself (measurements.md section 10). The two discriminators are the **event** (the firmware
answers these commands with Command Complete carrying its own status byte, an unimplementing
controller with Command Status) and **command 0xFE55**, whose word is bounded to the signed
range -800 to +800 and answers 0x11 outside it, giving three distinguishable outcomes instead
of two.

On any controller that is not Apple's, the expected verdict is "implements none of them". Report
anything else.

**Two commands are worth trying before the link-manager exchange**, because each is one command
with one byte and each would say something on its own. **0xFD02** sets how large a packet may be,
and its mode byte of 1 selects the 8 Mbit/s five-slot maximum (protocol.md 6.8). **0xFDEE** is
the shorter lever: one flag byte puts a link into Apple's packet-type table with all six of its
packet types permitted, with no negotiation at all (protocol.md 6.9). Neither is in the eighteen
a host was previously known to need, and both are in the earbuds' own command table. On
commodity silicon both should be refused, and which refusal arrives is the information.

**Then the exchange itself.** `openulla/` builds every PDU in the order a source must send them
(protocol.md 8.1), and `tools/ulla_sink.py` answers as an earbud on a real L2CAP channel, so the
host half of an implementation can be exercised before it touches earbuds. What to report: the
earbuds' answer to the setup request: accepted, or which of the refusal reasons of 5.4, since
the receiver's four checks and their codes are documented and one of them (0x24) is a code the
firmware's own formatter cannot even name.

**Which parts can actually send these PDUs.** The setup request is LMP opcode 0x73 and the
extended PDUs ride escape 0x7B (protocol.md 5.1, 5.7). Neither is a standard LMP opcode, and the
opcode and length check sits in the *controller firmware*, not in the host tool: InternalBlue
offers "arbitrary valid LMP messages (opcode and length must be standard compliant)", while its
`fuzzlmp` "installs a hook to sendlmp that skips checking opcodes and lengths inside firmware"
(`doc/features.md` and `internalblue/cli.py` in seemoo-lab/internalblue). That hook,
`FUZZLMP_ASM_CODE`, is defined for exactly two firmware targets in that tree:

| target | chip | devices |
|---|---|---|
| `fw_0x6109` | BCM4335C0 | Nexus 5, Xperia Z3 Compact, Galaxy Note 3, LG G4 |
| `fw_0x4208` | CYW20735B1 | CYW920735Q60EVB-01 evaluation board |

Plain `sendlmp` is defined for those plus `fw_0x240f` (BCM4358A3: Nexus 6P, Galaxy S6), but
plain is not enough for these opcodes. The one further firmware constraint that tree names is the
32-byte buffer from `lm_allocLmpBlock`; the longest PDU documented here is eleven bytes
([interop/vectors.txt](../interop/vectors.txt)), so every one of them fits with room to spare.

Two caveats on what you would see. Monitoring LMP needs that project's Android H4 driver patch,
which its own feature list says "still needs to be integrated into BlueZ", so a rooted Nexus 5
is the configuration that both injects and sees the earbuds' reply, while from Linux you would
be injecting half-blind, with only the mode-change vendor event of section 6.0 coming back over
HCI. And that project notes Broadcom disabled `Write_RAM` on chips from about 2020, so newer
silicon is worse for this, not better.

## 3. A software radio: the air format

The air format is the largest thing still open, but it is no longer entirely closed, and a
capture now has far more to match against than when this page was written. The modulation and
packet family come from Apple's own FCC measurements, pi/4 DQPSK in DH5-shaped five-slot packets
at 4 and 8 Mbit/s (ulla.md 2.3, 2.4). Three things have since been recovered from the firmware
(protocol.md 5.13 and 7.3), and all three are things a demodulated capture can be checked
against directly:

- **Six packet types with their exact payload maxima**, 112, 242, 703, 1288, 1416 and 2586
  bytes, in three slot lengths at two rates.
- **Their baseband TYPE codes**, which are the specification's own: 0x4 and 0x8 for one slot,
  0xA and 0xB for three, 0xE and 0xF for five. So the four TYPE bits of a captured packet header
  mean something known.
- **The payload header**, whose length field is **twelve bits at bit offset 3**, not the
  specification's ten. A decoder that assumes ten will mis-read **every** Apple packet, the
  one-slot ones included, since all six take the wider field,
  and that is the single most likely reason for a capture that demodulates but does not parse.

What is still missing is everything below the payload header: the access code, the header
coding, the guard and trailer, and the hopping sequence. A capture is still the only way to
those.

**You do not need to cover the whole band.** 79 channels across the 88 MHz of 5157 to 5245 MHz
is about 1 MHz of spacing, so a 20 MHz capture holds roughly twenty of them, and a five-slot
packet stays on one hop frequency for its whole duration, so about one packet in four lands in
the window intact. Recovering a format needs many whole packets, not every packet. The
assumption to check first is that a multi-slot packet keeps one frequency as it does in standard
Bluetooth, which is unverified for this link.

**And you may not need the 5 GHz bands at all.** Apple declares HDR4 and HDR8 in the ordinary
2.4 GHz band as well, and measured them there (ulla.md 2.3); a contributor observed an Apple N1
Mac sending exactly those packets to these earbuds in ordinary use (ulla.md section 8). The same
DH5-shaped family carries ULLA's audio, so capturing HDR at 2.4 GHz from an Apple host is the
cheapest route to the packet format, and it needs no ULLA session and no Vision Pro.

**Why not the Wi-Fi radio of a combo chip?** Three separate things block it and none of them is
firmware. A Wi-Fi transmitter is handed bits, not samples: the OFDM encoder is hardware and
there is no arbitrary-waveform path, so it cannot produce GFSK or pi/4 DQPSK at all. It cannot
hop 79 channels at a 625 us slot rate, because retuning takes orders of magnitude longer and the
MAC holds no hop sequence. And it arbitrates by CSMA with backoff rather than acting as a
slotted slave to another device's clock. The Bluetooth core of the same module cannot stand in
for it either: that core's synthesiser and filters are 2.4 GHz parts, which is silicon and not
firmware. What a patched Wi-Fi radio might still be good for is listening, since spectral-scan
bins would show energy and burst timing in these bands without demodulating anything, but that
is unverified here.

**And why a patched Bluetooth controller cannot carry HDR in either band.** Apple's own
measurements settle it, because one of its reports measures all three modes with one instrument:
1.248 to 1.252 MHz for ordinary DH5, 2.776 to 2.800 for 4-DH5 and 5.340 to 5.355 for 8-DH5
(ulla.md 2.4). HDR is two and four times the channel width of the Bluetooth packet it is named
after. The symbol rate follows from Apple's own declared rate and modulation: 4 and 8 Mbit/s of
pi/4 DQPSK is 2 and 4 Msym/s, against the 1 Msym/s a standard controller clocks. A modem's
symbol rate and channel filters are hardware. This is the
same wall as the band plan, and it is why a negotiation that succeeds on patched silicon is
expected to be followed by silence rather than audio.

## 4. No hardware: check our bytes

[interop/](../interop/) holds every message this project documents as bytes, the session
manager's table, the three controller procedures and every named code, all generated from the
implementation so they cannot drift from it. Implement a layer in another language, compare, and
report a mismatch either way round. This is the only kind of help that needs nothing but a
compiler.

## What is parked, so nobody duplicates it

These are open and **waiting on the hardware above**, not on analysis:

- **The vendor A2DP codec's eight bytes.** Confirmed as what the device advertises
  (measurements.md section 12), layout still unknown. A read cannot settle it, because a read
  returns what the device offers rather than which parts of it the device checks. What would
  settle it is offering a configuration back with one byte altered and seeing which AVDTP
  refuses, since a reject names the failing service category and carries an error code.
  **`tools/a2dp_probe.py` now does that**, and its dry run shows exactly what it would send.
  It has not been run live: a SetConfiguration changes the sink's state and needs BlueZ's own
  media plugin out of the way first, so this wants a deliberate session rather than a stray
  command. Both outcomes are results, and the likely one is worth saying in advance: if every
  altered byte is accepted, these bytes are not validated at all, which is the same shape of
  answer as the mode byte in section 11.
- **The stream's control path end to end.** Documented twice over (protocol.md 7.1 and the
  link-up half in 6.6) and untestable without a controller that exposes its vendor interface.
- **HDT3, HDT4 and HDT6.** Three mode names from an Apple FCC grant note for a 2026 digital
  media device, absent from every AirPods payload. The grant note is unreachable from this
  machine: fccid.io, fcc.report, apps.fcc.gov and api.fcc.gov all answer 403, including through
  a server-side fetch. Anyone who can open that exhibit in a browser can close this.
- **Whether the audio payload is encrypted after the mode change.** A hardware question; the
  setup establishes no key material of its own, which is as far as the firmware goes
  (protocol.md 7).

## What is settled, so nobody re-tests it

Measured here already, with the bytes in [measurements.md](measurements.md):

- A Linux host can open the accessory service, start a ULLA session and be told it succeeded;
  the session machine advances and refuses a second start until it is reset (sections 1 to 4).
- **The mode byte is not validated**: modes 0, 2 and 7 are all answered with the success status
  (section 11).
- The three advertised A2DP capabilities **are** the firmware's own table, byte for byte
  (section 12).
- A standard host cannot ask for the HDR packet types: the Create Connection packet-type field
  has no bit for 4-DH5 or 8-DH5 (section 9).
- An Intel AX211 implements none of the vendor commands documented here, and answers 0x0C to
  every unimplemented opcode in that group (section 10).
- **How HDR is selected, and what its packets are** (protocol.md 5.13, 6.8, 7.3). Apple's own
  LMP request negotiates a third packet-type table; six packet types live in it with known
  payload maxima and TYPE codes; vendor command 0xFD02's mode byte configures the controller for
  either the basic-rate five-slot packet or the 8 Mbit/s one; and the payload header's length
  field is twelve bits rather than ten. None of that needs re-deriving.
- **HDR is announced nowhere a standard host can see it.** All three of the earbuds' LMP feature
  pages were read off the air: 54 bits set, every one of them a defined Bluetooth feature, every
  reserved position zero (section 13). They announce EDR 2 and 3 Mb/s in page 0 bits 25 and 26,
  so the one mechanism a new ACL rate has for announcing itself is present and in use on this
  very device, and HDR is not in it. `tools/bt_link_state.py MAC` prints this.

## Sharing results

Open an issue at <https://github.com/mstampfli/openulla/issues>. **Scrub captures before
sharing**: a real Bluetooth trace can carry link keys and other people's device addresses. One
message found here deserves naming for the same reason: 0x7B/0x23 carries a whole link's worth
of state including a sixteen-byte field whose contents are not established
([protocol.md](protocol.md) 5.15). Treat its body as sensitive and do not post it. The
opcodes, the parameters and the status codes are the part this project needs; the raw trace can
stay with you, as both contributors in ulla.md section 8 chose to do.
