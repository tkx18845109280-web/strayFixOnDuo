"""Apply the two per-title host changes after the verified controller update."""
import subprocess
import sys

import native_host as native


def apply():
    from stage2_host import patch, check, RECIPES, TESTS
    changes = [
        ("scanner_patch_sha256", "stray-periodic-stacks.patch", "app/Madeira/Winios/Winios.m",
         "0961c7a099b0a8538e1062f522d2f1e72bef4429083f7fdd3345752979b69d9f"),
        ("renderer_patch_sha256", "stray-renderer-selection.patch", "app/Madeira/WineProcessBridge.m",
         "5771cabfc5e5f3b8f768558d8b79be3ad747ac3e276e7325f48f5d5eaa16894c"),
    ]
    for _, filename, target, expected in changes:
        entry = RECIPES / "patches" / filename
        native.verify(entry, expected)
        patch(entry, [target])
    test = TESTS / "test-periodic-stacks.py"
    native.verify(test, "fd56ae38b1b9002a3e57983d3d0ffc7dd68e4139ec5aff33f4bdac64b8bde0f8")
    subprocess.run([sys.executable, test, native.BCD], check=True)
    bridge = (native.BCD / "app/Madeira/WineProcessBridge.m").read_text(encoding="utf8")
    check(bridge.count("BOOL useStrayD3D11 = NO;") == 1 and
          bridge.count('const char *d3d11Source = "d3d11-src.dll";') == 1 and
          bridge.count('if (useStrayD3D11 && [fm fileExistsAtPath:') == 1 and
          bridge.count('{ "d3d11.dll", d3d11Source }') == 1 and
          bridge.count('[ecDir stringByAppendingPathComponent:@(d3d11Source)]') == 1,
          "Per-title renderer option lost its default, file check or link target")
    return {
        "patches": {key: digest for key, _, _, digest in changes},
        "markers": ["[winios] periodic thread stacks off for this session", "[stray-d3d11] selected per-title renderer"],
    }
