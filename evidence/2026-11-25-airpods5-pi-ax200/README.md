# AirPods 5 start acknowledgements on Pi 4B and Intel AX200

One pair of AirPods 5 (Apple product ID `0x2030`) acknowledged an AAP mode-0 start on both hosts. Three follow-up Pi runs ended in HCI Connection Timeout about 31 seconds after the acknowledgement. An open-only Pi control kept the ACL alive throughout its 60-second observation window. These measurements extend [issue #5](https://github.com/mstampfli/openulla/issues/5).

## First start on each host, November 25

Both hosts ran BlueZ 5.82, used an existing or newly completed pairing and exercised service 4 on PSM `0x1001` with openulla revision `454cc316`. Each opened service 4, received version 1.3, closed the channel, opened a fresh one and sent one start. The two directions of that channel carried these AAP payloads.

```text
host to earbuds    04 00 04 00 36 00 00
earbuds to host    04 00 04 00 37 00 00 00 00 00
```

| Host | Start → status 0 | Request to reply | End of observation on that connection |
| --- | --- | ---: | --- |
| Pi 4B onboard BCM4345C0 | #356 → #358 | 231.393 ms | Host requested channel closure at #359, +3.773 s. |
| Intel AX200 Bluetooth, USB passthrough to Debian 13 under WSL 2 | #570 → #572 | 147.274 ms | Authenticated Payload Timeout Expired at #573, +29.992 s. Disconnect Complete at #574, +31.439 s, reason `0x08`. |

The AX200 open-only channel was host-closed after about two seconds. It does not give a 60-second control for the AX200. In a separate Windows 11 25H2 run on the same AX200 (driver 24.10.0.4), AVDTP configuration and streaming events identified A2DP AAC at 48 kHz, stereo. These start timings measure an AAP round trip. Audio latency was not measured.

## Four Pi comparisons, November 26

The same bonded pair reconnected to the Pi's stock BCM4345C0, LMP subversion `0x6119`, build `0382`, kernel `6.18.34+rpt-rpi-v8`. The BlueZ monitor recorded AAP and Broadcom local TX and RX diagnostics from before connection. Each file has one target ACL. Times below use the host monitor clock. The last two columns start at the status reply.

| Condition | Start → status 0 | Request to reply | Attributed LMP, TX / RX after reply | ACL outcome |
| --- | --- | ---: | ---: | --- |
| Service open only | No start | — | — | Host closed the AAP channel at #194. The peer still answered a standard unsniff request (#197 → #199) at capture time 63.894 s. |
| Start, then query clock offset | #134 → #136 | 237.438 ms | 6 / 1 | #148, +31.390 s, reason `0x08` |
| Start, then listen only | #135 → #138 | 412.332 ms | 6 / 1 | #159, +31.470 s, reason `0x08` |
| Start, then request stream | #134 → #137 | 408.210 ms | 4 / 1 | Host sent AAP `0x38` at #138 (+336 µs). Disconnect at #145, +31.149 s, reason `0x08`. |

In the clock-query file, the status reply was followed by outgoing Classic AFH updates and an incoming channel classification (#139). The local clock-offset request diagnostic at #147 preceded Disconnect Complete by 7 µs. No clock-offset response was captured. The timing describes host records and gives no RF transmit time.

A separate standard reconnect provides a positive control for the monitor. Pi TX `1e 01` at #68 was followed by peer RX `06 0f` at #69, 25.454 ms later, with the same TID. This is an acceptance of standard opcode `0x0f`. In the four comparisons, each file has 15 LMP diagnostics whose address suffix could not be assigned to an active target connection. They are excluded from target counts.

**No host submitted LMP `0x73` in these experiments.** The four Pi files contain no attributed `0x73` and no attributed acceptance or refusal referencing it. The peer's response to a real request is unknown. The open-only comparison concerns one pair on one Pi. The repeated timeout does not identify its cause, a frequency change or a media bearer. There was no RF receiver. An earlier connection attempt that ended in Page Timeout before AAP opened is outside these four runs.

## Checking the observations

**Why no directed setup result is reported.** In a separate offline replay of the Pi's stock 0382 HCD (63,806 bytes, SHA-256 `51c45e77ddad91a19e96dc8fb75295b2087c279940df2634b23baf71b6dea42c`) with a public same-model ROM and synthetic connection, a nine-byte standard `0x27` control reached the emulated transmit queue. With only the opcode changed to `0x73`, the stock path copied the input and returned no transmit descriptor. The replay stopped there. The controller's on-device ROM digest was unavailable. The AX200 audit likewise found no verified host operation for this transaction in the paths examined on firmware `27-17.2025`. Neither audit measured a peer refusal.

[runs.json](runs.json) lists one-based btsnoop frame numbers, relative microseconds, source byte sizes and SHA-256 for all seven private files. The November 25 start captures have AAP visibility. The four November 26 files add controller diagnostics in the same ACL window. Complete traces remain private. Readers with equivalent local captures can inspect AAP with `tools/capture_decode.py` and the Broadcom diagnostics with [inspect_pi_lmp.py](../../tools/inspect_pi_lmp.py).

```sh
python3 tools/capture_decode.py "$capture"
python3 tools/inspect_pi_lmp.py "$capture" --peer "$peer"
```

The diagnostic reader qualifies the controller manufacturer and matches the recorded address suffix to a unique active Classic connection for the supplied peer. Its nine-byte setup prefix, if present, uses the expected protocol length. The padded diagnostic has no independent over-the-air length. An unobserved opcode on a capture with no usable LMP diagnostics carries no negative evidence.
