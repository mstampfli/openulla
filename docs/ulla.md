# ULLA: Apple's AirPods Pro 2 USB-C to Vision Pro link

Facts about the proprietary radio link between AirPods Pro 2 (USB-C) and Apple
Vision Pro. Every statement here comes from one of three kinds of source and
says which: Apple's own text (FCC exhibits, patents), the firmware disassembly
(with the address), or a measurement on a Linux host. Nothing is inferred.
What is not known is listed as open, not filled in.

## 1. Sources

| Source | How obtained |
|---|---|
| FCC ID BCG-A3047 (AirPods Pro 2 USB-C, left bud) | fccid.io exhibit PDFs, text extracted with pdftotext (`tools/fetch_fcc.sh`, index in `docs/fcc/README.md`) |
| FCC ID BCGA2117 (Apple Vision Pro) | same |
| FCC ID BCGA3240 (Apple MacBook Air, model A3240) | its narrowband test report, looked up by hand in a public FCC listing; not covered by `tools/fetch_fcc.sh` |
| FCC ID BCG-E9198A (Apple smartphone, models A3719/A3720/A3721) | its grant, looked up the same way; not covered by `tools/fetch_fcc.sh` |
| FCC ID BCG-A1978 (Apple Watch), FCC ID BCGA2124 (Apple iPad) | their public FCC listing indexes only: exhibit titles and dates. The reports themselves were not read, so nothing is claimed about their contents |
| FCC ID BCGA2918 (Apple laptop, model A2918), FCC ID BCGA3593 (Apple digital media device, model A3593) | same, listing indexes only: exhibit titles, dates and equipment class |
| US 12683269 B2, US 12706371 B2 (Apple) | Google Patents |
| AirPods firmware 9A348 for A3048 (right bud) | Apple's public asset catalog (mesu.apple.com), fetched with `tools/fetch_firmware.sh`, parsed with `tools/uarp.py` and `tools/ftab.py` |
| Measurements on a Linux host (Intel AX211, BlueZ 5.82, PipeWire 1.4) | btmon, bluetoothctl, PipeWire |

Firmware image: `com_apple_MobileAsset_UARP_A3048/5d0896d881bbc7e321f89b4ada11b31cf8b5569c.zip`,
11,455,085 bytes, SHA-256 prefix `a04ff3f0d74e47e4`. Signed, not encrypted.
The image is not redistributed here.

## 2. FCC exhibits (Apple's declarations)

### 2.1 Grants, AirPods Pro 2 USB-C (BCG-A3047)

| Grant | Band | Power | Modes tested | Granted |
|---|---|---|---|---|
| 15C | 2402 to 2480 MHz | 17.7 mW peak | Bluetooth BDR, EDR, LE, HDR | 2023-09-12 |
| 15E | 5157 to 5245 MHz (U-NII-1) | 9.8 mW conducted | NB UNII BDR, HDR, LE | 2023-09-12 |
| 15E | 5731 to 5844 MHz (U-NII-3) | 21.7 mW conducted | NB UNII BDR, HDR, LE | 2023-09-12 |
| 15E VLP | 6108 to 6420 MHz (U-NII-5) | 0.73 mW EIRP | NB UNII GFSK and pi/4 DQPSK, 1 and 2 Mbit/s | 2024-08-30 |

The Vision Pro (BCGA2117) carries NB UNII BDR, HDR and LE grants in the same
5 GHz bands (2024-01-16) and a 6 GHz Very Low Power grant dated 2024-08-30.
The AirPods' 6 GHz report names the Vision Pro (A2117) as the companion device
used to establish the link under test.

### 2.2 Declared capabilities (quoted)

AirPods Pro 2 USB-C: "Bluetooth (1x, BDR, EDR, LE1M, LE2M, HDR4, HDR8, HDRp4,
HDRp8), NB UNII (1x, LE2M, HDR4, HDR8, HDRp4, HDRp8)." The 6 GHz report adds
BDR to the NB UNII list.

Vision Pro: "802.11b/g/n/ax WLAN, 802.11a/n/ac/ax UNII, Bluetooth (1x, EDR,
LE1M, LE2M, HDR4, HDR8), NB UNII (1x, BDR, LE1M, LE2M, HDR4, HDR8, HDRp4,
HDRp8)."

### 2.3 Physical layer statements (AirPods HDR report, quoted)

- "The hopping sequence is pseudorandom"
- "79 channels can be used at a time for hopping"
- "The receiver input bandwidth equals the transmit bandwidth"
- "The receiver hops in sequence with the transmit signal"
- "Narrowband UNII can only hop within the same UNII band and cannot hop between band"
- "This device is capable of operating in hopping and non-hopping mode."
- Test channels: 5157, 5201, 5245 MHz and 5731, 5788, 5844 MHz; 6108, 6264,
  6420 MHz in the 6 GHz band.

Reported values:

| Mode | Data rate (stated) | 26 dB bandwidth | 99 % occupied | Duty cycle |
|---|---|---|---|---|
| NB UNII BDR | 1 Mbit/s | about 1 MHz | | 76.8 % |
| NB UNII LE2M | 2 Mbit/s | 2.08 MHz | | 80.0 % |
| NB UNII HDR4 | 4 Mbit/s | 2.557 to 2.559 MHz | 2.252 to 2.281 MHz | 78.0 % |
| NB UNII HDR8 | 8 Mbit/s | 5.072 to 5.101 MHz | 4.449 to 4.548 MHz | 78.0 % |
| NB UNII HDRp4 | | 2.736 to 2.779 MHz | 2.402 to 2.435 MHz | 87.4 % |
| NB UNII HDRp8 | | 5.415 to 5.423 MHz | 4.822 to 4.859 MHz | 88.2 % |

(Table 7-2 of that report puts the 99 % occupied column before the 26 dB
column. An earlier revision of this document had the two swapped.)

The AirPods reports state the modulation types GFSK and pi/4 DQPSK without
saying which belongs to which HDR mode; another Apple filing does, see 2.4.
HDR4 and HDR8 were also measured in the ordinary 2.4 GHz Bluetooth band
(2404, 2441, 2480 MHz) at 2.30 MHz and 4.52 MHz. The "p" variants are not
explained anywhere; measurably they are slightly wider and run at a higher
duty cycle.

Link setup, quoted from the 6 GHz report: "after establishing communication
between the EUT and the companion device, NB UNII HDR is used to maintain
communication and traffic. NB UNII BDR and NB UNII LE are used for
establishing the initial connection with the companion device." The report
states transmit power control is triggered by RSSI.

Both devices' reports carry simultaneous-transmission tables listing the 2.4 GHz
Bluetooth radio (all modes, including HDR and HDRp), 2.4 and 5 GHz WLAN and the
NB UNII radio as combinations certified to transmit together.

### 2.4 The same radio modes are in a MacBook

The narrowband test report for FCC ID BCGA3240, an Apple MacBook Air (model
A3240), declares narrowband operation in "US: 5162-5245 MHz" and "US and
Canada: 5733-5844 MHz", the same two bands the AirPods use, and states: "Both
power modes support HDR4 (4-DH5) and HDR8 (8-DH5). iPA mode additionally
supports Basic Rate (DH5)." It supports those modes on "two different cores
(Core 0 and Core 1)" and on both simultaneously.

Three things follow:

- The radio capability this link needs is **not unique to the Vision Pro**. A
  current MacBook has it. What a Mac's software does with it is a separate
  question and is not established here.
- That report names the modulation per mode in its measurement headers, as
  "pi/4 DQPSK (4-DH5)" and "pi/4 DQPSK (8-DH5)". So **HDR8 is pi/4 DQPSK, not
  8DPSK**, and both HDR modes ride in DH5-shaped, five-slot packets at 4 and
  8 Mbit/s, written 4-DH5 and 8-DH5 in Bluetooth's own packet naming.
- Two independently usable radio cores match two facts recorded elsewhere here:
  the AirPods' tuning map carries separate transmit power settings for two
  source links, and Apple's patents claim each earbud holds its own direct link
  to the source.

Its measured bandwidths (26 dB / 99 %, MHz) are 1.248 to 1.252 / 0.996 to 1.000
for DH5, 2.776 to 2.800 / 2.344 for 4-DH5, and 5.340 to 5.355 / 4.635 to 4.650
for 8-DH5. Those sit within a few hundred kilohertz of the AirPods' HDR and
HDRp figures above, which is consistent with one PHY family. It is not enough to
claim that a given Mac mode is the earbuds' HDR4 rather than HDRp4, and no such
mapping is claimed.

**The names 4-DH5 and 8-DH5 have since been matched to the firmware.** The
earbuds' controller holds an eighteen-entry ACL packet-type table where
Bluetooth defines twelve, and the six extra entries are selectable only through
a third packet-type table that Apple's own link-manager request can negotiate
and the specification's cannot. Those six are three slot lengths times two
rates; their payload maxima divided by 4 and 8 Mb/s give one consistent payload
window per slot length, the way the specified entries do at 1, 2 and 3 Mb/s; and
the five-slot pair carries 1288 and 2586 bytes. [protocol.md](protocol.md)
section 5.13 has the tables, the addresses and the reasoning. The firmware never
writes "HDR", so the identification of those two rates with the HDR4 and HDR8 of
the filings quoted above is this project's reading; the table is not.

Recent iPhones carry a narrowband UNII radio in the same two bands as well: the
grant for FCC ID BCG-E9198A states that besides 802.11 the device "supports
narrowband UNII with bandwidths of 1 MHz, 2 MHz, 4 MHz and 8 MHz in UNII bands
1 and 3 and 802.15.4ab in UNII-3". That grant names bandwidth classes rather
than modes, and the same sentence also covers a ranging standard, so it does not
by itself show that an iPhone implements the HDR modes.

Neither of these two filings is fetched by `tools/fetch_fcc.sh`, which covers
only the AirPods and Vision Pro exhibits. To reproduce them, look up FCC IDs
BCGA3240 and BCG-E9198A in a public FCC listing: the MacBook statements come
from its narrowband test report, the iPhone sentence from the grant itself.

### 2.5 Which other Apple devices file narrowband reports

Besides the MacBook Air of 2.4 and the iPhone grant quoted there, two listing indexes are
worth recording. Only titles, dates and equipment classes were read, from the indexes, not the
reports, so nothing is claimed about what the reports contain.

- **FCC ID BCGA2918**, an Apple laptop (model A2918), carries an exhibit titled "Narrowband
  part1" dated 2023-10-30, beside 2.4, 5 and 6 GHz parts. The nearest earlier laptop filings
  looked at (BCGA2941 of 2023-06-05, BCGA2141 of 2019, BCGA1989 and BCGA1990 of 2018) show no
  narrowband exhibit in their indexes. So the earliest Mac narrowband report found so far is
  from October 2023, weeks after the USB-C AirPods Pro 2 went on sale, and the 2018 and 2019
  machines have none. This is not an exhaustive search of Apple's filings.
- **FCC ID BCGA3593**, an Apple digital media device (model A3593), latest application
  2026-10-13, is listed under the 6 GHz very-low-power equipment class. Two public FCC
  databases disagree about its frequency list, one showing 2412-2472 and 6885-7085 MHz and the
  other a document covering 6106-6425 MHz, so the only safe statement is the equipment class
  and the date. It is recorded here because it is the first non-wearable, non-computer Apple
  device seen with a 6 GHz very-low-power class, and because 6106-6425 is the same U-NII-5
  span the earbud firmware's own band plan covers (4.5).

### 2.6 HDR is not ULLA, and HDR is not new

Two different things appear in these exhibits: a set of high-rate Bluetooth
packet modes, and a protocol. They are separate, and the first one is not the
subject of this report.

HDR4 and HDR8 are packet modes of the radio:

- Apple's reports measure them as five-slot packets at 4 and 8 Mbit/s with
  pi/4 DQPSK modulation, named 4-DH5 and 8-DH5 in Bluetooth's own packet
  naming (2.3 and 2.4 above).
- They are declared in the ordinary 2.4 GHz Bluetooth band as well as in the
  narrowband 5 GHz bands (2.1 and 2.2 above), so they are not inherently a
  5 GHz feature.
- They are not new. The public FCC listing index for FCC ID BCG-A1978, an Apple
  Watch, carries an exhibit titled "Bluetooth HDR Test Report" dated
  2018-09-12, and the listing for FCC ID BCGA2124, an iPad, carries one dated
  2019-03-18. Only those titles and dates were read, from the listing indexes,
  not the reports. The sole claim made here is therefore the one they support:
  Apple was filing Bluetooth HDR test reports in 2018 and 2019, years before
  the Vision Pro.
- The rates are Apple's own, not the standard's. The packet-type field a host
  sends with a connection request has twelve defined ACL packet types, the
  largest being 3-DH5 at 3 Mbit/s, and no bit of it can express 4 or 8 Mbit/s; a
  standard host cannot ask for an HDR mode over that interface whatever its
  controller supports. Bit values and the measurement that confirms it are in
  `measurements.md` section 9. That is consistent with the earbud firmware
  reaching these modes through a vendor command (`protocol.md` section 6) and
  link-manager PDUs (`protocol.md` section 5) instead of the standard path.

ULLA is the protocol: the firmware's own name for the feature (4.2 below), with
a session manager, a host command, earbud-to-earbud messages and link-manager
PDUs, written up field by field in `protocol.md`. HDR is transport it can use.

The consequence for reading any Apple filing: a device declaring HDR4 and HDR8
is declaring a radio capability. It is not evidence that the device implements
ULLA, and no such inference is drawn anywhere in this report. Which devices
other than the Vision Pro drive this link remains open (section 7).

## 3. Apple's patents (quoted)

US 12683269 B2 and US 12706371 B2:

- "a non-Bluetooth, ultra-low-latency audio communications protocol optimized
  to support low latency and high data rate audio streaming from device 10 to
  earbuds 22L and 22R"
- "may be conveyed using an unlicensed band at 2.4 GHz and/or an unlicensed
  band between 5-6 GHz"
- "The ultra-low-latency audio protocol may involve communications without
  performing time division duplexing"
- "requires both earbuds 22R and 22L to convey radio-frequency signals 24A
  directly with device 10" and audio "is concurrently (e.g., simultaneously)
  transmitted to both earbuds"

## 4. Firmware

### 4.1 Container

The UARP SuperBinary (format 3, version 9.0.348.0) holds 20 payloads
(`tools/ftab.py ... payloads` lists them). `FTAB` is a plain "rkosftab" table
of 46 entries, 7,811,072 bytes: a 32-byte header, the magic at 0x20, then
16-byte entries of tag, offset (u32 LE), size (u32 LE) and padding, with the
offsets relative to the table start (`tools/ftab.py` lists and extracts it).
Entries used here: `acib` (1,066,208 bytes) and `rkof` (2,088,320 bytes). Both
are Cortex-M images at base 0 (Thumb). `acib` starts with the stack pointer
0x1001bd80 and the reset vector 0x109.

`CTAB`, the second-largest payload at 5,898,277 bytes, is that same table
again: one LZ stream, 75.5% of the table's size, holding the whole of it. It is
worth saying so because the tag invites a session of work and there is nothing
inside it that `FTAB` does not already give:

- Its stream is unframed. None of libcompression's block magics (`bvx-`,
  `bvx1`, `bvx2`, `bvxn`, `bvx$`) occurs anywhere in it, so it is neither
  LZFSE nor the LZVN container, and no zlib, gzip, xz, LZ4-frame or bzip2
  header occurs either. Raw inflate, at each of the first 64 start offsets of
  the `acib` region, yields nothing.
- The manifest is literal enough to read without decompressing. The magic
  `rkosftab` sits at 0x3d, and 33 of the table's 45 non-empty entries have
  their offset in the stream as a three-byte little-endian literal, 20 of them
  with their size immediately after it, in the table's own order. The offsets
  and sizes are `FTAB`'s own, to the byte.
- What is missing is what an LZ leaves out: the zero high byte of each u32, the
  four bytes of padding per entry, and any value the table has already emitted.
  Two tags are a character short in the stream (`\x09tpf`, `\x03cib`), which is
  a match running on from that padding into the tag. That is why the records
  read as `{tag, 3-byte offset, 3-byte size}` packed tight rather than as
  16-byte rows.
- The output is the table, not a second build. Each payload's long literals
  appear in their own band of the stream and the bands follow the manifest's
  order. The median position **within the stream** of each payload's literals,
  in file order, is 0x1477d for `rkos`, 0x7ea24 for `aopf`, 0x18cd30 for
  `acib`, 0x198b42 for `phyb`, 0x25ef84 for `sbd1`, 0x28fea3 for `apmu` and
  0x41cd4b for `rkof`. Those are offsets into the stream and not into the
  payload each one names, which is why they are written this way round. The
  literals are the same strings the extracted payloads hold.

`tools/verify_claims.py` checks the first two of those four. The LZ variant
itself is unidentified: the stream is not LZVN at the start of a payload's
region, which was tested with a compiled decoder, and nothing in this project
depends on the answer.

### 4.2 Strings

Present verbatim in the image:

- `ULLA 1.0`, `ULLA 1.1`, "Obsolete ULLA version 0x%04X, local version 0x%04X"
- `ULLA_MODE_ON`, `ULLA_LITE_MODE_ON`, `ULLA_MODE_BT_ACL_ACTIVE`,
  `ULLA_MODE_CHANGE_SUCCESS`, `ULLA_MODE_CHANGE_SETUP_REJECTED_BY_PEER_ERROR`,
  `ULLA_MODE_CHANGE_LSTO_ERROR`, `ULLA_MODE_CHANGE_FAIL_TO_BE_ESTABLISHED`
- `lm_ulla_trans_sec_handle_setup_lmp_accepted`,
  `lm_ulla_trans_sec_handle_setup_lmp_not_accepted`,
  `lm_ulla_trans_source_ulla_teardown_init_handle_lmp_not_accepted`
- "ULLA: tried transmitting packet of size %d, maximal size is %d"
- In `rkof`, name tables for the ULLA manager: states (INIT, IDLE, HOST_SETUP,
  WAIT_START_SESSION_RESP, WAIT_LOCAL_FLOW_OFF_DONE, CONTROLLER_SETUP,
  DSP_SETUP, SESSION_OPEN, STREAM_HOST_SETUP, STREAM_OPEN, WAIT_AUDIO_STOPPED,
  STREAM_HOST_TEARDOWN, WAIT_STOP_SESSION_RESP, CONTROLLER_TEARDOWN,
  HOST_TEARDOWN, WAIT_ABORT_RESP, NOCHANGE), 40 event names, and UTP message
  names (START_SESSION_REQ/RSP, START_STREAM_REQ/RSP, STOP_SESSION_REQ/RSP,
  STOP_STREAM_REQ/RSP, DSP_CFG_CMPLT, SESSION_STOPPED, SOURCE_DISCONNECTED,
  STATS, CONFIG_ULLA_STATS). Listed in table order. The numeric value of each
  name is not established: no code indexing these tables has been read yet.

### 4.3 Link-manager transition in `acib`

Read from the Ghidra decompile and checked by hand. Addresses are offsets into
`acib` of build 9A348. The firmware has no symbols.

- 0x66d58 sets a global byte to 5 and calls 0x68450, which registers handler
  0x66f9c under id 0x36 through the link object's virtual table, sets state 4
  and dispatches event 5.
- Dispatcher 0x68294: `next = handlers[state](ctx, event, link)`, table at
  0xf2664 with nine handlers (0x68588, 0x685f8, 0x6869c, 0x68788, 0x687fc,
  0x6886c, 0x688f4, 0x688f8, 0x68924).
- State 0 (0x68588), event 0: sends a link-manager PDU with opcode 0x73, then
  calls 0x2515c(link, 5).
- State 1 (0x685f8), event 1: reads parameter ids 0x12 (4 bytes) and 0x13
  (2 bytes) and sends a PDU with opcode 0x74.
- State 2 (0x6869c), event 3: calls 0x66a00, which reads parameter ids 0x19a,
  0x19f, 0xc9 and 0x23 and tags its descriptor with 0x74.
- State 3 (0x68788), event 4: calls 0x66b98, 0x62cb8(1), 0x62058 and 0x10548.
- State 5 (0x6886c): event 2 sets bit 0x10 of the flag byte at ctx+0x23;
  event 6 calls 0x5ef34. The handler moves to state 7 when bits 0x04, 0x08 and
  0x10 of that byte are all set, and otherwise stays in 5.
- 0x68504 is an assert that logs the string "ULLA_TRANS: ERROR-
  lm_ulla_trans_source_ulla_teardown_init_handle_lmp_not_accepted". That name is where the
  firmware's own words for these machines come from: a *transaction*, the role *source*, and a
  phase *ulla_teardown_init*. Two more strings name the third role *sec*.
- The whole of that machine, the sink's eight-state and the sec role's seven-state versions of
  it, the events with their posting sites, both entry points of each, the module's phase and
  which result reaches the host where, are in
  [protocol.md 5.9 and 5.10](protocol.md). This section is the first reading; that one is the
  finished map, and `openulla/procedure.py` is the same tables as code.
- Raw PDU send 0x3ad7c (42 callers): writes the transaction id to byte 0 and
  the opcode to byte 1 of its record, and an extended opcode at byte 0xc only
  when the opcode is above 0x7a. 0x73 and 0x74 are sent without an extended
  opcode.

### 4.4 Receiving side in `acib`

- Parser 0x3af3c switches on the raw opcode minus one. Vendor opcodes handled:
  0x66, 0x70 to 0x77, escapes 0x7b to 0x7f.
- 0x73 goes to 0x63448, which calls the validator (0x675f4, 0x67e3c). Reject
  reasons: 1 from 0x62074 (enable flag), 2 from 0x62030 (vtable+0xb0 check),
  3 from 0x62154 (busy byte at obj+0x151), 4, 5, 6 from 0x620c8 (link state
  and role). It accepts only when the low nibble of the reason is zero. None of
  these functions compares a key, a nonce or a challenge.
- 0x74 goes to 0x6352c, which passes two values read from the body to the
  session object's vtable+0xc8. It has no accept or reject path.
- 0x72 goes to 0x3590c. Procedure table at 0xf1e78; entry 5 is 0x34bb8, which
  switches on cases 0 to 3 of a type byte and rejects with reason 0x0c when
  called in the wrong procedure state.
- Attribute table 0xf1f58, one byte per opcode: the low seven bits are a
  required link state compared against the current one; bit 7 only decides
  whether a violation is reported. There is no encryption bit. Opcode 0x73
  has attribute 0.

### 4.5 The radio's own band plan

A constant table in `acib`, eight records of seven bytes at 0xf52e8, holds the band plan that
the controller's band commands index: per band a start frequency in MHz, a width, a channel
count, a channel bandwidth, a channel spacing and the band's first channel in one global
numbering. Decoded it is the 2.4 GHz ISM band, U-NII-1, U-NII-3, an empty slot, and U-NII-5
split into four. The record index is the bit position in the allowed-bands mask, so the mask
bits are named by it. The full table, the internal checks that confirm the field assignment
and the rest of the band command family are in `protocol.md` sections 6.3 and 6.4.

Two things about it belong here rather than there. The firmware's plan contains exactly the
bands Apple's exhibits report for this product (2.1 to 2.3 above), arrived at independently of
them, which is a cross-check between Apple's regulatory text and Apple's own code. And it
contains more of the 6 GHz band than these earbuds are certified for, so the plan is the radio
family's and the regulatory selection narrows it, which is what the per-band country codes do.

### 4.6 The three A2DP endpoints, including the Apple vendor codec

The sink registers three stream endpoints through one registration function, each with a
descriptor naming the service category (media codec), the codec type, the length of the
capability bytes and a pointer to them [fw rkof 0x8a908..0x8a996, registration 0x450ac,
descriptors at 0x1c14d0, 0x1c14e8, 0x1c1500]. In each descriptor the codec type is the byte
at +0xa, its capability length the byte at +0xb and the pointer the word at +0xc, which is
what `tools/verify_claims.py` walks to check these bytes against the image: the three point
at 0x1c1530, 0x1c153e and 0x1c1544, in the order vendor, AAC, SBC.

| codec type | capability length | capability bytes |
|---|---|---|
| 0x00 SBC | 4 | `3f ff 02 35` |
| 0x02 AAC | 6 | `c0 01 8c 83 e8 00` |
| 0xFF vendor | 14 | `4c 00 00 00 01 80` then `00 80 00 8c 00 83 e8 00` |

Each is conditional on a configuration byte: AAC needs bit 1 of it, the vendor endpoint needs
it to be nonzero at all [fw rkof 0x8a94e, 0x8a982].

The AAC bytes decode as object types MPEG-2 and MPEG-4 AAC LC, 44.1 and 48 kHz, mono and
stereo, variable bit rate with a 256000 bit/s field, which is what a host sees on the air
(section 5), so the reading of these records is sound. The vendor capability is therefore
Apple's vendor id 0x0000004C, vendor codec id 0x8001, and **eight codec-specific bytes**.

What those eight bytes mean is open. Their last three are byte for byte the same variable
bit rate and 256000 bit/s encoding the AAC record uses, and the pair `00 8c` sits where AAC
carries its frequency bits and channel modes. If the layout followed AAC's with a wider
object-type field it would read as 48 kHz only, mono and stereo, capped at 256 kbit/s, but no
code that parses these bytes has been read, so that is a hypothesis to test and not a finding.
The test is cheap from any Linux host: read the endpoint's capabilities, then try a
configuration with altered fields and see which are refused.

The first half of that has been done. All three capabilities above were read back out of BlueZ
from real earbuds and **match this table byte for byte**, the vendor endpoint included, so the
eight bytes are confirmed as what the device advertises and not only as what the image holds
[measurements.md section 12; `tools/bt_link_state.py` prints the comparison]. What is left is
the other half: offering a configuration with altered fields and seeing which ones the earbuds
refuse, which is what would turn the layout from a hypothesis into a reading.

### 4.7 Which payloads hold the link's material, and which hold none of it

Every firmware claim in these documents comes from three payloads: `acib`, `rkof` and `sbd1`.
That could be where the looking happened rather than where the material is, so it was checked.
`tools/payload_survey.py` is the check and it scans whatever is in the directory you give it.

**What was measured, exactly.** The 46 entries of the `FTAB` table less `sbd2`, which is a
zero-byte record and so carries nothing, plus `CTAB` and `NERF`, which are SuperBinary payloads
in their own right: 45 + 2 = **47 files**. That is not the build. The
SuperBinary holds **twenty** payload records, and the ones not examined are `TMAP` three times,
`R1FW`, `STFC`, `STFW`, nine `AU*` records and `CMAP` twice. **`R1FW` is itself an `rkosftab`
container** and holds a second `rkos` of 335,008 bytes and a second `sbd1` of 284,096, so two
of the very tags every claim here rests on exist in a second, differently sized copy that this
project has not read. An earlier version of this section called the 47 "the whole build", which
was wrong twice over: it is about half the payload bytes, and 46 less one plus one is 46.

- **Within those 47 the link is named in four**: `acib`, `rkof`, `sbd1` and `ctab` - the last
  being the compressed copy of the whole table, so it holds the others' strings by
  construction. All sixteen case permutations of the name were tried. For three of the silent
  payloads the absence is a result rather than a gap, because they have string tables of their
  own to be silent in: `NERF` with 1,360 NUL-delimited strings of twelve characters or more,
  `aopf` with 471 and `rtpf` with 270. For the rest it says less and the survey does not
  pretend otherwise. `phyb` is separately accounted for in [protocol.md](protocol.md) 7.4, and
  `aopf` says what it is in its own strings: `AppleAOPVoiceTriggerController v600.14` at
  `aopf` 0x6f50c, though the same payload is also the power-management firmware.
- **But the name does occur outside those payloads**, in one of the records this survey did not
  read: the third `TMAP` record is an `NSKeyedArchiver` property list and names the link eight
  times, as `ulla_time_mode_both_off`, `_anc`, `_trcy`, `_auto_anc`, `_hearing_test`, `_pse`,
  `_ssl` and `_pse_plus`. Those are user-facing mode names rather than protocol material, but
  "named in three payloads and nowhere else" was not true of the build and is withdrawn.
- **The packet-type table's signature appears in two of the 47**, `acib` and `ctab`, and the
  `ctab` copies are the same table byte-packed rather than a byte-for-byte duplicate of
  `acib`'s. In `acib` the three maxima are adjacent halfwords in the two array regions 5.13
  documents. A null model of random triples drawn from the same numeric range satisfies the
  256-byte rule about one time in a hundred, so the signature is specific; it is not unique.
- **Every occurrence of `hdr` read in context is the word "header"**, in strings like `rx hdr`,
  `raw length %u < hdr` and `Clog hdr len`. The mixed-case occurrences in `acib`, `aopf`,
  `rkof` and `rtpf` are not strings at all but Thumb instruction bytes.

**One method note, because this check twice produced the opposite answer.** Counting
occurrences of a vendor opcode as a two-byte pattern says nothing in this firmware, and the
reason is worse than "once per 64 KB": these images are nothing like random, so in some of them
halfwords in the 0xFD00 range are several times commoner than a flat model predicts. So each
opcode is now judged against **its own payload's distribution of every halfword in that range**.
Measured that way **no documented opcode exceeds its own payload's 99th percentile anywhere**,
which is the conclusion - and a bare count would still have reported hits in twenty of the 47,
thirteen of which are RIFF audio files that implement no vendor command.

The ten hand-picked controls an earlier version of this section leaned on are kept in the
tool's output only because seeing them makes the point. They are **not** the instrument, and
the claim that the documented opcodes "occur no more often than the controls in any of the 47"
was false: `0xFF7C` occurs 44 times in `ls01` against a control maximum of 28, and a documented
opcode outscores all ten controls in ten of the 47 - audio files among them. The controls' own
level sits near each payload's 62nd percentile rather than its median, so they were never the
neutral yardstick they were presented as. What is evidence that a payload knows an opcode is a
handler in a dispatch table, which is how section 6 of [protocol.md](protocol.md) reaches every
opcode it names.

## 5. Measured on a non-Apple host

- The SDP record lists Audio Sink, AVRCP, Handsfree, PnP and two vendor GATT
  services. No LE Audio services. LE advertising is Apple proximity pairing
  only (type 0x07, model 0x2027).
- The standard link-manager feature pages carry no vendor bits.
- A2DP sink endpoints: SBC, AAC with a 256 kbit/s field, and an Apple vendor
  codec with id 0x8001. Its exact advertised capability is in 4.6 above.
- HFP codec offer: `AT+BAC=1,2,127,255,128,129,130,256`.
- Link-manager PDUs do not appear in an HCI capture; they never cross the host
  interface.
- Stereo A2DP and the voice link are mutually exclusive with these earbuds on
  an Intel AX211, tested in both orders with the kernel's link list as the
  witness: transport first, the voice link never establishes and the
  microphone records zeros; voice link first, acquiring the transport fails
  with an input/output error.
- Intel AX211 (kernel 7.0) reports "Secure boot is enabled", "OTP lock is
  enabled" and "API lock is enabled"; its firmware `ibt-0040-0041.sfi` is an
  Intel CSS image with a 2048-bit RSA signature.

## 6. The protocol itself

The setup protocol is recovered in full and documented separately, because it is
what an independent implementation actually needs:

- [docs/protocol.md](protocol.md): the host interface, the session state
  machine, the earbud-to-earbud messages, the link-manager PDUs, the three
  procedures the controllers run to drive them, the eleven vendor commands that
  reach an ULLA session and the two that enable the features the rest are gated
  on, each item citing the firmware address it was read from.
- [docs/measurements.md](measurements.md): what was observed on a Linux host,
  including a session started from an ordinary laptop with no Apple
  authentication of any kind.

## 7. Open

Not recovered, and not to be filled in until it is:

- The over-the-air packet format **below the payload header**: the access code,
  the header coding, the guard and trailer, and the hopping sequence. The parts
  above it are no longer open, and are in [protocol.md](protocol.md) 5.13 and
  7.3: the six packet types with their payload maxima, their baseband TYPE
  codes, their slot counts, and the payload header's twelve-bit length field.
  What remains is produced by radio hardware
  programmed through registers, and the register semantics are not public. The
  PHY payload `phyb` does carry strings, which is worth stating correctly: a
  build tag, source paths under `WxBtPhy/firmware/w3` for more than one
  component, the fault handlers, and digital-pre-distortion and
  analog-front-end logging. That is transmit-chain calibration, not framing, so
  the conclusion stands while the earlier wording ("no symbols or strings")
  does not. The payload's structure now says the same thing independently of
  its strings: it is M-profile ARM code, at least ARMv7-M, for a third
  processor it calls BTP in its own diagnostics, opening with a 48-word
  exception vector table whose thirty-two device interrupt slots hold
  twenty-five entries, thirteen of them unhandled stubs and twelve handlers for
  the calibration blocks. Two earlier descriptions of this payload were
  narrower than the file: it has far more than "about two dozen" printable
  strings, and more than one source path. Detail in
  [protocol.md](protocol.md) sections 7 and 7.4.
- The audio codec. The DSP payload `sbd1` names exactly one codec, Opus, and
  that one sits among the Siri uplink names and is called from there; AAC, SBC,
  LC3, ALAC, FLAC, AptX and LDAC appear nowhere in it. So the one codec this
  firmware implements is on another path, and this one has a configuration
  entry point of its own instead - `ullaDecConfig`, beside the standard
  `encConfig` in the same module. What that entry point sets is not
  established. [protocol.md](protocol.md) section 7.
- Whether the audio payload is encrypted. The setup establishes no key material
  of its own, but whether the radio keeps applying the ordinary link cipher
  after the mode change is a hardware question these files cannot answer.
- The maximum ULLA packet size, in one narrow sense only: the firmware carries a
  message about it through a numbered trace mechanism whose text table ships
  separately, and that entry is unreferenced, so whatever constant it would
  print is unreachable. The field a maximum packet size actually lives in is
  known, along with everything that writes it, including a hard-coded 1024;
  [protocol.md](protocol.md) sections 6.8 and 7.2.
- Meaning of HDRp4 and HDRp8, with one thing now ruled out: they are **not**
  separate packet types in the controller's table. Its rate-class array holds
  exactly four groups beyond the basic set, two of them EDR's and two Apple's,
  and it stops there ([protocol.md](protocol.md) 5.13). Whatever the "p"
  variants are, this table has no entry for them.
- Which devices other than the Vision Pro use the lite mode.
- The eight codec-specific bytes of the Apple vendor A2DP capability (4.6). The
  bytes themselves are recorded; their field layout is not established.
- The two setup fields whose values come from a runtime hardware read and from
  the two computed values of protocol.md 5.2, one a 26-bit quantity built from a
  per-link byte and one a module's own measurement less 0x57. What they measure
  is open; they do not affect acceptance, only the radio configuration.

## 8. Observed on Apple hosts, by contributors

Neither of these is our own measurement. Both were reported publicly in
[issue #3](https://github.com/mstampfli/openulla/issues/3) by the people who
made them, with their own limits stated; they are recorded here because they
bear directly on the question section 2.4 leaves open, what a Mac's software
does with a radio that has the modes. Raw captures stayed with their authors.

**An Apple N1 Mac, reported by NuiceT** (MacBook Pro Mac17,9, macOS 27.0.1,
these earbuds on 9A348, read-only observation of logs and binaries):

- bluetoothd's A2DP link-quality reports counted **every transmitted packet as
  HDR4 or HDR8 and none as 3-EDR** while these earbuds were the only connected
  audio device. So an N1 Mac does use Apple's HDR packet types with them. The
  week-long sums are not attributed per device.
- A `BandSwitchManager` evaluated these earbuds **73 times in two and a half
  hours**, every time with the 5 GHz mode requested and the link still at
  2.4 GHz, every time ending "HRB conditions are not met". No completed band
  change and no failure of a band-change command appears in the retained week.
- `/usr/sbin/bluetoothd` carries the message ids `BTAudioMsgIdUllaChangeMode`,
  `BTAudioMsgIdUllaModeChanged`, `BTAudioMsgIdUllaClearSession` and
  `BTAudioMsgIdUllaModeChangeLongTimeout`, and metric names of the form
  `ulla_time_mode_both_*`. The same metric family is in the earbuds' own RF
  tuning map, reproducible with `tools/tmap_keys.py`, so the two ends share that
  vocabulary. A name in a binary is not a running code path.

**A Broadcom Mac, reported by dberweger2017** (MacBook Pro 17,1, macOS 27.2,
BCM_4378, same earbud firmware):

- An HCI capture of an **established** AirPods session, 18,623 records, contains
  **no 0xFE50**, and its whole vendor-command inventory is `FC1C FC57 FCE2 FCE9
  FD02 FD4A FD4B FD4D FDC1 FDDD FDE4 FE2D`. Comparing that inventory against
  this project's own tables, which is ours rather than theirs: **not one of the
  eighteen vendor opcodes documented here appears in it** - no 0xFE50, none of
  the eleven session commands of protocol.md 6.6, neither feature-bit command of
  5.8, and no band command of 6.1. Their 0xFE2D is in the same numeric range and
  is not one of ours.
- That comparison was against the eighteen opcodes this project had mapped, and
  it was the wrong table to ask. Put through the earbuds' **own** command table,
  **seven of their twelve are implemented there**, with handlers: 0xFD02,
  0xFD4B, 0xFD4D, 0xFDC1, 0xFDDD, 0xFDE4 and 0xFE2D. The other five are absent.
  One of the seven ties this capture to the radio: **0xFD02 configures the
  controller's maximum packet size, and its mode 1 selects 2586 bytes**, the
  payload maximum of the five-slot packet only Apple's third packet-type table
  can reach, against 339 for the basic-rate one. [protocol.md](protocol.md)
  section 6.8 has the handler addresses and the branch. What that Mac's own
  controller does with its 0xFD02 is not established; what the earbuds do with
  theirs is.
- The same `BandSwitchManager` exists there, logging `EnableHRB 1,
  forceSupportHRB 0`, which is configuration rather than a switch.
- The microphone path is ordinary: an eSCO link set up with the standard
  Synchronous Connection command, with the AirPods input at 24 kHz.

What the pair establishes and what it does not. On a Broadcom-era Mac, in steady
state, **none of the host interface this project documents is in use** - which is
a real negative result, and the strongest reading of that capture. It does not
close the N1 question: a different controller, and the one command that would
appear is sent at a mode change rather than in steady state, so a capture
spanning connection setup or a deliberate band switch is where it would show. It
also does not establish that HRB is ULLA; Apple's own patent language argues
against reading "HRB TDD" that way (2.6).
