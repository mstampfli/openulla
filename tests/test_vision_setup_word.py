"""Synthetic vectors from M5 24M372 and AirPods3,8 9B47, independent of Unicorn."""

from vision_setup_word import WordGenerator, config_fields

from openulla.lmp import Configuration, SetupRequest


def test_successive_setup_words_and_public_setup_serializer():
    generator = WordGenerator(1, 2, 3, 4)
    words = [generator.setup_word() for _ in range(3)]
    assert words == [0xAF440D26, 0x2EF28235, 0x5AAD10C8]
    assert SetupRequest(words[0], 150, 3, 1).to_bytes() == bytes.fromhex(
        "e6 26 0d 44 af 96 00 03 01"
    )


def test_configuration_fields_include_clock_rollover_and_signed_correction():
    fields = config_fields(0x123456, 100, 0, 0, 20, 0, 8, 2)
    frame = bytes.fromhex("e8 7e 34 12 00 45 00")
    assert fields == frame[1:]
    assert Configuration.from_bytes(frame).to_bytes() == frame
    rollover = config_fields((1 << 26) - 40, 1249, 0, 1, 4, 0, 16, 0)
    assert int.from_bytes(rollover[:4], "little") == 1
    assert int.from_bytes(rollover[4:], "little", signed=True) == -31
