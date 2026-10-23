# M1 / Broadcom: established-session microphone transitions

Observed on 2026-10-18 by `dberweger2017`. See [summary.json](summary.json) for
setup versions, counts and selected record indexes, and the [evidence policy and
validation notes](../README.md) for interpretation and privacy limits.

Sources: [initial report](https://github.com/mstampfli/openulla/issues/3#issuecomment-6253991655)
and [parameter follow-up](https://github.com/mstampfli/openulla/issues/3#issuecomment-6266962214).

## Procedure

The host was a MacBook Pro M1 (`MacBookPro17,1`) with a Broadcom `BCM_4378`
controller, paired with USB-C AirPods Pro 2 on firmware `9A348`. PacketLogger
26.0.0 received actual HCI traffic after Apple's Bluetooth logging profile was
installed and the Mac restarted. The temporary profile was removed after saving.

Audible playback continued while the Sound settings input meter served as the
microphone client. CoreAudio verified its actual input device in each phase.
Wi-Fi and Bluetooth settings were unchanged; the AirPods stayed connected.
YouTube and GeForce NOW were running, and playback sources were not isolated.

| Phase | Window | AirPods output endpoint | SCO records |
| --- | --- | --- | --- |
| Client uses built-in microphone | 41.810 s | 48,000 Hz | 0 |
| Client uses AirPods microphone | 37.299 s | 24,000 Hz | 4,413 |
| Client restored to built-in microphone | 30.005 s | 48,000 Hz | 313 |

The final window includes teardown: its SCO records occur during the first
approximately 2.5 seconds. The whole saved file spans 453.393 seconds of timestamps
and includes traffic preceding these windows.

## Observations

- Command `0x0428` is followed by Synchronous Connection Complete (`0x2C`), status
  `0x00`, link type 2/eSCO, and bidirectional SCO traffic. The peer address was
  matched locally to the AirPods; the address is omitted. The synchronous link
  disconnects after the input client returns to the built-in microphone.
- The full capture has 18,623 records and twelve distinct vendor command opcodes.
  It contains no `0xFE50`, `0xFDEE`, `0xFD7C`, `0xFD7D` or `0xFD5F` command.
- `0xFD02` occurs once, with five parameter bytes. Its first parameter is `0x00`;
  Command Complete with status `0x00` follows 3 ms later.
- `0xFD4B` occurs 68 times, each with 33 parameter bytes. Its nearest occurrence
  is 11.108 seconds after `0xFD02`.
- No dedicated LMP records or Wireshark-dissected LMP packets were found. Generic
  vendor events remain unclassified; this does not establish absence of LMP on air.

A separate 15-minute retained-log query before the HCI experiment found one
`Server.BandSwitchManager` message: `EnableHRB 1, forceSupportHRB 0`. This is a
configuration observation. The earlier short live log samples were not retained
as raw files and did not establish a band switch or an allowed-bands mask.

## Limits

This capture covers an already-established connection and microphone transitions,
without initial connection setup, a triggered band switch or an over-the-air
measurement. It directly supports synchronous voice fallback in the tested
sequence. It does not establish a wireless codec, radio band, ULLA support, or
equivalent vendor-command semantics in the Mac and earbud firmware.

PacketLogger marked 17 items as errors; their causes remain uninvestigated.
The source trace is private and is not included in this directory.
