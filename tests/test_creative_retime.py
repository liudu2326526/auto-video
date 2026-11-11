import copy
import importlib.util
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from creative_agent import retime


class RetimeValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.source=self.root/'synthetic.mp4'
        self.source.write_bytes(b'Synthetic mocked metadata, not a product execution')
        self.path=self.root/'plan.json'
        self.plan={'schema':'editorial_retime/v1','id':'fixture',
            'source':{'path':str(self.source),'sha256':retime.digest(self.source)},
            'segments':[{'source_in_frame':0,'source_out_frame':2,'output_frames':2},
                        {'source_in_frame':3,'source_out_frame':5,'output_frames':2},
                        {'source_in_frame':5,'source_out_frame':6,'output_frames':4}],
            'notice_area':{'x':10,'y':400,'width':620,'height':60},
            'evidence_areas':[{'x':0,'y':0,'width':640,'height':350}]}
        self.media={'streams':[{'codec_type':'video','width':640,'height':480,
            'avg_frame_rate':'30/1','r_frame_rate':'30/1','nb_read_frames':'6'}]}
        self.stamps={'frames':[{'best_effort_timestamp_time':str(i/30)} for i in range(6)]}

    def check(self):
        self.path.write_text(json.dumps(self.plan))
        with patch.object(retime,'probe',return_value=self.media),patch.object(retime,'run',return_value=json.dumps(self.stamps)):
            return retime.validate_retime(self.path)

    def test_explicit_chronological_mapping_and_truthful_notice(self):
        _,_,size,_,notice=self.check()
        self.assertEqual(size,(640,480,8));self.assertIn('跳剪',notice);self.assertIn('停帧',notice)

    def test_reverse_speed_change_cross_bounds_bool_and_noop_rejected(self):
        original=copy.deepcopy(self.plan)
        for segments in [[],[{'source_in_frame':0,'source_out_frame':7,'output_frames':7}],
            [{'source_in_frame':0,'source_out_frame':6,'output_frames':3}],
            [{'source_in_frame':0,'source_out_frame':6,'output_frames':6}],
            [{'source_in_frame':True,'source_out_frame':2,'output_frames':1}],
            [{'source_in_frame':4,'source_out_frame':6,'output_frames':2},{'source_in_frame':1,'source_out_frame':2,'output_frames':1}],
            [{'source_in_frame':0,'source_out_frame':1,'output_frames':301}]]:
            self.plan=copy.deepcopy(original);self.plan['segments']=segments
            with self.subTest(segments=segments),self.assertRaises(retime.EditError):self.check()

    def test_missing_notice_overlap_unknown_fields_or_source_change_rejected(self):
        original=copy.deepcopy(self.plan)
        changes=[('notice_area',{'x':10,'y':300,'width':620,'height':60}),
                 ('evidence_areas',[]),('unknown',True),('source',{'path':str(self.source),'sha256':'0'*64})]
        for key,value in changes:
            self.plan=copy.deepcopy(original);self.plan[key]=value
            with self.subTest(key=key),self.assertRaises(retime.EditError):self.check()
        self.plan=copy.deepcopy(original);del self.plan['notice_area']
        with self.assertRaises(retime.EditError):self.check()

    def test_audio_vfr_and_timestamp_gap_rejected(self):
        original=copy.deepcopy(self.media)
        self.media['streams'].append({'codec_type':'audio'})
        with self.assertRaises(retime.EditError):self.check()
        self.media=copy.deepcopy(original);self.media['streams'][0]['r_frame_rate']='60/1'
        with self.assertRaises(retime.EditError):self.check()
        self.media=original;self.stamps['frames'][2]['best_effort_timestamp_time']='.9'
        with self.assertRaises(retime.EditError):self.check()


@unittest.skipUnless(importlib.util.find_spec('PIL') and retime.FONT.is_file() and shutil.which('ffmpeg'),
                     'Actual local render needs existing Pillow, macOS font and FFmpeg')
class RealRetimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve();self.source=self.root/'synthetic-color.mp4'
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=red:s=640x480:r=30:d=0.1',
            '-f','lavfi','-i','color=blue:s=640x480:r=30:d=0.1','-filter_complex','[0:v][1:v]concat=n=2:v=1:a=0[v]',
            '-map','[v]','-an','-c:v','libx264','-pix_fmt','yuv420p','-n',str(self.source)],check=True)
        self.plan=self.root/'plan.json'
        self.plan.write_text(json.dumps({'schema':'editorial_retime/v1','id':'actual-color-fixture',
            'source':{'path':str(self.source),'sha256':retime.digest(self.source)},
            'segments':[{'source_in_frame':0,'source_out_frame':2,'output_frames':2},
                        {'source_in_frame':3,'source_out_frame':5,'output_frames':2},
                        {'source_in_frame':5,'source_out_frame':6,'output_frames':4}],
            'notice_area':{'x':10,'y':400,'width':620,'height':60},
            'evidence_areas':[{'x':0,'y':0,'width':640,'height':350}]}))

    def test_actual_cfr_mapping_hold_and_evidence_color_without_audio(self):
        from PIL import Image
        result=retime.prepare_retime(self.plan,self.root/'attempt')
        self.assertEqual(result['frames'],8);self.assertEqual(result['audio_tracks'],0)
        self.assertFalse(result['automatic_semantic_or_rights_approval'])
        for index,color in [(0,'red'),(1,'red'),(2,'blue'),(4,'blue'),(7,'blue')]:
            raw=subprocess.check_output(['ffmpeg','-v','error','-i',result['path'],'-vf',f'select=eq(n\\,{index})',
                '-frames:v','1','-f','image2pipe','-vcodec','png','-'])
            rgb=Image.open(io.BytesIO(raw)).convert('RGB').getpixel((320,150))
            self.assertGreater(rgb[0 if color=='red' else 2],240)
            self.assertLess(rgb[2 if color=='red' else 0],12)
        with self.assertRaises(FileExistsError):retime.prepare_retime(self.plan,self.root/'attempt')
        alias=self.root/'alias';alias.symlink_to(self.root/'attempt',target_is_directory=True)
        with self.assertRaises(retime.EditError):retime.prepare_retime(self.plan,alias/'unsafe')

    def test_real_encoder_timeout_retains_failure_without_final_file(self):
        real_run=retime.run
        def bounded(args,timeout=120):
            return real_run(args,timeout=.001 if args[0]=='ffmpeg' else timeout)
        with patch.object(retime,'run',side_effect=bounded),self.assertRaises(retime.EditError):
            retime.prepare_retime(self.plan,self.root/'failed')
        result=json.loads((self.root/'failed/result.json').read_text())
        self.assertFalse(result['final_approved'])
        self.assertFalse((self.root/'failed/retimed.mp4').exists())
