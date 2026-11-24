"""Re-link the Stray host with an iOS SDK that supports the full Duo display."""
from pathlib import Path
import json
import os
import shutil
import stat
import subprocess
import sys
import zipfile

import native_host as native


REPO = "tkx18845109280-web/strayFixOnDuo"
RUN = 45365087790
COMMIT = "601a7fe17d9954b2925d364fa3576a0efcf39c35"
PATCH_DIGEST = "f08229f918c9554b13f114711b62b5a97b07d2ab45de0f2c139262dc944668f1"
INPUTS = {
    "wine": (14907925300, "c2c0d0a3c5db819ad8c2b221c918c527c6a3f0db75e7901553f90c911ffd1666"),
    "graphics": (14910450108, "16211ece72c8c249127f15b563f3b21f8298bceacffc4bafe90c4ecd69bdc375"),
    "fex": (14908119728, "73aacf127b6c2ff0e995813461f5dd85e6f9dac4ba75e2f7a46d681eeff63d7d"),
}


def api(path):
    return json.loads(subprocess.check_output(["gh","api",f"repos/{REPO}/{path}"]))


def component_inputs():
    native.verify(native.PATCH, PATCH_DIGEST)
    run = api(f"actions/runs/{RUN}")
    if (run["head_sha"],run["conclusion"],run["path"]) != (
            COMMIT,"success",".github/workflows/native-host.yml"):
        raise ValueError("Unexpected source build or commit")
    native.OUT.mkdir(exist_ok=True)
    available = {a["id"]:a for a in api(f"actions/runs/{RUN}/artifacts")["artifacts"]}
    for name,(ident,digest) in INPUTS.items():
        a = available[ident]
        if a["expired"] or (a["name"],a["digest"]) != ("component-"+name,"sha256:"+digest):
            raise ValueError(f"Unexpected or expired {name} archive")
        outer = Path(os.environ["RUNNER_TEMP"])/f"component-{name}.zip"
        with outer.open("wb") as fd:
            subprocess.run(["gh","api",f"repos/{REPO}/actions/artifacts/{ident}/zip"],stdout=fd,check=True)
        native.verify(outer,digest)
        member = name+".tar.gz"
        with zipfile.ZipFile(outer) as archive:
            if archive.namelist()!=[member]:
                raise ValueError(f"Unexpected content in verified artifact {ident}")
            info = archive.getinfo(member)
            if stat.S_ISLNK(info.external_attr>>16):
                raise ValueError(member)
            with archive.open(info) as src,(native.OUT/member).open("xb") as dst:
                shutil.copyfileobj(src,dst)
        outer.unlink()


def version(s):
    return tuple(int(v) for v in s.strip().split("."))


def select_xcode():
    options = {}
    for app in Path("/Applications").glob("Xcode*.app"):
        developer = (app/"Contents/Developer").resolve()
        if developer in options or not developer.is_dir():
            continue
        env = dict(os.environ,DEVELOPER_DIR=str(developer))
        probe = subprocess.run(["xcrun","--sdk","iphoneos","--show-sdk-version"],
                               capture_output=True,text=True,env=env)
        if probe.returncode==0:
            sdk = version(probe.stdout)
            if sdk >= (27,1):
                # Reproduce the official SDK exactly whenever available.
                rank = (sdk[:2]==(27,2), sdk >= (27,2), "beta" not in str(developer).lower(), sdk)
                options[developer] = rank
    if not options:
        raise RuntimeError("Runner has no iOS 27.1+ device SDK")
    selected = max(options,key=options.get)
    with open(os.environ["GITHUB_ENV"],"a") as f:
        print("DEVELOPER_DIR="+str(selected),file=f)
    print("Selected",selected,"device SDK",options[selected][-1])


def relink(controller, before_link=None):
    version_used = version(subprocess.check_output(["xcrun","--sdk","iphoneos","--show-sdk-version"],text=True))
    if version_used < (27,1):
        raise RuntimeError("The selected iOS SDK cannot use the whole Duo display")
    patch_digest = None
    if controller:
        patch = native.ROOT/"recipes/patches/stray-late-controller.patch"
        # Only allow application source in this optional host-only patch.
        diff = subprocess.check_output(["git","apply","--numstat","-z",str(patch)],
                                       cwd=native.BCD).split(b"\0")
        paths = [native.safe_name(e.split(b"\t",2)[2].decode()) for e in diff if e]
        if not paths or any(p.parts[:2]!=("app","Madeira") or
                            p.suffix not in {".swift",".m",".mm",".c",".h"} for p in paths):
            raise ValueError("A controller fix may only modify the application source")
        for flags in (["--check"],[]):
            subprocess.run(["git","-c","core.autocrlf=false","apply",*flags,str(patch)],
                           cwd=native.BCD,check=True)
        patch_digest = native.sha(patch)
    if before_link is not None:
        before_link()
    native.link()
    out = native.ROOT/"out"
    built = native.mach(out/"Madeira")
    actual = (built["sdk"]>>16, (built["sdk"]>>8)&255)
    if (actual < (27,1) or actual!=version_used[:2] or built["platform"]!=2 or
            built["minos"]!=0x120000 or not built["patch_marker"] or built["reservations"]):
        raise ValueError(f"Duo host failed SDK/ABI/Stray marker checks: {built}")
    manifest = out/"build.json"
    report = json.loads(manifest.read_text())
    report["component_run_id"] = RUN
    report["component_commit"] = COMMIT
    report["component_zip_sha256"] = {n:v[1] for n,v in INPUTS.items()}
    report["controller_patch_sha256"] = patch_digest
    report["xcode"] = subprocess.check_output(["xcodebuild","-version"],text=True).strip()
    manifest.write_text(json.dumps(report,indent=2)+"\n")


if __name__=="__main__":
    operation = sys.argv[1]
    if operation=="select":
        select_xcode()
    elif operation=="inputs":
        component_inputs()
    elif operation=="link":
        relink(os.getenv("STRAY_CONTROLLER_PATCH")=="true")
    else:
        raise SystemExit("Expected select, inputs or link")
