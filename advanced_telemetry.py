# -*- coding: utf-8 -*-
"""Detailed telemetry for the observed 24-field BAISON VMIII PI30 QPIGS.
Read-only decoding: no EEPROM access or write commands.
"""
from protocol import parse_live, ProtocolError

# index, Arabic label, unit, group, confidence
FIELD_MAP = (
    (0,'جهد كهرباء الشبكة','V','الشبكة','موثق'),
    (1,'تردد كهرباء الشبكة','Hz','الشبكة','موثق'),
    (2,'جهد خرج الإنفرتر','V','الخرج','موثق'),
    (3,'تردد خرج الإنفرتر','Hz','الخرج','موثق'),
    (4,'القدرة الظاهرية للحمل','VA','الخرج','موثق'),
    (5,'القدرة الفعلية للحمل','W','الخرج','موثق'),
    (6,'نسبة الحمل','%','الخرج','موثق'),
    (7,'جهد ناقل الطاقة الداخلي','V','التشخيص','موثق'),
    (8,'فولت البطارية','V','البطارية','موثق'),
    (9,'تيار شحن البطارية','A','البطارية','موثق'),
    (10,'نسبة سعة البطارية','%','البطارية','موثق'),
    (11,'حرارة مشتت الإنفرتر','°C','التشخيص','موثق'),
    (12,'تيار الدخل الشمسي','A','الألواح','موثق'),
    (13,'جهد الدخل الشمسي','V','الألواح','موثق'),
    (14,'جهد البطارية من متحكم الشحن','V','البطارية','موثق'),
    (15,'تيار تفريغ البطارية','A','البطارية','موثق'),
    (16,'أعلام حالة التشغيل','bits','التشخيص','تفسير جزئي'),
    (17,'فرق تشغيل المراوح','10mV','التشخيص','موثق'),
    (18,'رقم إصدار EEPROM','','التشخيص','موثق'),
    (19,'قدرة الشحن من الألواح','W','الألواح','موثق'),
    (20,'أعلام تشغيل إضافية','bits','التشخيص','تفسير جزئي'),
    (21,'حالة إعادة الطاقة للشبكة','','التشخيص','حقل محجوز'),
    (22,'رمز التنظيم الإقليمي','','التشخيص','حقل محجوز'),
    (23,'قدرة التصدير إلى الشبكة','W','التشخيص','حقل محجوز'),
)

BIT_HELP = {
    4:'وجود حمل فعّال',
    3:'ثبات جهد البطارية أثناء الشحن',
    2:'الشحن فعّال',
    1:'شحن من متحكم الألواح',
    0:'شحن من كهرباء الشبكة',
}

def decode_status_flags(bits):
    if not isinstance(bits,str) or len(bits)!=8 or any(c not in '01' for c in bits):
        return []
    return [{'bit':i,'title':BIT_HELP[i], 'active':bits[7-i]=='1'} for i in sorted(BIT_HELP,reverse=True)]

def extended_rows(payload):
    live=parse_live(payload)
    fields=live['raw_fields']
    if len(fields)!=24:
        # Unknown firmware layout: show numeric raw values only, NO guessed labels.
        return [{'index':i,'title':'حقل خام %02d'%(i+1),'value':val,'unit':'','group':'غير موثق','confidence':'غير موثق'} for i,val in enumerate(fields)]
    return [{'index':i,'title':title,'value':fields[i], 'unit':unit,'group':group,'confidence':confidence}
            for i,title,unit,group,confidence in FIELD_MAP]
