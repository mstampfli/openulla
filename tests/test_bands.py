"""The band plan: the records themselves, the four identities, and the selection rules."""
import pytest

from openulla import bands
from openulla.errors import ProtocolError

def test_every_record_is_seven_bytes_and_round_trips():
    """The comparison against the firmware's own bytes is tools/verify_claims.py's job."""
    assert len(bands.BAND_TABLE) == bands.BAND_COUNT
    for band in bands.BAND_TABLE:
        raw = band.to_bytes()
        assert len(raw) == bands.RECORD_SIZE
        assert int.from_bytes(raw[:2], "little") == band.start_mhz
        assert tuple(raw[2:]) == (
            band.width_mhz,
            band.channels,
            band.channel_bandwidth_mhz,
            band.spacing_mhz,
            band.first_channel,
        )


def test_width_is_the_channels_times_the_spacing_plus_one_bandwidth():
    for band in bands.BAND_TABLE:
        if band.defined:
            assert band.width_mhz == (
                (band.channels - 1) * band.spacing_mhz + band.channel_bandwidth_mhz
            )


def test_the_six_gigahertz_bands_are_contiguous_and_end_at_the_upper_edge():
    for lower, upper in ((4, 5), (5, 6), (6, 7)):
        assert bands.band(lower).end_mhz == bands.band(upper).start_mhz
    assert bands.band(7).end_mhz == 6425


def test_the_channel_numbering_is_one_contiguous_sequence():
    nxt = 0
    for band in bands.BAND_TABLE:
        if not band.defined:
            continue
        assert band.first_channel == nxt
        nxt = band.first_channel + band.channels
    assert nxt == 48
    assert bands.band(7).last_channel == 47


def test_the_spans_contain_the_extents_apples_filings_report():
    assert bands.band(1).start_mhz <= 5157 and 5245 <= bands.band(1).end_mhz
    assert bands.band(5).start_mhz <= 6108 and 6420 <= bands.band(7).end_mhz


def test_band_three_is_empty_and_refused_everywhere():
    assert not bands.band(bands.EMPTY_BAND).defined
    assert bands.band(bands.EMPTY_BAND).to_bytes() == bytes(bands.RECORD_SIZE)
    with pytest.raises(ProtocolError):
        bands.check_allowed_mask(1 << bands.EMPTY_BAND)
    with pytest.raises(ProtocolError):
        bands.channel_frequency(bands.EMPTY_BAND, 0)


def test_the_frequency_function_is_the_one_the_firmware_computes():
    assert bands.channel_frequency(0, 0) == (2401, 22)
    assert bands.channel_frequency(0, 12) == (2401 + 12 * 5, 22)
    assert bands.channel_frequency(1, 13) == (5150, 20)
    assert bands.channel_frequency(1, 17) == (5230, 20)
    assert bands.channel_frequency(7, 47) == (6285 + 6 * 20, 20)
    assert bands.channel_frequency(1, 13, correction=10) == (5140, 20)


def test_a_channel_outside_its_band_is_refused():
    with pytest.raises(ProtocolError):
        bands.channel_frequency(1, 18)
    assert bands.band_of_channel(18) == 2


def test_the_mask_is_built_from_the_country_code_slots():
    assert bands.mask_from_country_codes([0] * 8) == 0xFF
    assert bands.mask_from_country_codes([0xFF] * 8) == 0
    assert bands.mask_from_country_codes([0, 0xFF, 0, 0xFF, 0, 0xFF, 0, 0xFF]) == 0b01010101
    with pytest.raises(ProtocolError):
        bands.mask_from_country_codes([0] * 7)


def test_the_masks_the_controller_refuses():
    with pytest.raises(ProtocolError):
        bands.check_allowed_mask(0)
    with pytest.raises(ProtocolError):
        bands.check_allowed_mask(0b1000)
    bands.check_allowed_mask(0b11110111)


def test_a_band_byte_turns_back_into_its_index():
    assert bands.index_from_band_byte(0b00100000) == 5
    assert bands.index_from_band_byte(1) == 0
    assert bands.index_from_band_byte(0) == bands.NO_BAND
    assert bands.index_from_band_byte(0b00100100) == 5  # the highest bit wins


def test_the_link_mask_drops_band_zero_unless_it_is_all_there_is():
    assert bands.link_mask(0b11110011, 0b11110001) == 0b11110000
    assert bands.link_mask(0b00000011, 0b00000001) == 1
    assert bands.link_mask(1, 1) == 1
    assert bands.link_mask(0b0010, 0b0100) is None


def test_mask_bands_lists_what_a_mask_names():
    assert bands.mask_bands(0b10010001) == (0, 4, 7)
