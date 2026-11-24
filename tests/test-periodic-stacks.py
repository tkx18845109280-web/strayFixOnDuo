"""Run the patched per-session reader and timer guard against actual BCD game files."""
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile


def main():
    root = Path(sys.argv[1]).resolve()
    src = (root / "app/Madeira/Winios/Winios.m").read_text(encoding="utf-8")
    bridge = (root / "app/Madeira/WineProcessBridge.m").read_text(encoding="utf-8")
    assert '#include "../../../build/madeira_cfg.h"' in src
    assert "void winios_session_reset(void) {\n    winios_configure_periodic_stacks();" in src
    assert "extern void winios_session_reset(void); winios_session_reset();" in bridge
    assert "if (waited >= 5.0 && !wms_dumped && wms_dumps < 4)" in src
    assert "dispatch_async(dispatch_get_global_queue(QOS_CLASS_UTILITY, 0), ^{ ios_dump_all_thread_stacks(); });" in src
    config = re.search(r"(static _Atomic int winios_periodic_stacks_enabled = 1;.*?\n})", src, re.S)
    timer = re.search(r"dispatch_source_set_event_handler\(stack_timer, \^\{(.*?)\n        \}\);", src, re.S)
    assert config and timer
    with tempfile.TemporaryDirectory() as tmp:
        here = Path(tmp)
        (here / "Stray.cfg").write_text("periodic-thread-stacks = 1\nperiodic-thread-stacks = 0\n")
        (here / "Sekiro.cfg").write_text("unrelated = 1\n")
        (here / "typo.cfg").write_text("periodic-thread-stacks = 01\n")
        (here / "bcd.cfg").write_text("periodic-thread-stacks = 1\n")
        harness = '''
#include <stdio.h>
#include <stdatomic.h>
#include "madeira_cfg.h"
static int dumps;
static void ios_dump_all_thread_stacks(void) { ++dumps; }
''' + config[1] + "\nstatic void tick(void) {" + timer[1] + '''
}
static void game(const char *profile) {
#ifdef _WIN32
    _putenv_s("MADEIRA_CFG_GAME", profile);
#else
    if (*profile) setenv("MADEIRA_CFG_GAME", profile, 1);
    else unsetenv("MADEIRA_CFG_GAME");
#endif
    winios_configure_periodic_stacks();
}
int main(void) {
    tick(); if (dumps != 1) return 1;
    game("Stray.cfg"); tick(); tick(); if (dumps != 1) return 2;
    game("Sekiro.cfg"); tick(); if (dumps != 2) return 3;
    game("Stray.cfg"); tick(); if (dumps != 2) return 4;
    game(""); tick(); if (dumps != 3) return 5;
    game("typo.cfg"); tick(); if (dumps != 4) return 6;
    game("missing.cfg"); tick(); if (dumps != 5) return 7;
    return 0;
}
'''
        (here / "test.c").write_text(harness, encoding="utf-8")
        binary = here / ("test.exe" if os.name == "nt" else "test")
        subprocess.run([os.environ.get("CC", "cc"), "-std=gnu11", "-I", str(root / "build"),
                        "test.c", "-o", str(binary)], cwd=here, check=True)
        env = {**os.environ, "MADEIRA_DOCS_DIR": str(here), "MADEIRA_CFG_GAME": ""}
        run = subprocess.run([str(binary)], cwd=here, env=env, capture_output=True, check=True)
        assert run.stderr.count(b"[winios] periodic thread stacks off for this session") == 2
    print("Per-session timer guard and official profile parser passed. Watchdog remains independent.")


if __name__ == "__main__":
    main()
