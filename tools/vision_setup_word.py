"""Normal ULLA setup word & config arithmetic, using only the Python standard library.

The four seed words are caller-supplied PRNG state inputs; use a separate state per
generator. Generating compatible words does not predict a peer's seed or RF waveform.
"""

import argparse
import struct
from dataclasses import dataclass

MASK = 0xFFFFFFFF


@dataclass
class WordGenerator:
    a: int
    b: int
    c: int
    d: int
    carry: int = 0

    def draw(self):
        self.a = (self.a + 0x542023AB) & MASK
        b = self.b ^ ((self.b << 5) & MASK)
        b ^= b >> 7
        self.b = (b ^ (b << 22)) & MASK
        total = (self.c + self.d + self.carry) & MASK
        self.c, self.d, self.carry = self.d, total & 0x7FFFFFFF, total >> 31
        return (self.a + self.b + self.d) & MASK

    def setup_word(self, *, fallback_clock=None):
        for attempt in range(1, 11):
            word = shape(self.draw())
            passed = qualified(word)
            if attempt < 10 and passed:
                return word
        # Original decrements remaining attempts before testing its fallback.
        if fallback_clock is None:
            raise ValueError(
                "Exhausted normal draws; provide (coarse, fine) for the local fallback clock"
            )
        coarse, fine = fallback_clock
        return shape(coarse | (fine & 0xFFFF))


def shape(raw):
    """Bit shaping and bit reversal in ACIB 24M372 +0xdc3cc."""
    word = raw & MASK
    previous = word & 1
    run = 1
    budget = 23
    early_changes = high_ones = high_zeros = 0
    balance = True
    for index in range(1, 32):
        remaining = 32 - index
        if index in (4, 5) and early_changes <= 1:
            bit = 1 - previous
        else:
            if index == 8:
                bit = 1 - (word & 1)
            elif budget and (index <= 24 or budget >= remaining):
                bit = (raw >> index) & 1
            else:
                bit = previous
            if index >= 25:
                high_ones += previous
                high_zeros += 1 - previous
                if index >= 29 and balance and high_ones >= 5:
                    budget, bit, balance = previous, 0, False
                elif index >= 29 and balance and high_zeros >= 5:
                    budget, bit, balance = 1 - previous, 1, False
            if bit == previous and run >= 6:
                bit = 1 - previous
        if bit != previous:
            if index < 6:
                early_changes += 1
            budget = (budget - 1) & MASK
            run = 1
        else:
            run += 1
        if index == 16:
            budget = min(11, budget)
        word = (word & ~(1 << index)) | (bit << index)
        previous = bit
    word = int(f"{word:032b}"[::-1], 2)
    return word ^ MASK if (word ^ 0x8E89BED6).bit_count() <= 1 else word


def qualified(word):
    """Alternating prefix/window predicate in ACIB 24M372 +0xdc504."""
    joined = (word << 8) | (0x55 if word & 1 else 0xAA)
    min_uncompared = (7, 11, 14, 17, 19, 22, 25, 27, 30)
    for step in range(29):
        shift = max(0, step - 21)
        width = min(32, 11 + step)
        matching = ~((joined >> shift) ^ (word >> max(0, 21 - step))) & ((1 << width) - 1)
        uncompared = 32 - width
        score = uncompared + matching.bit_count()
        if score <= 26:
            continue
        if score > 35 or uncompared < min_uncompared[score - 27]:
            return False
    return True


def config_fields(
    coarse, fine, coarse_offset, fine_offset, table_byte, adjust_flag, divisor, scale, lead=40
):
    """Default slot-6 registration; a caller must obtain synchronized link inputs."""
    if not divisor:
        raise ValueError("Need an initialized nonzero timing divisor")
    target = (coarse + coarse_offset + (fine + fine_offset) // 1250 + lead) & 0x03FFFFFF
    correction = ((table_byte - bool(adjust_flag)) & 255) + 2 + 17 * scale + 800 // divisor - 87
    return struct.pack("<IH", target, correction & 0xFFFF)


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("seed", nargs=4, type=lambda s: int(s, 0), help="unsigned 32-bit seed word")
    cli.add_argument("--count", type=int, default=3, help="number of consecutive setup words")
    args = cli.parse_args()
    if any(word < 0 or word > MASK for word in args.seed) or args.count < 1:
        cli.error("supply four unsigned 32-bit seed words and a positive count")
    state = WordGenerator(*args.seed)
    try:
        for _ in range(args.count):
            print(state.setup_word().to_bytes(4, "little").hex(" "))
    except ValueError as error:
        cli.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
