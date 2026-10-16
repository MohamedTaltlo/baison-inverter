import sys,tempfile,unittest
from pathlib import Path
from datetime import datetime,timedelta
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from storage import Store

class Retention(unittest.TestCase):
 def test_does_not_erase_maintenance(self):
  with tempfile.TemporaryDirectory() as t:
   s=Store(Path(t)/'db.sqlite3');qid='96342305101831'
   s.ensure_device(qid,'VMIII-4200','00085.04')
   s.save_service({'sticker_serial':qid,'serviced_at':'2026-10-16 13:00','customer_name':'Test',
    'fault_type':'Fan','team':'Center','phone':'','notes':''})
   now=datetime(2026,10,16,13)
   s.record_event(qid,'temp','info','old',( {'test':True}),timestamp=(now-timedelta(days=20)).isoformat(timespec='seconds'))
   s.record_snapshot(qid,{'v':1},timestamp=(now-timedelta(days=5)).isoformat(timespec='seconds'))
   s.record_event(qid,'temp','info','new',timestamp=now.isoformat(timespec='seconds'))
   removed=s.prune_observations(now=now)
   self.assertEqual(removed,{'events':1,'snapshots':1})
   self.assertEqual(s.observation_counts(qid)['events'],1)
   s.clear_observations(qid)
   self.assertEqual(len(s.list_services(qid)),1)
   self.assertGreater(s.db.execute('SELECT COUNT(*) FROM audit').fetchone()[0],0)
   s.close()
if __name__=='__main__':unittest.main()
