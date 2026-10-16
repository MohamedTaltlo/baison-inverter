# -*- coding: utf-8 -*-
"""Read-only Voltronic PI30 parsing for the validated BAISON KING test unit.

USB transactions are performed in hid_windows.py. This module has no device I/O.
"""
from __future__ import annotations
from dataclasses import dataclass

READ_ONLY = frozenset(('QPI', 'QID', 'QSID', 'QMN', 'QVFW', 'QMOD', 'QPIGS', 'QPIRI', 'QPIWS', 'QDI', 'QFLAG', 'QVFW3', 'QGMN', 'QLED', 'QBEQI', 'QET', 'QLT', 'QMCHGCR', 'QMUCHGCR', 'QOPPT', 'QCHPT'))
MODES = {'P': 'وضع التشغيل (Power On)', 'S': 'استعداد (Standby)', 'L': 'كهرباء خارجية (Line)',
         'B': 'بطارية (Battery)', 'F': 'عطل (Fault)', 'H': 'توفير الطاقة (Power Saving)'}

class ProtocolError(Exception):
    pass

def crc16_xmodem(data: bytes) -> int:
    crc = 0
    for b in data:
        crc ^= (b << 8)
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xffff if crc & 0x8000 else (crc << 1) & 0xffff
    return crc

def escaped_crc(data: bytes) -> bytes:
    crc = crc16_xmodem(data)
    out = []
    for b in ((crc >> 8) & 0xff, crc & 0xff):
        out.append(b + 1 if b in (0x0a, 0x0d, 0x28) else b)
    return bytes(out)

def make_query(command: str) -> bytes:
    if command not in READ_ONLY:
        raise ProtocolError('الأمر غير موجود ضمن قائمة أوامر القراءة الآمنة')
    raw = command.encode('ascii')
    packet = raw + escaped_crc(raw) + b'\r'
    if len(packet) > 48:
        raise ProtocolError('أمر القراءة أطول من الحد المسموح')
    return packet

def decode_response(response: bytes) -> str:
    if not response or not response.startswith(b'(') or not response.endswith(b'\r') or len(response) < 5:
        raise ProtocolError('إطار استجابة غير مكتمل')
    payload = response[:-3]
    if escaped_crc(payload) != response[-3:-1]:
        raise ProtocolError('CRC غير صحيح؛ الاستجابة مرفوضة')
    try:
        text = payload.decode('ascii')
    except UnicodeDecodeError as exc:
        raise ProtocolError('الاستجابة ليست نص PI30 معروف') from exc
    if text == '(NAK':
        raise ProtocolError('الجهاز رفض أمر القراءة (NAK)')
    return text[1:]

def words(payload: str) -> list[str]:
    return payload.strip().split()

def safe_float(value: str):
    try:
        return float(value)
    except (ValueError, TypeError):
        return None

def parse_live(payload: str) -> dict:
    """Only map the positions confirmed for the tested 24-field PI30 layout.

    Positions 0..13 based on PI30 QPIGS field layout and validated with the
    supplied BAISON trace. Extended fields are exposed raw, not guessed.
    """
    fs = words(payload)
    if len(fs) < 14:
        raise ProtocolError('عدد حقول QPIGS غير متوقع: %d' % len(fs))
    return {
        'grid_v': safe_float(fs[0]), 'grid_hz': safe_float(fs[1]),
        'out_v': safe_float(fs[2]), 'out_hz': safe_float(fs[3]),
        'va': safe_float(fs[4]), 'watts': safe_float(fs[5]),
        'load_percent': safe_float(fs[6]), 'bus_v': safe_float(fs[7]),
        'battery_v': safe_float(fs[8]), 'charge_amp': safe_float(fs[9]),
        'battery_percent': safe_float(fs[10]), 'temp_c': safe_float(fs[11]),
        'pv_amp': safe_float(fs[12]), 'pv_v': safe_float(fs[13]),
        # PI30 status extension: trace from BAISON VMIII-4200 has 24 fields.
        # Raw bitstrings MUST retain their leading zeroes.
        'scc_battery_v': safe_float(fs[14]) if len(fs)>14 else None,
        'battery_discharge_amp': safe_float(fs[15]) if len(fs)>15 else None,
        'status_bits': fs[16] if len(fs)>16 else None,
        'fan_offset_10mv': safe_float(fs[17]) if len(fs)>17 else None,
        'eeprom_version': fs[18] if len(fs)>18 else None,
        'pv_charge_watts': safe_float(fs[19]) if len(fs)>19 else None,
        'status_bits_extra': fs[20] if len(fs)>20 else None,
        'solar_feed_grid_status': fs[21] if len(fs)>21 else None,
        'regulatory_profile': fs[22] if len(fs)>22 else None,
        'solar_feed_grid_watts': safe_float(fs[23]) if len(fs)>23 else None,
        'raw_fields': fs, 'raw': payload,
    }

def parse_identity(values: dict) -> dict:
    qid = (values.get('QID') or '').strip()
    qsid = (values.get('QSID') or '').strip()
    # QSID from the test device: 2-char decimal count, followed by ID plus
    # additional fields; do not assume remaining digits are another serial.
    qs_serial = ''
    if len(qsid) >= 2 and qsid[:2].isdigit():
        size = int(qsid[:2])
        if 0 < size <= 40 and len(qsid) >= 2 + size:
            qs_serial = qsid[2:2 + size]
    return {'internal_id': qid or qs_serial, 'qsid_serial': qs_serial,
            'model': (values.get('QMN') or '').strip(),
            'firmware': (values.get('QVFW') or '').replace('VERFW:', '').strip(),
            'mode': (values.get('QMOD') or '').strip(),
            'rated_raw': values.get('QPIRI') or '', 'defaults_raw': values.get('QDI') or '',
            'flags_raw': values.get('QFLAG') or ''}


def parse_eeprom_diagnostics(qpigs: str, qflag: str, qpiws: str) -> str:
    """Read-only diagnostics. Firmware mapping remains provisional."""
    fields=words(qpigs)
    version=fields[18] if len(fields)>=19 else 'غير متاح'
    enabled=None
    if qflag.startswith('E') and 'D' in qflag:
        enabled='z' in qflag[1:qflag.find('D')]
    fault=None
    if qpiws and len(qpiws)>=32 and set(qpiws).issubset({'0','1'}):
        fault=qpiws[17] == '1'
    flag_txt=('مفعّل' if enabled else 'معطّل' if enabled is False else 'غير معروف')
    fault_txt=('موجود' if fault else 'غير ظاهر' if fault is False else 'غير معروف')
    return (f'EEPROM — نسخة المتحكم: {version} | علم تسجيل رموز الأعطال z: {flag_txt} | '
            f'مؤشر خطأ EEPROM حسب جدول Axpert: {fault_txt} | '
            'قراءة تشخيصية فقط؛ لا توجد مساحة كتابة صيانة موثقة.')
