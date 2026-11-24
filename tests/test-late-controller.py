"""Check the Stray late-pair patch against stock BCD and exercise its Swift gates."""
import argparse
import hashlib
from itertools import product
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

parser = argparse.ArgumentParser()
parser.add_argument('bcd',type=Path,help='BCD source checkout after applying the controller patch')
parser.add_argument('--swift',action='store_true',help='also execute the extracted production gates with the Swift compiler')
args=parser.parse_args()
s=(args.bcd/'app/Madeira/GamepadInput.swift').read_text(encoding='utf8')
# The sampler below the connection guard is the complete pinned v712 implementation,
# including the PAD->XInput button mapping, thumbsticks, HID and keyboard/mouse.
mapping=s.split('            var state = winios_gamepad()\n',1)[1]
assert hashlib.sha256(mapping.encode()).hexdigest() == '26409059c35105b6ccb7e5fa1f5e097dfa4fce2038e2adb2b5a5cdb3f1d27d75'
assert 'gameLate: MadeiraConfig.gameValue("env.MADEIRA_PAD_LATE_CONNECT") == "1"' in s
assert 'keys: LibraryModel.shared.controllerMode == "keys"' in s
assert 'mode: Self.configuredPadMode().value' in s
assert 'queue.sync { [self] in latePhysicalPlayerOne = true; sample() }' in s
assert 'guard touch || paired else {' in s
assert 'guard pad != nil || touchConnected || gyroConnected || aimConnected || reservedPhysical else {' in s
predicate=re.search(r'static func reservesLatePhysicalPad\([^)]*\) -> Bool \{\s*(.*?)\s*\}',s,re.S)[1]
reserve=re.search(r'let reservedPhysical = ([^\n]+)',s)[1]
# Keep portable unit tests and the native-toolchain test on the actual source expressions.
assert predicate == 'gameEarly && gameLate && mode == "xinput" && !keys'
assert reserve == 'i == 0 && latePhysicalPlayerOne && !hid && keyboardMouse == nil'
expected=[]
for early,late,mode,keys in product([False,True],[False,True],['xinput','dualsense','generic','xbox360'],[False,True]):
    for slot,hid,kb in product(range(4),[False,True],[False,True]):
        allowed=early and late and mode=='xinput' and not keys
        value=allowed and slot==0 and not hid and not kb
        def sw(b): return str(b).lower()
        expected.append(f'check({sw(early)}, {sw(late)}, "{mode}", {sw(keys)}, {slot}, {sw(hid)}, {sw(kb)}, {sw(value)})')
if args.swift:
    compiler=shutil.which('swiftc')
    if not compiler: raise SystemExit('swiftc unavailable')
    with tempfile.TemporaryDirectory() as tmp:
        path=Path(tmp); file=path/'main.swift'; exe=path/'controller-check'
        file.write_text('func check(_ gameEarly: Bool, _ gameLate: Bool, _ mode: String, _ keys: Bool, _ i: Int, _ hid: Bool, _ kb: Bool, _ expected: Bool) {\n'+
                        ' let keyboardMouse: Int? = kb ? 1 : nil\n let latePhysicalPlayerOne = '+predicate+'\n let reserved = '+reserve+'\n precondition(reserved == expected)\n}\n'+'\n'.join(expected))
        subprocess.run([compiler,file,'-o',exe],check=True); subprocess.run([exe],check=True)
print(f'Native input mapping byte-identical to v712. Checked reservation wiring; {len(expected)} native predicate cases '+('passed.' if args.swift else 'prepared for swiftc.'))
