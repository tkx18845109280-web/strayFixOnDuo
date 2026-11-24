"""Rebuild the native host from BCD's pinned public v712 release sources."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import plistlib
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import zipfile

ROOT = Path.cwd().resolve()
BCD = ROOT / "bcd"
OUT = ROOT / "transfer"
PATCH = ROOT / "recipes/patches/stray-va-cursor.patch"
RELEASE = "https://github.com/thebcd69/bcd-games/releases/download/v0.1.712/"
SOURCE = "fb3a7d62d3c47c2a2b62918baaa81641eb48444bca66c0f1a6db63bf5c1563a0"
IPA = "c7fd6e94b7a22cde2d2cd6baeba8f852f34fc50d52db00c44aeb1ad92a20c0be"
WORKFLOW = "a47c746e646be8176ce13e160ca1bb623f079aad688ddc9eae26d6e8b2985791"
CONVERTER = "073f903be98e973ff38f4d79f2c48d61ef938754a77b1caedda79c9f05a068c2"
CONVERTER_URL = "https://raw.githubusercontent.com/willfaust/Madeira/8a8cabebe5fdf159af0a4c69fcff85b3b1fc4e05/app/Madeira/d3d12/libmetalirconverter.dylib"
OFFICIAL_COMMIT = "f811dd05a9183cda20ffd3956cb1958cafe654f7"
MARKER = b"[stray-va] pid=%llu full=%llu expensive=%llu hint_hit=%llu"

# These are step start lines in the SHA-verified published recipe. The run
# commands come directly from that recipe, never from a mutable branch.
STEPS = {
    "wine": [1310,1367,1393,1407,1426,1478,1500,1503,1520,1524,1557,
             1733,1741,1749,1757,1765,1773,1782,1790,1798,1804,1812,1819,
             1824,1831,1837,1845,1854,2051,2057,2194,2201,2209,2216,2224,
             2230,2239,2246,2256,2265,2287,2296,2298,2310,2372,2380,2387,
             2394,2552,2590,2599,2608,2618,2627,2633,6028],
    "fex": [2837,2861,2865,2896],
    "llvm": [2994,3041],
    "graphics": [3094,3102,3125,3194,3199,3203,3206,3212,3220,3225,3230,
                 3235,3240,3245,3251,3257,3263,3268,3278,3288,3298,3308,
                 3321,3327,3333,3339,3345,3355,3361,3371,3378,3384,3387,
                 3393,3399,3416,3423,3433,3506,3516,3519,3526,3534,3541,
                 3546,3553,3559,3566,3572,3581,3590,3600,3607,3614,3622,
                 3633,3644,3654,3665,3674,3682,3688,3693,3700,3706,3712,
                 3721,3731,3737,3747,3752,3762,3771,3782,3788,3848,3857,
                 4073,4089],
    "link": [6047,6070,6105],
}
WINE_LIBS = ["lib"+s+".a" for s in ("ntdll_unix","wineserver","win32u_unix",
             "madeira_rppairing","avformat","avcodec","swresample","avutil",
             "gnutls","nettle","hogweed","gmp")]
FEX_LIBS = ["FEXCore/Source/libFEXCore.a","FEXCore/Source/libFEXCore_Base.a",
            "FEXCore/Source/libJemallocLibs.a","External/cephes/libcephes_128bit.a",
            "External/fmt/libfmt.a","External/SoftFloat-3e/libsoftfloat_3e.a",
            "External/xxhash/cmake_unofficial/libxxhash.a"]

def run(cmd, **kw):
    return subprocess.run(cmd, check=True, **kw)

def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1024*1024), b""):
            h.update(block)
    return h.hexdigest()

def verify(path, expected):
    if sha(path) != expected:
        raise ValueError(f"Unexpected SHA-256 for {path}")

def fetch(url, path, expected):
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.is_file():
        tmp = path.with_name(path.name+".download")
        run(["curl","--fail","--silent","--show-error","--location","--retry","4",
             "--proto","=https","--proto-redir","=https","--output",str(tmp),url])
        verify(tmp, expected)
        tmp.replace(path)
    verify(path, expected)

def safe_name(name):
    p = PurePosixPath(name)
    if not p.parts or p.is_absolute() or ".." in p.parts or "\\" in name:
        raise ValueError(f"Unsafe archive member {name!r}")
    return p

def unpack_source(archive):
    # The published source contains only regular files and directories.
    with tarfile.open(archive, "r|xz") as t:
        for member in t:
            p = safe_name(member.name)
            if p.parts[0] != "bcd-0.1.712" or not (member.isfile() or member.isdir()):
                raise ValueError(f"Unexpected source member {member.name}")
            path = BCD.joinpath(*p.parts[1:])
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                with t.extractfile(member) as src, path.open("xb") as dst:
                    shutil.copyfileobj(src,dst)
                path.chmod(member.mode & 0o777)

def prepare():
    commit = subprocess.check_output(["git","-C",str(ROOT/"publisher"),"rev-parse","HEAD"],text=True).strip()
    if commit != OFFICIAL_COMMIT:
        raise ValueError("The release publisher checkout is not the pinned revision")
    archive = Path(os.environ["RUNNER_TEMP"])/"BCD-source.tar.xz"
    fetch(RELEASE+"BCD-source.tar.xz", archive, SOURCE)
    unpack_source(archive)
    verify(BCD/".github/workflows/build-ipa.yml",WORKFLOW)
    print("Available staging disk GiB",shutil.disk_usage(BCD).free//2**30,flush=True)

def published_step(lines, line):
    start=line-1
    if not lines[start].startswith("      - name: "):
        raise ValueError(f"Wrong upstream recipe anchor {line}")
    name=lines[start][14:].strip('"')
    idx=start+1
    while idx<len(lines) and not lines[idx].startswith("        run: "):
        if lines[idx].startswith("      - "):
            raise ValueError(f"{name}: no run command")
        idx+=1
    cmd=lines[idx][13:]
    if cmd == "|":
        block=[]
        for l in lines[idx+1:]:
            if l.strip() and not l.startswith("          "):
                break
            block.append(l[10:])
        cmd="\n".join(block).rstrip()
    # Documentation and service-worker tests have inputs the public source
    # excludes. Preserve the shell's actual patch and verification commands.
    cmd="\n".join(l for l in cmd.splitlines() if not l.lstrip().startswith("python3 tests/host/") or line in {2296,3094})
    if line==1557:
        cmd=cmd.replace("${{ steps.ffmpegcache.outputs.cache-hit }}","false")
    if "${{" in cmd or not cmd.strip():
        raise ValueError(f"{name}: unresolved input")
    return name, cmd

def execute_steps(phase):
    path=BCD/".github/workflows/build-ipa.yml"
    verify(path,WORKFLOW)
    lines=path.read_text().splitlines()
    env=dict(os.environ,GITHUB_WORKSPACE=str(BCD))
    if phase=="graphics":
        env["MADEIRA_MSC_INCLUDE"]=str(BCD/"madeira-d3d12/third_party/metal-shader-converter/include")
    for line in STEPS[phase]:
        name,shell=published_step(lines,line)
        print("::group::"+name,flush=True)
        run(["/bin/bash","-eo","pipefail","-c",shell],cwd=BCD,env=env)
        print("::endgroup::",flush=True)

def files_for(phase):
    if phase=="graphics":
        paths=[BCD/"app/Madeira/libdxmt_combined.a"]
    elif phase=="wine":
        paths=[BCD/"app/Madeira"/n for n in WINE_LIBS]
    else:
        base=BCD/"FEX/build-ios"
        files={base/n for n in FEX_LIBS}
        # Xcode uses generated FEX headers directly. Exclude object files
        # and build-system caches with per-run absolute paths.
        files.update(p for p in base.rglob("*") if p.is_file() and p.suffix in {".h",".hpp",".inl",".inc",".def"})
        paths=sorted(files)
    for p in paths:
        if not p.is_file() or (p.suffix==".a" and p.stat().st_size<=8):
            raise FileNotFoundError(p)
    return paths

def pack(phase):
    OUT.mkdir(parents=True,exist_ok=True)
    with tarfile.open(OUT/(phase+".tar.gz"),"w:gz",compresslevel=1) as archive:
        for path in files_for(phase):
            archive.add(path,arcname=path.relative_to(BCD),recursive=False)

def unpack_components():
    for phase in ("fex","graphics","wine"):
        with tarfile.open(OUT/(phase+".tar.gz")) as t:
            for m in t:
                p=safe_name(m.name)
                if not m.isfile() or not (p.parts[:2]==("FEX","build-ios") or p.parts[:2]==("app","Madeira")):
                    raise ValueError(f"Unexpected component path {p}")
                dst=BCD.joinpath(*p.parts)
                dst.parent.mkdir(parents=True,exist_ok=True)
                with t.extractfile(m) as src,dst.open("wb") as out:
                    shutil.copyfileobj(src,out)
        files_for(phase)

def converter():
    path=BCD/"app/Madeira/d3d12/libmetalirconverter.dylib"
    fetch(CONVERTER_URL,path,CONVERTER)

def resource_stage():
    archive=Path(os.environ["RUNNER_TEMP"])/"official.ipa"
    fetch(RELEASE+"BCD.ipa",archive,IPA)
    directories={"gl","d3d12","legal","licenses","vulkan","compat","nls","soundfont","fonts-ja",
                 "aarch64-windows","arm64ec-windows","i386-windows","x86_64-vcruntime"}
    loose={"prefix-template.tar.gz","cacert.pem","recommendations.json","BCDThemes.json",
           "madeira-jit.js","Madeira JIT.shortcut"}
    app=BCD/"app/Madeira"
    with zipfile.ZipFile(archive) as z:
        info=plistlib.loads(z.read("Payload/Madeira.app/Info.plist"))
        if (info["CFBundleVersion"],info["CFBundleIdentifier"])!=("712","com.thebcd69.bcd"):
            raise ValueError("Unexpected original app")
        for ent in z.infolist():
            p=safe_name(ent.filename)
            if ent.is_dir() or p.parts[:2]!=("Payload","Madeira.app"):
                continue
            rel=p.parts[2:]
            if not rel or (rel[0] not in directories and not (len(rel)==1 and rel[0] in loose)):
                continue
            if stat.S_ISLNK(ent.external_attr>>16):
                raise ValueError(f"Unexpected symbolic link in game resources: {p}")
            dst=app.joinpath(*rel)
            dst.parent.mkdir(parents=True,exist_ok=True)
            with z.open(ent) as src,dst.open("wb") as out:
                shutil.copyfileobj(src,out)
        # Used to compare ABI and present/absent executable JIT reservations.
        stock=Path(os.environ["RUNNER_TEMP"])/"Madeira.stock"
        with z.open("Payload/Madeira.app/Madeira") as src, stock.open("wb") as out:
            shutil.copyfileobj(src,out)
    # deps.sh checks the original upstream bytes before any bundle re-signing.
    dst=app/"d3d12/libmetalirconverter.dylib"
    dst.unlink()
    converter()
    return stock

def mach(path):
    data=path.read_bytes()
    magic,cpu,sub,filetype,ncmd=struct.unpack_from("<5I",data)
    if (magic,cpu,filetype)!=(0xfeedfacf,0x100000c,2):
        raise ValueError("Invalid arm64 Mach-O executable")
    out={"subtype":sub,"dylibs":[],"reservations":{}}
    pos=32
    for _ in range(ncmd):
        cmd,size=struct.unpack_from("<II",data,pos)
        if cmd==0x32:
            out["platform"],out["minos"],out["sdk"]=struct.unpack_from("<3I",data,pos+8)
        if cmd==0x1b:
            out["uuid"]=data[pos+8:pos+24].hex()
        if cmd in {0xc,0x80000018,0x8000001f}:
            off=struct.unpack_from("<I",data,pos+8)[0]
            out["dylibs"].append((cmd,data[pos+off:pos+size].split(b"\0")[0].decode()))
        if cmd==0x19:
            ns=struct.unpack_from("<I",data,pos+64)[0]
            for i in range(ns):
                ptr=pos+72+i*80
                sec=data[ptr:ptr+16].rstrip(b"\0").decode()
                seg=data[ptr+16:ptr+32].rstrip(b"\0").decode()
                length=struct.unpack_from("<Q",data,ptr+40)[0]
                if length>=128*2**20:
                    out["reservations"][seg+","+sec]=length
        pos+=size
    if out.get("platform")!=2:
        raise ValueError("Host is not built for physical iOS")
    out["dylibs"].sort()
    out["patch_marker"]=MARKER in data
    return out

def link():
    unpack_components()
    stock=resource_stage()
    reference=mach(stock)
    if reference["reservations"]:
        raise ValueError("Unexpected official JIT reservation; determine stock compile-time pool before proceeding")
    execute_steps("link")
    destination=ROOT/"out"
    destination.mkdir(exist_ok=True)
    archive=Path(os.environ["RUNNER_TEMP"])/"Madeira.xcarchive"
    log=BCD/"archive.log"
    with log.open("w") as stream:
        result=subprocess.run(["xcodebuild","-project","app/Madeira.xcodeproj","-scheme","Madeira",
                "-sdk","iphoneos","-configuration","Debug","-destination","generic/platform=iOS",
                "CODE_SIGNING_ALLOWED=NO","ENABLE_DEBUG_DYLIB=NO","ARCHS=arm64",
                "GCC_PREPROCESSOR_DEFINITIONS=$(inherited) BCD_POOL_BSS_MB=0",
                "archive","-archivePath",str(archive)],
                cwd=BCD,stdout=stream,stderr=subprocess.STDOUT)
    if result.returncode:
        for msg in log.read_text(errors="replace").splitlines():
            if "error:" in msg or "Undefined symbols" in msg or "referenced from:" in msg:
                print(msg,flush=True)
        raise RuntimeError("Xcode archive failed; see archive.log")
    host=archive/"Products/Applications/Madeira.app/Madeira"
    built=mach(host)
    for field in ["platform","minos","subtype","reservations"]:
        if reference[field]!=built[field]:
            raise ValueError(f"Native ABI mismatch in {field}")
    if not built["patch_marker"] or built["uuid"]==reference["uuid"]:
        raise ValueError("No evidence of an updated native executable")
    # A rebuilt binary may legitimately import new SDK Swift overlay dylibs.
    # Record every difference and reject loss of a stock-linked dependency.
    missing=set(reference["dylibs"])-set(built["dylibs"])
    if missing:
        raise ValueError(f"Missing stock Mach-O imports: {sorted(missing)}")
    for cmd,dep in set(built["dylibs"])-set(reference["dylibs"]):
        if not dep.startswith(("/usr/lib/","/System/Library/Frameworks/")):
            raise ValueError(f"New external runtime dependency: {dep}")
    shutil.copy2(host,destination/"Madeira")
    report={"release":"0.1.712","release_commit":OFFICIAL_COMMIT,"source_sha256":SOURCE,
            "original_ipa_sha256":IPA,"patch_sha256":sha(PATCH),"host_sha256":sha(host),
            "workflow_commit":os.environ.get("GITHUB_SHA"),"official":reference,"rebuilt":built}
    (destination/"build.json").write_text(json.dumps(report,indent=2)+"\n")

if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("task",choices=("prepare","test-steps","run","pack","link"))
    parser.add_argument("phase",nargs="?")
    opt=parser.parse_args()
    if opt.task=="prepare":
        prepare()
    elif opt.task=="test-steps":
        path=Path(opt.phase) if opt.phase else BCD/".github/workflows/build-ipa.yml"
        verify(path,WORKFLOW)
        lines=path.read_text(encoding="utf8").splitlines()
        for phase,indices in STEPS.items():
            for idx in indices:
                name,cmd=published_step(lines,idx)
                print(f"{phase:10} {idx:5} {name}")
    elif opt.task=="run":
        if opt.phase=="wine":
            verify(BCD/"build/ntdll-unix/virtual_ios.c","c3ab8d95a5988fd2768e03a8d00a54ca8ac7a773fdc106b9657c78d90b8f8def")
            run(["git","-c","core.autocrlf=false","apply","--check",str(PATCH)],cwd=BCD)
            run(["git","-c","core.autocrlf=false","apply",str(PATCH)],cwd=BCD)
            verify(BCD/"build/ntdll-unix/virtual_ios.c","d6bebc808fe225c94d4215c0f4d6d431d55bde4d9f3cdd6f335906f6ce8a731c")
            run([sys.executable,str(ROOT/"recipes/tests/test-patch.py"),str(BCD)])
        if opt.phase=="graphics":
            converter()
            # All 470 source-repository blobs occur unchanged in the public
            # source archive. DXMT embeds git describe in its shader cache ID.
            dxmt=BCD/"dxmt"
            rev="55cecd983b1dc224a9cbd84ed4c682eb26a37ac7"
            run(["git","init","--quiet"],cwd=dxmt)
            run(["git","fetch","--quiet","--depth=1","--filter=blob:none","--no-tags",
                 "https://github.com/thebcd69/bcd-dxmt.git",rev],cwd=dxmt)
            run(["git","update-ref","HEAD",rev],cwd=dxmt)
        execute_steps(opt.phase)
    elif opt.task=="pack":
        pack(opt.phase)
    else:
        link()
