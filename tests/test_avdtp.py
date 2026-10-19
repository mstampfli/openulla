"""The AVDTP signalling this project needs, and the probe built on it.

Every layout here is the specification's, so what these tests pin is that the module encodes it
the way the specification says and that the probe asks a question whose answer means something.
"""
import pytest

from openulla import a2dp, avdtp
from openulla.errors import ProtocolError


def test_the_header_packs_the_label_the_packet_type_and_the_message_type():
    assert avdtp.header(0, avdtp.SIG_DISCOVER) == bytes((0x00, 0x01))
    assert avdtp.header(5, avdtp.SIG_SET_CONFIGURATION) == bytes((0x50, 0x03))
    accept = avdtp.header(5, avdtp.SIG_SET_CONFIGURATION, avdtp.RESPONSE_ACCEPT)
    head = avdtp.parse_header(accept)
    assert (head.label, head.signal, head.message_type) == (5, 0x03, avdtp.RESPONSE_ACCEPT)
    for bad in ((0x10, 1, 0), (0, 0x40, 0), (0, 1, 4)):
        with pytest.raises(ProtocolError):
            avdtp.header(*bad)
    with pytest.raises(ProtocolError):
        avdtp.parse_header(b"\x00")
    # Only single-packet messages are parsed, and a fragmented one says so rather than lying.
    with pytest.raises(ProtocolError):
        avdtp.parse_header(bytes((0x04, 0x01)))


def test_discover_parses_a_seid_per_two_bytes():
    reply = avdtp.header(0, avdtp.SIG_DISCOVER, avdtp.RESPONSE_ACCEPT) + bytes(
        (1 << 2, 0x08, (2 << 2) | 2, 0x08)
    )
    endpoints = avdtp.parse_discover(reply)
    assert [(e.seid, e.in_use, e.is_sink) for e in endpoints] == [(1, False, True),
                                                                  (2, True, True)]
    with pytest.raises(ProtocolError):
        avdtp.parse_discover(avdtp.header(0, 1, 2) + b"\x04")


def test_a_capability_list_round_trips_and_a_short_one_is_refused():
    codec = avdtp.media_codec(0xFF, a2dp.ENDPOINTS[0xFF][1])
    wire = avdtp.MEDIA_TRANSPORT.to_bytes() + codec.to_bytes()
    walked = avdtp.parse_capabilities(wire)
    assert walked == (avdtp.MEDIA_TRANSPORT, codec)
    assert walked[1].codec == 0xFF
    assert walked[1].codec_specific == a2dp.ENDPOINTS[0xFF][1]
    # The media codec capability is two fixed bytes plus the codec-specific ones.
    assert len(codec.payload) == len(a2dp.ENDPOINTS[0xFF][1]) + 2
    with pytest.raises(ProtocolError):
        avdtp.parse_capabilities(bytes((avdtp.CATEGORY_MEDIA_CODEC, 4, 1, 2)))
    with pytest.raises(ProtocolError):
        avdtp.Capability(category=1, payload=bytes(256)).to_bytes()
    with pytest.raises(ProtocolError):
        assert avdtp.MEDIA_TRANSPORT.codec_specific  # a transport has no codec bytes


def test_set_configuration_carries_both_seids_and_the_capabilities():
    message = avdtp.set_configuration(
        acp_seid=3, int_seid=1,
        capabilities=(avdtp.MEDIA_TRANSPORT, avdtp.media_codec(0x00, b"\x3f\xff\x02\x35")),
    )
    assert message[:2] == avdtp.header(0, avdtp.SIG_SET_CONFIGURATION)
    assert message[2] == 3 << 2 and message[3] == 1 << 2
    assert avdtp.parse_capabilities(message[4:])[1].codec == 0x00
    with pytest.raises(ProtocolError):
        avdtp.set_configuration(acp_seid=0x40, int_seid=1, capabilities=())


def test_a_refusal_names_the_category_and_the_error():
    """Which is the whole reason the probe can say *which* field a device validates."""
    accepted = avdtp.header(0, avdtp.SIG_SET_CONFIGURATION, avdtp.RESPONSE_ACCEPT)
    assert avdtp.parse_set_configuration_response(accepted) is None
    refused = avdtp.header(0, avdtp.SIG_SET_CONFIGURATION, avdtp.RESPONSE_REJECT) + bytes(
        (avdtp.CATEGORY_MEDIA_CODEC, 0xE1)
    )
    reject = avdtp.parse_set_configuration_response(refused)
    assert reject is not None
    assert reject.category == avdtp.CATEGORY_MEDIA_CODEC
    assert "NOT_SUPPORTED_CODEC_PARAMETER" in str(reject)
    assert avdtp.error_name(0x7F) == "error 0x7f"
    with pytest.raises(ProtocolError):
        avdtp.parse_set_configuration_response(
            avdtp.header(0, avdtp.SIG_DISCOVER, avdtp.RESPONSE_ACCEPT))
    with pytest.raises(ProtocolError):
        avdtp.parse_set_configuration_response(
            avdtp.header(0, avdtp.SIG_SET_CONFIGURATION, avdtp.RESPONSE_REJECT))


def test_the_probe_alters_only_the_eight_bytes_and_offers_the_whole_capability():
    """The bug this guards against would have made every answer worthless.

    A vendor codec's codec-specific field is four vendor bytes, two codec-id bytes and then the
    eight in question. Offering only the eight would be refused for being malformed rather than
    for the byte that changed.
    """
    import a2dp_probe  # the tools directory is on the path through conftest.py

    whole = a2dp.ENDPOINTS[0xFF][1]
    first = len(whole) - len(a2dp.vendor_specific(whole))
    assert first == 6
    altered = a2dp_probe.alterations(whole, first)
    specific = a2dp.vendor_specific(whole)
    # Two offers per byte, minus the ones a byte cannot take: nothing to clear in a 0x00 byte,
    # nothing to set in a 0xFF one.
    expected = sum(1 for b in specific if b != 0xFF) + sum(1 for b in specific if b != 0x00)
    assert len(altered) == expected
    for label, candidate in altered:
        assert len(candidate) == len(whole)
        # Exactly one byte differs, and it is inside the eight.
        differing = [i for i in range(len(whole)) if whole[i] != candidate[i]]
        assert len(differing) == 1, label
        index = differing[0] - first
        assert 0 <= index < len(specific), label
        assert candidate[:first] == whole[:first]
        # And exactly one bit of that byte differs, in the direction the label claims.
        before, after = whole[differing[0]], candidate[differing[0]]
        assert bin(before ^ after).count("1") == 1, label
        if label.endswith("set"):
            assert after > before, f"{label} must add a bit the device did not declare"
        else:
            assert after < before, f"{label} must remove a bit the device did declare"
        assert f"byte {index} " in label


def test_the_probe_reads_its_two_groups_against_each_other():
    """The control is the point: a sink refusing even a subset is not validating fields.

    An earlier version of the probe flipped the low bit of every byte, which sets an undeclared
    bit where that bit was clear and clears a declared one where it was set - two opposite
    experiments reported under one label. These are the four readings the two groups can give.
    """
    import a2dp_probe

    base_refused = a2dp_probe.read_answers({"unaltered": False, "byte 0 bit 0 set": True})
    assert "no row below means anything" in " ".join(base_refused)

    all_accepted = a2dp_probe.read_answers(
        {"unaltered": True, "byte 0 bit 0 set": True, "byte 1 bit 1 set": True,
         "byte 0 bit 3 cleared": True}
    )
    assert "not validated" in " ".join(all_accepted)

    control_failed = a2dp_probe.read_answers(
        {"unaltered": True, "byte 0 bit 0 set": False, "byte 0 bit 3 cleared": False}
    )
    assert "Control failed" in " ".join(control_failed)
    assert "do NOT identify fields" in " ".join(control_failed)

    informative = a2dp_probe.read_answers(
        {"unaltered": True, "byte 0 bit 0 set": False, "byte 1 bit 0 set": True,
         "byte 0 bit 3 cleared": True}
    )
    joined = " ".join(informative)
    assert "name real fields" in joined and "byte 0 bit 0 set" in joined
    assert "Control: 1 of 1" in joined


def test_the_simulated_sink_answers_from_the_firmware_s_own_table():
    """Discovery and the capability reads are as proven as the rest of the project."""
    sink = avdtp.SimulatedSink(a2dp.ENDPOINTS)
    endpoints = avdtp.parse_discover(sink.answer(avdtp.discover()))
    assert len(endpoints) == len(a2dp.ENDPOINTS)
    assert all(e.is_sink and not e.in_use for e in endpoints)
    seen = {}
    for endpoint in endpoints:
        reply = sink.answer(avdtp.get_capabilities(endpoint.seid, every=True))
        assert avdtp.parse_header(reply).message_type == avdtp.RESPONSE_ACCEPT
        capabilities = avdtp.parse_capabilities(reply[2:])
        assert capabilities[0] == avdtp.MEDIA_TRANSPORT
        codec = capabilities[1].codec
        assert codec is not None
        seen[codec] = capabilities[1].codec_specific
    assert seen == {codec: bytes(value[1]) for codec, value in a2dp.ENDPOINTS.items()}
    # An unknown endpoint is refused rather than answered with somebody else's capability.
    assert avdtp.parse_header(
        sink.answer(avdtp.get_capabilities(0x3F, every=True))
    ).message_type == avdtp.RESPONSE_REJECT


def test_the_simulated_sink_exercises_both_branches_of_the_probe():
    """Its refusal policy is its own; what is checked here is that both paths are reachable."""
    sink = avdtp.SimulatedSink(a2dp.ENDPOINTS)
    endpoints = avdtp.parse_discover(sink.answer(avdtp.discover()))
    vendor = next(
        e.seid for e in endpoints
        if avdtp.parse_capabilities(
            sink.answer(avdtp.get_capabilities(e.seid, every=True))[2:]
        )[1].codec == 0xFF
    )
    whole = a2dp.ENDPOINTS[0xFF][1]
    unaltered = avdtp.set_configuration(
        acp_seid=vendor, int_seid=1,
        capabilities=(avdtp.MEDIA_TRANSPORT, avdtp.media_codec(0xFF, whole)),
    )
    assert avdtp.parse_set_configuration_response(sink.answer(unaltered)) is None
    changed = bytearray(whole)
    changed[-1] ^= 0x01
    altered = avdtp.set_configuration(
        acp_seid=vendor, int_seid=1,
        capabilities=(avdtp.MEDIA_TRANSPORT, avdtp.media_codec(0xFF, bytes(changed))),
    )
    reject = avdtp.parse_set_configuration_response(sink.answer(altered))
    assert reject is not None and reject.category == avdtp.CATEGORY_MEDIA_CODEC


def test_the_probe_runs_end_to_end_against_the_simulation():
    """The tool's own path, verified without hardware, which is all a simulation can verify."""
    import a2dp_probe

    assert a2dp_probe.main(["--simulate", "--probe", "--live", "00:00:00:00:00:00"]) == 0
    # The dry run needs no address check either way and must send nothing.
    assert a2dp_probe.main(["--simulate", "--probe"]) == 0
