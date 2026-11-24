#!/usr/bin/env bash
# Build the seven FEX libraries referenced by BCD v0.1.712's Xcode project.
set -euo pipefail

root="${GITHUB_WORKSPACE:?}/bcd/FEX/build-ios"
revision=c2c6997d2397f450aa7837411fe08adf8fbabcba
archives=(
  FEXCore/Source/libFEXCore.a FEXCore/Source/libFEXCore_Base.a
  FEXCore/Source/libJemallocLibs.a External/cephes/libcephes_128bit.a
  External/fmt/libfmt.a External/SoftFloat-3e/libsoftfloat_3e.a
  External/xxhash/cmake_unofficial/libxxhash.a
)

verify() {
  [[ "$(uname -m)" == arm64 ]]
  for a in "${archives[@]}"; do
    test -s "$root/$a"
    test "$(xcrun lipo -archs "$root/$a")" = arm64

  done
  test "$(find "$root" -type f -name '*.a' | wc -l | tr -d ' ')" = 7
  # All remaining files must be compiler-traced public includes.
  python3 - "$root" <<'PY'
from pathlib import Path
import struct, sys
root = Path(sys.argv[1]); files = [p for p in root.rglob('*') if p.is_file()]
for path in files:
    if path.suffix not in {'.a', '.h', '.hpp', '.inc', '.inl'}:
        raise SystemExit(f'unexpected cache file: {path}')
    if path.suffix != '.a':
        continue
    # Apple libtool emits a BSD ar archive. Verify the load command of every
    # object so an arm64 macOS or simulator object cannot pass as a device build.
    data = memoryview(path.read_bytes()); offset = 8; count = 0
    if bytes(data[:8]) != b'!<arch>\n':
        raise SystemExit(f'{path}: expected a static archive')
    while offset < len(data):
        header = bytes(data[offset:offset+60]); size = int(header[48:58]); start = offset+60
        if header[58:60] != b'`\n' or start + size > len(data):
            raise SystemExit(f'{path}: invalid ar member')
        payload = data[start:start+size]; offset = start+size+(size%2)
        name = header[:16].rstrip().decode('ascii')
        if name.startswith('#1/'):
            n = int(name[3:]); name = bytes(payload[:n]).rstrip(b'\0').decode(); payload = payload[n:]
        if name in {'/', '//', '/SYM64/'} or name.startswith('__.SYMDEF'):
            continue
        magic, cpu, _, kind, ncmd, _, _, _ = struct.unpack_from('<8I', payload)
        if (magic, cpu, kind) != (0xfeedfacf, 0x0100000c, 1):
            raise SystemExit(f'{path}:{name}: expected an arm64 Mach-O object')
        off = 32; platform = minimum = None
        for _ in range(ncmd):
            cmd, length = struct.unpack_from('<2I', payload, off)
            if length < 8 or off+length > len(payload):
                raise SystemExit(f'{path}:{name}: malformed Mach-O load command')
            if cmd == 0x32:
                platform, minimum = struct.unpack_from('<2I', payload, off+8)
            elif cmd == 0x25:
                platform, minimum = 2, struct.unpack_from('<I', payload, off+8)[0]
            off += length
        if (platform, minimum) != (2, 0x110000):
            raise SystemExit(f'{path}:{name}: expected iOS 17.0, saw {platform}, {minimum}')
        count += 1
    if not count:
        raise SystemExit(f'{path}: zero Mach-O objects')
    print(f'{path.relative_to(root)}: {count} iOS device objects')
if not any(p.suffix != '.a' for p in files):
    raise SystemExit('FEXBridge generated header dependencies are missing')
PY
}

case ${1:-} in
  verify) verify; exit ;;
  build) ;;
  *) echo 'usage: build-fex.sh build|verify' >&2; exit 2 ;;
esac
[[ "$(uname -m)" == arm64 ]]
work=$(mktemp -d "${RUNNER_TEMP:?}/bcd-fex.XXXXXX")
trap 'rm -rf "$work"' EXIT
cd "$work"
# The published source contains the pinned transitive FEX submodules, including
# the allocator, disjoint from test binaries that the native target never uses.
curl --fail --location --retry 3 --output source.tar.xz \
  'https://github.com/thebcd69/bcd-games/releases/download/v0.1.712/BCD-source.tar.xz'
printf '%s  source.tar.xz\n' fb3a7d62d3c47c2a2b62918baaa81641eb48444bca66c0f1a6db63bf5c1563a0 | shasum -a 256 -c -
release=bcd-0.1.712
tar -xJf source.tar.xz --strip-components 1 --exclude '*/External/fex-*-bins' --exclude '*/unittests' \
  "$release/FEX" "$release/tools/patch-fex-ios-probes.py" \
  "$release/app/Madeira/FEXBridge.mm" "$release/app/Madeira/FEXBridge.h" \
  "$release/app/Madeira/JITAllocator.h" "$release/app/Madeira/hello_x86.h"
rm source.tar.xz
# CMake reads the embedded FEX revision with git; never let it read the outer
# project revision from an ambient working copy. No FEX blobs are redownloaded.
git -C FEX init -q
git -C FEX fetch -q --filter=blob:none --depth=1 --no-tags https://github.com/thebcd69/bcd-fex.git "$revision"
git -C FEX update-ref HEAD FETCH_HEAD
test "$(git -C FEX rev-parse HEAD)" = "$revision"

# In the allocator-disabled branch upstream's optional allocator lock does not
# exist. Use BCD's original v712 guard and diagnostics overlay in their CI order.
python3 - <<'PY'
from pathlib import Path
p = Path('FEX/FEXCore/Source/Utils/AllocatorHooks.cpp'); s = p.read_text()
guard = '#ifndef IOS_RPM_GUARD\n#define IOS_RPM_GUARD() ((void)0)\n#endif\n'
anchor = 'size_t malloc_usable_size(void* ptr) {\n  IOS_RPM_GUARD();'
if guard not in s and 'IOS_RPM_GUARD' in s:
    if anchor not in s:
        raise SystemExit('unexpected AllocatorHooks.cpp guard location')
    p.write_text(s.replace(anchor, guard + anchor))
PY
python3 tools/patch-fex-ios-probes.py
# Match the upstream preference for the newest installed Xcode and the toolchain
# that compiles the primary app target.
xc=$(python3 - <<'PY'
from pathlib import Path
import re
choices = list(Path('/Applications').glob('Xcode*.app'))
print(max(choices, key=lambda p: tuple(map(int, re.findall(r'\d+', p.name)))))
PY
)
export DEVELOPER_DIR="$xc/Contents/Developer"
sdk=$(xcrun --sdk iphoneos --show-sdk-path)
cmake -S FEX -B FEX/build-ios -G Ninja \
  -DCMAKE_SYSTEM_NAME=iOS -DCMAKE_SYSTEM_PROCESSOR=aarch64 \
  -DCMAKE_OSX_SYSROOT="$sdk" -DCMAKE_OSX_ARCHITECTURES=arm64 \
  -DCMAKE_OSX_DEPLOYMENT_TARGET=17.0 -DCMAKE_BUILD_TYPE=Release -DCMAKE_MACOSX_BUNDLE=OFF \
  -DBUILD_TESTING=OFF -DBUILD_FEXCONFIG=OFF -DBUILD_THUNKS=OFF \
  -DENABLE_LTO=OFF -DENABLE_CCACHE=OFF -DTUNE_CPU=generic -DTUNE_ARCH=generic \
  -DCMAKE_DISABLE_FIND_PACKAGE_fmt=ON
cmake --build FEX/build-ios --parallel "$(sysctl -n hw.ncpu)" \
  --target FEXCore FEXCore_Base JemallocLibs cephes_128bit softfloat_3e fmt xxhash

# Resolve the generated headers actually included by the sole FEX-consuming app
# source. This uses the same search order as the target's HEADER_SEARCH_PATHS.
includes=()
for dir in FEXCore/include FEXHeaderUtils CodeEmitter External/fmt/include External/range-v3/include \
  External/unordered_dense/include build-ios build-ios/include build-ios/FEXCore/Source .; do
  includes+=("-I$work/FEX/$dir")
done
xcrun --sdk iphoneos clang++ -target arm64-apple-ios17.0 -isysroot "$sdk" -std=c++20 \
  "${includes[@]}" -MM -MT FEXBridge -MF host.d app/Madeira/FEXBridge.mm
mkdir -p "$root"
python3 - "$root" <<'PY'
from pathlib import Path
import shlex, shutil, sys
build = Path('FEX/build-ios').resolve(); out = Path(sys.argv[1]); deps = Path('host.d').read_text()
for word in shlex.split(deps.replace('\\\n', ' '))[1:]:
    p = Path(word).resolve()
    if p.is_relative_to(build):
        target = out / p.relative_to(build); target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, target); print(f'FEXBridge header {p.relative_to(build)}')
PY
for a in "${archives[@]}"; do
  mkdir -p "$root/$(dirname "$a")"
  mv "FEX/build-ios/$a" "$root/$a"
done
verify
