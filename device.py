"""One read/write transaction at a time; Q commands read only."""
from protocol import parse_live, parse_identity
from hid_windows import HIDDevice, find_device_paths
from settings import packet_for_known_command

BASE=['QPI','QID','QMN','QVFW','QPIRI','QDI','QFLAG','QMOD','QPIGS','QPIWS']
OPTIONAL=['QMCHGCR','QMUCHGCR','QBEQI','QGMN','QET','QLT','QVFW3','QOPPT','QCHPT']

class Inverter:
    def __enter__(self):
        paths=find_device_paths()
        if len(paths)!=1:raise RuntimeError('يجب توصيل إنفرتر واحد فقط بالـ USB HID (0665:5161)')
        self.device=HIDDevice(paths[0]);return self
    def __exit__(self,*args):self.device.close()
    def read(self,commands):
        result={}
        for cmd in commands:
            try:result[cmd]=self.device.query(cmd)
            except Exception as e:result[cmd]='' ;result['ERROR_'+cmd]=str(e)
        return result
    def write(self,command):
        return self.device.send_setting_frame(packet_for_known_command(command))

def pull(full=False):
    with Inverter() as dev:
        return dev.read(BASE+OPTIONAL if full else ['QPI','QID','QVFW','QMN','QMOD','QPIGS','QPIWS'])
