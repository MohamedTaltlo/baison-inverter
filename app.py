# -*- coding: utf-8 -*-
"""BAISON Service Manager 2.0.1 — native Qt RTL, documented PI30 control."""
import csv, json, sys, time
from pathlib import Path
from datetime import datetime
from dataclasses import dataclass
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QDateTime
from PySide6.QtGui import QPixmap, QIcon, QFont
from PySide6.QtWidgets import (QApplication,QMainWindow,QWidget,QHBoxLayout,QVBoxLayout,QGridLayout,
    QLabel,QPushButton,QFrame,QStackedWidget,QTableWidget,QTableWidgetItem,QHeaderView,
    QComboBox,QLineEdit,QTextEdit,QDialog,QDialogButtonBox,QMessageBox,QFormLayout,
    QSpinBox,QCheckBox,QScrollArea,QFileDialog,QTabWidget,QGroupBox,QSplitter,QAbstractItemView,QBoxLayout)
from protocol import parse_live, parse_identity
from device import Inverter, BASE, OPTIONAL
from settings_display import friendly_setting_value
from settings import (SPECS,BY_KEY,FLAGS,active_flags,values_qpiri,normalize,make_command,
                      flag_command,validate_identity,IDENTITY)
from advanced_telemetry import extended_rows
from event_recorder import EventRecorder
from storage import Store

VERSION='4.0.0'
ROOT=Path(getattr(sys,'_MEIPASS',Path(__file__).parent))
COLORS={'navy':'#263554','purple':'#7366ff','blue':'#23b9dc','paper':'#f4f6fd'}
STYLE='''
QMainWindow, QDialog {background:#f4f6fd;color:#172242}
QWidget {font-family:"Segoe UI", "Tahoma", "Arial";font-size:11pt;color:#172242}
QLabel#heading {font-size:19pt;font-weight:700}
QLabel#hint {color:#64748b;font-size:9pt}
QFrame#surface {background:#ffffff;border:1px solid #e2e7f1;border-radius:10px}
QFrame#sidebar {background:#ffffff;border-left:1px solid #e1e7f4}
QLabel#brand {background:#ffffff;border-radius:10px}
QPushButton {background:#ffffff;border:1px solid #dce3f1;border-radius:7px;padding:9px 14px;color:#172242;font-weight:600}
QPushButton:hover {background:#f0edff;border-color:#7366ff}
QPushButton#primary {background:#7366ff;border:1px solid #7366ff;color:white}
QPushButton#primary:hover {background:#5d50e3}
QPushButton#nav {background:transparent;border:none;text-align:right;color:#263554;padding:14px 16px}
QPushButton#nav:checked {background:#eeebff;color:#5546d8;border-right:4px solid #7366ff}
QLineEdit,QComboBox,QTextEdit,QSpinBox,QDateTimeEdit {background:white;color:#182443;border:1px solid #b9c6df;border-radius:5px;padding:7px;selection-background-color:#7869ff}
QComboBox QAbstractItemView {background:white;color:#172242;selection-background-color:#e1ddff;selection-color:#172242}
QTableWidget {background:white;alternate-background-color:#f5f7fd;gridline-color:#e5eaf4;color:#0f172a;font-size:12pt;selection-background-color:#dedbff;selection-color:#0f172a}
QHeaderView::section {background:#edf0f9;color:#101a32;padding:11px;border:0;border-left:1px solid #dfe4f0;font-size:12pt;font-weight:bold}
QTabWidget::pane {background:#ffffff;border:1px solid #dfe4f0}
QTabBar::tab {background:#e9edf8;padding:10px 18px;color:#172242}
QTabBar::tab:selected {background:#7366ff;color:#fff}
QMessageBox {background:white}
'''

def label(text,role=None):
    out=QLabel(str(text));out.setWordWrap(True)
    if role:out.setObjectName(role)
    return out

def button(text,callback=None,primary=False):
    b=QPushButton(text)
    if primary:b.setObjectName('primary')
    if callback:b.clicked.connect(callback)
    return b

def panel(vertical=True):
    f=QFrame();f.setObjectName('surface')
    layout=QVBoxLayout(f) if vertical else QHBoxLayout(f)
    layout.setContentsMargins(16,16,16,16);layout.setSpacing(10)
    return f,layout

def clear_layout(layout):
    while layout.count():
        obj=layout.takeAt(0)
        if obj.widget():obj.widget().deleteLater()
        elif obj.layout():clear_layout(obj.layout())

def config_table(headers):
    table=QTableWidget(0,len(headers));table.setHorizontalHeaderLabels(headers)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.setAlternatingRowColors(True);table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.verticalHeader().setVisible(False)
    return table

def put_row(t,values):
    n=t.rowCount();t.insertRow(n)
    for c,v in enumerate(values):
        item=QTableWidgetItem(str(v) if v is not None else '—')
        if isinstance(v,(float,int)):item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        t.setItem(n,c,item)

@dataclass(frozen=True)
class DiagnosticSpec:
    """Read-only info row; never a settings command."""
    key: str
    title: str
    details: str

# The user-facing 'Other information' category is populated only by read
# inquiries, not by fabricated writable device settings.
DIAGNOSTIC_FIELDS = (
    ('QPI', 'إصدار بروتوكول الاتصال', 'البروتوكول الذي أبلغ عنه جهاز USB.'),
    ('QID', 'هوية الجهاز الإلكترونية', 'المعرّف المقروء من الإنفرتر؛ مفتاح سجل الصيانة.'),
    ('QSID', 'الرقم التسلسلي الموسع', 'تتضمن الاستجابة طول الرقم وحقولاً إضافية.'),
    ('QMN', 'الموديل الداخلي', 'اسم الموديل المعلن من الـFirmware.'),
    ('QVFW', 'إصدار البرنامج الثابت', 'النسخة البرمجية الرئيسية للجهاز.'),
    ('QVFW3', 'البرنامج الثابت الإضافي', 'استعلام اختياري قد لا يدعمه هذا الموديل.'),
    ('QPIWS', 'خانات التحذير', 'أعلام التحذيرات اللحظية، ليست سجل أعطال تاريخي.'),
    ('QFLAG', 'أعلام وظائف التشغيل', 'الأعلام المفعلة والمعطلة على الجهاز.'),
    ('QET', 'إجمالي الطاقة المسجل', 'استعلام اختياري للطاقة التراكمية.'),
    ('QLT', 'إجمالي طاقة الأحمال', 'استعلام اختياري عن طاقة الأحمال.'),
    ('QBEQI', 'حالة الموازنة', 'قد لا يكون مدعوماً مع بعض أنواع البطاريات.'),
    ('QOPPT', 'بيانات إضافية للخرج', 'استعلام اختياري خاص ببعض الإصدارات.'),
    ('QCHPT', 'بيانات إضافية للشحن', 'استعلام اختياري خاص ببعض الإصدارات.'),
)

class Task(QThread):
    finished_result=Signal(object)
    failed=Signal(str)
    def __init__(self,mode='read',command=None,verification=None):
        super().__init__();self.mode=mode;self.command=command;self.verification=verification
    def run(self):
        try:
            with Inverter() as inverter:
                if self.mode=='read':
                    raw=inverter.read(BASE+OPTIONAL)
                    if not raw.get('QPI') or not raw.get('QID'):
                        raise RuntimeError('تعذرت قراءة هوية الجهاز')
                    self.finished_result.emit({'kind':'read','raw':raw})
                elif self.mode=='poll':
                    raw=inverter.read(['QID','QMOD','QPIGS','QPIWS'])
                    if not raw.get('QID') or not raw.get('QPIGS'):
                        raise RuntimeError('انقطع رد الإنفرتر')
                    self.finished_result.emit({'kind':'poll','raw':raw})
                elif self.mode=='write':
                    before=inverter.read(['QPI','QID','QMN','QVFW','QPIRI','QPIGS','QFLAG'])
                    validate_identity(before)
                    live=parse_live(before['QPIGS'])
                    if self.verification.get('index') in (9,10,11) and values_qpiri(before['QPIRI'])[12] != '2':
                        raise RuntimeError('تعديل فولت الشحن محجوب ما لم يكن نوع البطارية User (مخصص)')
                    # zero power avoids live-load source changes; user confirms separately.
                    if (live.get('watts') is None or live['watts']>0 or (live.get('load_percent') or 0)>0):
                        raise RuntimeError('التعديل يتطلب فصل الأحمال ووصول قراءة الحمل إلى صفر')
                    if self.verification['before']!=self._value(before):
                        raise RuntimeError('تغيّرت القيمة منذ فتح الصفحة؛ اقرأ الإعدادات مجدداً')
                    status=inverter.write(self.command)
                    if status!='ACK':raise RuntimeError('رفض الإنفرتر الأمر أو لم يرسل ACK: '+str(status))
                    time.sleep(0.6)
                    after=inverter.read(['QPIRI','QFLAG','QID','QVFW'])
                    if after.get('QID')!=IDENTITY[0]:raise RuntimeError('تغيرت هوية الجهاز أثناء التعديل')
                    expected=self.verification['expected']
                    actual=self._value(after)
                    success=actual==expected or (self.verification['kind']=='field' and self._numeric_equal(actual,expected))
                    self.finished_result.emit({'kind':'write','verified':success,'before':before,'after':after,
                                               'expected':expected,'actual':actual,'command':self.command})
        except Exception as e:self.failed.emit(str(e))
    def _value(self,raw):
        spec=self.verification
        if spec['kind']=='flag':return active_flags(raw.get('QFLAG','')).get(spec['flag'])
        return values_qpiri(raw.get('QPIRI',''))[spec['index']]
    @staticmethod
    def _numeric_equal(a,b):
        try:return float(a)==float(b)
        except (ValueError,TypeError):return False

class ServiceDialog(QDialog):
    REQUIRED=(('sticker_serial','هوية الجهاز'),('customer_name','اسم الزبون'),
              ('serviced_at','تاريخ ووقت الصيانة'),('fault_type','نوع العطل'),
              ('team','الفريق المسؤول'))

    def __init__(self,device_id='',data=None,parent=None,save_callback=None):
        super().__init__(parent)
        self._save_callback=save_callback
        self.setWindowTitle('BAISON | سجل الصيانة')
        self.setMinimumSize(580,560)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        main=QVBoxLayout(self)
        main.addWidget(label('إضافة / تعديل صيانة','heading'))
        scrolling=QScrollArea();scrolling.setWidgetResizable(True)
        scrolling.setFrameShape(QFrame.Shape.NoFrame)
        body=QWidget();form=QFormLayout(body);form.setSpacing(11)
        self.fields={}
        captions=[('sticker_serial','هوية الجهاز *'),('customer_name','اسم الزبون *'),
                  ('serviced_at','تاريخ ووقت الصيانة *'),('fault_type','نوع العطل *'),
                  ('team','الفريق المسؤول *'),('phone','رقم الهاتف (اختياري)')]
        for key,caption in captions:
            edit=QLineEdit();edit.setMinimumHeight(38)
            default=(device_id if key=='sticker_serial' else
                     datetime.now().strftime('%Y-%m-%d %H:%M') if key=='serviced_at' else '')
            value=(data or {}).get(key)
            edit.setText(str(value if value is not None else default))
            if key=='sticker_serial':edit.setReadOnly(True)
            if key=='serviced_at':edit.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
            self.fields[key]=edit;form.addRow(caption,edit)
            edit.textChanged.connect(self.update_form_validity)
        self.notes=QTextEdit();self.notes.setMinimumHeight(115)
        self.notes.setPlainText((data or {}).get('notes',''))
        form.addRow('ملاحظات الصيانة',self.notes)
        scrolling.setWidget(body);main.addWidget(scrolling,1)
        self.validation_text=QLabel('');self.validation_text.setWordWrap(True)
        self.validation_text.setStyleSheet('color:#a34f13;font-size:10pt;font-weight:600')
        main.addWidget(self.validation_text)
        main.addWidget(label('يُحفظ السجل محلياً على الكمبيوتر، وليس في ذاكرة EEPROM.','hint'))
        actions=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        self.save_button=actions.button(QDialogButtonBox.StandardButton.Save)
        self.save_button.setText('حفظ الصيانة')
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText('إلغاء')
        actions.accepted.connect(self.save_and_accept)
        actions.rejected.connect(self.reject)
        main.addWidget(actions)
        self.update_form_validity()

    def data(self):
        return {**{k:v.text().strip() for k,v in self.fields.items()},
                'notes':self.notes.toPlainText().strip()}

    def validation_error(self):
        data=self.data()
        for key,caption in self.REQUIRED:
            if not data[key]:return 'الحقل «'+caption+'» مطلوب'
        # The serial must be the real QID; no placeholder serial can be saved.
        if not Store.valid_identity(data['sticker_serial']):
            return 'هوية الجهاز غير صالحة. يجب قراءة QID من الإنفرتر.'
        try:
            datetime.strptime(data['serviced_at'],'%Y-%m-%d %H:%M')
        except ValueError:
            return 'تاريخ ووقت الصيانة يجب أن يكونا بصيغة YYYY-MM-DD HH:MM'
        return ''

    def update_form_validity(self,*args):
        error=self.validation_error()
        self.save_button.setEnabled(not bool(error))
        self.validation_text.setText(error)
        return not bool(error)

    def save_and_accept(self):
        if not self.update_form_validity():return
        try:
            # IMPORTANT: saving occurs BEFORE the dialog closes.
            # DB rejection never discards user edits.
            if self._save_callback is not None:
                self._save_callback(self.data())
        except Exception as exc:
            self.validation_text.setText('تعذر حفظ الصيانة: '+str(exc))
            self.validation_text.setFocus()
            return
        self.accept()

class Main(QMainWindow):
    def __init__(self):
        super().__init__();self.setWindowTitle('BAISON Service Manager | MT_RADIUS 4.0.0')
        self.resize(1380,820);self.setMinimumSize(1080,650)
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.store=Store();self.store.prune_observations();self.recorder=EventRecorder(self.store)
        self.raw={};self.device_id='';self.worker=None;self.writing=False;self._legacy_offer_done=False
        holder=QWidget();self.setCentralWidget(holder);root=QHBoxLayout(holder);root.setSpacing(0);root.setContentsMargins(0,0,0,0)
        self.sidebar=QFrame();self.sidebar.setObjectName('sidebar');self.sidebar.setFixedWidth(235)
        sidebar=QVBoxLayout(self.sidebar);sidebar.setSpacing(9);sidebar.setContentsMargins(12,20,12,16)
        self.logo=QLabel();self.logo.setObjectName('brand');self.logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.logo.setFixedHeight(135)
        pix=QPixmap(str(ROOT/'assets'/'baison_logo.png'))
        self.logo.setPixmap(pix.scaled(210,115,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
        sidebar.addWidget(self.logo);branding=label('SERVICE MANAGER   •   V4','hint');branding.setAlignment(Qt.AlignmentFlag.AlignCenter);sidebar.addWidget(branding)
        self.stack=QStackedWidget();self.nav=[]
        tabs=[('لوحة المراقبة',self.dashboard),('البيانات الموسّعة',self.telemetry_page),('الإعدادات والتحكم',self.settings_page),
              ('الزبائن والصيانة',self.maintenance_page),('سجل الأحداث',self.events_page)]
        self.pages=[]
        for i,(title,make) in enumerate(tabs):
            b=button(title,lambda checked=False,i=i:self.switch(i));b.setObjectName('nav');b.setCheckable(True)
            sidebar.addWidget(b);self.nav.append(b)
            page=make();self.stack.addWidget(page);self.pages.append(page)
        sidebar.addStretch(1)
        # A dedicated LTR footer avoids word order reversal inside RTL UI.
        credits=QWidget();credits.setLayoutDirection(Qt.LayoutDirection.LeftToRight)
        credit_layout=QVBoxLayout(credits)
        credit_layout.setContentsMargins(0,0,0,0);credit_layout.setSpacing(2)
        credit_row=QHBoxLayout();credit_row.setDirection(QBoxLayout.Direction.LeftToRight)
        credit_row.setSpacing(4)
        lead=QLabel('Developed by:');lead.setStyleSheet('color:#64748b;font-size:8pt')
        developer=QLabel('<a href="https://wa.me/963969451248" style="color:#5143d6;text-decoration:none;">MT_RADIUS</a>')
        developer.setObjectName('developerCredit');developer.setOpenExternalLinks(True)
        developer.setToolTip('Mohamed Taltlo\nWhatsApp: +963969451248')
        developer.setStyleSheet('font-size:9pt;font-weight:700')
        years=QLabel('2020-'+str(datetime.now().year));years.setStyleSheet('color:#64748b;font-size:8pt')
        credit_row.addWidget(lead);credit_row.addWidget(developer);credit_row.addWidget(years)
        credit_row.addStretch(1);credit_layout.addLayout(credit_row)
        sidebar.addWidget(credits)
        root.addWidget(self.sidebar);root.addWidget(self.stack,1)
        self.statusBar().showMessage('جاهز — وصل جهاز BAISON عبر USB ثم اضغط قراءة')
        self.switch(0)
        self.timer=QTimer(self);self.timer.timeout.connect(self.poll);self.timer.start(15000)
        self.retention_timer=QTimer(self);self.retention_timer.timeout.connect(self.prune_observations);self.retention_timer.start(21600000)
        QTimer.singleShot(500,self.refresh)
    def switch(self,index):
        self.stack.setCurrentIndex(index)
        for i,b in enumerate(self.nav):b.setChecked(i==index)
        if index==3:self.load_repairs()
        if index==4:self.load_events()
    def page_base(self,title,subtitle=''):
        p=QWidget();layout=QVBoxLayout(p);layout.setContentsMargins(22,18,22,18);layout.setSpacing(13)
        layout.addWidget(label(title,'heading'))
        if subtitle:layout.addWidget(label(subtitle,'hint'))
        return p,layout
    def dashboard(self):
        page,body=self.page_base('لوحة مراقبة الإنفرتر','قراءات حقيقية عبر USB HID، وليس عبر الإنترنت')
        h=QHBoxLayout();h.addWidget(button('قراءة جميع المعلومات',self.refresh,True));self.status_label=label('بانتظار الاتصال');h.addWidget(self.status_label,1)
        body.addLayout(h)
        cards=QGridLayout();self.cards={}
        data=[('grid_v','دخل الكهرباء','V'),('out_v','خرج الكهرباء','V'),('battery_v','فولت البطارية','V'),('pv_v','جهد الألواح','V'),
              ('watts','الحمل الفعلي','W'),('pv_charge_watts','قدرة الشحن الشمسي','W'),('charge_amp','تيار الشحن','A'),('battery_discharge_amp','تيار التفريغ','A')]
        for i,(key,caption,unit) in enumerate(data):
            frame,ly=panel();ly.addWidget(label(caption,'hint'));v=label('— '+unit);v.setObjectName('telemetryValue');v.setStyleSheet('font-size:24pt;font-weight:800;color:#101b37');ly.addWidget(v);self.cards[key]=(v,unit);cards.addWidget(frame,i//4,i%4)
        body.addLayout(cards)
        p,ly=panel();self.banner=label('الجهاز غير متصل');self.banner.setStyleSheet('font-size:14pt;font-weight:600');ly.addWidget(self.banner)
        self.details=label('الموديل —   |   Firmware —   |   QID —');ly.addWidget(self.details);body.addWidget(p)
        self.warning_label=label('التحذيرات: بانتظار القراءة');body.addWidget(self.warning_label)
        body.addStretch(1)
        return page
    def telemetry_page(self):
        page,body=self.page_base('البيانات الموسّعة','كل حقول QPIGS والقراءات الإضافية المدعومة، مع إظهار الخانات المحجوزة بوضوح')
        self.telemetry=config_table(['البيان','القيمة','الوحدة','درجة التوثيق']);body.addWidget(self.telemetry)
        p,ly=panel();self.extra_info=label('الأوامر الإضافية تظهر هنا بعد قراءة الجهاز');ly.addWidget(self.extra_info);body.addWidget(p)
        return page
    def settings_page(self):
        page,body=self.page_base('الإعدادات والتحكم','كل قيمة تُقرأ من الجهاز. التعديل لا يتم إلا بتأكيد الفني ونجاح ACK وقراءة القيمة بعد الكتابة.')
        info,ly=panel();self.profile_notice=label('لم يتم فحص موديل الإنفرتر بعد');ly.addWidget(self.profile_notice);body.addWidget(info)
        splitter=QSplitter(Qt.Orientation.Horizontal)
        right,rl=panel();filter_row=QHBoxLayout();filter_row.addWidget(label('القسم'))
        self.group=QComboBox();self.group.addItems(['الكل','الطاقة','البطارية','الشحن','الخرج','الألواح','أعلام التشغيل','معلومات أخرى'])
        self.group.currentIndexChanged.connect(self.render_settings);filter_row.addWidget(self.group);rl.addLayout(filter_row)
        self.setting_table=config_table(['الإعداد','القيمة الحالية','إعداد المصنع'])
        self.setting_table.setWordWrap(True)
        self.setting_table.verticalHeader().setDefaultSectionSize(48)
        self.setting_table.horizontalHeader().setMinimumSectionSize(120)
        self.setting_table.setFont(QFont('Segoe UI',12,QFont.Weight.DemiBold))
        self.setting_table.itemSelectionChanged.connect(self.choose_setting);rl.addWidget(self.setting_table)
        splitter.addWidget(right)
        left,ll=panel();ll.addWidget(label('شرح الإعداد المحدد','heading'))
        self.setting_description=label('حدد أحد الإعدادات لعرض معناه وخياراته وحدود التعديل.');ll.addWidget(self.setting_description)
        self.old_setting=label('الحالية: —');ll.addWidget(self.old_setting)
        self.setting_options=QComboBox();self.setting_options.currentIndexChanged.connect(self.show_choice_help);ll.addWidget(self.setting_options)
        self.custom_setting=QLineEdit();self.custom_setting.setPlaceholderText('القيمة الجديدة — مثال 27.0');self.custom_setting.hide();ll.addWidget(self.custom_setting)
        self.choice_help=label('');ll.addWidget(self.choice_help)
        self.enable_write=QCheckBox('أؤكد أنني فني مخوّل، وأن الأحمال مفصولة وأن القيمة مناسبة للبطارية');ll.addWidget(self.enable_write)
        self.apply_btn=button('إرسال التعديل والتحقق من النتيجة',self.apply_change,True);ll.addWidget(self.apply_btn);ll.addStretch(1)
        splitter.addWidget(left);splitter.setSizes([760,380]);body.addWidget(splitter,1)
        self.current_spec=None;self.row_specs=[]
        return page
    def maintenance_page(self):
        page,body=self.page_base('إدارة الزبائن وسجل الصيانة','ربط كل زبون وصيانة بالرقم QID المقروء فعلياً من الإنفرتر؛ لا يُقبل الرقم الوهمي')
        row=QHBoxLayout();self.customer=QLineEdit();self.customer.setPlaceholderText('اسم الزبون');self.sticker=QLineEdit();self.sticker.setPlaceholderText('رقم الملصق (اختياري)')
        row.addWidget(label('اسم الزبون'));row.addWidget(self.customer);row.addWidget(label('رقم الملصق'));row.addWidget(self.sticker)
        row.addWidget(button('حفظ بيانات الزبون',self.save_customer,True));row.addWidget(button('إزالة ربط الزبون',self.unlink_customer))
        body.addLayout(row)
        self.bound_qid=label('QID: بانتظار قراءة الجهاز','hint');body.addWidget(self.bound_qid)
        actions=QHBoxLayout();actions.addWidget(button('إضافة صيانة',self.new_service,True));actions.addWidget(button('تعديل الصيانة',self.edit_service));actions.addWidget(button('حذف الصيانة',self.delete_service))
        actions.addWidget(button('تصدير CSV',self.export_services));actions.addStretch(1);body.addLayout(actions)
        self.services=config_table(['ID','التاريخ','اسم الزبون','العطل','الفريق','الهاتف','الملاحظات']);body.addWidget(self.services,1)
        return page
    def events_page(self):
        page,body=self.page_base('سجل أحداث الجهاز',
           'تنظيف القراءات والأحداث المؤقتة فقط. سجلات الزبائن والصيانة والتدقيق محفوظة.')
        policy=self.store.retention_settings()
        row=QHBoxLayout();row.addWidget(label('الأحداث (يوم)'))
        self.events_days=QSpinBox();self.events_days.setRange(1,365)
        self.events_days.setValue(policy['events_days']);row.addWidget(self.events_days)
        row.addWidget(label('القراءات (يوم)'))
        self.snapshots_days=QSpinBox();self.snapshots_days.setRange(1,365)
        self.snapshots_days.setValue(policy['snapshots_days']);row.addWidget(self.snapshots_days)
        row.addWidget(button('حفظ سياسة التنظيف',self.save_retention))
        row.addStretch(1);body.addLayout(row)
        row2=QHBoxLayout()
        row2.addWidget(button('تحديث السجل',self.load_events))
        row2.addWidget(button('تصدير CSV',self.export_events))
        row2.addWidget(button('حذف سجل الجهاز الحالي',lambda:self.clear_event_history(True)))
        row2.addWidget(button('حذف كل سجلات المراقبة',lambda:self.clear_event_history(False)))
        row2.addStretch(1);body.addLayout(row2)
        self.eeprom_info=label('تشخيص EEPROM: قراءة فقط','hint')
        body.addWidget(self.eeprom_info)
        self.events_info=label('','hint');body.addWidget(self.events_info)
        self.events=config_table(['الوقت','الحدث','الشدة','التفاصيل']);body.addWidget(self.events)
        return page

    def save_retention(self):
        try:
            self.store.set_retention(self.events_days.value(),self.snapshots_days.value())
            self.store.prune_observations();self.load_events()
            QMessageBox.information(self,'تم الحفظ','تم تحديث سياسة الاحتفاظ دون حذف سجلات صيانة.')
        except Exception as exc:QMessageBox.warning(self,'تعذر الحفظ',str(exc))

    def prune_observations(self):
        try:
            self.store.prune_observations()
            if self.stack.currentIndex()==4:self.load_events()
        except Exception as exc:self.statusBar().showMessage('تعذر تنظيف سجل المراقبة: '+str(exc),6000)

    def clear_event_history(self,one_device):
        qid=self.device_id if one_device else None
        if one_device and not self.store.valid_identity(qid):
            QMessageBox.warning(self,'اختر جهازاً','يجب قراءة هوية الجهاز أولاً.');return
        counts=self.store.observation_counts(qid)
        msg=('هل تريد حذف %d حدث و%d لقطة قياس؟\n'
             'الحذف محلي ونهائي؛ لا يمس الزبائن ولا سجل الصيانة والتدقيق.'
             %(counts['events'],counts['snapshots']))
        if QMessageBox.question(self,'تأكيد حذف السجل',msg,
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        self.store.clear_observations(qid)
        if qid:
            self.recorder.previous.pop(qid,None);self.recorder.last_snapshot.pop(qid,None)
        else:
            self.recorder.previous.clear();self.recorder.last_snapshot.clear()
        self.load_events()

    def start_task(self,mode='read',command=None,verification=None):
        if self.worker and self.worker.isRunning():return
        self.worker=Task(mode,command,verification);self.worker.finished_result.connect(self.task_ok);self.worker.failed.connect(self.task_error)
        self.worker.start()
    def refresh(self):self.status_label.setText('جارِ الاتصال وقراءة البيانات…');self.start_task('read')
    def poll(self):
        if not self.writing and self.device_id and not (self.worker and self.worker.isRunning()):self.start_task('poll')
    def task_error(self,message):
        self.statusBar().showMessage('فشل الاتصال: '+message,12000)
        self.status_label.setText('تعذر الاتصال: '+message)
        self.recorder.disconnected(message)
        self.writing=False
        if self.worker and self.worker.mode=='write':QMessageBox.warning(self,'تعديل غير مكتمل',message)
    def task_ok(self,result):
        kind=result['kind']
        if kind=='write':
            self.writing=False
            self.store.record_event(self.device_id,'settings_change','info' if result['verified'] else 'warning',
                'تعديل إعداد من البرنامج' if result['verified'] else 'ACK وصل لكن القراءة لم تؤكد التعديل',
                {'command':result['command'],'expected':result['expected'],'actual':result['actual']})
            QMessageBox.information(self,'نتيجة التعديل',('نجح التعديل وتطابقت القراءة الجديدة.' if result['verified'] else 'رد الجهاز ACK لكن القراءة لم تتطابق. لا نعتبر العملية ناجحة.'))
            self.refresh();return
        raw=result['raw']
        if kind=='read':self.raw=raw
        else:self.raw.update(raw)
        candidate=self.raw.get('QID','').strip()
        if not self.store.valid_identity(candidate):
            self.device_id=''
            self.statusBar().showMessage('استجابة QID مفقودة أو غير صالحة؛ لن يتم ربط أي زبون',12000)
            self.status_label.setText('فشلت قراءة الهوية الفعلية؛ التسجيل معطّل')
            return
        self.device_id=candidate
        if hasattr(self,'bound_qid'):self.bound_qid.setText('الهوية الإلكترونية المؤكدة من USB: '+self.device_id)
        identity=parse_identity(self.raw)
        if kind=='read':
            self.store.ensure_device(self.device_id,identity.get('model',''),identity.get('firmware',''))
            self.offer_legacy_repair()
        live=parse_live(self.raw['QPIGS']) if self.raw.get('QPIGS') else {}
        if live:
            for key,(widget,unit) in self.cards.items():
                val=live.get(key)
                widget.setText((str(val) if val is not None else '—')+' '+unit)
                if key=='battery_v' and val is not None and (val<10 or val>34):
                    widget.setToolTip('قراءة خارج المجال المعتاد لبطارية 24V؛ تحقق من شاشة الجهاز وتوصيله')
                    widget.setStyleSheet('font-size:24pt;font-weight:800;color:#b45309')
                else:
                    widget.setToolTip('')
                    widget.setStyleSheet('font-size:24pt;font-weight:800;color:#101b37')
            self.recorder.capture(self.device_id,identity,live,self.raw.get('QPIWS',''))
        self.status_label.setText('متصل • '+datetime.now().strftime('%H:%M:%S'))
        self.banner.setText('وضع التشغيل: '+ {'L':'كهرباء خارجية','B':'بطارية','S':'استعداد','F':'عطل','C':'شحن','P':'بدء التشغيل'}.get(identity.get('mode',''),'غير معروف'))
        self.details.setText('الموديل: '+identity['model']+'     •     Firmware: '+identity['firmware']+'     •     QID: '+self.device_id)
        warnings=self.raw.get('QPIWS','')
        count=warnings.count('1') if set(warnings)<=set('01') else 'غير معروف'
        self.warning_label.setText('خانات التحذير النشطة: '+str(count)+' — يتضمن فقدان الشمس عند غياب الألواح')
        from protocol import parse_eeprom_diagnostics
        diag=parse_eeprom_diagnostics(self.raw.get('QPIGS',''), self.raw.get('QFLAG',''), self.raw.get('QPIWS',''))
        if hasattr(self,'eeprom_info'):self.eeprom_info.setText(diag)
        self.statusBar().showMessage('تمت القراءة من الإنفرتر',6000)
        if kind=='read':
            self.render_settings();self.render_telemetry();self.load_repairs();self.load_events()
        if self.store.by_internal_id(self.device_id):
            card=self.store.by_internal_id(self.device_id)
            if kind=='read':
                self.customer.setText(card.get('customer_name',''));self.sticker.setText(card.get('physical_serial',''))
    def render_telemetry(self):
        self.telemetry.setRowCount(0)
        if self.raw.get('QPIGS'):
            for r in extended_rows(self.raw['QPIGS']):put_row(self.telemetry,[r['title'],r['value'],r['unit'],r['confidence']])
        rest=[]
        for cmd in OPTIONAL:
            if self.raw.get(cmd):rest.append(cmd+' : '+self.raw[cmd])
        self.extra_info.setText('\n'.join(rest) if rest else 'لم يُبلغ الجهاز عن بيانات إضافية')
    def setting_rows(self):
        rows=[]
        fields=[]
        if self.raw.get('QPIRI'):
            fields=self.raw['QPIRI'].split()
        defaults=self.raw.get('QDI','').split()
        for spec in SPECS:
            val=fields[spec.index] if len(fields)==25 else '—'
            # QDI order differs from QPIRI. Only map verified common indexes.
            default_index={'output_voltage':0,'output_frequency':1,'max_utility_charge':2,'battery_cutoff':3,
                           'float_voltage':4,'bulk_voltage':5,'back_to_grid':6,'max_charge':7,
                           'input_range':8,'output_priority':9,'charger_priority':10,'battery_type':11,
                           'back_to_battery':22,'pv_ok':23,'pv_balance':24}.get(spec.key)
            factory=defaults[default_index] if len(defaults)==25 and default_index is not None else '—'
            rows.append((spec,spec.title,val,factory,spec.risk))
        flags=active_flags(self.raw.get('QFLAG',''))
        for key,(title,desc,risk) in FLAGS.items():
            spec=('flag',key,title,desc,risk)
            val=flags.get(key)
            rows.append((spec,title,'مفعّل' if val else 'معطّل' if val is False else 'غير معروف','—',risk))
        for key,title,details in DIAGNOSTIC_FIELDS:
            value=(self.raw.get(key) or '').strip()
            if not value:
                status='غير مدعوم أو مرفوض' if self.raw.get('ERROR_'+key) else 'لم تتم قراءته'
                value=status
            rows.append((DiagnosticSpec(key,title,details),title,value,'—','قراءة فقط'))
        live=self.raw.get('QPIGS','').split()
        if len(live)==24:
            rows.append((DiagnosticSpec('eeprom','إصدار EEPROM',
                'رقم إصدار داخلي ضمن QPIGS، وليس سعة أو مساحة كتابة.'),
                'إصدار EEPROM',live[18],'—','قراءة فقط'))
        return rows
    def render_settings(self,*args):
        if not hasattr(self,'setting_table'):return
        self.setting_table.setRowCount(0);self.row_specs=[]
        valid=(self.raw.get('QMN')=='VMIII-4200' and len(self.raw.get('QPIRI','').split())==25)
        self.profile_notice.setText(('تم التعرف على التخطيط VMIII-4200. التعديلات تتطلب الهوية وFirmware المطابقين.' if valid else
              'لم يتأكد تخطيط هذا الموديل؛ ستبقى الإعدادات للقراءة فقط.'))
        group=self.group.currentText()
        for spec,title,current,default,risk in self.setting_rows():
            category=('معلومات أخرى' if isinstance(spec,DiagnosticSpec) else
                      'أعلام التشغيل' if isinstance(spec,tuple) else spec.group)
            if group!='الكل' and group!=category:continue
            self.row_specs.append((spec,current))
            status=('قراءة فقط' if isinstance(spec,DiagnosticSpec) else
                    'موثق — اختبار مطلوب' if valid else 'قراءة فقط')
            put_row(self.setting_table,[title,friendly_setting_value(spec,current),friendly_setting_value(spec,default)])
            row_index=self.setting_table.rowCount()-1
            for col,raw_value in ((1,current),(2,default)):
                cell=self.setting_table.item(row_index,col)
                if cell:
                    cell.setToolTip('القيمة الأصلية من بروتوكول PI30: '+str(raw_value))
                    cell.setFont(QFont('Segoe UI',12,QFont.Weight.DemiBold))
        if self.setting_table.rowCount():self.setting_table.selectRow(0)
    def choose_setting(self):
        idx=self.setting_table.currentRow()
        if idx<0 or idx>=len(self.row_specs):return
        spec,current=self.row_specs[idx];self.current_spec=spec
        self.setting_options.blockSignals(True);self.setting_options.clear();self.custom_setting.hide();self.setting_options.show()
        if isinstance(spec,DiagnosticSpec):
            self.setting_description.setText(spec.title+'\n'+spec.details+'\nبيانات تشخيصية للقراءة فقط')
            self.old_setting.setText('البيانات المقروءة: '+str(current))
            self.choice_help.setText('لا يتوفر أمر تعديل لهذا الحقل.')
            self.setting_options.hide();self.custom_setting.hide()
            self.apply_btn.setEnabled(False);self.enable_write.setEnabled(False)
            self.setting_options.blockSignals(False)
            return
        self.apply_btn.setEnabled(True);self.enable_write.setEnabled(True)
        if isinstance(spec,tuple):
            _,key,title,desc,risk=spec
            self.setting_description.setText(title+'\n'+desc+'\nدرجة الخطورة: '+risk)
            for status,text in [(True,'تفعيل'),(False,'تعطيل')]:self.setting_options.addItem(text,status)
            self.setting_options.setCurrentIndex(0 if current=='مفعّل' else 1)
        else:
            self.setting_description.setText(spec.title+'\n'+spec.details+'\nدرجة الخطورة: '+spec.risk)
            if spec.kind=='choice':
                for raw,text in spec.options:self.setting_options.addItem(text,raw)
                for i in range(self.setting_options.count()):
                    if self.setting_options.itemData(i)==current:self.setting_options.setCurrentIndex(i);break
            elif spec.kind=='current':
                key='QMCHGCR' if spec.key=='max_charge' else 'QMUCHGCR'
                options=[x for x in self.raw.get(key,'').split() if x.isdigit()]
                for x in options:self.setting_options.addItem(str(int(x))+' A',str(int(x)))
                if not options:self.setting_options.addItem('لم يقدم الجهاز خيارات تيار موثقة',None)
            else:
                self.setting_options.hide();self.custom_setting.show();self.custom_setting.setText(current)
        self.old_setting.setText('القيمة الحالية: '+friendly_setting_value(spec,current));self.setting_options.blockSignals(False)
        self.show_choice_help()
    def show_choice_help(self,*args):
        if not self.current_spec or isinstance(self.current_spec,DiagnosticSpec):return
        if isinstance(self.current_spec,tuple):
            self.choice_help.setText('ستُرسل حالة العلم وتُقرأ من QFLAG بعد التنفيذ.')
        else:
            self.choice_help.setText('القيمة الجديدة ستؤثر في: '+self.current_spec.details)
    def apply_change(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self,'الإنفرتر مشغول','انتظر انتهاء القراءة الحالية ثم حاول مجدداً');return
        spec=self.current_spec
        if not spec or isinstance(spec,DiagnosticSpec):return
        if not self.enable_write.isChecked():
            QMessageBox.warning(self,'إقرار مطلوب','يرجى تأكيد صلاحية الفني وفصل الأحمال أولاً');return
        try:
            validate_identity(self.raw)
            if isinstance(spec,tuple):
                key=spec[1];expected=self.setting_options.currentData();before=active_flags(self.raw.get('QFLAG','')).get(key)
                if before is None:raise ValueError('حالة العلم غير معلومة')
                command=flag_command(key,expected)
                verification={'kind':'flag','flag':key,'before':before,'expected':expected}
                name=spec[2];risk=spec[4]
            else:
                value=(self.custom_setting.text() if spec.kind=='voltage' else self.setting_options.currentData())
                if value is None:raise ValueError('لا توجد قيم يدعمها الجهاز')
                command=make_command(spec,value,self.raw)
                before=values_qpiri(self.raw['QPIRI'])[spec.index]
                expected=normalize(spec,value,self.raw)
                verification={'kind':'field','index':spec.index,'before':before,'expected':expected}
                name=spec.title;risk=spec.risk
            if risk=='critical':
                if QMessageBox.warning(self,'إعداد حساس','هذا إعداد بطارية أو خرج حساس. تأكد من قيمة BMS ومواصفات البطارية. هل تريد المتابعة؟',
                    QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
            msg=f'الجهاز: {self.device_id}\nالإعداد: {name}\nالحالية: {before}\nالجديدة: {expected}\nالأمر: {command}\n\nهل ترسل الأمر الآن؟'
            if QMessageBox.question(self,'تأكيد إرسال أمر للجهاز',msg,QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
            self.writing=True;self.start_task('write',command,verification)
        except Exception as e:QMessageBox.warning(self,'تعذر التعديل',str(e))
    def offer_legacy_repair(self):
        if self._legacy_offer_done or not self.device_id or self.device_id!='96342305101831':
            return
        self._legacy_offer_done=True
        if not self.store.legacy_placeholder_has_data():
            return
        reply=QMessageBox.question(self,'تصحيح بطاقة قديمة',
            'وجدت بطاقة قديمة بهوية وهمية (أصفار). هل تريد نقل ارتباطها وسجلات صيانتها إلى '+self.device_id+'؟\n'
            'سيُحفظ أثر النقل في سجل التدقيق، ولن تُحذف الصيانة.',
            QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)
        if reply==QMessageBox.StandardButton.Yes:
            try:
                self.store.relink_legacy_placeholder(self.device_id)
                self.statusBar().showMessage('تم تصحيح الربط مع QID الحقيقي',9000)
            except Exception as exc:
                QMessageBox.warning(self,'تعذر نقل البيانات القديمة',str(exc))

    def save_customer(self):
        if not self.store.valid_identity(self.device_id):QMessageBox.warning(self,'الجهاز','أوصل الإنفرتر واقرأ QID الحقيقي أولاً');return
        try:
            self.store.ensure_device(self.device_id,self.raw.get('QMN',''),self.raw.get('QVFW',''))
            self.store.update_device_card(self.device_id,self.sticker.text(),self.customer.text());self.statusBar().showMessage('تم حفظ بيانات الزبون',6000)
        except Exception as e:QMessageBox.warning(self,'خطأ',str(e))
    def unlink_customer(self):
        if not self.device_id:return
        if QMessageBox.question(self,'إزالة الربط','إزالة بيانات الزبون من البطاقة مع الاحتفاظ بسجل الصيانة؟',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)!=QMessageBox.StandardButton.Yes:return
        self.store.remove_customer(self.device_id);self.customer.clear();self.sticker.clear()
    def new_service(self):
        if not self.store.valid_identity(self.device_id):QMessageBox.warning(self,'الجهاز','اقرأ QID الحقيقي أولاً');return
        row=self.store.by_internal_id(self.device_id)
        if not row:
            QMessageBox.warning(self,'بيانات الجهاز','تعذّر العثور على بطاقة QID بعد قراءتها');return
        dialog=ServiceDialog(self.device_id,{'customer_name':self.customer.text()},self,save_callback=lambda data:self.store.save_service(data))
        if dialog.exec()==QDialog.DialogCode.Accepted:self.load_repairs()
    def selected_service(self):
        idx=self.services.currentRow()
        return int(self.services.item(idx,0).text()) if idx>=0 and self.services.item(idx,0) else None
    def edit_service(self):
        sid=self.selected_service()
        if not sid:return
        old=self.store.get_service(sid)
        if not old or old['sticker_serial']!=self.device_id:
            QMessageBox.warning(self,'تحذير','هذا السجل غير مرتبط بهوية الجهاز المقروءة الآن');return
        dialog=ServiceDialog(self.device_id,old,self,save_callback=lambda data:self.store.save_service(data,sid))
        if dialog.exec()==QDialog.DialogCode.Accepted:self.load_repairs()
    def delete_service(self):
        sid=self.selected_service()
        if not sid:return
        if QMessageBox.question(self,'حذف','حذف هذا القيد من السجل الظاهر؟ سيتم حفظ التغيير بسجل التدقيق.',QMessageBox.StandardButton.Yes|QMessageBox.StandardButton.No)==QMessageBox.StandardButton.Yes:
            self.store.delete_service(sid);self.load_repairs()
    def load_repairs(self):
        if not hasattr(self,'services'):return
        self.services.setRowCount(0)
        for r in self.store.list_services(self.device_id or None):put_row(self.services,[r.get(k) for k in ('id','serviced_at','customer_name','fault_type','team','phone','notes')])
    def load_events(self):
        if not hasattr(self,'events'):return
        n=self.store.observation_counts(self.device_id or None)
        self.events_info.setText('الأحداث: %d • اللقطات: %d'%(n['events'],n['snapshots']))
        self.events.setRowCount(0)
        for r in self.store.list_events(self.device_id or None,1000):put_row(self.events,[r['recorded_at'],r['message'],r['severity'],r['details_json']])
    def export_services(self):self.export_table(self.services,'baison_maintenance.csv')
    def export_events(self):self.export_table(self.events,'baison_events.csv')
    def export_table(self,table,default):
        path,_=QFileDialog.getSaveFileName(self,'تصدير CSV',default,'CSV (*.csv)')
        if not path:return
        with open(path,'w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f)
            writer.writerow([table.horizontalHeaderItem(j).text() for j in range(table.columnCount())])
            for row in range(table.rowCount()):writer.writerow([table.item(row,j).text() if table.item(row,j) else '' for j in range(table.columnCount())])
        self.statusBar().showMessage('تم التصدير: '+path,8000)
    def closeEvent(self,event):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self,'انتظر','انتظر انتهاء اتصال USB قبل الإغلاق');event.ignore();return
        self.store.close();event.accept()

def main():
    app=QApplication(sys.argv);app.setStyle('Fusion');app.setLayoutDirection(Qt.LayoutDirection.RightToLeft);app.setStyleSheet(STYLE)
    app.setWindowIcon(QIcon(str(ROOT/'assets'/'baison.ico')))
    window=Main();window.show();return app.exec()

if __name__=='__main__':sys.exit(main())
