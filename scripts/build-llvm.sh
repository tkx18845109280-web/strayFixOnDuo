#!/usr/bin/env bash
# BCD v712's pinned LLVM libraries and public headers for its DXMT build.
set -euo pipefail

revision=8dfdcc7b7bf66834a761bd8de445840ef68e4d1a
output="${GITHUB_WORKSPACE:?}/.stray-cache/llvm-15-ios17-arm64"
lib_dir="$output/toolchains/llvm-ios-build/lib"
cfg_dir="$output/toolchains/llvm-ios-build/include"
inc_dir="$output/toolchains/llvm-project/llvm/include"

verify() {
    test "$(cat "$output/revision")" = "$revision"
    for component in Core BitReader BitWriter Analysis TransformUtils InstCombine Passes Support Demangle; do
        test -s "$lib_dir/libLLVM$component.a"
    done
    for archive in "$lib_dir"/*.a; do
        test "$(xcrun lipo -archs "$archive")" = arm64
    done
    test -s "$inc_dir/llvm/IR/Module.h"
    test -s "$inc_dir/llvm-c/Core.h"
    test -s "$cfg_dir/llvm/Config/llvm-config.h"
    test -s "$cfg_dir/llvm/IR/IntrinsicEnums.inc"
    grep -Eq '^#define LLVM_VERSION_MAJOR +15$' "$cfg_dir/llvm/Config/llvm-config.h"
    grep -Eq '^#define LLVM_VERSION_MINOR +0$' "$cfg_dir/llvm/Config/llvm-config.h"
    grep -Eq '^#define LLVM_VERSION_PATCH +7$' "$cfg_dir/llvm/Config/llvm-config.h"
    (cd "$output" && shasum -a 256 -c archives.sha256 >/dev/null)
    echo "Verified $(find "$lib_dir" -name '*.a' | wc -l) arm64 LLVM archives."
}

case "${1:-}" in
    verify) verify; exit 0 ;;
    build) ;;
    *) echo "Usage: $0 build|verify" >&2; exit 2 ;;
esac

work=$(mktemp -d "${RUNNER_TEMP:?}/llvm-v712.XXXXXXXX")
src="$work/src"
host="$work/host"
device="$work/ios"
mkdir -p "$lib_dir" "$cfg_dir" "$inc_dir"
df -h "$work"
git clone --depth=1 --filter=blob:none --no-checkout --single-branch --branch llvmorg-15.0.7 \
    https://github.com/llvm/llvm-project.git "$src"
# LLVM 15 also includes common CMake modules from ../cmake/Modules at configure time.
git -C "$src" sparse-checkout init --no-cone
git -C "$src" sparse-checkout set --no-cone '/llvm/' '/cmake/Modules/'
test "$(git -C "$src" rev-parse HEAD)" = "$revision"
git -C "$src" checkout --detach "$revision"

# Apply the two iOS linker adjustments from the release workflow.
sed -i '' 's/MATCHES "Darwin"/MATCHES "Darwin|iOS"/g' "$src/llvm/cmake/modules/AddLLVM.cmake"
sed -i '' 's/Darwin|FreeBSD|OpenBSD|DragonFly|AIX|SunOS|OS390/Darwin|iOS|FreeBSD|OpenBSD|DragonFly|AIX|SunOS|OS390/g' "$src/llvm/cmake/modules/HandleLLVMOptions.cmake"
grep -q 'Darwin|iOS' "$src/llvm/cmake/modules/AddLLVM.cmake"
grep -q 'Darwin|iOS|FreeBSD' "$src/llvm/cmake/modules/HandleLLVMOptions.cmake"

parallel=$(sysctl -n hw.ncpu)
flags=(
    -G Ninja -DCMAKE_BUILD_TYPE=Release -DCMAKE_POLICY_VERSION_MINIMUM=3.5
    -DCMAKE_C_COMPILER=/usr/bin/clang -DCMAKE_CXX_COMPILER=/usr/bin/clang++
    -DLLVM_TARGETS_TO_BUILD= -DLLVM_INCLUDE_TESTS=OFF -DLLVM_INCLUDE_EXAMPLES=OFF
    -DLLVM_INCLUDE_BENCHMARKS=OFF -DLLVM_INCLUDE_DOCS=OFF -DLLVM_INCLUDE_RUNTIMES=OFF
    -DLLVM_ENABLE_TERMINFO=OFF -DLLVM_ENABLE_LIBXML2=OFF -DLLVM_ENABLE_ZSTD=OFF -DLLVM_ENABLE_ZLIB=OFF
)
cmake -S "$src/llvm" -B "$host" "${flags[@]}"
cmake --build "$host" --parallel "$parallel" --target llvm-tblgen
cp "$host/bin/llvm-tblgen" "$work/llvm-tblgen"
rm -rf "$host"

cmake -S "$src/llvm" -B "$device" "${flags[@]}" \
    -DCMAKE_SYSTEM_NAME=iOS -DCMAKE_OSX_SYSROOT="$(xcrun --sdk iphoneos --show-sdk-path)" \
    -DCMAKE_OSX_ARCHITECTURES=arm64 -DCMAKE_OSX_DEPLOYMENT_TARGET=17.0 -DCMAKE_MACOSX_BUNDLE=OFF \
    -DLLVM_TABLEGEN="$work/llvm-tblgen" -DLLVM_BUILD_UTILS=OFF \
    -DLLVM_BUILD_TOOLS=OFF -DLLVM_INCLUDE_UTILS=OFF
# Upstream combines every LLVM static archive into libdxmt_combined.a. Build all
# archive outputs Ninja generated, without unrelated host tools or dynamic modules.
ninja -C "$device" -t targets all > "$work/device-targets"
archives=()
while IFS= read -r target; do archives+=("$target"); done < <(
    sed -nE 's@^(lib/lib[^/ :]+\.a): .*@\1@p' "$work/device-targets" | sort -u
)
test "${#archives[@]}" -ge 10
cmake --build "$device" --parallel "$parallel" --target "${archives[@]}"
for target in "${archives[@]}"; do mv "$device/$target" "$lib_dir/"; done
generated_headers=("$device/include/llvm")
if [[ -d "$device/include/llvm-c" ]]; then generated_headers+=("$device/include/llvm-c"); fi
rsync -a --exclude=CMakeFiles --exclude=CMakeLists.txt --exclude=cmake_install.cmake \
    "${generated_headers[@]}" "$cfg_dir/"
rsync -a --exclude=CMakeLists.txt "$src/llvm/include/llvm" "$src/llvm/include/llvm-c" "$inc_dir/"
(cd "$output" && shasum -a 256 toolchains/llvm-ios-build/lib/*.a > archives.sha256)
printf '%s\n' "$revision" > "$output/revision"
rm -rf "$work"
du -sh "$output"
