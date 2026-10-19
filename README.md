# openulla: reverse engineering Apple's H2 5 GHz lossless audio link (AirPods Pro 2 USB-C, Vision Pro)

Apple sells it as "20-bit, 48 kHz Lossless Audio with a massive reduction in
audio latency", switched on for exactly one pairing: AirPods Pro 2 (USB-C) with
an Apple Vision Pro. Apple has never published a name or a specification for
the protocol. **The firmware calls it ULLA, ultra-low-latency audio**, which is
where this project gets its name.

It does not run over ordinary Bluetooth. Apple's own engineers have said the H2
chip in the USB-C model uses the 5 GHz band and that this is what enables the
feature; Apple's FCC filings show what that radio actually is, a narrowband
radio in the 5 GHz and, since 2024, 6 GHz bands.

**The radio is not exclusive to the Vision Pro.** A current MacBook's own FCC
test report declares the same two Apple modes, HDR4 and HDR8, in the same two
5 GHz bands, on two independently usable radio cores, and recent iPhones declare
a narrowband radio in those bands as well. Whether any of their software ever
drives this link is a different question and is still open. Sources in
[docs/ulla.md](docs/ulla.md) section 2.4. HDR4 and HDR8 are modes of the radio,
not the protocol, and they are much older than this link: see
[HDR is not ULLA](#hdr-is-not-ulla).

This repository documents that protocol and works toward speaking it from
hardware you already own. The purpose is interoperability: letting other hosts
talk to these earbuds, including the thing that started it, the fact that any
ordinary Bluetooth host drops these earbuds to a telephone-quality mono codec
the moment you open the microphone. See [NOTICE](NOTICE).

Searching for this subject is awkward because Apple never named it, so for the
record, the same thing is meant by: AirPods Pro 2 5 GHz protocol, H2 chip 5 GHz
lossless, AirPods Pro 2 Vision Pro lossless reverse engineering, and ULLA.

Independent research. Not affiliated with or endorsed by Apple. No Apple
firmware or other Apple-owned binaries are redistributed here; the tools fetch
public material from Apple's and the FCC's own servers.

Sources for the quoted audio specification and for the statement that the H2
uses the 5 GHz band: Apple's press release for the USB-C AirPods Pro 2, and
Apple's Ron Huang as reported by MacRumors and quoted by
[MacStories](https://www.macstories.net/?p=72982). The Mac and iPhone filings
are cited in [docs/ulla.md](docs/ulla.md) section 2.4.

## The short version

Seven bytes from a Linux laptop, over an ordinary Bluetooth channel, with no
Apple authentication of any kind, and the earbuds start setting up their
private audio link:

```
$ python3 tools/aap_probe.py --device AA:BB:CC:DD:EE:FF --seq start
connecting to AA:BB:CC:DD:EE:FF PSM 0x1001
connected
-> open service 4 versions 1,2: 00 00 04 00 01 00 02 00 00 00 00 00 00 00 00 00
  open <- type 1 (open-reply) service 4 status 0x00 pad 0x00 versions 1,3
-> step 1: start ULLA session, mode 0: 04 00 04 00 36 00 00
  step1 <- type 4 (data) service 4 opcode 0x0037 payload 00 00 00 00
```

That last line is the earbuds reporting success. Their session machine has
moved three states and is now waiting for the radio handshake.

(The transcript is abridged: the real tool also prints the raw bytes of every
frame, and the earbuds volunteer a stream of their own status frames in
between.)

Where the layers sit:

```
  host (phone, headset, computer)
    |  Apple accessory protocol, service 4, L2CAP PSM 0x1001   <- recovered, and it works
  earbud session manager                                       <- recovered, 17 states
    |  service 0x10, to the other earbud                       <- recovered, 14 messages
  other earbud
  ---
  earbud session manager -> its own controller, HCI vendor commands   <- recovered
  controller <-> controller, link-manager PDUs on the same ACL link   <- recovered
  then the radio changes mode and carries audio                       <- NOT recovered
```

And the order it happens in, each step read from the firmware or measured
(docs/protocol.md section 3.6 cites every one):

```
  host                     earbud manager              its controller / its sibling
   |-- open service 4 ------->|
   |<------ version 1.3 ------|
   |-- 0x36, one mode byte -->|  only while idle
   |                          |-- start session, address + mode --> sibling
   |                          |   app setup, supervision timeout, sibling's answer
   |<--- status 0 ------------|  once it waits for the local audio path
   |                          |-- vendor command 0xFE50 ----------> controller
   |                          |            feature exchange, bands, then the setup PDU
   |                          |<-- vendor event: mode, result, reason
   |                          |   link up -> DSP setup -> session open
   |-- 0x38, start stream --->|
```

The full field-by-field reference is [docs/protocol.md](docs/protocol.md); the
measurements behind the transcript are in
[docs/measurements.md](docs/measurements.md).

## The implementation

`openulla/` speaks every layer that has been recovered, in pure Python with no
dependencies, and stops exactly where the recovery stops. Every constant,
length and refusal in it cites the firmware address or the measurement that
proves it; nothing is filled in. `tools/ulla_demo.py` runs a whole setup
against a simulated earbud and prints every byte, so none of it needs hardware:

```
$ python3 tools/ulla_demo.py
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
```

Steps 1 to 3 are bytes a Linux laptop has really exchanged with real earbuds.
From step 4 on, the simulator stands in for a Bluetooth controller, because
those commands and PDUs have to come from one. `python3 -m pytest` runs the
suite that checks the layouts against the measured frames and the firmware's
own tables, including the band plan's four internal identities and the receiver
that recomputes a count instead of trusting it. [docs/implementation.md](docs/implementation.md)
says what the simulator does, what it refuses to invent, and why the last step
cannot be done from a commodity host.

## Why this exists

Spite, mostly.

I own AirPods Pro 2. The moment anything opens their microphone they drop
from stereo music quality to a 16 kHz mono phone-call codec. That is not a
Linux bug and not a Discord bug. Classic Bluetooth has exactly two audio
modes, one-way music or two-way telephone voice, and nothing in between, and
it has been that way since 2003. Every Bluetooth earbud on every operating
system, including Apple's, lives with it. Gaming headsets ship a proprietary
2.4 GHz dongle for this one reason. I did not understand how, in 2026, this
is still the state of the art for a device that sits in my ears all day.

Then I found out that the fix already exists, and that I had already paid
for it. The USB-C AirPods Pro 2 are certified for a 5 GHz radio mode, and
Apple sells 20-bit 48 kHz lossless audio with ultra-low latency on them, for
exactly one product, the Vision Pro. Not for an iPhone, not for a Mac, not
for anyone else. It is inside the earbuds; it is simply withheld.

I do not understand why that is acceptable, and I do not accept that the
answer is "buy a Vision Pro". So the question became: what is that link,
and what would it take to speak it from hardware I already own. This
repository holds what I found and the tools I used, so that the next person
who refuses to accept it does not have to start from zero.

## HDR is not ULLA

Two different things turn up in the same FCC exhibits and are easy to conflate,
so for the record.

**HDR4 and HDR8 are packet modes.** Five-slot Bluetooth packets at 4 and
8 Mbit/s, pi/4 DQPSK, written 4-DH5 and 8-DH5 after Bluetooth's own names for
its five-slot packets. The 4 and the 8 are Apple's. Bluetooth names only 2 and
3 Mbit/s variants: the packet-type field a host sends with a connection request
has twelve defined ACL packet types, one bit each for DM1, DH1, DM3, DH3, DM5
and DH5, and one exclusion bit for each of the six enhanced-rate types (BlueZ's
own `hci.h`, 0x0002 through 0x8000). No bit in it could mean 4 or 8 Mbit/s. Our
Linux host sends 0xcc18, which is exactly the six basic-rate types with
every exclusion bit clear, so it offers the whole standard set, and the standard
set has no HDR in it ([docs/measurements.md](docs/measurements.md) section 9).

**And HDR is old.** Apple filed a "Bluetooth HDR Test Report" for an Apple Watch
dated 2018-09-12 (FCC ID BCG-A1978) and for an iPad dated 2019-03-18 (FCC ID
BCGA2124), years before the Vision Pro, and the AirPods' own report measures the
HDR modes in the ordinary 2.4 GHz Bluetooth band as well as in the narrowband
5 GHz bands. Nothing about HDR is new, and nothing about it is particular to
this link.

**ULLA is the protocol.** It is the firmware's own name for the feature, and it
has a session manager, a host command, its own earbud-to-earbud messages and its
own link-manager PDUs. That is what this repository documents. HDR is a pipe it
can run over.

So "this device supports HDR4 and HDR8" says the radio can carry those packets.
It does not say the device speaks ULLA, and nothing here claims it does. What is
new in this repository is the layer above the radio, not the radio. Sources and
the exact wording of each filing: [docs/ulla.md](docs/ulla.md) section 2.6.

## What is known so far

Everything below comes from Apple's own FCC certification exhibits, Apple
patents, the public AirPods firmware image and measurements on a Linux host,
and each point is sourced in [docs/ulla.md](docs/ulla.md).

- Apple's test reports call the radio "Narrowband UNII": certified in the
  5 GHz U-NII-1 and U-NII-3 bands and, since August 2024, the 6 GHz U-NII-5
  band at very low power.
- The reports state 79-channel pseudorandom hopping within one band, and a
  non-hopping mode.
- Declared modes include HDR4 (4 Mbit/s) and HDR8 (8 Mbit/s), with "p"
  variants, both in the NB UNII bands and in the 2.4 GHz Bluetooth band.
- The firmware names the feature ULLA, versions 1.0 and 1.1, with a ULLA Lite
  mode.
- **The radio's band plan is in the firmware**, eight slots indexed by the bits of
  the allowed-bands mask: 2.4 GHz ISM, U-NII-1, U-NII-3, an empty slot the
  firmware refuses, and U-NII-5 split into four. Each slot carries its start
  frequency, width, channel count, channel bandwidth and spacing, and the 2.4 GHz
  slot is a fallback the firmware drops whenever anything else is available.
  Table and proof in [docs/protocol.md](docs/protocol.md) section 6.3.
- **The setup protocol is recovered in full**, from the command a host sends to
  the link-manager messages the two controllers exchange, and it is written
  down field by field in [docs/protocol.md](docs/protocol.md).
- **No Apple authentication guards any of it.** The earbuds' four acceptance
  checks look at feature flags, link state and roles. There is no key, no
  nonce and no challenge anywhere in the setup.
- **What does gate it is a feature exchange, not a secret.** Each controller
  sends the other a four-byte feature word, the receiver keeps the peer's
  against the link, and every ULLA message is gated on the AND of the two: one
  bit for the band family, another for the mode procedure. An implementation
  that never answers it is sent nothing at all, which makes answering it the
  first thing a source has to do. [docs/protocol.md](docs/protocol.md) 5.8.
- **The implementation exists**, in `openulla/`, with a simulated earbud, a
  decoder, a capture reader, an L2CAP sink server and a verifier that
  re-derives ten groups of claims from a firmware image. See
  [docs/implementation.md](docs/implementation.md).
- **A Linux laptop can start a ULLA session and be told it succeeded.** Sending
  seven bytes over an ordinary Bluetooth channel moves the earbuds' session
  machine through three states and returns a success status. Measured, with
  the bytes, in [docs/measurements.md](docs/measurements.md).
- Where it stops: the next step is a link-manager exchange that only the
  source's own Bluetooth controller can emit, and commodity controllers
  neither expose that interface nor implement the radio modes it needs.

## What is open

The audio layer, and now with a reason rather than a shrug. The over-the-air
packet format is produced by radio hardware whose registers are undocumented.
The sample rate, frame size and bit depth are not constants in the DSP image at
all: no rate value appears in its audio-node region, its log texts ship outside
the payload, and the data that ships beside the code is filter coefficients and
a hash manifest, not a graph description. So the format is configuration the DSP
is handed at runtime, which makes it a measurement question, not a reading one.
Whether the payload is encrypted is a hardware question the firmware cannot
answer either. Details in [docs/protocol.md](docs/protocol.md) section 7 and
[docs/ulla.md](docs/ulla.md#7-open). Nothing anywhere in this repository is
filled in by guesswork.

What the firmware does say about the messages themselves is now complete in one
direction and inventoried in the other: both escape spaces are listed message by
message, with what each carries and who consumes it, and three of them are
decoded in full. What the rest *mean* is open, and the names that would settle
it ship in a text table that is not in these payloads.

**This is not Vision-Pro-only hardware.** A current MacBook has the same radio:
the narrowband test report for an Apple MacBook Air states "Both power modes
support HDR4 (4-DH5) and HDR8 (8-DH5)" in the same two 5 GHz bands the AirPods
use, on two independently usable radio cores. Current iPhones declare a
narrowband radio in those bands too. Details and sources in
[docs/ulla.md](docs/ulla.md) section 2.4.

Two more facts from the material here: Apple declares the HDR modes in the
**ordinary 2.4 GHz Bluetooth band** as well, and the firmware treats a **lite
mode** as a first-class citizen, with its own mode value, its own receive client
in the audio DSP and its own failure reason.

What none of that establishes, and nobody should claim it does: that a Mac's or
an iPhone's software ever drives this link, that the lite mode uses the 2.4 GHz
band, or that a non-Apple controller could form these modulations. Those are the
open questions, and they are the cheap ones to answer.

## How you can help

In rough order of how little hardware it needs:

1. **If you have a Mac**, macOS can capture its own Bluetooth traffic with
   Apple's PacketLogger, which makes it the best platform for the cheapest open
   question we cannot answer ourselves: does anything other than a Vision Pro
   use this link? The controller's vendor command and its progress event are
   documented in [docs/protocol.md](docs/protocol.md) section 6, including the
   mode, result and reason bytes, so a host-side capture would show them
   directly if that host drives the link.

   One MacBook Air model's test report shows that machine has the radio modes
   (see "What is open"). Which other Macs carry them is not established, and a
   capture answers that for your machine at the same time.

   `tools/capture_decode.py` reads such a capture and prints the frames of this
   protocol in it, so there is nothing to decode by hand. It reads btsnoop,
   which `btmon -w` writes on Linux and which Wireshark can save a PacketLogger
   `.pklg` file as. What to look at first, in order: the **feature exchange**
   (section 5.8), because if a host drives this link at all that is its first
   message and the capture then holds the feature words both ends advertise;
   then the vendor command's parameters against the ones the earbud's own code
   uses (6.0); then the band mask and channel ranges (6.2 and 6.5).

   Do **not** try to answer this by asking the earbuds from a second host while
   an Apple device holds them. We measured that: the earbuds evict the second
   host and say so, with the Bluetooth reason "Remote Device Terminated due to
   Low Resources", even with every audio profile released on our side. By the
   time the asking host is connected, the other one is gone and the session has
   reset, so the test cannot tell its two outcomes apart. See
   [docs/measurements.md](docs/measurements.md) section 8.
2. **If you have a controller whose firmware you can patch**
   (InternalBlue-class tooling on a supported chip), the link-manager exchange
   is documented field by field in [docs/protocol.md](docs/protocol.md) section
   5 and needs no key or challenge of any kind. Emitting the setup PDU and
   reporting what the earbuds answer would tell us whether the mode change can
   be driven from non-Apple hardware at all. Whether current chips are supported
   by such tooling is unverified.

   `openulla/` builds every PDU of that exchange, in the order a source has to
   send them (section 8.1), and `tools/ulla_sink.py` answers as an earbud on a
   real L2CAP channel, so the host half of an implementation can be tested
   before any of it touches earbuds.
3. **If you have a wideband software radio**, the air format is the big unknown.
   The link hops 79 channels at Bluetooth timing, so capturing the whole band
   and un-hopping in software is the realistic approach rather than chasing the
   hops.

Please scrub captures before sharing them: a real Bluetooth trace can contain
link keys and other people's device addresses.

## Repository layout

- `openulla/`: the implementation. Pure Python, no dependencies, every constant
  citing the firmware address or the measurement that proves it. See
  [docs/implementation.md](docs/implementation.md).
- `tests/`: the suite over the layouts, the state machine, the band plan and a
  whole simulated negotiation. `python3 -m pytest`.
- `docs/implementation.md`: what the code covers, what the simulator refuses to
  invent, and why the last step needs a controller that does not exist yet.
- `docs/ulla.md`: the report; every claim cites a quote, a firmware address or
  a measurement.
- `docs/protocol.md`: the setup protocol as a reference, for implementing it.
  Frames, fields, opcodes, the session state machine with all its transitions,
  the earbud-to-earbud messages, the link-manager PDUs, the nine-state
  procedure the source's own controller runs, the controller's vendor command,
  and the reason codes.
- `docs/measurements.md`: what was observed against real earbuds from Linux,
  with the exact bytes sent and received.
- `docs/fcc/README.md`: index of the FCC exhibits used for the AirPods Pro 2
  USB-C left earbud (BCG-A3047) and the Apple Vision Pro (BCGA2117). The
  exhibits are not redistributed here; `tools/fetch_fcc.sh` downloads them
  from fccid.io and extracts their text locally.
- `tools/fetch_firmware.sh`: fetches the current AirPods firmware asset from
  Apple's public asset catalog into a local, ignored directory.
- `tools/uarp.py`: parses the UARP SuperBinary container of that firmware,
  lists its payloads and extracts one by tag.
- `tools/tmap_keys.py`: lists the radio-related parameter names in the RF
  tuning map payload.
- `tools/ulla_strings.sh`: pulls the ULLA, UTP and HDR identifiers out of a
  firmware image.
- `tools/ulla_decode.py`: decodes one frame, PDU, vendor command or vendor
  event from hex, naming its fields and, for the band messages, which bands a
  mask means. Useful when reading a capture or a log.
- `tools/ulla_sink.py`: answers like an earbud on a real L2CAP channel, PSM
  0x1001, so a source implementation can be tested over real Bluetooth with no
  earbuds involved. It prints every internal event it supplies by name, and
  `--no-advance` leaves the session machine where the host's own frames put it.
- `tools/capture_decode.py`: reads a btsnoop capture (`btmon -w`, or the HCI
  UART layout other tools write) and prints the frames of this protocol in it,
  finding the channel the accessory protocol runs on from the L2CAP signalling.
  It prints only what it recognises; captures stay on your machine.
- `tools/verify_claims.py`: re-derives what the docs and the code claim from a
  firmware image on your own machine, one line per claim: the session manager's
  whole transition table, the band plan, the command table with every handler
  address, the PDU lengths, both dispatch tables, the event's shape, the reason
  names and the stream endpoints. It fails on a single changed byte.
- `tools/ulla_demo.py`: runs a whole setup against the simulated earbud and
  prints every frame and PDU of it, with no hardware involved. `--reject`
  makes the peer controller refuse at one of its four checks.
- `tools/aap_probe.py`: speaks the accessory protocol to the earbuds over
  L2CAP. Opens the service, can ask them to start a ULLA session, subscribe to
  their report streams, or stop a session, and prints every frame that comes
  back. Every constant in it cites the firmware address that proves it.
- `tools/bt_link_state.py`: prints what is actually established for a device,
  the kernel's own link handles beside the transports and the profile, because
  a profile name is not evidence of a link.
- `tools/a2dp_send.py`: drives an A2DP transport from its own process, encoding
  with libsbc to whatever configuration the stack negotiated. Useful for
  measuring what a device really accepts. It does not grant stereo and
  microphone at once; see docs/ulla.md section 5.

## Reproducing

```sh
python3 -m pytest             # the implementation's own checks, no hardware needed
python3 tools/ulla_demo.py    # a whole setup against the simulator, every byte of it

tools/fetch_fcc.sh            # FCC exhibits -> fcc/*.pdf and fcc/*.txt (needs curl, pdftotext)
tools/fetch_firmware.sh       # firmware -> firmware/ (needs curl, unzip, python3)
python3 tools/uarp.py firmware/*/AssetData/Firmware.acsw/firmware.uarp list
python3 tools/tmap_keys.py firmware/*/AssetData/Firmware.acsw/firmware.uarp
tools/ulla_strings.sh firmware/*/AssetData/Firmware.acsw/firmware.uarp

# check every documented constant against that image, one line per claim
python3 tools/ftab.py firmware/*/AssetData/Firmware.acsw/firmware.uarp \
    extract acib payloads/acib.bin --payload FTAB
python3 tools/ftab.py firmware/*/AssetData/Firmware.acsw/firmware.uarp \
    extract rkof payloads/rkof.bin --payload FTAB
python3 tools/verify_claims.py --payloads payloads

# talk to a paired pair of earbuds (their address, not ours, goes here)
python3 tools/aap_probe.py --device AA:BB:CC:DD:EE:FF
python3 tools/aap_probe.py --device AA:BB:CC:DD:EE:FF --seq subscribe,start --wait 20
```

`aap_probe.py` needs nothing but a paired device and an ordinary user account.
It changes the earbuds' session state, which a disconnect of its channel
resets. Two behaviours worth knowing before you run it are recorded in
[docs/measurements.md](docs/measurements.md): the link dropping about half a
minute after the channel closes (section 6), and the earbuds evicting a second
host (section 8).

On macOS (tested on macOS 27.0.1), the firmware and FCC tools run as shown,
with `pdftotext` from Homebrew's `poppler` package and `strings` from the Xcode
Command Line Tools. `aap_probe.py`, `bt_link_state.py` and `a2dp_send.py` are
built on Linux's Bluetooth sockets, BlueZ and PipeWire, so they need a Linux
host, and on any other system they exit saying so.

## Contributing

Captures from Apple hosts (PacketLogger, Bluetooth logging profile), notes
from disassembling the controller blob, measurements with a software-defined
radio, and corrections to the report are all welcome. Open an issue or a pull
request. Every claim needs its source: a quote, a firmware address, or a
measurement.

- Write findings in your own words. Do not add firmware, string dumps,
  decompiler or disassembly listings, or copies of FCC reports; link to them.
- Captures must not contain link keys or other people's device addresses.
  Scrub them before committing.
- Nothing here circumvents authentication or encryption, and contributions
  must not either.
- Sign off your commits (`git commit -s`, Developer Certificate of Origin).
- You are bound by your own country's law on reverse engineering.

## License

MIT. See [LICENSE](LICENSE).
