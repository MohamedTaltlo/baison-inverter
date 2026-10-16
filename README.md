# BAISON Service Manager 4.0.0

Windows Qt application for BAISON/Voltronic PI30 USB HID inverters. Developed by MT_RADIUS.

## Features
- Monitor BAISON USB-HID inverter telemetry and documented settings, with read-back verification for allowed writes.
- Link customer and repair records to the validated electronic QID.
- Required repair form fields are validated live; the Save button stays disabled until complete. Database errors **never close the edit dialog**.
- Cleaned-up GUI; bold dashboard values, native Arabic RTL. Developer WhatsApp contact is visible only when hovering over MT_RADIUS.
- Per-device event cleanup with configurable retention; **maintenance and audit are never automatically deleted**.
- **RTC controls removed**: the device rejected DAT and remote setting was not validated; the application does not send QT/DAT.
- No custom EEPROM record-writing support.

## Windows download
See the [4.0.0 GitHub Release](https://github.com/MohamedTaltlo/baison-inverter/releases/tag/v4.0.0).

Source-only environment: install Python 3.12 and dependencies from requirements-build.txt; build using the Actions workflow or locally with PyInstaller. The released EXE includes the runtime; no Python installation is needed on service-center PCs.

> Testing must be performed with device power/load safety measures. A successful offline build does not guarantee firmware write acceptance. The executable is not Authenticode signed.
