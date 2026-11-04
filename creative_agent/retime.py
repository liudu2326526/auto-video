"""Explicit silent jump cuts/holds with a mandatory editorial notice, no speed claims."""
import json
import math
import os
from pathlib import Path

from editing_agent.core import EditError, digest, fields, identity, integer, read_json, run
from .core import bound_file, save_new
from .motion import FONT, overlaps, private_new_directory, probe, rectangle


def validate_retime(path):
    pp = Path(path).resolve(); p = read_json(pp)
    fields(p, ['schema', 'id', 'source', 'segments', 'notice_area', 'evidence_areas'])
    if p['schema'] != 'editorial_retime/v1':
        raise EditError('Unsupported retime schema')
    identity(p['id']); source = bound_file(p['source'], pp.parent)
    m = probe(source); videos = [s for s in m['streams'] if s['codec_type'] == 'video']
    if len(videos) != 1 or len(m['streams']) != 1:
        raise EditError('One silent video stream required')
    v = videos[0]; w = integer(v['width'], 240, 3840); h = integer(v['height'], 240, 3840)
    n = integer(int(v.get('nb_read_frames', -1)), 1, 300)
    if w % 2 or h % 2 or (v.get('avg_frame_rate'), v.get('r_frame_rate')) != ('30/1', '30/1'):
        raise EditError('Even geometry and CFR30 required')
    stamps = json.loads(run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
        '-select_streams', 'v:0', '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(source)]))
    times = [float(f.get('best_effort_timestamp_time', 'nan')) for f in stamps.get('frames', [])]
    if len(times) != n or any(not math.isfinite(t) or abs(t - i/30) > .002 for i,t in enumerate(times)):
        raise EditError('Source PTS must be contiguous zero-based CFR30')
    if not isinstance(p['segments'], list) or not 1 <= len(p['segments']) <= 12:
        raise EditError('1..12 explicit segments required')
    output = 0; previous = 0; jumped = False; held = False
    for segment in p['segments']:
        fields(segment, ['source_in_frame', 'source_out_frame', 'output_frames'])
        lo = integer(segment['source_in_frame'], 0, n-1)
        hi = integer(segment['source_out_frame'], 1, n)
        frames = integer(segment['output_frames'], 1, 300)
        if hi <= lo or lo < previous or (frames != hi-lo and hi-lo != 1):
            raise EditError('Only chronological normal-speed ranges or single-frame holds supported')
        jumped |= lo != previous; held |= frames > hi-lo
        output += frames; previous = hi
    jumped |= previous != n
    if not 1 <= output <= 300 or not (jumped or held):
        raise EditError('Bounded actual cut or hold required; do not label unchanged footage')
    area = rectangle(p['notice_area'], w, h)
    if not 300 <= area[2] <= 1024 or not 40 <= area[3] <= 100:
        raise EditError('Notice rectangle must be 300..1024 by 40..100 pixels')
    if not isinstance(p['evidence_areas'], list) or not 1 <= len(p['evidence_areas']) <= 12:
        raise EditError('Declared evidence rectangles required')
    if any(overlaps(area, rectangle(e, w, h)) for e in p['evidence_areas']):
        raise EditError('Notice overlaps declared evidence')
    notice = '操作已跳剪 · 含停帧' if jumped and held else '操作片段 · 已跳剪' if jumped else '操作片段 · 含停帧'
    return p, source, (w,h,output), area, notice


def prepare_retime(plan, output):
    p, source, (w,h,n), area, notice = validate_retime(plan)
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise EditError('Retime notice needs already installed Pillow') from exc
    if not FONT.is_file(): raise EditError('Reviewed macOS font unavailable')
    label = Image.new('RGBA', (area[2],area[3]), '#263B50')
    font = ImageFont.truetype(str(FONT), max(14, min(42, round(area[3]*.55))))
    while font.getbbox(notice)[2] > area[2]-20 and font.size > 14:
        font = ImageFont.truetype(str(FONT),font.size-1)
    if font.getbbox(notice)[2] > area[2]-20:
        raise EditError('Mandatory notice does not fit declared area')
    ImageDraw.Draw(label).text((10,3), notice, fill='#E6EDF4', font=font)
    out = private_new_directory(output)
    save_new(out/'request.json', {'plan':p,'plan_sha256':digest(plan),'notice':notice})
    tmp = out/'retime.partial.mp4'; final = out/'retimed.mp4'
    try:
        label.save(out/'notice.png')
        count = len(p['segments'])
        parts = ['[0:v]split='+str(count)+''.join('[s'+str(i)+']' for i in range(count))]
        mapping = []; cursor = 0
        for i,s in enumerate(p['segments']):
            lo,hi,frames = s['source_in_frame'],s['source_out_frame'],s['output_frames']
            f = f'[s{i}]trim=start_frame={lo}:end_frame={hi},setpts=PTS-STARTPTS'
            if frames > hi-lo: f += f',loop=loop={frames-1}:size=1:start=0,trim=end_frame={frames}'
            f += f',setpts=N/(30*TB)[c{i}]'; parts.append(f)
            mapping.append({**s,'output_in_frame':cursor,'output_out_frame':cursor+frames});cursor+=frames
        parts.append(''.join('[c'+str(i)+']' for i in range(count))+f'concat=n={count}:v=1:a=0[cut]')
        parts.append(f'[cut][1:v]overlay={area[0]}:{area[1]},format=yuv420p[v]')
        run(['ffmpeg','-nostdin','-v','error','-protocol_whitelist','file,pipe','-i',str(source),
             '-i',str(out/'notice.png'),'-filter_complex',';'.join(parts),'-map','[v]','-an',
             '-frames:v',str(n),'-r','30','-c:v','libx264','-preset','fast','-crf','18',
             '-map_metadata','-1','-n',str(tmp)],timeout=90)
        m = probe(tmp); v = m['streams'][0]
        if (len(m['streams']) != 1 or v.get('codec_type') != 'video'
            or (v.get('width'),v.get('height'),v.get('avg_frame_rate'),int(v.get('nb_read_frames',-1))) != (w,h,'30/1',n)):
            raise EditError('Prepared output geometry/count/silence mismatch')
        stamps = json.loads(run(['ffprobe','-v','error','-protocol_whitelist','file,pipe',
            '-select_streams','v:0','-show_entries','frame=best_effort_timestamp_time','-of','json',str(tmp)]))
        times = [float(f.get('best_effort_timestamp_time','nan')) for f in stamps.get('frames',[])]
        if len(times) != n or any(not math.isfinite(t) or abs(t-i/30) > .002 for i,t in enumerate(times)):
            raise EditError('Prepared output PTS mismatch')
        run(['ffmpeg','-v','error','-xerror','-protocol_whitelist','file,pipe','-i',str(tmp),'-f','null','-'])
        if digest(source) != p['source']['sha256']: raise EditError('Source changed while preparing')
        os.link(tmp,final);tmp.unlink();final.chmod(0o600)
        result = {'schema':'editorial_retime_result/v1','status':'prepared_retime',
            'source_sha256':digest(source),'output_sha256':digest(final),'path':str(final),
            'width':w,'height':h,'frames':n,'fps':30,'audio_tracks':0,'notice':notice,
            'mapping':mapping,'full_decode_exit':0,'automatic_semantic_or_rights_approval':False,
            'boundary':'Chronological source-frame mapping only; label geometry/content are operator assertions. Does not infer product timing, shorten narration, or approve final viewing/listening.'}
        save_new(out/'result.json',result);return result
    except BaseException:
        if not (out/'result.json').exists():
            save_new(out/'result.json',{'status':'failed_or_cancelled','final_approved':False,'partial_media_retained':tmp.exists()})
        raise
