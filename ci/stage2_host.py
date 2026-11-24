"""Rebuild one Wine member and link the authenticated v712 inputs using the original Duo recipe."""
from collections import OrderedDict
from pathlib import Path
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

import duo_relink as duo
import native_host as native

RECIPES = native.ROOT / "recipes"
TESTS = RECIPES / "tests"
SOURCE = native.BCD / "build/ntdll-unix/virtual_ios.c"
WINE_PATCH = RECIPES / "patches/stray-va-stage2.patch"
WINE_SHA = "3f6b3470af0c4ed2580846b7182a23b8f9aa1aa51e81bb5d15465f16d2ac4909"
PREVIOUS_SOURCE = "d6bebc808fe225c94d4215c0f4d6d431d55bde4d9f3cdd6f335906f6ce8a731c"
PATCHED_SOURCE = "62806f6b0fed7ea8f8c04efe2279e74279ff78bff146313a2d7350dbcecd0b1a"
PRESSURE_PATCH = RECIPES / "patches/stray-va-pressure.patch"
PRESSURE_SHA = "16fad4faa009abc330e4174c26b5414d82e33f3140e2c2603f870eabf44dcb85"
PRESSURE_SOURCE = "7bbe4d71e6fe5e9944aa6449cd1e9a8b58c69c0ecef4de5fe4be944fe9ca8149"
REGION_PATCH = RECIPES / "patches/stray-va-live-region.patch"
REGION_SHA = "365e76fa0fe6fc16a9fef850e11d06f49ef215b6b551e93d4ba32f7b4ce34658"
REGION_SOURCE = "61d10b196bbaae68c1fc85d5e8e7672ec715bae0afb57d13497f127a68b7f113"
PRESSURE_MARKER = b"early_tries=%llu early_hits=%llu"
ARCHIVE = "app/Madeira/libntdll_unix.a"
REPORT = native.ROOT / "stage2-member.json"
STAGE2_MARKER = b"second_tries=%llu second_hits=%llu second_skips=%llu"
CONTROLLER_MARKER = b"slot=0 ready before launch for late physical attachment"


def check(ok, reason):
    if not ok:
        raise ValueError(reason)


def patch(path, allowed):
    stat = subprocess.check_output(["git", "apply", "--numstat", "-z", str(path)], cwd=native.BCD)
    entries = [item.split(b"\t", 2)[2].decode() for item in stat.split(b"\0") if item]
    check(set(entries) == set(allowed) and len(entries) == len(allowed), "Unapproved patch paths")
    for options in (["--check"], []):
        subprocess.run(["git", "-c", "core.autocrlf=false", "apply", *options, str(path)], cwd=native.BCD, check=True)


def header_steps():
    recipe = native.BCD / ".github/workflows/build-ipa.yml"
    native.verify(recipe, native.WORKFLOW)
    lines = recipe.read_text(encoding="utf-8").splitlines()
    # Apply the real upstream source edits and generate headers. freetype,
    # crypto and ffmpeg do not contribute files to the virtual translation unit.
    skip = {1393, 1478, 1500, 1503, 1520, 1524, 1557}
    for step in native.STEPS["wine"]:
        if step >= 2298:
            break
        if step not in skip:
            name, body = native.published_step(lines, step)
            print("::group::" + name, flush=True)
            subprocess.run(["bash", "-eo", "pipefail", "-c", body], cwd=native.BCD,
                           env=dict(os.environ, GITHUB_WORKSPACE=str(native.BCD)), check=True)
            print("::endgroup::", flush=True)


def compile_one():
    recipe = SOURCE.parent / "build.sh"
    native.verify(recipe, "f353d1e53d85cd4a2e1077e54e80272844553a595126591a68e7b4549cba565c")
    text = recipe.read_text(encoding="utf-8")
    start = text.index("compile_one() {")
    end = text.index("\n}\n", start) + len("\n}\n")
    script = SOURCE.parent / ".compile-stage2-virtual.sh"
    script.write_text(text[:end] + '\ncompile_one "$BUILD_DIR/virtual_ios.c" "virtual"\ntest "$FAILED" = 0\n')
    subprocess.run(["bash", script], cwd=native.BCD, check=True)
    return SOURCE.parent / "obj/virtual.o"


def library_map(path):
    # Darwin ar uses BSD #1/name-length records for alignment and long names.
    # Hash every occurrence separately, including any repeated member names.
    result = []
    with path.open("rb") as f:
        check(f.read(8) == b"!<arch>\n", "Invalid native archive")
        while header := f.read(60):
            check(len(header) == 60 and header[-2:] == b"`\n", "Truncated ar member")
            name = header[:16].decode().rstrip()
            size = int(header[48:58].strip())
            payload = f.read(size)
            check(len(payload) == size, "Truncated ar member contents")
            if size & 1:
                check(len(f.read(1)) == 1, "Missing ar padding")
            if name.startswith("#1/"):
                length = int(name[3:])
                check(length <= size, "Invalid BSD name length")
                name = payload[:length].rstrip(b"\0").decode()
                payload = payload[length:]
            elif name.endswith("/") and name != "/":
                name = name[:-1]
            if name.startswith("__.SYMDEF") or name == "/":
                continue
            result.append((name, hashlib.sha256(payload).hexdigest()))
    return result


def symbols(path, flags):
    return set(subprocess.check_output(["xcrun", "nm", flags, str(path)], text=True).splitlines())


def stream_sha(stream):
    h = hashlib.sha256()
    for block in iter(lambda: stream.read(1 << 20), b""):
        h.update(block)
    return h.hexdigest()


def replace_member(root, new_member):
    original_tar = native.OUT / "wine.tar.gz"
    old_tar_sha = native.sha(original_tar)
    old_archive = root / "library.a"
    with tarfile.open(original_tar) as t:
        check(t.getnames().count(ARCHIVE) == 1, "Missing or duplicate Wine library")
        with t.extractfile(ARCHIVE) as src, old_archive.open("wb") as dst:
            shutil.copyfileobj(src, dst)
    before = library_map(old_archive)
    check([n for n, _ in before].count("virtual.o") == 1, "Ambiguous virtual.o")
    old_object = root / "old-virtual.o"
    with old_object.open("wb") as dst:
        subprocess.run(["xcrun", "ar", "-p", old_archive, "virtual.o"], stdout=dst, check=True)
    check(symbols(old_object, "-gUj") == symbols(new_member, "-gUj"), "Published Wine symbol set changed")
    added = symbols(new_member, "-guj") - symbols(old_object, "-guj")
    check(not added, "Unexpected new external symbols: " + ", ".join(sorted(added)))
    subprocess.run(["xcrun", "ar", "-r", old_archive, new_member], check=True)
    subprocess.run(["xcrun", "ranlib", old_archive], check=True)
    after = library_map(old_archive)
    expected = [(name, native.sha(new_member) if name == "virtual.o" else digest) for name, digest in before]
    check(after == expected and after != before, "Extra member changes in Wine library")
    new_tar = root / "wine-stage2.tar.gz"
    unchanged = OrderedDict()
    with tarfile.open(original_tar) as src, tarfile.open(new_tar, "w:gz", compresslevel=1) as dst:
        for member in src:
            check(member.isfile() and member.name not in unchanged, "Unexpected original Wine tar entry")
            if member.name == ARCHIVE:
                member.size = old_archive.stat().st_size
                with old_archive.open("rb") as data:
                    dst.addfile(member, data)
                unchanged[member.name] = native.sha(old_archive)
            else:
                with src.extractfile(member) as data:
                    unchanged[member.name] = stream_sha(data)
                with src.extractfile(member) as data:
                    dst.addfile(member, data)
    with tarfile.open(new_tar) as src:
        check(src.getnames() == list(unchanged), "Wine tar inventory changed")
        for member in src:
            with src.extractfile(member) as data:
                check(stream_sha(data) == unchanged[member.name], member.name)
    new_tar.replace(original_tar)
    return dict(original_wine_tar_sha256=old_tar_sha, wine_tar_sha256=native.sha(original_tar),
                library_sha256=native.sha(old_archive), previous_virtual_sha256=native.sha(old_object),
                virtual_sha256=native.sha(new_member), wine_member_count=len(before))


def wine():
    native.verify(native.PATCH, duo.PATCH_DIGEST)
    native.verify(WINE_PATCH, WINE_SHA)
    native.verify(SOURCE, "c3ab8d95a5988fd2768e03a8d00a54ca8ac7a773fdc106b9657c78d90b8f8def")
    patch(native.PATCH, ["build/ntdll-unix/virtual_ios.c"])
    native.verify(SOURCE, PREVIOUS_SOURCE)
    subprocess.run([sys.executable, TESTS / "test-patch.py", native.BCD], check=True)
    patch(WINE_PATCH, ["build/ntdll-unix/virtual_ios.c"])
    native.verify(SOURCE, PATCHED_SOURCE)
    native.verify(PRESSURE_PATCH, PRESSURE_SHA)
    patch(PRESSURE_PATCH, ["build/ntdll-unix/virtual_ios.c"])
    native.verify(SOURCE, PRESSURE_SOURCE)
    contents = SOURCE.read_text(encoding="utf-8")
    check(contents.count((TESTS / "stage2.h").read_text(encoding="utf-8").strip()) == 1,
          "Stage2 policy tests differ from production")
    with tempfile.TemporaryDirectory(dir=native.ROOT) as directory:
        temp = Path(directory)
        def section(begin, end):
            start = contents.index(begin)
            return contents[start:contents.index(end, start)]
        helpers = section("/* Called only after stray_va_hint", "#ifdef WINE_IOS")
        helpers += section("/* A search hint, never a reservation.", "/* Measurement cannot displace")
        body = section("                    uintptr_t first, last, pid =", "\n                }\n                else ptr = map_free_area")
        template = (TESTS / "stage2-integration.in.c").read_text(encoding="utf-8")
        integration = temp / "integration.c"
        integration.write_text(template.replace("/* @production_helpers */", helpers).replace("/* @production_path */", body))
        for source in (TESTS / "stage2-test.c", integration):
            test = temp / source.stem
            subprocess.run(["cc", "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                            source, "-o", test], check=True)
            subprocess.run([test], check=True)
        header_steps()
        native.verify(SOURCE, PRESSURE_SOURCE)
        baseline = temp / "before-region.c"
        shutil.copyfile(SOURCE, baseline)
        native.verify(REGION_PATCH, REGION_SHA)
        patch(REGION_PATCH, ["build/ntdll-unix/virtual_ios.c"])
        native.verify(SOURCE, REGION_SOURCE)
        subprocess.run([sys.executable, TESTS / "test-live-region.py", baseline, SOURCE], check=True)
        member = compile_one()
        check(all(m in member.read_bytes() for m in (STAGE2_MARKER, PRESSURE_MARKER)), "Missing Wine cursor telemetry")
        report = replace_member(temp, member)
        REPORT.write_text(json.dumps(dict(report, stage2_patch_sha256=WINE_SHA, stage2_source_sha256=PATCHED_SOURCE,
                                          pressure_patch_sha256=PRESSURE_SHA, pressure_source_sha256=PRESSURE_SOURCE,
                                          region_patch_sha256=REGION_SHA, region_source_sha256=REGION_SOURCE), indent=2))


def link():
    import stage2_scanner
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    check(native.sha(native.OUT / "wine.tar.gz") == report["wine_tar_sha256"], "Wine build changed")
    changes = {}
    def apply_app_changes():
        changes.update(stage2_scanner.apply())
    duo.relink(True, before_link=apply_app_changes)
    out = native.ROOT / "out"
    host = (out / "Madeira").read_bytes()
    check(all(m in host for m in (STAGE2_MARKER, PRESSURE_MARKER, CONTROLLER_MARKER))
          and all(marker.encode() in host for marker in changes["markers"]),
          "Native mapper, controller or diagnostic selector missing")
    manifest = out / "build.json"
    final = json.loads(manifest.read_text(encoding="utf-8"))
    check(final["rebuilt"]["sdk"] == 0x1b0200 and final["host_sha256"] == native.sha(out / "Madeira"),
          "Unexpected SDK or native executable digest")
    final.update(report)
    final.update(changes["patches"])
    manifest.write_text(json.dumps(final, indent=2) + "\n")


if __name__ == "__main__":
    {"wine": wine, "link": link}[sys.argv[1]]()
