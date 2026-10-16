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
    if b"BAISON_W70_OTA_V6_BOOT" not in raw: sys.exit("Missing firmware boot marker.")
    if b"SW_UPLOAD" not in raw: sys.exit("Missing recovery HTTP firmware-update handler!")
    if raw.count(MAGIC)!=1: sys.exit("Runtime OTA handler target ID differs from W70!")
    if b"HF-LPx70x1 Image" in raw: sys.exit("Wrong LPX70 OTA ID remains embedded in runtime!")
    out=Path(sys.argv[2]);out.write_bytes(bytes(b))
    print("W70 public factory-image envelope verified:")
    print("magic",MAGIC.decode(), "flag0x24",b[0x24])
    print("compressed",len(b)-256,"decompressed",len(raw))
    print("sha256",hashlib.sha256(bytes(b)).hexdigest())
    print("SW_UPLOAD / BAISON / PI30 / MQTT broker embedded")
    print("This is still untested on physical SWB1 SolarV63.")

if __name__=="__main__":main()
