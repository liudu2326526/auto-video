import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from creative_agent.montage import prepare_montage, validate_montage
from creative_agent.motion import FONT
from editing_agent.core import EditError, digest


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg required')
class ResultMontageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory(); cls.root = Path(cls.tmp.name)
        for name,color,size,fps in [('base','0x172640','640x480',30),('red','red','320x240',30),
                                     ('blue','blue','320x240',30),('wrong-fps','red','320x240',25)]:
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=c={color}:s={size}:r={fps}:d=1',
                            '-an','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(cls.root/(name+'.mp4'))],
                           check=True,capture_output=True,timeout=30)
        def binding(name):
            p=cls.root/(name+'.mp4');return {'path':str(p),'sha256':digest(p)}
        cls.template={'schema':'result_excerpt_montage/v1','id':'test-montage','base':binding('base'),'base_frame':5,
            'annotation_area':{'x':320,'y':80,'width':320,'height':300},
            'evidence_areas':[{'x':0,'y':0,'width':300,'height':480}],
            'claim':'editorial_preview_not_execution','source_note':'Generated colour controls; not product evidence.',
            'panels':[{'source':binding(c),'in_frame':0,'out_frame':15,
                       'crop':{'x':0,'y':0,'width':320,'height':240},'observed_content':c+' control'} for c in ['red','blue']]}

    @classmethod
    def tearDownClass(cls): cls.tmp.cleanup()

    def setUp(self):
        self.case=Path(tempfile.mkdtemp(dir=self.root));self.plan=self.case/'plan.json';self.data=copy.deepcopy(self.template)

    def write(self): self.plan.write_text(json.dumps(self.data));return self.plan

    def rejects(self):
        with self.assertRaises(EditError):validate_montage(self.write())

    def test_source_hash_must_match(self): self.data['panels'][0]['source']['sha256']='0'*64;self.rejects()
    def test_unknown_fields_rejected(self): self.data['assume_approval']=True;self.rejects()
    def test_claim_cannot_be_real_execution(self): self.data['claim']='new_live_demo';self.rejects()
    def test_annotation_cannot_cover_input_evidence(self): self.data['annotation_area']['x']=200;self.rejects()
    def test_source_crop_cannot_escape(self): self.data['panels'][0]['crop']['width']=322;self.rejects()
    def test_odd_crop_rejected(self): self.data['panels'][0]['crop']['width']=319;self.rejects()
    def test_frame_range_cannot_escape(self): self.data['panels'][0]['out_frame']=31;self.rejects()
    def test_flash_too_short_rejected(self): self.data['panels'][0]['out_frame']=14;self.rejects()
    def test_duration_cannot_silently_change(self): self.data['panels'][0]['out_frame']=16;self.rejects()
    def test_at_least_two_actual_excerpts(self): self.data['panels']=self.data['panels'][:1];self.rejects()
    def test_observation_required(self): self.data['panels'][0]['observed_content']='';self.rejects()
    def test_wrong_fps_rejected(self):
        p=self.root/'wrong-fps.mp4';self.data['panels'][0]['source']={'path':str(p),'sha256':digest(p)};self.rejects()

    @unittest.skipUnless(importlib.util.find_spec('PIL') and FONT.is_file(), 'Existing Pillow/Mac display font required')
    def test_render_first_frame_result_order_protected_region_and_silence(self):
        result=prepare_montage(self.write(),self.case/'output')
        self.assertEqual(result['frames'],30);self.assertEqual(result['audio_tracks'],0)
        self.assertFalse(result['final_video_rendered']);self.assertFalse(result['automatic_semantic_or_rights_approval'])
        self.assertEqual([(x['output_in_frame'],x['output_out_frame']) for x in result['mapping']],[(0,15),(15,30)])
        raw=subprocess.run(['ffmpeg','-v','error','-i',result['path'],'-f','rawvideo','-pix_fmt','rgb24','pipe:1'],
                           check=True,capture_output=True,timeout=30).stdout
        self.assertEqual(len(raw),640*480*3*30)
        def pixel(frame,x,y):
            at=(frame*640*480+y*640+x)*3;return tuple(raw[at:at+3])
        first=pixel(0,480,260);last=pixel(29,480,260)
        self.assertGreater(first[0],first[2]+150);self.assertGreater(last[2],last[0]+150)
        for f in [0,14,15,29]:
            self.assertTrue(all(abs(a-b)<8 for a,b in zip(pixel(f,100,200),(23,38,64))))

    @unittest.skipUnless(importlib.util.find_spec('PIL') and FONT.is_file(), 'Existing Pillow/Mac display font required')
    def test_existing_output_not_overwritten(self):
        out=self.case/'output';out.mkdir();sentinel=out/'keep.txt';sentinel.write_text('keep')
        with self.assertRaises(FileExistsError):prepare_montage(self.write(),out)
        self.assertEqual(sentinel.read_text(),'keep')


if __name__=='__main__':unittest.main()
