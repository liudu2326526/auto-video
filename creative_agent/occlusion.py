"""Explicit, operator-declared platform occlusion simulations. Never phone approval."""
from pathlib import Path
import subprocess

from editing_agent.core import EditError, fields, identity, integer, read_json
from .core import bound_file, digest, save_new, words, unique
from .motion import overlaps, private_new_directory, probe, rectangle


def contain_rect(rect, source_size, viewport):
    """Map source pixel edges to a centered contain viewport, preserving fractions."""
    sw, sh = source_size; vw, vh = viewport
    scale = min(vw / sw, vh / sh)
    ox, oy = (vw - sw * scale) / 2, (vh - sh * scale) / 2
    x, y, w, h = rect
    return ox + x * scale, oy + y * scale, w * scale, h * scale


def validate_simulation(path):
    pp = Path(path).resolve(); p = read_json(pp)
    fields(p, ['schema', 'id', 'source', 'profiles', 'regions', 'anchor_frames'])
    if p['schema'] != 'platform_occlusion_simulation/v1':
        raise EditError('Unsupported simulation schema')
    identity(p['id']); src = bound_file(p['source'], pp.parent)
    videos = [v for v in probe(src)['streams'] if v['codec_type'] == 'video']
    if len(videos) != 1:
        raise EditError('Exactly one source video required')
    v = videos[0]
    w = integer(v['width'], 240, 3840); h = integer(v['height'], 240, 3840)
    n = integer(int(v.get('nb_read_frames', -1)), 1, 18000)
    if (v.get('r_frame_rate'), v.get('avg_frame_rate')) != ('30/1', '30/1'):
        raise EditError('Simulation source must be explicit CFR30')
    profiles = unique(p['profiles'], 'id')
    if not 1 <= len(profiles) <= 3:
        raise EditError('Expected one to three explicit viewport profiles')
    for profile in profiles.values():
        fields(profile, ['id', 'viewport', 'blocked_areas', 'basis'])
        fields(profile['viewport'], ['width', 'height'])
        vw = integer(profile['viewport']['width'], 240, 1440)
        vh = integer(profile['viewport']['height'], 240, 2560)
        words(profile['basis'])
        if not isinstance(profile['blocked_areas'], list) or not 1 <= len(profile['blocked_areas']) <= 8:
            raise EditError('Expected explicit bounded blocked areas')
        for rect in profile['blocked_areas']:
            rectangle(rect, vw, vh)
    regions = unique(p['regions'], 'id')
    if not regions:
        raise EditError('Operator-declared important content regions required')
    for region in regions.values():
        fields(region, ['id', 'in_frame', 'out_frame', 'rectangle', 'observation'])
        if not integer(region['in_frame'], 0, n - 1) < integer(region['out_frame'], 1, n):
            raise EditError('Empty or reversed region interval')
        rectangle(region['rectangle'], w, h); words(region['observation'])
    anchors = p['anchor_frames']
    if (not isinstance(anchors, list) or not 1 <= len(anchors) <= 40
            or any(type(i) is not int or not 0 <= i < n for i in anchors)
            or anchors != sorted(set(anchors))):
        raise EditError('Expected one to forty sorted unique frame anchors')
    return p, src, (w, h, n)


def simulate(plan, output):
    p, src, (w, h, n) = validate_simulation(plan)
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise EditError('Simulation needs already installed Pillow') from exc
    out = private_new_directory(output)
    save_new(out / 'request.json', p)
    rows = []; images = []
    for profile in p['profiles']:
        vw, vh = (profile['viewport'][k] for k in ('width', 'height'))
        blocked = [rectangle(b, vw, vh) for b in profile['blocked_areas']]
        for region in p['regions']:
            mapped = contain_rect(rectangle(region['rectangle'], w, h), (w, h), (vw, vh))
            rows.append({'profile_id': profile['id'], 'region_id': region['id'],
                         'in_frame': region['in_frame'], 'out_frame': region['out_frame'],
                         'mapped_rectangle': mapped,
                         'intersects_blocked_area': any(overlaps(mapped, b) for b in blocked)})
        for i in p['anchor_frames']:
            # The source is a local file, already hash-bound. No browser/network capture.
            try:
                decoded = subprocess.run(['ffmpeg', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-i', str(src),
                    '-vf', f'select=eq(n\\,{i})', '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'],
                    capture_output=True, timeout=60)
            except subprocess.TimeoutExpired as exc:
                raise EditError('Source anchor decode timed out') from exc
            if decoded.returncode:
                raise EditError('Source anchor decode failed')
            raw = decoded.stdout
            if len(raw) != w * h * 3:
                raise EditError('Incomplete source anchor')
            source = Image.frombytes('RGB', (w, h), raw)
            canvas = Image.new('RGB', (vw, vh), '#161616')
            x, y, ww, hh = contain_rect((0, 0, w, h), (w, h), (vw, vh))
            canvas.paste(source.resize((round(ww), round(hh)), Image.Resampling.LANCZOS), (round(x), round(y)))
            overlay = Image.new('RGBA', canvas.size)
            draw = ImageDraw.Draw(overlay)
            for x, y, ww, hh in blocked:
                draw.rectangle((x, y, x + ww - 1, y + hh - 1), fill=(80, 0, 0, 220), outline='#FF7474')
            draw.text((6, 6), 'SIMULATION / NOT PHONE APPROVAL', fill='white')
            canvas = Image.alpha_composite(canvas.convert('RGBA'), overlay).convert('RGB')
            image = out / (profile['id'] + '-frame-' + str(i) + '.jpg')
            canvas.save(image, quality=92); image.chmod(0o600)
            images.append({'path': str(image), 'sha256': digest(image), 'frame': i, 'profile_id': profile['id']})
    if digest(src) != p['source']['sha256']:
        raise EditError('Source changed during simulation')
    result = {'schema': 'platform_occlusion_result/v1', 'status': 'simulated',
              'plan_sha256': digest(plan), 'source_sha256': digest(src), 'frames': n,
              'regions': rows, 'images': images,
              'operator_declarations_only': True, 'actual_phone_test': False,
              'automatic_readability_or_privacy_approval': False,
              'boundary': 'Explicit contain mapping and declared masks only. No OCR/semantics; unlisted content or actual platform layout may differ.'}
    save_new(out / 'result.json', result)
    return result
