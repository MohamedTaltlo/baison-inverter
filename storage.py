# -*- coding: utf-8 -*-
"""Local development-stage storage. This does NOT write onto the inverter."""
import os
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timedelta


def base_folder():
    if os.name == 'nt':
        root = Path(os.environ.get('LOCALAPPDATA', str(Path.home())))
        return root / 'BAISON' / 'ServiceManager'
    return Path.home() / '.local' / 'share' / 'BAISON' / 'ServiceManager'

class Store:
    LEGACY_INVALID = ('0000000000000000', '000000000000000', '00000000000000000')

    @staticmethod
    def valid_identity(identity):
        return isinstance(identity, str) and identity.isdigit() and 10 <= len(identity) <= 32 and any(c != '0' for c in identity)

    def __init__(self, filepath=None):
        self.path = Path(filepath) if filepath else base_folder() / 'baison_service.sqlite3'
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(self.path))
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA busy_timeout=3000')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS devices (
          sticker_serial TEXT PRIMARY KEY,
          internal_id TEXT NOT NULL DEFAULT '',
          model TEXT NOT NULL DEFAULT '',
          firmware TEXT NOT NULL DEFAULT '',
          customer_name TEXT NOT NULL DEFAULT '',
          registered_at TEXT NOT NULL,
          updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS services (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          sticker_serial TEXT NOT NULL REFERENCES devices(sticker_serial),
          serviced_at TEXT NOT NULL,
          customer_name TEXT NOT NULL,
          fault_type TEXT NOT NULL,
          team TEXT NOT NULL,
          phone TEXT NOT NULL DEFAULT '',
          notes TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          deleted_at TEXT,
          FOREIGN KEY (sticker_serial) REFERENCES devices(sticker_serial)
        );
        CREATE INDEX IF NOT EXISTS idx_service_device ON services(sticker_serial, deleted_at, serviced_at);
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          service_id INTEGER, sticker_serial TEXT NOT NULL,
          activity TEXT NOT NULL, created_at TEXT NOT NULL,
          before_json TEXT NOT NULL DEFAULT '', after_json TEXT NOT NULL DEFAULT ''
        );
        ''')
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS device_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          internal_id TEXT NOT NULL, recorded_at TEXT NOT NULL,
          kind TEXT NOT NULL, severity TEXT NOT NULL,
          message TEXT NOT NULL, details_json TEXT NOT NULL DEFAULT '{}',
          source TEXT NOT NULL DEFAULT 'USB_LIVE_OBSERVATION'
        );
        CREATE INDEX IF NOT EXISTS idx_events_device ON device_events(internal_id,recorded_at DESC);
        CREATE TABLE IF NOT EXISTS device_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT, internal_id TEXT NOT NULL,
          recorded_at TEXT NOT NULL, values_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_snap_device ON device_snapshots(internal_id,recorded_at DESC);
        """)
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS app_preferences (key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_retention_events ON device_events(recorded_at);
        CREATE INDEX IF NOT EXISTS idx_retention_snaps ON device_snapshots(recorded_at);
        """)
        columns={r['name'] for r in self.db.execute('PRAGMA table_info(devices)')}
        if 'physical_serial' not in columns:
            self.db.execute("ALTER TABLE devices ADD COLUMN physical_serial TEXT NOT NULL DEFAULT ''")
        if 'archived_at' not in columns:
            self.db.execute('ALTER TABLE devices ADD COLUMN archived_at TEXT')
        self.db.commit()

    def close(self):
        self.db.close()

    def register(self, serial, internal='', model='', firmware='', customer=''):
        serial = serial.strip()
        if not serial or len(serial) > 80:
            raise ValueError('الرقم الموجود على الملصق مطلوب (حتى 80 حرفاً)')
        now = datetime.now().isoformat(timespec='seconds')
        with self.db:
            self.db.execute('''INSERT INTO devices
              (sticker_serial,internal_id,model,firmware,customer_name,registered_at,updated_at)
              VALUES (?,?,?,?,?,?,?) ON CONFLICT(sticker_serial) DO UPDATE SET
              internal_id=excluded.internal_id, model=excluded.model,
              firmware=excluded.firmware, customer_name=excluded.customer_name,
              updated_at=excluded.updated_at''', (serial, internal, model, firmware, customer, now, now))
        return serial

    def get_device(self, serial):
        obj = self.db.execute('SELECT * FROM devices WHERE sticker_serial=?', (serial,)).fetchone()
        return dict(obj) if obj else None

    def list_devices(self):
        return [dict(x) for x in self.db.execute('SELECT * FROM devices WHERE archived_at IS NULL ORDER BY updated_at DESC')]

    def list_services(self, serial=None, keyword=''):
        sql = 'SELECT * FROM services WHERE deleted_at IS NULL'
        args = []
        if serial:
            sql += ' AND sticker_serial=?'
            args.append(serial)
        if keyword:
            sql += ''' AND (customer_name LIKE ? OR fault_type LIKE ? OR team LIKE ? OR
              phone LIKE ? OR notes LIKE ? OR sticker_serial LIKE ?)'''
            args += ['%' + keyword + '%'] * 6
        sql += ' ORDER BY serviced_at DESC, id DESC'
        return [dict(x) for x in self.db.execute(sql, args)]

    def get_service(self, sid):
        obj = self.db.execute('SELECT * FROM services WHERE id=? AND deleted_at IS NULL', (sid,)).fetchone()
        return dict(obj) if obj else None

    def _validate_service(self, value):
        for name in ('sticker_serial', 'serviced_at', 'customer_name', 'fault_type', 'team'):
            if not str(value.get(name, '')).strip():
                raise ValueError('الحقل «%s» مطلوب' % {'sticker_serial':'هوية الجهاز','serviced_at':'تاريخ ووقت الصيانة','customer_name':'اسم الزبون','fault_type':'نوع العطل','team':'الفريق المسؤول'}.get(name,name))
        try:
            datetime.strptime(value['serviced_at'], '%Y-%m-%d %H:%M')
        except ValueError as exc:
            raise ValueError('صيغة التاريخ المطلوبة YYYY-MM-DD HH:MM') from exc
        if not self.valid_identity(value['sticker_serial']):
            raise ValueError('لا يُسمح بربط الصيانة برقم أصفار أو هوية وهمية')
        if not self.get_device(value['sticker_serial']):
            raise ValueError('تعذّر إيجاد بطاقة الجهاز المرتبطة بهوية USB. اضغط «قراءة جميع المعلومات» ثم حاول مجدداً.')

    def save_service(self, data, sid=None):
        self._validate_service(data)
        now = datetime.now().isoformat(timespec='seconds')
        cols = ['sticker_serial', 'serviced_at', 'customer_name', 'fault_type', 'team', 'phone', 'notes']
        values = [str(data.get(x, '')).strip() for x in cols]
        import json
        with self.db:
            if sid is None:
                cur = self.db.execute('''INSERT INTO services
                    (sticker_serial,serviced_at,customer_name,fault_type,team,phone,notes,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?)''', values + [now, now])
                sid = cur.lastrowid
                activity = 'CREATE'
                prior = ''
            else:
                before = self.get_service(sid)
                if not before:
                    raise ValueError('سجل الصيانة غير موجود')
                prior = json.dumps(before, ensure_ascii=False)
                self.db.execute('''UPDATE services SET sticker_serial=?,serviced_at=?,customer_name=?,
                    fault_type=?,team=?,phone=?,notes=?,updated_at=? WHERE id=? AND deleted_at IS NULL''', values + [now, sid])
                activity = 'UPDATE'
            self.db.execute('''INSERT INTO audit (service_id, sticker_serial, activity, created_at,before_json,after_json)
                VALUES (?,?,?,?,?,?)''', (sid, values[0], activity, now, prior,
                 json.dumps(dict(zip(cols, values)), ensure_ascii=False)))
        return sid

    def delete_service(self, sid):
        before = self.get_service(sid)
        if not before:
            raise ValueError('سجل الصيانة غير موجود')
        import json
        now = datetime.now().isoformat(timespec='seconds')
        with self.db:
            self.db.execute('UPDATE services SET deleted_at=?,updated_at=? WHERE id=?', (now, now, sid))
            self.db.execute('''INSERT INTO audit (service_id,sticker_serial,activity,created_at,before_json)
                VALUES (?,?,?,?,?)''', (sid, before['sticker_serial'], 'DELETE', now,
                                     json.dumps(before, ensure_ascii=False)))

    def stats(self, serial=None):
        data = self.list_services(serial)
        return {'count': len(data), 'last_service': data[0]['serviced_at'] if data else '—'}

    def by_internal_id(self, internal_id):
        row=self.db.execute('SELECT * FROM devices WHERE internal_id=? ORDER BY updated_at DESC LIMIT 1', (internal_id,)).fetchone()
        return dict(row) if row else None

    def ensure_device(self, internal_id, model='', firmware='', reactivate=False):
        if not self.valid_identity(internal_id):
            raise ValueError('هوية الجهاز الإلكترونية غير صالحة')
        found=self.by_internal_id(internal_id)
        if found and found['sticker_serial'] != internal_id:
            # Move a legacy physically-keyed row to real QID while preserving
            # repairs, audit trail, and customer details. Do not erase history.
            target=self.get_device(internal_id)
            if target and target['internal_id'] != internal_id:
                raise ValueError('تعارض بين هوية الجهاز والبطاقة القديمة')
            with self.db:
                if not target:
                    self.db.execute('''INSERT INTO devices(sticker_serial,internal_id,model,firmware,customer_name,registered_at,updated_at,physical_serial,archived_at)
                    SELECT ?,internal_id,model,firmware,customer_name,registered_at,updated_at,
                    CASE WHEN physical_serial<>'' THEN physical_serial ELSE sticker_serial END,archived_at
                    FROM devices WHERE sticker_serial=?''',(internal_id,found['sticker_serial']))
                self.db.execute('UPDATE services SET sticker_serial=? WHERE sticker_serial=?',(internal_id,found['sticker_serial']))
                self.db.execute('UPDATE audit SET sticker_serial=? WHERE sticker_serial=?',(internal_id,found['sticker_serial']))
                self.db.execute('DELETE FROM devices WHERE sticker_serial=?',(found['sticker_serial'],))
                self.db.execute('INSERT INTO audit(service_id,sticker_serial,activity,created_at,before_json,after_json) VALUES(NULL,?,?,?,?,?)',
                    (internal_id,'NORMALIZE_QID_KEY',datetime.now().isoformat(timespec='seconds'),json.dumps(found,ensure_ascii=False),json.dumps({'qid':internal_id},ensure_ascii=False)))
            found=self.by_internal_id(internal_id)
        if found:
            if found.get('archived_at') and reactivate:
                with self.db:
                    self.db.execute('UPDATE devices SET archived_at=NULL, updated_at=? WHERE sticker_serial=?',
                        (datetime.now().isoformat(timespec='seconds'),found['sticker_serial']))
            return found['sticker_serial']
        existing=self.get_device(internal_id)
        if existing and existing['internal_id']!=internal_id:
            raise ValueError('تعارض هوية مع بطاقة موجودة')
        return self.register(internal_id,internal_id,model,firmware,'')

    def record_event(self, internal_id, kind, severity, message, details=None, timestamp=None):
        if not internal_id:return
        ts=timestamp or datetime.now().isoformat(timespec='seconds')
        with self.db:
            self.db.execute("""INSERT INTO device_events
              (internal_id,recorded_at,kind,severity,message,details_json) VALUES (?,?,?,?,?,?)""",
              (internal_id,ts,kind,severity,message,json.dumps(details or {},ensure_ascii=False)))

    def record_snapshot(self, internal_id, values, timestamp=None):
        if not internal_id:return
        ts=timestamp or datetime.now().isoformat(timespec='seconds')
        with self.db:
            self.db.execute('INSERT INTO device_snapshots(internal_id,recorded_at,values_json) VALUES(?,?,?)',
              (internal_id,ts,json.dumps(values,ensure_ascii=False)))

    def list_events(self, internal_id=None, limit=1000):
        if internal_id:
            rows=self.db.execute('SELECT * FROM device_events WHERE internal_id=? ORDER BY id DESC LIMIT ?',
                 (internal_id,limit))
        else:
            rows=self.db.execute('SELECT * FROM device_events ORDER BY id DESC LIMIT ?', (limit,))
        return [dict(x) for x in rows]

    def snapshot_count(self, internal_id=None):
        if internal_id:
            return self.db.execute('SELECT COUNT(*) FROM device_snapshots WHERE internal_id=?',(internal_id,)).fetchone()[0]
        return self.db.execute('SELECT COUNT(*) FROM device_snapshots').fetchone()[0]

    def retention_settings(self):
        values={'events_days':14,'snapshots_days':3}
        for row in self.db.execute("SELECT key,value FROM app_preferences WHERE key IN ('events_days','snapshots_days')"):
            try:
                days=int(row['value'])
                if 1<=days<=365:values[row['key']]=days
            except (TypeError,ValueError):pass
        return values

    def set_retention(self,events_days,snapshots_days):
        policy={'events_days':int(events_days),'snapshots_days':int(snapshots_days)}
        if any(x<1 or x>365 for x in policy.values()):
            raise ValueError('مدة الاحتفاظ 1 إلى 365 يوماً')
        with self.db:
            for key,value in policy.items():
                self.db.execute('INSERT INTO app_preferences(key,value) VALUES(?,?) '
                    'ON CONFLICT(key) DO UPDATE SET value=excluded.value',(key,str(value)))

    def observation_counts(self,qid=None):
        if qid is not None and not self.valid_identity(qid):raise ValueError('هوية غير صحيحة')
        where=' WHERE internal_id=?' if qid is not None else ''
        params=(qid,) if qid is not None else ()
        return {'events':self.db.execute('SELECT COUNT(*) FROM device_events'+where,params).fetchone()[0],
                'snapshots':self.db.execute('SELECT COUNT(*) FROM device_snapshots'+where,params).fetchone()[0]}

    def clear_observations(self,qid=None):
        counts=self.observation_counts(qid)
        with self.db:
            if qid is None:
                self.db.execute('DELETE FROM device_events')
                self.db.execute('DELETE FROM device_snapshots')
            else:
                self.db.execute('DELETE FROM device_events WHERE internal_id=?',(qid,))
                self.db.execute('DELETE FROM device_snapshots WHERE internal_id=?',(qid,))
        return counts

    def prune_observations(self,now=None):
        now=now or datetime.now()
        policy=self.retention_settings()
        c1=(now-timedelta(days=policy['events_days'])).isoformat(timespec='seconds')
        c2=(now-timedelta(days=policy['snapshots_days'])).isoformat(timespec='seconds')
        with self.db:
            n1=self.db.execute('DELETE FROM device_events WHERE recorded_at<?',(c1,)).rowcount
            n2=self.db.execute('DELETE FROM device_snapshots WHERE recorded_at<?',(c2,)).rowcount
            # Caps for very busy service centers, preserving latest entries.
            n1+=self.db.execute('DELETE FROM device_events WHERE id IN '
                '(SELECT id FROM device_events ORDER BY id DESC LIMIT -1 OFFSET 50000)').rowcount
            n2+=self.db.execute('DELETE FROM device_snapshots WHERE id IN '
                '(SELECT id FROM device_snapshots ORDER BY id DESC LIMIT -1 OFFSET 100000)').rowcount
        return {'events':n1,'snapshots':n2}

    def update_device_card(self, internal_id, physical_serial='', customer_name=''):
        if not self.valid_identity(internal_id):raise ValueError('يجب قراءة QID الحقيقي من الجهاز')
        row=self.by_internal_id(internal_id)
        if not row:raise ValueError('هوية الجهاز غير مسجلة')
        physical_serial=physical_serial.strip()
        if len(physical_serial)>80:raise ValueError('رقم الملصق طويل جداً')
        conflict=self.db.execute('SELECT internal_id FROM devices WHERE physical_serial=? AND internal_id<>?',
                                  (physical_serial,internal_id)).fetchone() if physical_serial else None
        if conflict:raise ValueError('رقم الملصق مرتبط بهوية إلكترونية مختلفة')
        with self.db:
            self.db.execute('UPDATE devices SET physical_serial=?,customer_name=?,updated_at=? WHERE sticker_serial=?',
                (physical_serial,customer_name.strip(),datetime.now().isoformat(timespec='seconds'),row['sticker_serial']))
        return row['sticker_serial']

    def archive_device_card(self, internal_id):
        """Hide a customer/device card without destroying repair/event history."""
        row=self.by_internal_id(internal_id)
        if not row:
            raise ValueError('لم يتم العثور على الجهاز')
        when=datetime.now().isoformat(timespec='seconds')
        with self.db:
            self.db.execute('UPDATE devices SET archived_at=?,updated_at=? WHERE sticker_serial=?',
                            (when,when,row['sticker_serial']))
            self.db.execute("INSERT INTO audit(service_id,sticker_serial,activity,created_at,before_json,after_json) VALUES(NULL,?,?,?,?,?)", (row['sticker_serial'],'ARCHIVE_CARD',when,json.dumps(row,ensure_ascii=False),'{}'))
        return row['sticker_serial']

    def remove_customer(self, internal_id):
        """Detach current customer data; preserve old repairs as historical records."""
        row=self.by_internal_id(internal_id)
        if not row:
            raise ValueError('لم يتم العثور على الجهاز')
        when=datetime.now().isoformat(timespec='seconds')
        with self.db:
            self.db.execute('UPDATE devices SET customer_name=?,updated_at=? WHERE sticker_serial=?',
                           ('',when,row['sticker_serial']))
            self.db.execute("INSERT INTO audit(service_id,sticker_serial,activity,created_at,before_json,after_json) VALUES(NULL,?,?,?,?,?)", (row['sticker_serial'],'UNLINK_CUSTOMER',when,json.dumps(row,ensure_ascii=False),'{}'))
        return row['sticker_serial']

    def legacy_placeholder_has_data(self):
        placeholders=','.join('?' for _ in self.LEGACY_INVALID)
        return self.db.execute('SELECT 1 FROM devices WHERE sticker_serial IN ('+placeholders+') LIMIT 1',self.LEGACY_INVALID).fetchone() is not None

    def relink_legacy_placeholder(self, real_id):
        """Explicit, audited migration; preserves existing repair and audit data."""
        if not self.valid_identity(real_id):
            raise ValueError('هوية QID غير صالحة')
        placeholders=','.join('?' for _ in self.LEGACY_INVALID)
        now=datetime.now().isoformat(timespec='seconds')
        with self.db:
            target=self.db.execute('SELECT * FROM devices WHERE sticker_serial=? AND internal_id=?',(real_id,real_id)).fetchone()
            if not target:
                raise ValueError('اقرأ الجهاز أولاً قبل التصحيح')
            placeholders_rows=self.db.execute('SELECT * FROM devices WHERE sticker_serial IN ('+placeholders+')',self.LEGACY_INVALID).fetchall()
            for old in placeholders_rows:
                alias=old['sticker_serial']
                if not target['customer_name'] and old['customer_name']:
                    self.db.execute('UPDATE devices SET customer_name=?,updated_at=? WHERE sticker_serial=?',(old['customer_name'],now,real_id))
                if not target['physical_serial'] and old['physical_serial']:
                    self.db.execute('UPDATE devices SET physical_serial=? WHERE sticker_serial=?',(old['physical_serial'],real_id))
                self.db.execute('UPDATE services SET sticker_serial=? WHERE sticker_serial=?',(real_id,alias))
                self.db.execute('UPDATE audit SET sticker_serial=? WHERE sticker_serial=?',(real_id,alias))
                self.db.execute('INSERT INTO audit (service_id,sticker_serial,activity,created_at,before_json,after_json) VALUES (NULL,?,?,?,?,?)',
                    (real_id,'RELINK_LEGACY_ZERO_ID',now,json.dumps({'legacy_id':alias,'original':dict(old)},ensure_ascii=False),json.dumps({'real_qid':real_id},ensure_ascii=False)))
                self.db.execute('DELETE FROM devices WHERE sticker_serial=?',(alias,))
        return len(placeholders_rows)
