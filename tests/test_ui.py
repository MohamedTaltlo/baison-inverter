# -*- coding: utf-8 -*-
import os,sys,unittest
from pathlib import Path
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

class TestQt(unittest.TestCase):
 def _app(self):
  from PySide6.QtWidgets import QApplication
  return QApplication.instance() or QApplication([])

 def test_service_form_never_discards_unsaved_edits(self):
  self.qt_app=self._app()
  from PySide6.QtWidgets import QDialog, QDialogButtonBox
  from app import ServiceDialog
  calls=[]
  def fail_once(data):
   calls.append(data.copy())
   if len(calls)==1:raise ValueError('قاعدة البيانات مشغولة')
  form=ServiceDialog('96342305101831',save_callback=fail_once)
  self.assertFalse(form.save_button.isEnabled())
  form.fields['customer_name'].setText('زبون تجريبي')
  form.fields['fault_type'].setText('مروحة')
  self.assertFalse(form.save_button.isEnabled())  # team missing
  form.fields['team'].setText('فريق الصيانة')
  self.assertTrue(form.save_button.isEnabled())
  form.save_and_accept()
  self.assertEqual(form.result(),0)
  self.assertEqual(form.fields['customer_name'].text(),'زبون تجريبي')
  self.assertIn('قاعدة البيانات',form.validation_text.text())
  form.save_and_accept()
  self.assertEqual(form.result(),QDialog.DialogCode.Accepted)
  self.assertEqual(len(calls),2)

 def test_invalid_date_disables_save(self):
  self.qt_app=self._app()
  from app import ServiceDialog
  f=ServiceDialog('96342305101831')
  f.fields['customer_name'].setText('أحمد')
  f.fields['fault_type'].setText('عطل')
  f.fields['team'].setText('ورشة')
  self.assertTrue(f.save_button.isEnabled())
  f.fields['serviced_at'].setText('2026-13-99 26:99')
  self.assertFalse(f.save_button.isEnabled())
  self.assertIn('تاريخ',f.validation_text.text())
  f.close()

 def test_maintenance_footer(self):
  self.qt_app=self._app()
  from PySide6.QtWidgets import QDialogButtonBox
  from PySide6.QtCore import Qt
  from app import ServiceDialog,ROOT
  w=ServiceDialog('96342305101831')
  self.assertEqual(w.layoutDirection(),Qt.LayoutDirection.RightToLeft)
  self.assertEqual(w.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).text(),'حفظ الصيانة')
  self.assertTrue((ROOT/'assets'/'baison_logo.png').exists())
  w.close()

 def test_main_window_has_no_clock(self):
  self.qt_app=self._app()
  from PySide6.QtCore import Qt
  from PySide6.QtWidgets import QLabel
  from app import Main
  w=Main()
  self.assertEqual(w.stack.count(),5)
  self.assertFalse(hasattr(w,'clock_label'))
  self.assertFalse(hasattr(w,'confirm_clock_sync'))
  self.assertEqual(w.findChild(QLabel,'developerCredit').parentWidget().layoutDirection(),Qt.LayoutDirection.LeftToRight)
  self.assertIn('24pt',w.cards['grid_v'][0].styleSheet())
  w.timer.stop();w.retention_timer.stop();w.close()

if __name__=='__main__':unittest.main()
