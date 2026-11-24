#!/bin/bash
set -euo pipefail
developer="$(python3 - <<'PY'
from pathlib import Path
import glob, os, plistlib
candidates = []
for path in glob.glob("/Applications/Xcode*.app"):
    app = Path(path)
    with (app/"Contents"/"Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    version = info["CFBundleShortVersionString"]
    numeric = tuple(int(part) for part in version.split(".") if part.isdigit())
    if numeric >= (26,) and (app/"Contents/Developer/Platforms/iPhoneOS.platform").is_dir():
        candidates.append((numeric, "beta" not in path.lower(), path))
if not candidates:
    raise SystemExit("BCD needs an Xcode installation with the iOS 26 or later SDK")
print(Path(max(candidates)[2])/"Contents/Developer")
PY
)"
sudo xcode-select --switch "$developer"
sdk="$(xcrun --sdk iphoneos --show-sdk-version)"
xcodebuild -version
printf 'id=%s-%s\n' "$(xcodebuild -version | awk '/Build version/{print $NF}')" "$sdk" >> "$GITHUB_OUTPUT"
df -h .
