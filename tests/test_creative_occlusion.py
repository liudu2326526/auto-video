import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from creative_agent import occlusion


class SimulationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.src = self.root/'source.mp4'; self.src.write_bytes(b'mock media')
        self.plan = self.root/'plan.json'
        self.value = {'schema':'platform_occlusion_simulation/v1','id':'fixture',
          'source':{'path':'source.mp4','sha256':occlusion.digest(self.src)},
          'profiles':[{'id':'phone','viewport':{'width':360,'height':640},'basis':'Test-only declared bottom mask, not platform spec.',
              'blocked_areas':[{'x':0,'y':540,'width':360,'height':100}]}],
          'regions':[{'id':'caption','in_frame':0,'out_frame':30,
              'rectangle':{'x':300,'y':1650,'width':600,'height':150},'observation':'Mock caption region'}],
          'anchor_frames':[0]}
        self.metadata={'streams':[{'codec_type':'video','width':1080,'height':1920,
            'avg_frame_rate':'30/1','r_frame_rate':'30/1','nb_read_frames':'30'}]}

    def validate(self):
        self.plan.write_text(json.dumps(self.value))
        with patch.object(occlusion,'probe',return_value=self.metadata):
            return occlusion.validate_simulation(self.plan)

    def test_contain_letterbox_mapping_and_touching_edges(self):
        self.assertEqual(occlusion.contain_rect((0,0,1080,1920),(1080,1920),(360,780)),(0,70,360,640))
        self.assertTrue(occlusion.overlaps((100,550,200,50),(0,540,360,100)))
        self.assertFalse(occlusion.overlaps((100,490,200,50),(0,540,360,100)))

    def test_explicit_masks_and_bound_source_required(self):
        self.assertEqual(self.validate()[2],(1080,1920,30))
        self.src.write_bytes(b'changed')
        with self.assertRaises(occlusion.EditError): self.validate()

    def test_unknown_fields_outside_rect_and_boolean_anchor_rejected(self):
        original=json.loads(json.dumps(self.value))
        self.value['approval']=True
        with self.assertRaises(occlusion.EditError): self.validate()
        self.value=json.loads(json.dumps(original)); self.value['anchor_frames']=[True]
        with self.assertRaises(occlusion.EditError): self.validate()
        self.value=json.loads(json.dumps(original)); self.value['profiles'][0]['blocked_areas'][0]['height']=300
        with self.assertRaises(occlusion.EditError): self.validate()

    def test_bad_region_time_and_missing_profiles_rejected(self):
        self.value['regions'][0]['out_frame']=31
        with self.assertRaises(occlusion.EditError): self.validate()
        self.value['regions'][0]['out_frame']=30; self.value['profiles']=[]
        with self.assertRaises(occlusion.EditError): self.validate()

    @unittest.skipUnless(importlib.util.find_spec('PIL') and shutil.which('ffmpeg'),'Needs existing Pillow/FFmpeg')
    def test_actual_mask_render_rejects_overwrite_and_has_no_phone_approval(self):
        subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','color=white:s=640x480:r=30:d=1',
            '-an','-c:v','libx264','-pix_fmt','yuv420p','-y',str(self.src)],check=True)
        self.value['source']['sha256']=occlusion.digest(self.src)
        self.value['regions'][0]['rectangle']={'x':0,'y':400,'width':640,'height':80}
        self.value['profiles'][0].update(viewport={'width':320,'height':240},blocked_areas=[{'x':0,'y':200,'width':320,'height':40}])
        self.plan.write_text(json.dumps(self.value))
        result=occlusion.simulate(self.plan,self.root/'attempt')
        self.assertTrue(result['regions'][0]['intersects_blocked_area'])
        self.assertFalse(result['actual_phone_test'])
        self.assertFalse(result['automatic_readability_or_privacy_approval'])
        from PIL import Image
        im=Image.open(result['images'][0]['path'])
        self.assertGreater(im.getpixel((100,210))[0],im.getpixel((100,210))[1]+30)
        with self.assertRaises(FileExistsError): occlusion.simulate(self.plan,self.root/'attempt')
        (self.root/'link').symlink_to(self.root/'attempt',target_is_directory=True)
        with self.assertRaises(occlusion.EditError): occlusion.simulate(self.plan,self.root/'link')


if __name__=='__main__': unittest.main()
