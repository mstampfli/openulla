# What this project needs tested, and by whom

Everything the AirPods' own firmware can answer has been answered: the setup protocol is
documented field by field in [protocol.md](protocol.md), an implementation of it is in
`openulla/`, and `tools/verify_claims.py` re-derives twenty-three groups of claims from the
firmware image itself. What is left needs hardware this project does not have.

Two different kinds of hardware answer two different questions, and it is worth being precise
about which:

| if you have | you can answer | you cannot answer |
|---|---|---|
| an Apple host that drives the link (Vision Pro, possibly a Mac) | whether anything other than a Vision Pro drives it, with what parameters, and whether "HRB" is this protocol | nothing about non-Apple sources |
| a Bluetooth controller whose firmware you can patch | whether the **negotiation** completes from non-Apple silicon, and what the earbuds answer when it does not | whether audio follows: the HDR radio modes are not in commodity silicon, so the mode change cannot succeed there |
| a wideband software radio | what the air format actually is | anything about the host or controller interfaces |
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
parameter shape of section 6 — a connection handle, a flag byte, a two-byte configuration word,
a flag byte, a mode byte — then HRB is this protocol and the question is settled. If it is a
different vendor opcode, the question is settled the other way, which is just as useful.

**What not to do.** Do not try to answer this by asking the earbuds from a second host while an
Apple device holds them. That was measured here and it cannot work: the earbuds evict the second
host with "Remote Device Terminated due to Low Resources", and by the time the asking host is
connected the first one is gone and the session has reset, so the test cannot tell its two
outcomes apart ([measurements.md](measurements.md) section 8).

## 2. A patchable controller: drive the negotiation

**The question.** Can the link-manager exchange of protocol.md section 5 be driven from
non-Apple silicon, and what do the earbuds answer when it is? The exchange needs **no key and no
challenge of any kind** — that is established from the firmware, and it is why this is worth
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
itself (measurements.md section 10). The two discriminators are the **event** — the firmware
answers these commands with Command Complete carrying its own status byte, an unimplementing
controller with Command Status — and **command 0xFE55**, whose word is bounded to the signed
range -800 to +800 and answers 0x11 outside it, giving three distinguishable outcomes instead
of two.

On any controller that is not Apple's, the expected verdict is "implements none of them". Report
anything else.

**Then the exchange itself.** `openulla/` builds every PDU in the order a source must send them
(protocol.md 8.1), and `tools/ulla_sink.py` answers as an earbud on a real L2CAP channel, so the
host half of an implementation can be exercised before it touches earbuds. What to report: the
earbuds' answer to the setup request — accepted, or which of the refusal reasons of 5.4, since
the receiver's four checks and their codes are documented and one of them (0x24) is a code the
firmware's own formatter cannot even name.

Whether any current chip is supported by InternalBlue-class tooling is **unverified here**; that
is part of what a contributor with one would be telling us.

## 3. A software radio: the air format

The air format is the largest thing still open, and the firmware cannot answer it:
protocol.md section 7 says why. The link hops 79 channels at Bluetooth timing, so capturing the
whole band and un-hopping in software is the realistic approach rather than chasing hops. The
modulation and packet family are known from Apple's own FCC measurements — pi/4 DQPSK in
DH5-shaped five-slot packets at 4 and 8 Mbit/s (ulla.md 2.3, 2.4) — so a capture has a known
shape to match against.

## 4. No hardware: check our bytes

[interop/](../interop/) holds every message this project documents as bytes, the session
manager's table, the three controller procedures and every named code, all generated from the
implementation so they cannot drift from it. Implement a layer in another language, compare, and
report a mismatch either way round. This is the only kind of help that needs nothing but a
compiler.

## What is parked, so nobody duplicates it

These are open and **waiting on the hardware above**, not on analysis:

- **The vendor A2DP codec's eight bytes.** Confirmed as what the device advertises
  (measurements.md section 12), layout still unknown. A read cannot settle it; what would is
  offering a configuration with altered fields and seeing which the earbuds refuse. That needs
  an A2DP configuration attempt rather than a probe, so it needs new code as well as earbuds.
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

## Sharing results

Open an issue at <https://github.com/mstampfli/openulla/issues>. **Scrub captures before
sharing**: a real Bluetooth trace can carry link keys and other people's device addresses. The
opcodes, the parameters and the status codes are the part this project needs; the raw trace can
stay with you, as both contributors in ulla.md section 8 chose to do.
