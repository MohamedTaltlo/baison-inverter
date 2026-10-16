# -*- coding: utf-8 -*-
"""Arabic, human-readable labels for PI30 configuration values.

Presentation-only. Never converts an incoming read into a write command.
Unknown wire codes are shown explicitly, never guessed.
"""
from __future__ import annotations

OPTION_LABELS = {
    'output_priority': {
        '0': 'USB — الكهرباء أولاً',
        '1': 'SOL / SUB — الطاقة الشمسية أولاً',
        '2': 'SBU — الشمس ثم البطارية ثم الكهرباء',
    },
    'charger_priority': {
        '1': 'CSO — الطاقة الشمسية أولاً',
        '2': 'SNU — الشمس والكهرباء معاً',
        '3': 'OSO — الطاقة الشمسية فقط',
    },
    'input_range': {
        '0': 'APL — نطاق كهرباء واسع',
        '1': 'UPS — نطاق كهرباء ضيق',
    },
    'battery_type': {
        '0': 'AGM — بطارية مغلقة',
        '1': 'FLD — بطارية سائلة',
        '2': 'USE — ضبط البطارية يدوياً',
        '3': 'PYL — بطارية Pylontech',
        '4': 'Shinheung — ليثيوم',
        '5': 'WECO — ليثيوم',
        '6': 'Soltaro — ليثيوم',
        '7': 'BAK — ليثيوم',
        '8': 'LIB — بطارية ليثيوم',
        '9': 'LIC — بطارية ليثيوم',
    },
    'pv_balance': {
        '0': 'تعطيل موازنة طاقة الألواح',
        '1': 'تفعيل موازنة طاقة الألواح',
    },
    'pv_ok': {
        '0': 'تكفي ألواح على جهاز واحد',
        '1': 'الألواح مطلوبة بكل الأجهزة',
    },
}


def friendly_setting_value(spec, raw):
    """Translate documented codes for current/factory fields; preserve unknowns."""
    value = str(raw).strip() if raw is not None else ''
    if not value or value in ('—', '-', 'غير معروف'):
        return '—'
    if isinstance(spec, tuple):
        return value  # QFLAG rows are already Arabic; diagnostics are read-only.
    key = getattr(spec, 'key', '')
    if key in ('output_voltage', 'output_frequency'):
        try:
            number = float(value)
            if key == 'output_voltage' and not 100 <= number <= 300:
                return 'قيمة خرج غير متوقعة: ' + value + ' (تحقق من البروتوكول)'
            return ('%g' % number) + (' V' if key == 'output_voltage' else ' Hz')
        except ValueError:
            return 'قيمة غير مفهومة: ' + value
    if getattr(spec, 'kind', '') == 'choice':
        options = OPTION_LABELS.get(key, {})
        code = str(int(value)) if value.isascii() and value.isdecimal() else value
        if code in options:
            return options[code]
        return 'رمز غير معروف: ' + value
    if key in ('max_charge', 'max_utility_charge'):
        try:
            return str(int(value)) + ' A'
        except ValueError:
            return 'قيمة غير مفهومة: ' + value
    if key in ('output_voltage', 'output_frequency'):
        # The spec uses choice, so this is a future-proof fallback only.
        return value + (' V' if key == 'output_voltage' else ' Hz')
    if getattr(spec, 'kind', '') == 'voltage':
        return value + ' V'
    return value
