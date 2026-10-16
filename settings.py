"""Validated PI30 setters for the observed BAISON VMIII-4200 sample.

Source: Axpert Remote Panel Protocol (VMIII & KING & MKSIII), 2022-06-16.
Device accepts individual commands only if firmware supports them. ACK is not
sufficient: caller MUST perform read-back of QPIRI/QFLAG.
"""
from __future__ import annotations
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from protocol import escaped_crc

IDENTITY = ('96342305101831', 'VMIII-4200', '00085.04')

@dataclass(frozen=True)
class Spec:
    key: str
    title: str
    group: str
    details: str
    prefix: str
    index: int | None = None
    options: tuple = ()  # (wire value, human description)
    kind: str = 'choice'
    minimum: str = ''
    maximum: str = ''
    risk: str = 'medium'
    readback: str = 'QPIRI'

SPECS = [
 Spec('output_priority','أولوية خرج الكهرباء','الطاقة','يحدد المصدر الذي يغذي الأحمال؛ قد يسبب التحويل أثناء التشغيل.','POP',16,(('0','USB — الكهرباء أولاً'),('1','SOL / SUB — الشمس أولاً'),('2','SBU — الشمس ثم البطارية ثم الكهرباء'))),
 Spec('charger_priority','أولوية شحن البطارية','الطاقة','يحدد المصدر المستخدم للشحن؛ قد يوقف الشحن من الكهرباء.','PCP',17,(('1','CSO — الشمس أولاً'),('2','SNU — الشمس والكهرباء معاً'),('3','OSO — الشمس فقط'))),
 Spec('input_range','نطاق قبول كهرباء الشبكة','الطاقة','UPS قبول أضيق؛ Appliance قبول أوسع.','PGR',15,(('0','APL — نطاق واسع'),('1','UPS — نطاق ضيق'))),
 Spec('battery_type','نوع البطارية','البطارية','اختيار خاطئ قد يضر البطارية أو يعطّل بروتوكول BMS.','PBT',12,(('0','AGM'),('1','Flooded'),('2','User'),('3','Pylontech'),('4','Shinheung'),('5','WECO'),('6','Soltaro'),('7','BAK'),('8','LIB'),('9','LIC')),risk='critical'),
 Spec('back_to_grid','الرجوع إلى كهرباء الشبكة','البطارية','عتبة رجوع الحمل إلى الكهرباء عندما تنخفض البطارية.','PBCV',8,kind='voltage',minimum='22.0',maximum='28.0',risk='high'),
 Spec('back_to_battery','الرجوع إلى البطارية','البطارية','عتبة الرجوع لاستخدام البطارية بعد الشحن. بعض النسخ تقبل 00.0 للدلالة على الامتلاء، غير مفعّلة هنا.','PBDV',22,kind='voltage',minimum='24.0',maximum='30.0',risk='high'),
 Spec('battery_cutoff','فولت فصل البطارية المنخفض','البطارية','حد حماية أساسي؛ راجع نوع البطارية وحدود BMS قبل تغييره.','PSDV',9,kind='voltage',minimum='21.0',maximum='25.0',risk='critical'),
 Spec('bulk_voltage','فولت الشحن الرئيسي','البطارية','جهد CV/Bulk ويجب أن يطابق مواصفات البطارية.','PCVV',10,kind='voltage',minimum='25.0',maximum='31.5',risk='critical'),
 Spec('float_voltage','فولت الشحن العائم','البطارية','جهد Float ويجب أن يطابق مواصفات البطارية.','PBFT',11,kind='voltage',minimum='25.0',maximum='31.5',risk='critical'),
 Spec('max_charge','أقصى تيار شحن إجمالي','الشحن','يجب عدم تجاوز قدرة الشاحن أو البطارية أو الأسلاك. تظهر الخيارات الفعلية من QMCHGCR.','MNCHGC',14,kind='current',minimum='10',maximum='120',risk='critical'),
 Spec('max_utility_charge','أقصى تيار شحن من الشبكة','الشحن','يحدد الشحن من مصدر الكهرباء فقط. الخيارات الفعلية من QMUCHGCR.','MUCHGC',13,kind='current',minimum='2',maximum='120',risk='high'),
 Spec('output_frequency','تردد خرج الكهرباء','الخرج','يؤثر في الأجهزة الموصولة. افصل الأحمال قبل التغيير.','F',3,(('50','50 Hz'),('60','60 Hz')),risk='high'),
 Spec('output_voltage','فولت خرج الكهرباء','الخرج','تغيير جهد الخرج قد يضر الأحمال؛ للإنفرتر VMIII فقط.','V',2,(('220','220 V'),('230','230 V'),('240','240 V')),risk='critical'),
 Spec('pv_balance','موازنة الطاقة الشمسية','الألواح','0 يحد دخل الألواح بالتيار المخصص للشحن، 1 يسمح بقدرة الشحن والأحمال.','PSPB',24,(('0','حدّ طاقة الألواح وفق الشحن'),('1','قدرة الألواح للشحن والأحمال')),risk='high'),
 Spec('pv_ok','شرط توفر الألواح عند التوازي','الألواح','مهم للأنظمة المتوازية؛ لا تغيره في شبكة بدون مراجعة رسم التوصيل.','PPVOKC',23,(('0','يكفي جهاز واحد مع ألواح'),('1','كل الأجهزة لديها ألواح')),risk='high'),
]

FLAGS = {
 'a': ('كتم صوت الصفارة','يغيّر سلوك التنبيه الصوتي دون إزالة سبب التحذير','low'),
 'b': ('تجاوز الحمل الزائد','يسمح بتحويل الحمل وفق إعدادات التجاوز','high'),
 'k': ('عودة الشاشة الرئيسية','يعود تلقائياً إلى الصفحة الرئيسية بعد دقيقة','low'),
 'u': ('إعادة التشغيل بعد زيادة الحمل','إعادة محاولة العمل بعد زوال الحمل الزائد','high'),
 'v': ('إعادة التشغيل بعد الحرارة','إعادة محاولة العمل بعد زوال ارتفاع الحرارة','high'),
 'x': ('إضاءة شاشة التحكم','تشغيل أو إيقاف إضاءة الشاشة','low'),
 'y': ('إنذار فقد المصدر الأساسي','تفعيل صوت تنبيه فقدان المصدر الأول','low'),
 'z': ('تسجيل رمز العطل','علم تشغيل تسجيل أكواد الأعطال؛ ليس مساحة لإضافة سجلات صيانة','medium'),
}

def active_flags(raw):
    raw=(raw or '').strip()
    if not raw.startswith('E') or 'D' not in raw:return {}
    enabled,disabled=raw[1:].split('D',1)
    return {key: True if key in enabled else False if key in disabled else None for key in FLAGS}

def values_qpiri(raw):
    parts=(raw or '').split()
    if len(parts)!=25:
        raise ValueError('QPIRI layout changed: expected 25 fields, received %s'%len(parts))
    return parts

def normalize(spec, value, capabilities=None):
    v=str(value).strip()
    if spec.kind=='choice':
        if v not in dict(spec.options):raise ValueError('قيمة غير مسموحة لهذا الإعداد')
        if spec.key=='output_voltage':return v
        if spec.key=='output_frequency':return v
        return v.zfill(2)
    try:decimal=Decimal(v)
    except InvalidOperation: raise ValueError('الرجاء إدخال قيمة رقمية صحيحة')
    if not decimal.is_finite() or decimal<Decimal(spec.minimum) or decimal>Decimal(spec.maximum):
        raise ValueError('القيمة خارج مجال الاختبار المعتمد')
    if spec.kind=='voltage':
        if decimal*10!=(decimal*10).to_integral_value():raise ValueError('خطوة الفولت 0.1 فقط')
        return f'{decimal:.1f}'.zfill(4)
    if decimal!=decimal.to_integral_value():raise ValueError('تيار الشحن عدد صحيح')
    raw=str(int(decimal))
    capkey='QMCHGCR' if spec.key=='max_charge' else 'QMUCHGCR'
    choices=(capabilities or {}).get(capkey,'').split()
    if not choices:raise ValueError('تعذرت قراءة قيم التيار المدعومة؛ تم منع التعديل')
    if str(int(decimal)) not in {str(int(c)) for c in choices if c.isdigit()}:
        raise ValueError('قيمة التيار غير موجودة ضمن الخيارات التي أرسلها الجهاز')
    return raw.zfill(3)

def make_command(spec, value, capabilities=None):
    return spec.prefix + normalize(spec,value,capabilities)

def flag_command(flag, active):
    if flag not in FLAGS or type(active) is not bool:raise ValueError('علم تشغيل غير معتمد')
    return ('PE' if active else 'PD')+flag

def packet_for_known_command(command):
    """Never accepts unrestricted console command strings."""
    if not command.isascii() or len(command)>36 or not command.isalnum():
        raise ValueError('Invalid command shape')
    valid_flag=(command[:2] in ('PE','PD') and len(command)==3 and command[2] in FLAGS)
    valid_setting=False
    for spec in SPECS:
        if not command.startswith(spec.prefix):continue
        suffix=command[len(spec.prefix):]
        try:
            if spec.kind=='current':
                valid_setting=bool(len(suffix)==3 and suffix.isdigit() and int(suffix)>=int(spec.minimum) and int(suffix)<=int(spec.maximum))
            else:
                valid_setting=(normalize(spec,suffix.lstrip('0') or '0')==suffix) if spec.kind=='choice' and spec.key not in ('output_voltage','output_frequency') else (normalize(spec,suffix)==suffix)
        except ValueError:pass
        if valid_setting:break
    if not (valid_flag or valid_setting):raise ValueError('Unknown or invalid write command')
    raw=command.encode('ascii')
    return raw+escaped_crc(raw)+b'\r'

def validate_identity(raw):
    if (raw.get('QID',''),raw.get('QMN',''),raw.get('QVFW','').replace('VERFW:',''))!=IDENTITY or raw.get('QPI')!='PI30':
        raise ValueError('الموديل أو Firmware أو الهوية لا تطابق جهاز الاختبار')
    current=values_qpiri(raw.get('QPIRI',''))
    if current[5]!='4200' or current[7]!='24.0':
        raise ValueError('خصائص الجهاز غير متطابقة')
    return True

BY_KEY={item.key:item for item in SPECS}
