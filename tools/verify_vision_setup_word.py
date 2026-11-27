"""Compare 384 synthetic Classic setup words against a local 24M372 ACIB in Unicorn."""

import argparse
import hashlib
import json
import random
import struct
from pathlib import Path

from vision_setup_word import WordGenerator

DIGEST = "32373dd3e5984f218d1da537d4a43413853fc00f7782eb48aee34bc8c18a8b9d"
RAM = 0x10000000
SEED_STATE = RAM + 0x708
STOP = 0xF0000


def firmware_samples(blob, seed, count):
    try:
        from unicorn import UC_ARCH_ARM, UC_HOOK_CODE, UC_MODE_MCLASS, UC_MODE_THUMB, Uc
        from unicorn import arm_const as reg
    except ImportError as error:
        raise SystemExit("Install unicorn==2.1.4 for the firmware comparison.") from error
    if hashlib.sha256(blob).hexdigest() != DIGEST:
        raise ValueError("24M372 M5 controller required")
    if struct.unpack_from("<I", blob, 0xF7980 + (0x63 - 1) * 4)[0] != 0x800DC5ED:
        raise ValueError("Property 0x63 dispatch mismatch")
    cpu = Uc(UC_ARCH_ARM, UC_MODE_THUMB | UC_MODE_MCLASS)
    cpu.mem_map(0, (len(blob) + 4095) & ~4095)
    cpu.mem_write(0, blob)
    cpu.mem_map(RAM, 0x30000)
    cpu.mem_write(RAM + 0x2000, struct.pack("<4I", *seed))
    observations = []

    def call(entry, arg):
        cpu.reg_write(reg.UC_ARM_REG_R0, arg)
        cpu.reg_write(reg.UC_ARM_REG_SP, RAM + 0x2F000)
        cpu.reg_write(reg.UC_ARM_REG_LR, STOP | 1)
        cpu.emu_start(entry | 1, STOP, count=500000)
        if cpu.reg_read(reg.UC_ARM_REG_PC) != STOP:
            raise ValueError("Call did not return")

    def observe(machine, pc, size, user):
        if pc in (0x73F62, 0x73D58):
            machine.reg_write(reg.UC_ARM_REG_PC, machine.reg_read(reg.UC_ARM_REG_LR))
        elif pc == 0x6F720:
            # Only on fallback: synthetic two-part return from the clock/context helper.
            machine.mem_write(
                machine.reg_read(reg.UC_ARM_REG_R1), struct.pack("<IH", 0x12345678, 0x0123)
            )
            machine.reg_write(reg.UC_ARM_REG_PC, machine.reg_read(reg.UC_ARM_REG_LR))
            observations.append({"fallback": True})
        elif pc in (0xDC600, 0xDC630, 0xDC642):
            key = {0xDC600: "prng_word", 0xDC630: "transformed", 0xDC642: "predicate"}[pc]
            value = machine.reg_read(reg.UC_ARM_REG_R0)
            observations.append({key: value if key == "predicate" else f"{value:08x}"})

    cpu.hook_add(UC_HOOK_CODE, observe)
    call(0x77460, RAM + 0x2000)
    if struct.unpack("<5I", cpu.mem_read(SEED_STATE, 20)) != (*seed, 0):
        raise ValueError("Seed initializer differs")
    rows = []
    for _ in range(count):
        observations.clear()
        call(0xDC5EC, RAM + 0x3000)
        field = bytes(cpu.mem_read(RAM + 0x3000, 4))
        rows.append({"pdu_bytes_1_4_le": field.hex(" "), "attempts": list(observations)})
    return {
        "firmware_sha256": DIGEST,
        "seed_le_words": [f"{x:08x}" for x in seed],
        "setup_words": rows,
        "hooks": [
            "0x73f62 and 0x73d58 logs return immediately",
            "0x6f720 fallback only: synthetic 0x12345678 and 0x0123",
        ],
    }


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("acib", type=Path, help="locally extracted Vision Pro M5 24M372 ACIB")
    args = cli.parse_args()
    blob = args.acib.read_bytes()
    rng = random.Random(2030)
    retries = 0
    for _ in range(128):
        seed = [rng.getrandbits(32) for _ in range(4)]
        state = WordGenerator(*seed)
        for row in firmware_samples(blob, seed, 3)["setup_words"]:
            expected = state.setup_word(fallback_clock=(0x12345678, 0x0123))
            if expected.to_bytes(4, "little") != bytes.fromhex(row["pdu_bytes_1_4_le"]):
                raise ValueError(f"Setup word mismatch for synthetic seed {seed}")
            retries += sum(step.get("predicate") == 0 for step in row["attempts"])
    print(json.dumps(firmware_samples(blob, [1, 2, 3, 4], 3), indent=2))
    print(f"384 words matched; {retries} rejected candidates.")


if __name__ == "__main__":
    main()
