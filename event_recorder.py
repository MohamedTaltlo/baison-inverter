# -*- coding: utf-8 -*-
"""Capture *observed* inverter changes on connected PC. Never writes to inverter.

All events are keyed to QID, captured only from real successful PI30 reads.
Transitions aren't retroactive and aren't presented as device EEPROM history.
"""
from datetime import datetime

WARNINGS = {
 1: 'عطل في الإنفرتر', 2: 'ارتفاع جهد ناقل التيار', 3: 'انخفاض جهد ناقل التيار',
 4: 'فشل تشغيل الناقل', 5: 'فقد الكهرباء الخارجية', 6: 'قصر في خرج الإنفرتر',
 7: 'انخفاض جهد خرج الإنفرتر', 8: 'ارتفاع جهد خرج الإنفرتر',
 9: 'ارتفاع درجة الحرارة', 10: 'توقف المروحة', 11: 'ارتفاع جهد البطارية',
 12: 'انخفاض جهد البطارية', 14: 'البطارية منخفضة جداً',
 16: 'زيادة الحمل', 17: 'خلل EEPROM', 18: 'تيار زائد في الإنفرتر',
 19: 'فشل بدء الإنفرتر', 20: 'فشل الاختبار الذاتي',
 21: 'ارتفاع جهد DC الداخلي', 22: 'فصل اتصال البطارية',
 23: 'خلل مستشعر التيار', 24: 'قصر البطارية',
 25: 'تحديد القدرة', 26: 'ارتفاع فولت الطاقة الشمسية',
 27: 'زيادة حمل MPPT', 28: 'تحذير حمل MPPT', 29: 'البطارية منخفضة للشحن',
}

class EventRecorder:
    def __init__(self, store, snapshot_seconds=60):
        self.store = store
        self.snapshot_seconds = snapshot_seconds
        self.previous = {}  # one previous state for each actual QID
        self.last_snapshot = {}
        self.online_id = None

    def capture(self, device_id, identity, live, warnings='', at=None):
        if not device_id or not identity or not live:
            return []
        at = at or datetime.now()
        ts = at.isoformat(timespec='seconds')
        warning_bits = warnings if warnings and set(warnings) <= {'0','1'} else ''
        state = {
            'mode': identity.get('mode') or '',
            'grid': (live.get('grid_v') or 0) >= 80,
            'pv': (live.get('pv_v') or 0) >= 15,
            'warnings': warning_bits,
            'firmware': identity.get('firmware') or '',
        }
        prior = self.previous.get(device_id)
        events = []
        def add(kind, level, message, details=None):
            self.store.record_event(device_id, kind, level, message, details or {}, ts)
            events.append((kind,message))
        if prior is None:
            add('first_observation','info','بدأت مراقبة هذا الإنفرتر على الكمبيوتر', {'mode':state['mode']})
        elif self.online_id is None:
            add('usb_reconnected','info','عاد اتصال USB لمراقبة الإنفرتر')
            if warning_bits:
                for i, bit in enumerate(warning_bits):
                    if bit=='1' and i in WARNINGS:
                        add('warning_active','warning',WARNINGS[i],{'bit':i,'initial':True})
        else:
            if prior['mode']!=state['mode']:
                add('mode_change','info','تغيّر وضع التشغيل من %s إلى %s' % (prior['mode'],state['mode']))
            for key, label in [('grid','الكهرباء الخارجية'),('pv','الطاقة الشمسية')]:
                if prior[key]!=state[key]:
                    add(key+'_change','warning' if not state[key] else 'info',
                        ('انقطع مصدر ' if not state[key] else 'عاد مصدر ')+label)
            if prior['firmware'] and prior['firmware']!=state['firmware']:
                add('firmware_change','warning','تغيّر إصدار Firmware المسجل أثناء المراقبة',
                    {'before':prior['firmware'],'after':state['firmware']})
            # Only compare fault states when both reads are valid and equal length.
            old = prior['warnings']; new = warning_bits
            if old and new and len(old)==len(new):
                for i,(x,y) in enumerate(zip(old,new)):
                    if x!=y and i in WARNINGS:
                        add('warning_active' if y=='1' else 'warning_cleared',
                            'warning' if y=='1' else 'info',
                            ('ظهر تحذير: ' if y=='1' else 'زال تحذير: ')+WARNINGS[i], {'bit':i})
        self.previous[device_id]=state
        last=self.last_snapshot.get(device_id)
        if last is None or (at-last).total_seconds() >= self.snapshot_seconds:
            self.store.record_snapshot(device_id, live, ts)
            self.last_snapshot[device_id]=at
        self.online_id=device_id
        return events

    def disconnected(self, reason='', at=None):
        if self.online_id:
            self.store.record_event(self.online_id,'usb_disconnect','warning',
                                    'انقطع اتصال مراقبة USB',{'reason':str(reason)[:300]},
                                    (at or datetime.now()).isoformat(timespec='seconds'))
            self.online_id=None
