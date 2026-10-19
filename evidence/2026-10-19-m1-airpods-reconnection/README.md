# M1 / Broadcom: capture spanning AirPods reconnection

Observed on 2026-10-19 by `dberweger2017`, on the same M1 / Broadcom host and
AirPods firmware as the [first run](../2026-10-18-m1-airpods-established-session/README.md).
See [summary.json](summary.json) and the [evidence policy and validation notes](../README.md).
Source: [reconnection and parameter follow-up](https://github.com/mstampfli/openulla/issues/3#issuecomment-6266962214).

## Procedure

Apple's temporary Bluetooth logging profile was installed with consent. Actual
PacketLogger HCI traffic was verified before disconnecting the AirPods; this run
did not require a Mac restart. A filtered `bluetoothd` log stream ran alongside it,
covering BandSwitchManager, ULLA, allowed-bands text and link-quality reports.

The AirPods were disconnected and reconnected through their device controls while
capture was running. The Bluetooth radio remained on. There was no re-pairing,
controller reset or Wi-Fi setting change. The trace contains a successful
Connection Complete event matched locally to the AirPods, with status `0x00` and
link type 1/ACL.

GeForce NOW was running. Reconnection briefly made the AirPods the default input
and GeForce followed it, so the trace includes a short SCO interval. The built-in
input was restored, CoreAudio verified GeForce using it, and the AirPods output
endpoint returned to 48,000 Hz during the following playback window.

The trace was stopped and saved locally. The profile was removed immediately
after saving, its logging configuration was verified absent, and PacketLogger was
closed. The filtered log stream was stopped. No raw files were uploaded.

## Observations

- The file contains 23,767 records spanning 316.030 seconds of timestamps,
  including prelude traffic and the reconnection.
- `0xFD02` occurs once, with five parameter bytes and first parameter `0x00`.
  Command Complete with status `0x00` follows 2 ms later. The command occurs
  10.713 seconds after the successful Connection Complete event.
- `0xFD4B` occurs 38 times, each with 33 parameter bytes. Its nearest occurrence
  is 10.855 seconds before `0xFD02`, or 142 ms before Connection Complete.
- There are no commands with opcodes `0xFDEE`, `0xFD7C`, `0xFD7D`, `0xFD5F` or
  `0xFE50`. Counts for the other requested band commands are in the JSON summary.
- No dedicated LMP records or Wireshark-dissected LMP packets were found. The
  `0x7B/0x0D` versus `0x7F/0x0B` packet-type negotiation question remains open.
- Of 532 parsed synchronized log events, 526 are `Server.Audio` link-quality
  reports and six are `Server.BandSwitchManager` messages. All six say
  `EnableHRB 1, forceSupportHRB 0`. No allowed-bands change event, allowed-band
  mask, evaluation state dump or ULLA message was observed.

Wireshark independently confirmed record-type counts, the first `0xFD02`
parameter, and absence of the five requested opcodes listed above.

## Limits

This adds normal connection setup to the observations, but no band switch was
triggered. The first `0xFD02` byte is reported without assuming that the Mac's
Broadcom firmware implements the earbud handler's meaning. The five-byte
`0xFD02` and 33-byte `0xFD4B` lengths are preserved for that comparison.

No captured LMP does not mean no LMP was exchanged over the air. The transient
microphone selection is part of the run, so this is not an isolated playback-only
experiment. PacketLogger marked seven items as errors, still uninvestigated.
Raw captures and logs remain private; the reported counts do not prove completeness.
