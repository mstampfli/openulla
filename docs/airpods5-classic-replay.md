# Classic setup on Vision Pro M5 24M372 and AirPods 5 9B47

Offline replay connects the two controller images across the mode-0 setup transaction. The M5 transmitter generates the four bytes at offsets 1–4 of LMP `0x73` in software on its ordinary path. The earbud uses the received word in a 48-bit controller template. These observations extend [the setup request description](protocol.md#51-opcode-0x73-the-setup-request) to the two builds below. [Issue #5](https://github.com/mstampfli/openulla/issues/5) records the physical Pi and AX200 tests, which reached the earlier AAP acknowledgement.

## Source material

| Role | Public Apple asset | Component and SHA-256 |
| --- | --- | --- |
| Source | [Vision Pro M5 restore 24M372](https://updates.cdn-apple.com/2026FallFCS/f07f1823-ed20-434c-b3a5-035e03e17e3a/Apple_Vision_Pro_M5_27.0.1_24M372_Restore.ipsw), RealityDevice17,1 | `acib` in `Firmware/n301aw3btulla/ftab.bin`, `32373dd3e5984f218d1da537d4a43413853fc00f7782eb48aee34bc8c18a8b9d` |
| Sink | [AirPods3,8 accessory asset 9B47](https://updates.cdn-apple.com/MobileAssets2026/patches/140-17192/71427E7B-8EF9-45CF-8724-D5D42C872065/com_apple_MobileAsset_UARP_A3440/40f3d493d1fa54d05e951e6ea04d59e5768c0a0a.zip), board b868map | `acib`, `c40f76a92fb0afbdeb2a77a6526e7a087ccf8b548535df73f18799a603558047` |

Offsets are relative to the named component. The accessory asset's version matched the version reported by the paired `0x2030` earbuds. The two firmware images were executed in stages on a host, with synthetic link state, timestamps, scheduler callbacks and queue delivery.

## Setup word and receiver template

At source offset `0x1db1a` the Classic start path requests property `0x63`. Its provider at `0xdc5ec` draws from software PRNG state, shapes the word and tests candidates. On exhausting the counter it uses a local clock-derived input. With four synthetic initial words (1, 2, 3, 4), the first ordinary draw is `0x64a023f5` and the resulting little-endian field is `26 0d 44 af`.

| Successive field at offsets 1–4 | Bit 15 of LE32 | Six bytes in the 9B47 formatter |
| --- | ---: | --- |
| `26 0d 44 af` | 0 | `55 55 26 0d 44 af` |
| `35 82 f2 2e` | 1 | `aa aa 35 82 f2 2e` |

On the sink, the parsed word reaches the stream object's offset `0x38`. The formatter at ACIB `0x98498` selects the alternating 16-bit prefix and passes a length of 48 to the descriptor builder at `0x959e0`. These are memory bytes. Their RF direction, over-the-air ordering and position in a successful transmission are open.

The [Python reference](../tools/vision_setup_word.py) can generate the field from a synthetic seed with the standard library. The [optional verifier](../tools/verify_vision_setup_word.py) executes the M5 generator from a locally supplied, SHA-256-checked component. It maps synthetic RAM, stubs two logging calls and supplies a fixed clock on the fallback path. With deterministic seeds it compares 128 sets of three successive words. The run matched all 384, with seven rejected candidates. The dedicated counter-exhaustion replay also found that even an otherwise qualified tenth draw takes the clock fallback. The optional verifier exercises the 384-word set; the branch and media observations below come from separate contributor replays.

```sh
python3 tools/vision_setup_word.py 1 2 3 4
python3 -m pip install unicorn==2.1.4
python3 tools/verify_vision_setup_word.py /path/to/24M372/acib
```

## One transaction under four link conditions

Every row starts with the same synthetic TID-0 request, `e6 26 0d 44 af 96 00 03 01`. The configuration word is 150. The last two bytes carry parameter 5's low two bits and bit 3. The source and sink ran their own serializer and receive paths. The harness supplied a valid established-link record and delivered handoffs between stages.

| Condition | Earbud output | Source behavior |
| --- | --- | --- |
| Accepted, source locally ready | `06 73` | Issues configuration `e8 7e 34 12 00 45 00`. |
| Accepted, source locally pending | `06 73` | Retains the acceptance. The same configuration follows a local ready event. |
| Sink rejects the link state | `08 73 86` | Reports the refusal reason and does not issue configuration. |
| Sibling participation | Forwards the request on the sibling link; returns `06 73` on the original link once both are ready | The sibling verdict was supplied by a callback. This case measures the sink's forwarding and reply ordering. |

The source readiness gate is at `0x682fa`–`0x68316`; the sink's dual-link checks are at `0x67896` and `0x6789e`. In the accepted example, configuration `0x74` schedules a corrected 26-bit link count plus 40 and a signed 16-bit correction. For synthetic count `0x123456`, zero clock offset and correction +69, the serialized fields are `7e 34 12 00 45 00`. The sink consumes the clock and correction through local scheduling. No physical switch instant was measured.

## What the replay can identify about audio

The sender and receiver also have observable local audio paths. On this source DSP, two DMA channels use 240-byte strides. On the 9B47 sink, a constructed Classic v1.1 type-3 local record with bitmap 3 carries 60 known stereo sample pairs in two 240-byte phases. The DSP replay recovered all samples on the corresponding sides. The original source PCM has yet to be traced through a normal playback DMA block to its radio PDU. These results establish a local memory layout and leave the on-air media format, end-to-end fidelity and audio latency open.
