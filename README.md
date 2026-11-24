# Stray memory allocation test for BCD 0.1.712

This experimental patch for the iOS Wine host uses a bounded search hint after an expensive virtual address scan in `Stray-Win64-Shipping.exe`. It applies to ordinary unhinted, non-executable read/write allocations. The existing full search runs on a miss.

The `[stray-va]` log entry reports counts of full searches, expensive searches, hint hits and misses, and cumulative time in each search path. A hardware run is needed to measure the effect on frame times.

The patch targets [BCD v0.1.712](https://github.com/thebcd69/bcd-games/releases/tag/v0.1.712), commit `f811dd05a9183cda20ffd3956cb1958cafe654f7`. Apply `patches/stray-va-cursor.patch` with `git apply` in that source tree. Then run `python3 tests/test-patch.py /path/to/patched-bcd` from this repository to verify the address search and counters.

Includes work from BCD by thebcd69 (https://github.com/thebcd69/bcd-games). The modified Wine file retains its upstream notices and LGPL-2.1-or-later license. The build workflow derives from BCD's published build recipe. Its upstream license and additional terms are included in [licenses](licenses/). This is an independent test build.
