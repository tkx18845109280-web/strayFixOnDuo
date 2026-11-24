"""Run bounded address search regressions against the patched release source."""
from pathlib import Path
import hashlib
import os
import subprocess
import sys
import tempfile

tests = Path(__file__).resolve().parent
source = Path(sys.argv[1]) / "build/ntdll-unix/virtual_ios.c"
contents = source.read_text(encoding="utf-8")
with tempfile.TemporaryDirectory() as tmp:
    for name in ("cursor", "stats"):
        header = (tests / f"{name}.h").read_text(encoding="utf-8")
        if contents.count(header.strip()) != 1:
            sys.exit(f"{name}: test functions differ from the patched production implementation")
        exe = Path(tmp) / (name + (".exe" if os.name == "nt" else ""))
        subprocess.run([os.environ.get("CC", "cc"), "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror",
                        tests / f"{name}-test.c", "-o", exe], check=True)
        subprocess.run([exe], check=True)
print("Tested production source SHA-256", hashlib.sha256(source.read_bytes()).hexdigest())
