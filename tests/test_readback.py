import json
from pathlib import Path
import tempfile
import unittest
import zlib
from tools.quest3.score import readback_metadata,score

class ReadbackTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.addCleanup(self.directory.cleanup)
        self.path=Path(self.directory.name)/'out.rgba';self.pixels=bytes(range(32))
        self.metadata={'schema_version':1,'readback_complete':True,'producer_complete':True,
            'readback_method':'gles_external_rgba8','width':4,'height':2,'bytes':32,
            'source_ahb_usage':0x10000300,'flip_y':False,'row_order':'texture_v0_first',
            'raw_rgba_crc32':f'{zlib.crc32(self.pixels):08x}'}
    def save(self):Path(str(self.path)+'.json').write_text(json.dumps(self.metadata),encoding='utf-8')
    def test_gpu_only_allocation_uses_consumer(self):
        self.save();self.assertEqual(readback_metadata(self.path,self.pixels,4,2)['status'],'completed_consumer_readback')
    def test_stale_or_incomplete_dump_is_rejected(self):
        self.metadata['readback_complete']=False;self.save()
        with self.assertRaises(ValueError):readback_metadata(self.path,self.pixels,4,2,True)
        self.metadata['readback_complete']=True;self.metadata['producer_complete']=False;self.save()
        with self.assertRaises(ValueError):readback_metadata(self.path,self.pixels,4,2)
    def test_changed_bytes_or_shape_cannot_reuse_sidecar(self):
        self.save()
        with self.assertRaises(ValueError):readback_metadata(self.path,bytes([255])+self.pixels[1:],4,2)
        with self.assertRaises(ValueError):readback_metadata(self.path,self.pixels,2,4)
    def test_cpu_lock_success_does_not_override_allocation(self):
        self.metadata.update(readback_method='cpu_usage_compatible',row_order='buffer_row0_first');self.save()
        with self.assertRaises(ValueError):readback_metadata(self.path,self.pixels,4,2)
        self.metadata['source_ahb_usage']|=3;self.save()
        self.assertEqual(readback_metadata(self.path,self.pixels,4,2)['status'],'completed_consumer_readback')
    def test_orientation_and_legacy_provenance_are_explicit(self):
        with self.assertRaises(ValueError):readback_metadata(self.path,self.pixels,4,2)
        self.assertEqual(readback_metadata(self.path,self.pixels,4,2,True)['status'],'unverified')
        self.metadata['flip_y']=True;self.save()
        with self.assertRaises(ValueError):readback_metadata(self.path,self.pixels,4,2)
        self.metadata['row_order']='texture_v1_first';self.save()
        self.assertEqual(readback_metadata(self.path,self.pixels,4,2)['status'],'completed_consumer_readback')
    def test_invalid_dimensions_do_not_create_scores(self):
        with self.assertRaises(ValueError):score(b'',b'',0,2)
        with self.assertRaises(ValueError):score(b'',b'',-1,2)

if __name__=='__main__':unittest.main()
