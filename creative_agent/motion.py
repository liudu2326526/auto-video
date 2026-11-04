"""Bounded original editorial illustrations, never a third-party asset clearance.

Requires locally installed Pillow and FFmpeg. Only this explicit CLI operation
renders; the review commands remain dependency-free apart from FFmpeg.
"""
import json
import math
import os
from pathlib import Path
import subprocess
import threading

from editing_agent.core import EditError, digest, fields, identity, integer, read_json, run
from .core import bound_file, save_new

FONT = Path('/System/Library/Fonts/STHeiti Medium.ttc')


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                           '-count_frames', '-show_streams', '-show_format', '-of', 'json', str(path)]))


def rectangle(row, width, height):
    fields(row, ['x', 'y', 'width', 'height'])
    x = integer(row['x'], 0, width); y = integer(row['y'], 0, height)
    w = integer(row['width'], 1, width); h = integer(row['height'], 1, height)
    if x + w > width or y + h > height:
        raise EditError('Rectangle outside source frame')
    return x, y, w, h


def overlaps(a, b):
    x, y, w, h = a; xx, yy, ww, hh = b
    return x < xx + ww and xx < x + w and y < yy + hh and yy < y + h


def validate_motion(path):
    pp = Path(path).resolve(); p = read_json(pp)
    fields(p, ['schema', 'id', 'source', 'annotation_area', 'evidence_areas', 'kind', 'items'])
    if p['schema'] != 'original_editorial_motion/v1':
        raise EditError('Unsupported motion schema')
    identity(p['id']); src = bound_file(p['source'], pp.parent)
    if p['kind'] not in {'paper_stack', 'review_advice', 'office_pause'}:
        raise EditError('Only original geometric illustration types supported')
    if (not isinstance(p['items'], list) or len(p['items']) != 3
            or any(not isinstance(x, str) or not x.strip() or len(x) > 10
                   or any(ord(c) < 32 for c in x) for x in p['items'])):
        raise EditError('Exactly three short editorial labels required')
    if p['kind'] == 'office_pause' and (len(p['items'][0]) > 4 or any(len(x) > 8 for x in p['items'][1:])):
        raise EditError('Office paper label at most four characters; action labels at most eight')
    m = probe(src); vs = [s for s in m['streams'] if s['codec_type'] == 'video']
    if len(vs) != 1 or any(s['codec_type'] == 'audio' for s in m['streams']):
        raise EditError('Source must have one silent video track')
    v = vs[0]; w = integer(v['width'], 240, 3840); h = integer(v['height'], 240, 3840)
    n = integer(int(v.get('nb_read_frames', -1)), 1, 300)
    if w % 2 or h % 2 or (v.get('avg_frame_rate'), v.get('r_frame_rate')) != ('30/1', '30/1'):
        raise EditError('Source must have even dimensions and CFR30')
    stamps = json.loads(run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-select_streams', 'v:0',
                             '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(src)]))
    frames = stamps.get('frames', [])
    if len(frames) != n or any(not math.isfinite(float(f.get('best_effort_timestamp_time', 'nan')))
                              or abs(float(f['best_effort_timestamp_time']) - i / 30) > .002
                              for i, f in enumerate(frames)):
        raise EditError('Source timestamps are not contiguous zero-based CFR30')
    area = rectangle(p['annotation_area'], w, h)
    if not 300 <= area[2] <= 1024 or not 160 <= area[3] <= 512:
        raise EditError('Annotation rectangle must be 300..1024 by 160..512 pixels')
    if p['kind'] == 'office_pause' and (area[2] < 600 or area[3] < 300):
        raise EditError('Office action requires at least 600 by 300 pixels')
    if not isinstance(p['evidence_areas'], list) or not 1 <= len(p['evidence_areas']) <= 12:
        raise EditError('Explicit bounded evidence areas required')
    for e in p['evidence_areas']:
        if overlaps(area, rectangle(e, w, h)):
            raise EditError('Editorial illustration overlaps declared evidence')
    return p, src, (w, h, n), area


def illustration(kind, items, width, height, index, frames):
    # No external images, emoji glyphs, stock, URLs, or imitation product controls.
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise EditError('This operation requires an already installed Pillow; nothing installed automatically') from exc
    if not FONT.is_file():
        raise EditError('Reviewed macOS STHeiti font unavailable')
    im = Image.new('RGBA', (width, height), '#172640'); d = ImageDraw.Draw(im)
    scale = min(width / 960, height / 300)
    def font(size): return ImageFont.truetype(str(FONT), max(12, round(size * scale)))
    def text(x, y, label, size=38, fill='#E9EDF5'):
        d.text((round(x * width / 960), round(y * height / 300)), label, font=font(size), fill=fill)
    text(24, 12, '情境动画 · 非产品界面' if kind == 'office_pause' else '情境示意 · 编辑提示', 30, '#9EABC1')
    phase = index / max(1, frames - 1)
    if kind == 'office_pause':
        # Original abstract office character: a paper moves out, is stopped,
        # then returns for checking. No stock, real likeness, product controls,
        # factual document contents, or simulated product execution.
        sx, sy = width / 960, height / 512
        def point(x, y): return round(x * sx), round(y * sy)
        def box(x, y, xx, yy): return (*point(x, y), *point(xx, yy))
        def line(coords, fill, thickness):
            d.line([point(x, y) for x, y in coords], fill=fill,
                   width=max(1, round(thickness * min(sx, sy))), joint='curve')
        def label(x, y, value, size, color):
            d.text(point(x, y), value,
                   font=ImageFont.truetype(str(FONT), max(12, round(size * min(sx, sy)))), fill=color)
        stop = .35 <= phase < .65
        movement = phase / .35 if phase < .35 else (1 if stop else max(0, (1-phase) / .35))
        doc_x = 535 + 210 * movement
        # Desk and blank generic monitor.
        d.rounded_rectangle(box(35, 418, 925, 448), radius=max(2, round(10*sx)), fill='#61758B')
        line([(90, 448), (90, 500)], '#61758B', 14)
        line([(865, 448), (865, 500)], '#61758B', 14)
        d.rounded_rectangle(box(65, 190, 280, 350), radius=max(2, round(14*sx)), fill='#39536A')
        d.rounded_rectangle(box(80, 205, 265, 333), radius=max(2, round(6*sx)), fill='#75DBCE')
        line([(172, 350), (172, 400), (120, 400), (225, 400)], '#61758B', 12)
        # Explicitly synthetic, geometric person; no particular person's face.
        d.ellipse(box(345, 100, 465, 220), fill='#FFCE92')
        d.polygon([point(x,y) for x,y in [(355,230),(445,230),(490,418),(310,418)]], fill='#648DED')
        eye_y = 154 if stop else 161
        for x in (383, 425): d.ellipse(box(x-5, eye_y-5, x+5, eye_y+5), fill='#172640')
        line([(390, 193), (422, 193 if not stop else 183)], '#684936', 4)
        # The moving paper is generic, not the source/Gamma report.
        d.rounded_rectangle(box(doc_x, 238, doc_x+135, 397), radius=max(2, round(9*sx)), fill='#F5EBCF')
        label(doc_x+12, 249, items[0], 24, '#28384B')
        for yy in (305, 331, 357): line([(doc_x+17, yy), (doc_x+112, yy)], '#8191A2', 4)
        hand_x = doc_x - 12
        line([(440, 270), (505, 323), (hand_x, 289)], '#FFCE92', 24)
        d.ellipse(box(hand_x-20, 265, hand_x+20, 310), fill='#FFCE92')
        for k in range(4): line([(hand_x-12+k*8, 279), (hand_x-14+k*8, 250)], '#FFCE92', 7)
        label(555, 80, items[1] if phase < .65 else items[2], 45, '#FFB2AC' if phase < .65 else '#D9FF60')
        if stop:
            for x in (842, 875): d.rounded_rectangle(box(x,145,x+15,207),radius=max(1,round(3*sx)),fill='#FFB2AC')
        elif phase >= .65:
            line([(840, 182), (750, 182), (775, 165), (750, 182), (775, 199)], '#75DBCE', 7)
    elif kind == 'paper_stack':
        # Three original paper cards gently enter; filenames are editorial jokes.
        for i, label in enumerate(items):
            ease = min(1, max(0, phase * 3 - i * .45))
            x = (40 + i * 245 + (1 - ease) * 80) * width / 960
            y = (70 + i * 17) * height / 300
            d.rounded_rectangle((round(x), round(y), round(x + 325 * width / 960),
                                 round(y + 150 * height / 300)), radius=max(5, round(9 * scale)),
                                fill=['#DAF8EE', '#FFE2AF', '#FFB2AC'][i])
            d.text((round(x + 18 * scale), round(y + 22 * scale)), label,
                   font=font(36), fill='#101827')
            for k in range(2):
                yy = round(y + (88 + 18 * k) * height / 300)
                d.line((round(x + 20 * scale), yy, round(x + 230 * scale), yy), fill='#708097', width=max(1, round(3 * scale)))
    else:
        # Moving highlight means advice to check, never an assertion of completion.
        active = min(2, index * 3 // max(1, frames))
        for i, label in enumerate(items):
            y = round((70 + i * 70) * height / 300)
            color = '#D9FF60' if i == active else '#9EABC1'
            radius = max(5, round(12 * scale)); cx = round(45 * width / 960)
            d.ellipse((cx-radius, y-radius, cx+radius, y+radius), fill=color)
            d.text((round(85 * width / 960), y-round(21 * scale)), label, font=font(42), fill=color)
    return im


def private_new_directory(output):
    raw = Path(output).absolute()
    # Resolve only macOS OS-owned aliases; reject user symlink ancestors.
    for alias in ('/var', '/tmp'):
        anchor = Path(alias)
        if raw.is_relative_to(anchor) and anchor.is_symlink() and anchor.resolve() == Path('/private' + alias):
            raw = Path('/private' + alias) / raw.relative_to(anchor)
    if '..' in raw.parts or any(x.is_symlink() for x in [raw, *raw.parents]):
        raise EditError('Symlink/traversal output path forbidden')
    raw.mkdir(mode=0o700, exist_ok=False)
    return raw


def render_motion(plan, output, timeout=90):
    if type(timeout) is not int or not 1 <= timeout <= 120:
        raise EditError('Render timeout outside 1..120 seconds')
    p, src, (w, h, n), area = validate_motion(plan)
    # Check the optional renderer dependency before creating an attempt.
    illustration(p['kind'], p['items'], area[2], area[3], 0, n)
    out = private_new_directory(output); process = timer = None
    save_new(out / 'request.json', {'schema': 'original_motion_request/v1', 'plan': p,
                                   'plan_sha256': digest(plan), 'source_sha256': digest(src)})
    tmp = out / 'render.partial.mp4'; final = out / 'original-motion.mp4'
    try:
        env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'TMPDIR') if k in os.environ}
        with (out / 'ffmpeg.stderr').open('xb') as err:
            process = subprocess.Popen(['ffmpeg', '-nostdin', '-v', 'error', '-protocol_whitelist', 'file,pipe',
                '-i', str(src), '-f', 'rawvideo', '-pixel_format', 'rgba', '-video_size', f'{area[2]}x{area[3]}',
                '-framerate', '30', '-i', 'pipe:0', '-filter_complex',
                f'[0:v][1:v]overlay={area[0]}:{area[1]}:shortest=1:format=auto,format=yuv420p[v]',
                '-map', '[v]', '-an', '-frames:v', str(n), '-r', '30', '-c:v', 'libx264',
                '-preset', 'fast', '-crf', '18', '-n', str(tmp)], stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=err, env=env, start_new_session=True)
            timer = threading.Timer(timeout, process.kill); timer.daemon = True; timer.start()
            try:
                for i in range(n):
                    process.stdin.write(illustration(p['kind'], p['items'], area[2], area[3], i, n).tobytes())
                process.stdin.close()
                code = process.wait(timeout=timeout)
            finally:
                if timer is not None: timer.cancel()
            if code:
                raise EditError('Motion encoder failed or reached its bounded timeout; inspect private attempt')
        m = probe(tmp); streams = m['streams']; v = next((s for s in streams if s['codec_type'] == 'video'), {})
        if (len(streams) != 1 or v.get('width') != w or v.get('height') != h
                or v.get('avg_frame_rate') != '30/1' or int(v.get('nb_read_frames', -1)) != n):
            raise EditError('Rendered media differs from source geometry/frame count/silence')
        run(['ffmpeg', '-v', 'error', '-xerror', '-protocol_whitelist', 'file,pipe', '-i', str(tmp), '-f', 'null', '-'])
        if digest(src) != p['source']['sha256']:
            raise EditError('Source changed during rendering')
        os.link(tmp, final); tmp.unlink(); final.chmod(0o600)
        result = {'schema': 'original_motion_result/v1', 'status': 'prepared_original_motion',
                  'source_sha256': digest(src), 'output_sha256': digest(final), 'path': str(final),
                  'width': w, 'height': h, 'frames': n, 'fps': 30, 'audio_tracks': 0,
                  'illustrative_only': True, 'external_overlay_assets_used': False,
                  'source_rights_unchanged': True, 'full_decode_exit': 0,
                  'automatic_semantic_or_rights_approval': False,
                  'boundary': 'Only declared annotation pixels composed before lossy encoding; declared evidence geometry is an operator assertion. Re-encoding can change pixel values outside that rectangle. Actual final viewing/listening remains required.'}
        save_new(out / 'result.json', result); return result
    except BaseException:
        if process is not None and process.poll() is None:
            process.kill(); process.wait(timeout=5)
        if process is not None and process.stdin is not None and not process.stdin.closed:
            try: process.stdin.close()
            except OSError: pass
        if timer is not None: timer.cancel()
        if not (out / 'result.json').exists():
            save_new(out / 'result.json', {'status': 'failed_or_cancelled', 'final_approved': False,
                                         'partial_media_retained': tmp.exists()})
        raise
