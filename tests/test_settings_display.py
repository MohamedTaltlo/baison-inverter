# -*- coding: utf-8 -*-
import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from settings import BY_KEY
from settings_display import friendly_setting_value

class DisplayTests(unittest.TestCase):
    def test_power_source_and_charger_codes(self):
        self.assertEqual(friendly_setting_value(BY_KEY['output_priority'],'0'),'USB — الكهرباء أولاً')
        self.assertTrue(friendly_setting_value(BY_KEY['output_priority'],'2').startswith('SBU'))
        self.assertTrue(friendly_setting_value(BY_KEY['charger_priority'],'2').startswith('SNU'))
        self.assertTrue(friendly_setting_value(BY_KEY['battery_type'],'2').startswith('USE'))
        self.assertTrue(friendly_setting_value(BY_KEY['input_range'],'1').startswith('UPS'))
    def test_units_and_unknown_codes(self):
        self.assertEqual(friendly_setting_value(BY_KEY['max_charge'],'010'),'10 A')
        self.assertEqual(friendly_setting_value(BY_KEY['bulk_voltage'],'29.2'),'29.2 V')
        self.assertEqual(friendly_setting_value(BY_KEY['output_voltage'],'220.0'),'220 V')
        self.assertEqual(friendly_setting_value(BY_KEY['output_priority'],'99'),'رمز غير معروف: 99')
        self.assertIn('غير متوقعة',friendly_setting_value(BY_KEY['output_voltage'],'2200'))
    def test_diagnostic_values_remain_raw(self):
        self.assertEqual(friendly_setting_value(('info','QT'),'20261016031928'),'20261016031928')
        self.assertEqual(friendly_setting_value(BY_KEY['charger_priority'],'—'),'—')

if __name__=='__main__': unittest.main()
