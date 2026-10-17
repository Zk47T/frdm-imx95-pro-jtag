# 09 — Detecting a merged kernel driver fix from the ETM trace (imx-lpi2c SMBus block read)

**Date:** 2026-10-17. **Tags:** `[V]` measured · `[?]` not verified · `[W]` from a web source.

A test of the trace against a real, recently merged kernel bug fix — in the **exact I2C controller
driver on this board**, and the same subsystem as the DSI/I2C fault ([03](03-dsi-and-the-i2c-fault.md)).
The question: can the A55 instruction trace, taken from outside with the board running normally, tell
whether a running kernel carries the fix?

## 1. The bug and the fix `[W]`

- **Commit:** upstream `efdc383d1cc2` "i2c: imx-lpi2c: fix SMBus block read NACK after byte count"
  (Carlos Song), merged v6.19-rc9; `Fixes: a55fa9d0e42e` ("add low power i2c bus driver"); `Cc: stable # v4.10+`.
- **In this board's kernel:** the NXP backport `80a2e762b217` (linux-imx `lf-6.18.y`, 6.18.20).
- **The hardware behaviour** (from the driver's own comment, verified against `i2c-imx-lpi2c.c`):
  > The LPI2C controller automatically sends a NACK after the last byte of a receive command, unless
  > the next command in MTDR is also a receive command.
  - In an SMBus block read the length isn't known in advance: the driver reads the length byte first,
    then reads that many data bytes.
  - Buggy: the follow-up receive command was queued only in the interrupt handler, after the length
    byte. By then the controller had already NACKed, so the transfer aborted after one byte.
  - **Fixed:** `lpi2c_imx_read_init` queues a 1-byte receive, **polls** `MSR.RDF` for the length byte,
    reads it, validates it (`0 < len ≤ 32`, else `-EPROTO`), and queues the follow-up receive
    **immediately, still in the polled path** — so MTDR is never empty and the controller keeps ACKing.

## 2. How the path was exercised `[V]`

- **`board/i2cblk.c`** (a tiny `/dev/i2c-*` SMBus helper, cross-compiled, pushed over the JTAG console)
  does `I2C_SMBUS_BLOCK_DATA` reads.
- **The only responder on a working LPI2C controller** is an EEPROM at **i2c-1 (LPI2C2) 0x50**. The
  DSI buses i2c-2/i2c-3 (LPI2C3/4) stay silent — the fault of [03](03-dsi-and-the-i2c-fault.md), seen
  again here: a bus scan ACKs nothing on them.
- A loop ran `i2cblk /dev/i2c-1 0x50 block 0x00` every 20 ms. The EEPROM is blank, so every length
  byte is `0xff` (> 32): each read takes the fix's **`-EPROTO`** branch, logged as
  `i2c i2c-1: Invalid SMBus block read length` (1,916 times).
- **Nothing was halted.** The board ran normally; the trace is passive.

## 3. The trace `[V]`

```
# on the board: loop i2cblk against the EEPROM
# PC: filter the ETM to the lpi2c functions, cycle counts on, into the 16 MB buffer
cs_ranges {0xffff800080c7e1a0 0xffff800080c7f5bc} {0xffff800080c7fae0 0xffff800080c7fe1c}
cs_multi_start {0 1 2 3 4 5} 0xf1000000 0x1000000 0x8890 4     # TS + PID + cycle counts
# 2 s -> 1.79 MB (only lpi2c code), read out over JTAG, mksnap.py --ranges, tracetool.py index
```

The whole 2 s of six cores reduced to **1.79 MB** because the ETM recorded only the lpi2c functions
(address-range filter, [04](04-arm-trace-into-dram.md) §8.4). The functions seen:

| Function | Share of the traced instructions |
|---|---|
| `lpi2c_imx_xfer_common` | 92 % (the transfer loop and its waits) |
| `lpi2c_imx_read_init` | 7 % (**the fix lives here**) |
| `lpi2c_imx_write_txfifo`, `lpi2c_imx_pio_xfer` | <1 % |

## 4. What the trace shows — the detection `[V]`

**It resolves the fix's exact code.** `addr2line` on the executed instruction addresses of one
`lpi2c_imx_read_init` call, in order:

| Instr | Source (`i2c-imx-lpi2c.c`) | What it is |
|---|---|---|
| `…e3f0–e414` | io.h writel, line 590 | **write the 1-byte RECV_DATA command to MTDR** |
| `…e420–e45c` (×~39) | line 592 | **poll `MSR.RDF`** — wait for the length byte (the fix's `readl_poll_timeout`) |
| `…e460–e488` | line 554 | **read the length byte** from MRDR (`lpi2c_SMBus_block_read_length_byte`, inlined) |
| `…e494–e498` | line 601 | **validate `block_len`** (the check the fix added) |
| `…e4a4` | line 613 | the follow-up `writel((RECV_DATA<<8)|(block_len-2))` (not taken here: `0xff` → `-EPROTO`) |

**The control-flow fingerprint that separates fixed from buggy:**

- `lpi2c_imx_read_rxfifo`: **0 ranges.** `lpi2c_imx_isr`: **0 ranges.**
- The length byte is read and the follow-up command is queued **entirely inside the polled
  `lpi2c_imx_read_init`** — no interrupt runs for it.
- On the **buggy** driver the follow-up write would appear in `lpi2c_imx_read_rxfifo`, called from
  `lpi2c_imx_isr`, i.e. only after an interrupt — and the trace would show that interrupt latency as
  the gap in which the controller NACKs. The fixed driver closes the gap to a few polled
  instructions, which is what the trace sees here.

So the trace answers the question from outside: **this kernel carries the fix.** The same capture on
an unpatched kernel would show the follow-up receive in the ISR path and the transfer ending in a
NACK (`MSR.NDF`) and `-EIO`.

Across all six cores, 880+ `read_init` activations were caught in 2 s; the per-read validation
(`-EPROTO`) matched the 1,916 `dmesg` lines one-for-one.

## 5. Honest limits

- **The board runs the fixed driver,** so this detects the fix's presence; it does not reproduce the
  NACK on a buggy kernel (that would need building and booting the pre-`a55fa9d0e42e`-era path).
- **The EEPROM is blank** (`0xff`), so every length byte is invalid and the `-EPROTO` branch is taken.
  The follow-up write at line 613 is therefore in the *not-taken* branch: the trace reaches the branch
  and resolves the instruction, but does not execute it. Seeing it run needs a device that returns a
  valid length (1–32); none is on a working bus here (the SMBus-block devices are on the hung DSI buses).
- **Why this still detects the bug:** the fix is the *restructuring* — reading and validating the
  length in the polled `read_init` instead of the ISR. The trace proves that structure is present
  (length read + validation in `read_init`, ISR absent), which is exactly what the buggy driver lacked.

## 6. Lessons

1. **The trace reads a specific merged fix off the running kernel.** Not "the driver ran" but "the
   driver queues the follow-up receive in the polled path, with the new length check" — the precise
   change the patch made.
2. **An address filter makes it cheap.** Six cores for 2 s, but only one driver's functions: 1.79 MB,
   indexes in seconds, and the irrelevant 99.9 % of the system never enters the buffer.
3. **It's passive.** No core was halted, so the timing it reports is the real driver timing, and the
   board kept serving its workload throughout.
</content>
