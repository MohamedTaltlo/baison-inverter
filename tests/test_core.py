import sys,unittest,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from protocol import make_query,crc16_xmodem,parse_live,decode_response,escaped_crc
from settings import SPECS,BY_KEY,FLAGS,values_qpiri,active_flags,normalize,make_command,flag_command,validate_identity,packet_for_known_command
from storage import Store
from event_recorder import EventRecorder

RATE='220.0 19.0 220.0 50.0 19.0 4200 4200 24.0 23.0 21.0 27.3 27.0 2 030 060 0 0 2 1 01 0 0 27.0 0 1'
LIVE='222.8 50.0 222.8 50.0 0000 0000 000 425 29.40 000 100 0014 00.0 000.0 00.00 00000 00010101 00 00 00000 111 0 01 0000'
QDI='230.0 50.0 0030 21.0 27.0 28.2 23.0 60 0 0 2 0 0 0 0 0 1 1 1 0 1 0 27.0 0 1'
ID={'QPI':'PI30','QID':'96342305101831','QMN':'VMIII-4200','QVFW':'VERFW:00085.04','QPIRI':RATE}

class TestProtocol(unittest.TestCase):
 def test_crc_from_actual_sample(self):self.assertEqual(make_query('QPI').hex(),'515049beac0d')
 def test_qpigs_crc(self):self.assertEqual(make_query('QPIGS').hex(),'5150494753b7a90d')
 def test_decode(self):
  p=b'(PI30'; self.assertEqual(decode_response(p+escaped_crc(p)+b'\r'),'PI30')
 def test_live(self):
  a=parse_live(LIVE);self.assertEqual(a['battery_v'],29.4);self.assertEqual(a['eeprom_version'],'00');self.assertEqual(a['pv_charge_watts'],0)
 def test_choices(self):
  self.assertEqual(make_command(BY_KEY['output_priority'],'2'),'POP02')
  self.assertEqual(make_command(BY_KEY['charger_priority'],'3'),'PCP03')
  self.assertEqual(make_command(BY_KEY['bulk_voltage'],'28.4'),'PCVV28.4')
  self.assertEqual(make_command(BY_KEY['float_voltage'],'27.0'),'PBFT27.0')
 def test_currents_must_match_inverter(self):
  self.assertEqual(make_command(BY_KEY['max_charge'],'60',{'QMCHGCR':'010 030 060 120'}),'MNCHGC060')
  with self.assertRaises(ValueError):make_command(BY_KEY['max_charge'],'50',{'QMCHGCR':'030 060'})
  with self.assertRaises(ValueError):make_command(BY_KEY['max_charge'],'30',{})
 def test_unrecognized(self):
  with self.assertRaises(ValueError):normalize(BY_KEY['output_priority'],'7')
  with self.assertRaises(ValueError):make_command(BY_KEY['battery_cutoff'],'17.0')
  with self.assertRaises(ValueError):packet_for_known_command('RTDL')
  with self.assertRaises(ValueError):packet_for_known_command('PF')
 def test_flag(self):
  self.assertEqual(flag_command('x',False),'PDx')
  self.assertTrue(active_flags('EakxyzDbdjlnuv')['z'])
 def test_identity(self):
  self.assertTrue(validate_identity(ID))
  changed=dict(ID,QID='another');
  with self.assertRaises(ValueError):validate_identity(changed)
 def test_25(self):self.assertEqual(len(values_qpiri(RATE)),25);self.assertEqual(len(QDI.split()),25)

class TestStore(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.store=Store(Path(self.tmp.name)/'dev.sqlite3')
  self.store.ensure_device(ID['QID'],ID['QMN'],ID['QVFW'])
 def tearDown(self):self.store.close();self.tmp.cleanup()
 def test_customer_edit_and_unlink(self):
  id=ID['QID'];self.store.update_device_card(id,'9204','زبون');self.assertEqual(self.store.by_internal_id(id)['customer_name'],'زبون')
  self.store.remove_customer(id);self.assertEqual(self.store.by_internal_id(id)['customer_name'],'')
 def test_maintenance_survives_unlink(self):
  id=ID['QID'];self.store.save_service({'sticker_serial':id,'serviced_at':'2026-10-16 12:00','customer_name':'أحمد','fault_type':'مروحة','team':'BAISON','phone':'','notes':'تم الإصلاح'})
  self.store.remove_customer(id);self.assertEqual(len(self.store.list_services(id)),1)
 def test_events(self):
  l=parse_live(LIVE);identity={'mode':'L','firmware':'00085.04'}
  e=EventRecorder(self.store);e.capture(ID['QID'],identity,l,'100000000000000000000000000000000000')
  self.assertGreaterEqual(len(self.store.list_events(ID['QID'])),1)

if __name__=='__main__':unittest.main()
