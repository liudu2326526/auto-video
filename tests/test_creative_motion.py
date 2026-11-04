import copy
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from creative_agent import motion


class MotionValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.source = self.root / 'synthetic.mp4'; self.source.write_bytes(b'mocked media, not a product test')
        self.path = self.root / 'plan.json'
        self.plan = {'schema': 'original_editorial_motion/v1', 'id': 'fixture',
                     'source': {'path': str(self.source), 'sha256': motion.digest(self.source)},
                     'annotation_area': {'x': 10, 'y': 290, 'width': 620, 'height': 180},
                     'evidence_areas': [{'x': 0, 'y': 0, 'width': 640, 'height': 260}],
                     'kind': 'paper_stack', 'items': ['周报', '再改一版', '先别急交']}
        self.media = {'streams': [{'codec_type': 'video', 'width': 640, 'height': 480,
                                  'avg_frame_rate': '30/1', 'r_frame_rate': '30/1', 'nb_read_frames': '30'}]}
        self.stamps = {'frames': [{'best_effort_timestamp_time': str(i / 30)} for i in range(30)]}
        self.write()

    def write(self): self.path.write_text(json.dumps(self.plan, ensure_ascii=False))
    def validate(self):
        self.write()
        with patch.object(motion, 'probe', return_value=self.media), patch.object(motion, 'run', return_value=json.dumps(self.stamps)):
            return motion.validate_motion(self.path)

    def test_declared_disjoint_geometry_accepted_without_approval(self):
        p, _, size, rect = self.validate()
        self.assertEqual(size, (640, 480, 30)); self.assertEqual(rect, (10, 290, 620, 180))
        self.assertNotIn('approved', p)

    def test_overlap_unknown_fields_invalid_geometry_and_text_rejected(self):
        variants = []
        p = copy.deepcopy(self.plan); p['annotation_area']['y'] = 250; variants.append(p)
        p = copy.deepcopy(self.plan); p['annotation_area']['x'] = True; variants.append(p)
        p = copy.deepcopy(self.plan); p['annotation_area']['width'] = 640; variants.append(p)
        p = dict(self.plan, score=100); variants.append(p)
        p = dict(self.plan, items=['one', 'two', 'control\ntext']); variants.append(p)
        p = dict(self.plan, kind='downloaded_stock'); variants.append(p)
        p = dict(self.plan, evidence_areas=[]); variants.append(p)
        for p in variants:
            self.plan = p
            with self.subTest(plan=p), self.assertRaises(motion.EditError): self.validate()

    def test_changed_source_audio_wrong_rate_count_and_timestamp_gap_rejected(self):
        self.plan['source']['sha256'] = '0' * 64
        with self.assertRaises(motion.EditError): self.validate()
        self.plan['source']['sha256'] = motion.digest(self.source)
        original = copy.deepcopy(self.media)
        self.media['streams'].append({'codec_type': 'audio'})
        with self.assertRaises(motion.EditError): self.validate()
        self.media = copy.deepcopy(original); self.media['streams'][0]['r_frame_rate'] = '60/1'
        with self.assertRaises(motion.EditError): self.validate()
        self.media = copy.deepcopy(original); self.media['streams'][0]['nb_read_frames'] = '301'
        with self.assertRaises(motion.EditError): self.validate()
        self.media = original; self.stamps['frames'][12]['best_effort_timestamp_time'] = '.9'
        with self.assertRaises(motion.EditError): self.validate()

    def test_no_overwrite_symlink_or_parent_traversal(self):
        dest = self.root / 'existing'; dest.mkdir()
        with self.assertRaises(FileExistsError): motion.private_new_directory(dest)
        alias = self.root / 'alias'; alias.symlink_to(dest, target_is_directory=True)
        with self.assertRaises(motion.EditError): motion.private_new_directory(alias / 'new')
        with self.assertRaises(motion.EditError): motion.private_new_directory(self.root / 'missing' / '..' / 'new')
        p = motion.private_new_directory(self.root / 'new'); self.assertEqual(p.stat().st_mode & 0o777, 0o700)

    def test_office_scene_requires_legible_bounded_space_and_disjoint_source(self):
        self.plan['kind'] = 'office_pause'
        with self.assertRaises(motion.EditError): self.validate()
        self.media['streams'][0].update(width=1080, height=1920)
        self.plan['annotation_area'] = {'x':60,'y':650,'width':960,'height':512}
        self.plan['evidence_areas'] = [{'x':60,'y':400,'width':960,'height':200}]
        self.assertEqual(self.validate()[2], (1080,1920,30))
        original = self.plan['items']
        self.plan['items'] = ['verylong', 'short', 'short']
        with self.assertRaises(motion.EditError): self.validate()
        self.plan['items'] = original
        self.plan['evidence_areas'][0]['height'] = 300
        with self.assertRaises(motion.EditError): self.validate()

    def test_reaction_shares_office_bounds_and_label_limits(self):
        self.plan['kind'] = 'office_reaction'
        with self.assertRaises(motion.EditError): self.validate()
        self.media['streams'][0].update(width=1080, height=1920)
        self.plan['annotation_area'] = {'x':60,'y':650,'width':960,'height':512}
        self.plan['evidence_areas'] = [{'x':60,'y':400,'width':960,'height':200}]
        self.assertEqual(self.validate()[2],(1080,1920,30))
        self.plan['items'] = ['label too long','stop','check']
        with self.assertRaises(motion.EditError): self.validate()


@unittest.skipUnless(importlib.util.find_spec('PIL') and motion.FONT.is_file() and shutil.which('ffmpeg'),
                     'Actual local renderer requires Pillow, reviewed macOS font and FFmpeg')
class RealMotionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve(); self.src = self.root / 'silent-fixture.mp4'
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=0x405060:s=640x480:r=30:d=1',
                        '-an', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-n', str(self.src)], check=True)
        self.plan = self.root / 'motion.json'
        self.plan.write_text(json.dumps({'schema': 'original_editorial_motion/v1', 'id': 'real-fixture',
            'source': {'path': str(self.src), 'sha256': motion.digest(self.src)},
            'annotation_area': {'x': 10, 'y': 290, 'width': 620, 'height': 180},
            'evidence_areas': [{'x': 0, 'y': 0, 'width': 640, 'height': 260}],
            'kind': 'review_advice', 'items': ['数字', '措辞', '完成程度']}, ensure_ascii=False))

    def test_real_silent_render_duration_dynamic_frames_and_unobscured_evidence(self):
        from PIL import Image
        import io
        result = motion.render_motion(self.plan, self.root / 'attempt')
        self.assertEqual(result['frames'], 30); self.assertEqual(result['audio_tracks'], 0)
        self.assertFalse(result['automatic_semantic_or_rights_approval'])
        imgs = []
        for index in (0, 29):
            raw = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', result['path'], '-vf',
                f'select=eq(n\\,{index})', '-frames:v', '1', '-f', 'image2pipe', '-vcodec', 'png', '-'])
            imgs.append(Image.open(io.BytesIO(raw)).convert('RGB'))
        self.assertNotEqual(imgs[0].crop((10, 290, 630, 470)).tobytes(), imgs[1].crop((10, 290, 630, 470)).tobytes())
        # A synthetic evidence field remains the source color, rather than receiving the overlay.
        for im in imgs:
            self.assertTrue(all(abs(c-e) < 8 for c, e in zip(im.getpixel((320, 130)), (64, 80, 96))))
        with self.assertRaises(FileExistsError): motion.render_motion(self.plan, self.root / 'attempt')

    def test_cancel_reaps_own_encoder_and_never_marks_final_approved(self):
        real_draw = motion.illustration; real_popen = subprocess.Popen; calls = 0; processes = []
        def drawing(*args):
            nonlocal calls
            calls += 1
            if calls == 2: raise KeyboardInterrupt('Controlled local fixture cancellation')
            return real_draw(*args)
        def popen(*args, **kwargs):
            p = real_popen(*args, **kwargs)
            if kwargs.get('stdin') is subprocess.PIPE: processes.append(p)
            return p
        with patch.object(motion, 'illustration', side_effect=drawing), patch.object(motion.subprocess, 'Popen', side_effect=popen):
            with self.assertRaises(KeyboardInterrupt): motion.render_motion(self.plan, self.root / 'cancelled')
        self.assertTrue(processes); self.assertTrue(all(p.poll() is not None for p in processes))
        result = json.loads((self.root / 'cancelled/result.json').read_text())
        self.assertFalse(result['final_approved']); self.assertFalse((self.root / 'cancelled/original-motion.mp4').exists())

    def test_original_office_action_moves_out_stops_and_returns_without_stock(self):
        images = [motion.illustration('office_pause', ['周报','先别发','先核对'],960,512,i,60)
                  for i in (0,29,59)]
        # Paper is in the right-hand stop region in the middle phase, then back.
        self.assertEqual(images[1].getpixel((850,350)), (245,235,207,255))
        for im in (images[0],images[2]):
            self.assertNotEqual(im.getpixel((850,350)), (245,235,207,255))
        self.assertEqual(images[0].getpixel((640,380)), images[2].getpixel((640,380)))
        self.assertEqual(images[0].size,(960,512))
        self.assertNotEqual(images[0].tobytes(),images[1].tobytes())
        plan = json.loads(self.plan.read_text())
        plan.update(kind='office_pause', items=['周报','先别发','先核对'],
                    annotation_area={'x':10,'y':170,'width':620,'height':300},
                    evidence_areas=[{'x':0,'y':0,'width':640,'height':160}])
        self.plan.write_text(json.dumps(plan,ensure_ascii=False))
        result = motion.render_motion(self.plan,self.root/'office-action')
        self.assertEqual(result['frames'],30)
        self.assertEqual(result['audio_tracks'],0)
        self.assertEqual(result['full_decode_exit'],0)
        self.assertFalse(result['external_overlay_assets_used'])
        self.assertFalse(result['automatic_semantic_or_rights_approval'])

    def test_watchdog_kills_encoder_during_a_stalled_frame_producer(self):
        real_draw = motion.illustration; calls = 0
        def stalled(*args):
            nonlocal calls
            calls += 1
            if calls == 2: time.sleep(2)  # controlled fixture stall; no desktop or capture
            return real_draw(*args)
        with patch.object(motion, 'illustration', side_effect=stalled):
            with self.assertRaises((OSError, motion.EditError)):
                motion.render_motion(self.plan, self.root / 'timeout', timeout=1)
        result = json.loads((self.root / 'timeout/result.json').read_text())
        self.assertFalse(result['final_approved'])
        self.assertFalse((self.root / 'timeout/original-motion.mp4').exists())

    def test_reaction_pose_changes_face_and_stop_hand_without_product_claim(self):
        from PIL import ImageChops
        poses=[motion.illustration('office_reaction',['周报','先别发','先核对'],960,512,i,45)
               for i in (0,22,44)]
        for a,b in zip(poses,poses[1:]):
            self.assertIsNotNone(ImageChops.difference(a.convert('RGB'),b.convert('RGB')).crop((270,85,470,335)).getbbox())
        old=motion.illustration('office_pause',['周报','先别发','先核对'],960,512,22,45)
        self.assertIsNotNone(ImageChops.difference(old.convert('RGB'),poses[1].convert('RGB')).crop((270,135,325,325)).getbbox())
        plan=json.loads(self.plan.read_text());plan.update(kind='office_reaction',items=['周报','先别发','先核对'],
            annotation_area={'x':10,'y':170,'width':620,'height':300},evidence_areas=[{'x':0,'y':0,'width':640,'height':160}])
        self.plan.write_text(json.dumps(plan,ensure_ascii=False))
        result=motion.render_motion(self.plan,self.root/'reaction')
        self.assertEqual((result['frames'],result['audio_tracks'],result['full_decode_exit']),(30,0,0))
        self.assertFalse(result['automatic_semantic_or_rights_approval'])


if __name__ == '__main__': unittest.main()
