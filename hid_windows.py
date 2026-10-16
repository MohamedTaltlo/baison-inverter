# -*- coding: utf-8 -*-
"""Native Windows HID backend for BAISON 0665:5161.

No additional USB drivers required. Inquiries pass the strict
read-only whitelist in protocol.py. Separate guarded setters exist for
documented PI30 configuration settings; no arbitrary memory/firmware writes.
"""
import ctypes
import os
import sys
from ctypes import wintypes
from protocol import ProtocolError, make_query, decode_response

VID_PID = 'vid_0665&pid_5161'

class DeviceError(RuntimeError):
    pass

if sys.platform == 'win32':
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    setupapi = ctypes.WinDLL('setupapi', use_last_error=True)
    hiddll = ctypes.WinDLL('hid', use_last_error=True)

    class GUID(ctypes.Structure):
        _fields_ = [('Data1', wintypes.DWORD), ('Data2', wintypes.WORD),
                    ('Data3', wintypes.WORD), ('Data4', ctypes.c_ubyte * 8)]

    class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
        _fields_ = [('cbSize', wintypes.DWORD), ('InterfaceClassGuid', GUID),
                    ('Flags', wintypes.DWORD), ('Reserved', ctypes.c_void_p)]

    class OVERLAPPED(ctypes.Structure):
        _fields_ = [('Internal', ctypes.c_void_p), ('InternalHigh', ctypes.c_void_p),
                    ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD),
                    ('hEvent', wintypes.HANDLE)]

    hiddll.HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
    hiddll.HidD_GetHidGuid.restype = None
    setupapi.SetupDiGetClassDevsW.argtypes = [ctypes.POINTER(GUID), wintypes.LPCWSTR, wintypes.HWND, wintypes.DWORD]
    setupapi.SetupDiGetClassDevsW.restype = wintypes.HANDLE
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(GUID),
                                                     wintypes.DWORD, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA)]
    setupapi.SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [wintypes.HANDLE, ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
                                                           ctypes.c_void_p, wintypes.DWORD,
                                                           ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    setupapi.SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL
    setupapi.SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
    setupapi.SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

    kernel32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                                      wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateEventW.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
                                   ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(OVERLAPPED)]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = kernel32.ReadFile.argtypes
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.GetOverlappedResult.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED),
                                              ctypes.POINTER(wintypes.DWORD), wintypes.BOOL]
    kernel32.GetOverlappedResult.restype = wintypes.BOOL
    kernel32.CancelIoEx.argtypes = [wintypes.HANDLE, ctypes.POINTER(OVERLAPPED)]
    kernel32.CancelIoEx.restype = wintypes.BOOL

    INVALID_HANDLE = ctypes.c_void_p(-1).value
    SHARE = 0x00000001 | 0x00000002
    OPEN_EXISTING = 3
    FILE_FLAG_OVERLAPPED = 0x40000000
    GEN_READ_WRITE = 0x80000000 | 0x40000000


def find_device_paths() -> list[str]:
    if sys.platform != 'win32':
        return []
    guid = GUID()
    hiddll.HidD_GetHidGuid(ctypes.byref(guid))
    group = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, 0x12)
    if group == INVALID_HANDLE or group is None:
        raise DeviceError('تعذر فحص منافذ USB HID')
    paths = []
    try:
        idx = 0
        while True:
            dev = SP_DEVICE_INTERFACE_DATA()
            dev.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            if not setupapi.SetupDiEnumDeviceInterfaces(group, None, ctypes.byref(guid), idx, ctypes.byref(dev)):
                if ctypes.get_last_error() == 259:  # ERROR_NO_MORE_ITEMS
                    break
                raise DeviceError('تعذر إكمال البحث عن أجهزة HID')
            idx += 1
            needed = wintypes.DWORD()
            setupapi.SetupDiGetDeviceInterfaceDetailW(group, ctypes.byref(dev), None, 0,
                                                       ctypes.byref(needed), None)
            if needed.value < 8:
                continue
            buf = ctypes.create_string_buffer(needed.value)
            ctypes.c_uint32.from_buffer(buf).value = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6
            result = setupapi.SetupDiGetDeviceInterfaceDetailW(group, ctypes.byref(dev),
                        ctypes.cast(buf, ctypes.c_void_p), needed.value, ctypes.byref(needed), None)
            if result:
                path = ctypes.wstring_at(ctypes.addressof(buf) + 4)
                if VID_PID in path.lower():
                    paths.append(path)
    finally:
        setupapi.SetupDiDestroyDeviceInfoList(group)
    return paths


def _err(prefix):
    return DeviceError('%s (Windows error %s)' % (prefix, ctypes.get_last_error()))

class HIDDevice:
    def __init__(self, path: str):
        if sys.platform != 'win32':
            raise DeviceError('يتطلب الاتصال الفعلي نظام Windows')
        self.handle = kernel32.CreateFileW(path, GEN_READ_WRITE, SHARE, None, OPEN_EXISTING,
                                           FILE_FLAG_OVERLAPPED, None)
        if self.handle == INVALID_HANDLE or not self.handle:
            raise _err('تعذر فتح منفذ USB؛ أغلق برامج مراقبة الإنفرتر الأخرى')

    def close(self):
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _io(self, operation: str, packet: bytes = b'', timeout_ms: int = 2000) -> bytes:
        event = kernel32.CreateEventW(None, True, False, None)
        if not event:
            raise _err('CreateEvent failed')
        op = OVERLAPPED()
        op.hEvent = event
        transferred = wintypes.DWORD(0)
        size = 9
        buffer = ctypes.create_string_buffer(packet if operation == 'write' else b'\x00' * size, size)
        try:
            fn = kernel32.WriteFile if operation == 'write' else kernel32.ReadFile
            ok = fn(self.handle, buffer, size, ctypes.byref(transferred), ctypes.byref(op))
            if not ok:
                err = ctypes.get_last_error()
                if err != 997:  # ERROR_IO_PENDING
                    raise DeviceError('%s HID failed (%d)' % (operation, err))
                wait = kernel32.WaitForSingleObject(event, timeout_ms)
                if wait != 0:
                    kernel32.CancelIoEx(self.handle, ctypes.byref(op))
                    kernel32.WaitForSingleObject(event, 1000)
                    raise DeviceError('انتهت مهلة %s عبر USB' % operation)
                if not kernel32.GetOverlappedResult(self.handle, ctypes.byref(op), ctypes.byref(transferred), False):
                    raise _err('GetOverlappedResult failed')
            if transferred.value != size:
                raise DeviceError('حجم تقرير USB غير متوقع: %d' % transferred.value)
            return buffer.raw
        finally:
            kernel32.CloseHandle(event)

    def send_setting_frame(self, packet: bytes) -> str:
        """Send ONLY a prevalidated 8-byte PI30 settings frame.

        The public query API stays read-only. Configuration writes are gated
        through inverter_settings.apply_setting and explicit UI confirmation.
        """
        from protocol import decode_response
        if not 4 <= len(packet) <= 48 or not packet.endswith(b'\r'):
            raise DeviceError('Invalid configuration packet')
        # HID report 0 consists of report ID + eight protocol bytes. Longer
        # setting commands are sent as consecutive reports, never truncated.
        for start in range(0, len(packet), 8):
            block = packet[start:start + 8]
            self._io('write', b'\x00' + block.ljust(8, b'\x00'), 1800)
        combined=bytearray()
        for index in range(8):
            data=self._io('read', timeout_ms=2400 if index==0 else 700)
            if data[0]!=0:
                raise DeviceError('Unexpected HID report ID')
            for b in data[1:]:
                if not combined and b==0:
                    continue
                combined.append(b)
                if b==13:
                    return decode_response(bytes(combined))
        raise DeviceError('Configuration ACK response incomplete')

    def query(self, command: str) -> str:
        raw = make_query(command)
        for start in range(0, len(raw), 8):
            block = raw[start:start + 8]
            self._io('write', b'\x00' + block.ljust(8, b'\x00'), 1800)
        combined = bytearray()
        for idx in range(72):
            try:
                data = self._io('read', timeout_ms=2400 if idx == 0 else 700)
            except DeviceError:
                if not combined:
                    raise
                break
            # Tested interface: a 9-byte report, first byte is report ID 0.
            if data[0] != 0:
                raise DeviceError('HID report identifier changed unexpectedly')
            for b in data[1:]:
                if not combined and b == 0:
                    continue
                combined.append(b)
                if b == 0x0d:
                    return decode_response(bytes(combined))
        raise ProtocolError('رد غير مكتمل من الجهاز (%d بايت)' % len(combined))


def query_one(command: str) -> str:
    paths = find_device_paths()
    if not paths:
        raise DeviceError('لم يتم العثور على إنفرتر BAISON عبر USB HID (0665:5161)')
    if len(paths) > 1:
        raise DeviceError('يوجد أكثر من جهاز متطابق؛ افصل أحدهما قبل القراءة')
    with HIDDevice(paths[0]) as device:
        return device.query(command)
