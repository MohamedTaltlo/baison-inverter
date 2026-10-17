#!/usr/bin/env python3
"""Package the custom BL602 application in the same outer envelope as official W70.
Source model: W70-v1.45.1-05221108-upgrade.bin.
This proves container-format fidelity, NOT compatibility with SolarV63.
"""
import hashlib
import lzma
import struct
import sys
import zlib
from pathlib import Path

MAGIC=b"EPORT-PW41 Image"
assert len(MAGIC)==16

def crc0(data):
    return (zlib.crc32(data,0xffffffff)^0xffffffff)&0xffffffff

def verify(data):
    assert len(data)>256
    assert data[:16]==MAGIC,(data[:16],MAGIC)
    comp,outer,vers,plain,inner=struct.unpack_from("<IIIII",data,16)
    assert vers==0x03000001,hex(vers)
    assert len(data)==256+comp,(len(data),comp)
    assert crc0(data[24:])==outer,(hex(crc0(data[24:])),hex(outer))
    raw=lzma.decompress(data[256:],format=lzma.FORMAT_ALONE)
    assert len(raw)==plain,(len(raw),plain)
    assert crc0(raw)==inner,(hex(crc0(raw)),hex(inner))
    return raw

def main():
    if len(sys.argv)!=3: sys.exit("Usage: package_ota.py input_HiFlying_Image output_W70.bin")
    original=Path(sys.argv[1]).read_bytes()
    b=bytearray(original)
    b[:16]=MAGIC
    # The actual public W70-v1.45.1-05221108 image has byte 0x24=0,
    # whereas an unrelated SDK postprocessor forcibly sets it to 1.
    b[0x24]=0
    struct.pack_into("<I",b,20,crc0(b[24:]))
    raw=verify(bytes(b))
    if b"mtradius.online" not in raw: sys.exit("Missing configured broker.")
    if b"QPIGS" not in raw: sys.exit("Missing PI30 telemetry command.")
    if b"BAISON_W70_OTA_V8_BOOT" not in raw: sys.exit("Missing firmware boot marker.")
    mandatory=[b"BAISON-W70-V8",b"BAISONOTA",b"BAISONVER",b"BAISONWRITE",
               b"BAISONUART",b"BAISONDIAG",b"/hide.html",b"/api/firmware",b"X-OTA-Code",b"QPIGS",b"QPIRI",b"QMOD",b"QFLAG",
               b"POP00",b"POP01",b"PBT01",b"PBT02",b"MUCHGC010",b"PGR00"]
    missing=[x.decode("ascii") for x in mandatory if x not in raw]
    if missing: sys.exit("Missing critical V7 components: "+", ".join(missing))
    if raw.count(MAGIC)<2: sys.exit("Runtime OTA validator or HTTP recovery lacks W70 signature!")
    if b"HF-LPx70x1 Image" in raw: sys.exit("Wrong LPX70 OTA ID remains embedded in runtime!")
    out=Path(sys.argv[2]);out.write_bytes(bytes(b))
    print("W70 public factory-image envelope verified:")
    print("magic",MAGIC.decode(), "flag0x24",b[0x24])
    print("compressed",len(b)-256,"decompressed",len(raw))
    print("sha256",hashlib.sha256(bytes(b)).hexdigest())
    print("BLE OTA / HTTP OTA / PI30 read-write allowlist / MQTT broker embedded")
    print("This is still untested on physical SWB1 SolarV63.")

if __name__=="__main__":main()
