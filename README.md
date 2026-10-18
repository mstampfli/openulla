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

Sources for the two claims above: Apple's press release for the USB-C AirPods
Pro 2, and Apple's Ron Huang on the H2's 5 GHz support, as reported by
MacRumors and quoted by
[MacStories](https://www.macstories.net/?p=72982).

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

The full field-by-field reference is [docs/protocol.md](docs/protocol.md); the
measurements behind the transcript are in
[docs/measurements.md](docs/measurements.md).

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
- **The setup protocol is recovered in full**, from the command a host sends to
  the link-manager messages the two controllers exchange, and it is written
  down field by field in [docs/protocol.md](docs/protocol.md).
- **No Apple authentication guards any of it.** The earbuds' four acceptance
  checks look at feature flags, link state and roles. There is no key, no
  nonce and no challenge anywhere in the setup.
- **A Linux laptop can start a ULLA session and be told it succeeded.** Sending
  seven bytes over an ordinary Bluetooth channel moves the earbuds' session
  machine through three states and returns a success status. Measured, with
  the bytes, in [docs/measurements.md](docs/measurements.md).
- Where it stops: the next step is a link-manager exchange that only the
  source's own Bluetooth controller can emit, and commodity controllers
  neither expose that interface nor implement the radio modes it needs.

## What is open

The audio layer. The over-the-air packet format is produced by radio hardware
whose registers are undocumented, the codec is not named anywhere in the DSP
image, and whether the audio payload is encrypted is a hardware question the
firmware cannot answer. Details in [docs/ulla.md](docs/ulla.md#7-open).
Nothing anywhere in this repository is filled in by guesswork.

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

1. **If you have a recent Mac, you already have the radio** (see "What is open"),
   and macOS can capture its own Bluetooth traffic with Apple's PacketLogger.
   That makes a Mac the best platform for the cheapest open question we cannot
   answer ourselves: does anything other than a Vision Pro use this link? The
   controller's vendor command and
   its progress event are documented in [docs/protocol.md](docs/protocol.md)
   section 6, including the mode, result and reason bytes, so a host-side
   capture on a Mac would show them directly if that host drives the link.

   Do **not** try to answer this by asking the earbuds from a second host while
   an Apple device holds them. We measured that: the earbuds evict the second
   host and say so, with the Bluetooth reason "Remote Device Terminated due to
   Low Resources", even with every audio profile released on our side. By the
   time the asking host is connected, the other one is gone and the session has
   reset, so the test cannot tell its two outcomes apart. See
   [docs/measurements.md](docs/measurements.md) section 8.
3. **If you have a controller whose firmware you can patch**
   (InternalBlue-class tooling on a supported chip), the link-manager exchange
   is documented field by field in section 5 and needs no key or challenge of
   any kind. Emitting the setup PDU and reporting what the earbuds answer would
   tell us whether the mode change can be driven from non-Apple hardware at
   all. Whether current chips are supported by such tooling is unverified.
4. **If you have a wideband software radio**, the air format is the big unknown.
   The link hops 79 channels at Bluetooth timing, so capturing the whole band
   and un-hopping in software is the realistic approach rather than chasing the
   hops.

Please scrub captures before sharing them: a real Bluetooth trace can contain
link keys and other people's device addresses.

## Repository layout

- `docs/ulla.md`: the report; every claim cites a quote, a firmware address or
  a measurement.
- `docs/protocol.md`: the setup protocol as a reference, for implementing it.
  Frames, fields, opcodes, the session state machine with all its transitions,
  the earbud-to-earbud messages, the link-manager PDUs, the controller's vendor
  command, and the reason codes.
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
tools/fetch_fcc.sh            # FCC exhibits -> fcc/*.pdf and fcc/*.txt (needs curl, pdftotext)
tools/fetch_firmware.sh       # firmware -> firmware/ (needs curl, unzip)
python3 tools/uarp.py firmware/*/AssetData/Firmware.acsw/firmware.uarp list
python3 tools/tmap_keys.py firmware/*/AssetData/Firmware.acsw/firmware.uarp
tools/ulla_strings.sh firmware/*/AssetData/Firmware.acsw/firmware.uarp

# talk to a paired pair of earbuds (their address, not ours, goes here)
python3 tools/aap_probe.py --device AA:BB:CC:DD:EE:FF
python3 tools/aap_probe.py --device AA:BB:CC:DD:EE:FF --seq subscribe,start --wait 20
```

`aap_probe.py` needs nothing but a paired device and an ordinary user account.
It changes the earbuds' session state, which a disconnect of its channel
resets; see docs/measurements.md section 6 for the one side effect observed.

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
