# openulla

Documenting, and eventually speaking, ULLA: the proprietary low-latency
lossless audio link built into AirPods Pro 2 (USB-C) that Apple only switches
on for the Vision Pro. The purpose is interoperability: letting other hosts
talk to these earbuds. See [NOTICE](NOTICE).

Independent research. Not affiliated with or endorsed by Apple. No Apple
firmware or other Apple-owned binaries are redistributed here; the tools fetch
public material from Apple's and the FCC's own servers.

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
