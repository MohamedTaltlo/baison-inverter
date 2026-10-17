#!/usr/bin/env python3
"""Replace LPX70-SDK's embedded OTA target ID before packaging for W70.
Both strings are exactly 16 bytes so linker offsets remain unchanged.
"""
from pathlib import Path
import sys
OLD=b"HF-LPx70x1 Image"
NEW=b"EPORT-PW41 Image"
if len(sys.argv)!=2: sys.exit("usage: python3 patch_runtime_magic.py raw_app.bin")
p=Path(sys.argv[1]);b=p.read_bytes()
n=b.count(OLD)
if n!=1: sys.exit("Unexpected embedded target ID count: %d" % n)
# The recovery HTTP handler also embeds the W70 signature by design.
modified=b.replace(OLD,NEW)
assert len(modified)==len(b)
p.write_bytes(modified)
print("Embedded update validator now uses genuine W70 product ID; changed 16 bytes.")
