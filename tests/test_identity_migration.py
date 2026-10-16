import tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from storage import Store
from protocol import parse_eeprom_diagnostics

REAL='96342305101831'
ZERO='0000000000000000'

class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.db=Store(Path(self.tmp.name)/'test.db')
    def tearDown(self):
        self.db.close();self.tmp.cleanup()
    def test_reject_zero_ids(self):
        self.assertFalse(self.db.valid_identity(ZERO))
        self.assertTrue(self.db.valid_identity(REAL))
        with self.assertRaises(ValueError):self.db.ensure_device(ZERO)
    def test_legacy_relink_keeps_repair(self):
        self.db.register(ZERO,ZERO,'VMIII-4200','', 'زبون سابق')
        self.db.ensure_device(REAL,'VMIII-4200','00085.04')
        self.db.db.execute("INSERT INTO services(sticker_serial,serviced_at,customer_name,fault_type,team,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",(ZERO,'2026-10-16 02:00','زبون سابق','عطل','فريق','2026-10-16','2026-10-16'))
        self.db.db.commit()
        self.assertTrue(self.db.legacy_placeholder_has_data())
        self.assertEqual(self.db.relink_legacy_placeholder(REAL),1)
        self.assertFalse(self.db.legacy_placeholder_has_data())
        self.assertEqual(self.db.list_services(REAL)[0]['sticker_serial'],REAL)
        self.assertEqual(self.db.by_internal_id(REAL)['customer_name'],'زبون سابق')
    def test_eeprom_readonly_label(self):
        response=parse_eeprom_diagnostics(' '.join(['0']*18+['00']), 'EakxyzDbdjlnuv','0'*36)
        self.assertIn('مفعّل',response)
        self.assertIn('غير ظاهر',response)

if __name__=='__main__':unittest.main()
