"""Prepare labelled, source-bound result excerpts over a fixed local template.

This is an editorial preview, never a recording, product test, or final edit.
"""
import json
import math
import os
from pathlib import Path

from editing_agent.core import EditError, digest, fields, identity, integer, read_json, run
from .core import bound_file, save_new
from .motion import FONT, overlaps, private_new_directory, probe, rectangle


def silent_cfr(path, maximum):
    m = probe(path)
    if len(m['streams']) != 1 or m['streams'][0].get('codec_type') != 'video':
        raise EditError('One silent video stream required')
    v = m['streams'][0]
    w = integer(v['width'], 240, 3840); h = integer(v['height'], 240, 3840)
    n = integer(int(v.get('nb_read_frames', -1)), 1, maximum)
    if w % 2 or h % 2 or (v.get('avg_frame_rate'), v.get('r_frame_rate')) != ('30/1', '30/1'):
        raise EditError('Even geometry and CFR30 required')
    stamps = json.loads(run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe',
        '-select_streams', 'v:0', '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'json', str(path)]))
    times = [float(f.get('best_effort_timestamp_time', 'nan')) for f in stamps.get('frames', [])]
    if len(times) != n or any(not math.isfinite(t) or abs(t - i/30) > .002 for i,t in enumerate(times)):
        raise EditError('Contiguous zero-based CFR30 timestamps required')
    return w, h, n


def validate_montage(plan):
    pp = Path(plan).resolve(); p = read_json(pp)
    fields(p, ['schema', 'id', 'base', 'base_frame', 'annotation_area', 'evidence_areas',
               'panels', 'claim', 'source_note'])
    if p['schema'] != 'result_excerpt_montage/v1' or p['claim'] != 'editorial_preview_not_execution':
        raise EditError('Only labelled editorial result excerpts supported')
    identity(p['id'])
    if (not isinstance(p['source_note'], str) or not p['source_note'].strip()
            or len(p['source_note']) > 500 or any(ord(c) < 32 for c in p['source_note'])):
        raise EditError('Bounded source/history note required')
    base = bound_file(p['base'], pp.parent); w,h,n = silent_cfr(base, 180)
    integer(p['base_frame'], 0, n-1)
    area = rectangle(p['annotation_area'], w,h)
    if any(x % 2 for x in area) or not 300 <= area[2] <= 1920 or not 180 <= area[3] <= 1440:
        raise EditError('Bounded even annotation rectangle required')
    if not isinstance(p['evidence_areas'], list) or not 1 <= len(p['evidence_areas']) <= 12:
        raise EditError('Explicit protected evidence rectangles required')
    if any(overlaps(area, rectangle(e,w,h)) for e in p['evidence_areas']):
        raise EditError('Result montage overlaps protected evidence')
    if not isinstance(p['panels'], list) or not 2 <= len(p['panels']) <= 4:
        raise EditError('2..4 observed result excerpts required')
    panels = []; dimensions = {}; total = 0
    for row in p['panels']:
        fields(row, ['source', 'in_frame', 'out_frame', 'crop', 'observed_content'])
        if (not isinstance(row['observed_content'], str) or not row['observed_content'].strip()
                or len(row['observed_content']) > 200 or any(ord(c) < 32 for c in row['observed_content'])):
            raise EditError('Source observation required; this is not automatic semantic approval')
        src = bound_file(row['source'], pp.parent)
        if src not in dimensions: dimensions[src] = silent_cfr(src, 9000)
        sw,sh,sn = dimensions[src]
        lo = integer(row['in_frame'],0,sn-1); hi = integer(row['out_frame'],1,sn)
        if not 15 <= hi-lo <= 90:
            raise EditError('Each normal-speed excerpt must be 15..90 frames')
        crop = rectangle(row['crop'],sw,sh)
        if any(x % 2 for x in crop): raise EditError('Even source crop required')
        total += hi-lo; panels.append((src,lo,hi,crop))
    if total != n:
        raise EditError('Excerpts must fill the original template duration exactly')
    return p,base,(w,h,n),area,panels


def prepare_montage(plan, output):
    p,base,(w,h,n),area,panels = validate_montage(plan)
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError as exc:
        raise EditError('Already installed Pillow required for excerpt labels') from exc
    if not FONT.is_file(): raise EditError('Reviewed local display font unavailable')
    out = private_new_directory(output); tmp = out/'montage.partial.mp4'; final = out/'result-montage.mp4'
    save_new(out/'request.json', {'plan':p, 'plan_sha256':digest(plan),
        'author':'operator-supplied source selections; no model-generated product evidence'})
    try:
        run(['ffmpeg','-nostdin','-v','error','-protocol_whitelist','file,pipe','-i',str(base),
             '-vf',f"select=eq(n\\,{p['base_frame']})",'-frames:v','1','-n',str(out/'base-original.png')],timeout=60)
        im = Image.open(out/'base-original.png').convert('RGB'); background = im.getpixel((0,0))
        ImageDraw.Draw(im).rectangle((area[0],area[1],area[0]+area[2]-1,area[1]+area[3]-1),fill=background)
        im.save(out/'base.png'); label_h = 60; pane_h = area[3]-label_h
        mapping = []; cursor = 0
        for i,(src,lo,hi,crop) in enumerate(panels):
            label = Image.new('RGB',(area[2],label_h),background)
            font = ImageFont.truetype(str(FONT),36)
            notice = f'页面节选 {i+1}/{len(panels)}'
            if font.getbbox(notice)[2] > area[2]-16: raise EditError('Mandatory excerpt notice does not fit')
            ImageDraw.Draw(label).text((8,4),notice,font=font,fill='#6BC8BB'); label.save(out/f'label-{i}.png')
            x,y,cw,ch = crop
            filt = (f'[1:v]trim=start_frame={lo}:end_frame={hi},setpts=N/(30*TB),crop={cw}:{ch}:{x}:{y},'
                    f'scale={area[2]}:{pane_h}:force_original_aspect_ratio=decrease:force_divisible_by=2,'
                    f'pad={area[2]}:{pane_h}:(ow-iw)/2:(oh-ih)/2:color=0x0a1622[pane];'
                    f'[0:v][2:v]overlay={area[0]}:{area[1]}[labelled];'
                    f'[labelled][pane]overlay={area[0]}:{area[1]+label_h},format=yuv420p[v]')
            run(['ffmpeg','-nostdin','-v','error','-filter_complex_threads','1','-protocol_whitelist','file,pipe',
                 '-loop','1','-framerate','30','-i',str(out/'base.png'),'-i',str(src),'-i',str(out/f'label-{i}.png'),
                 '-filter_complex',filt,'-map','[v]','-an','-frames:v',str(hi-lo),'-r','30','-c:v','libx264',
                 '-threads','2','-preset','fast','-crf','18','-map_metadata','-1','-n',str(out/f'panel-{i}.mp4')],timeout=90)
            mapping.append({'source_sha256':digest(src),'source_in_frame':lo,'source_out_frame':hi,
                'crop':p['panels'][i]['crop'],'output_in_frame':cursor,'output_out_frame':cursor+hi-lo,
                'notice':notice,'observed_content':p['panels'][i]['observed_content']}); cursor+=hi-lo
        inputs = []; count = len(panels)
        for i in range(count):inputs += ['-i',str(out/f'panel-{i}.mp4')]
        filt = ''.join(f'[{i}:v]' for i in range(count))+f'concat=n={count}:v=1:a=0,format=yuv420p[v]'
        run(['ffmpeg','-nostdin','-v','error','-filter_complex_threads','1','-protocol_whitelist','file,pipe',*inputs,
             '-filter_complex',filt,'-map','[v]','-an','-frames:v',str(n),'-r','30','-c:v','libx264','-threads','2',
             '-preset','fast','-crf','18','-map_metadata','-1','-movflags','+faststart','-n',str(tmp)],timeout=90)
        if silent_cfr(tmp,180) != (w,h,n): raise EditError('Prepared montage geometry/count mismatch')
        run(['ffmpeg','-v','error','-xerror','-protocol_whitelist','file,pipe','-i',str(tmp),'-f','null','-'])
        if digest(base) != p['base']['sha256']: raise EditError('Base changed during preparation')
        for row,(src,_,_,_) in zip(p['panels'],panels):
            if digest(src) != row['source']['sha256']: raise EditError('Result source changed during preparation')
        os.link(tmp,final);tmp.unlink();final.chmod(0o600)
        result = {'schema':'result_excerpt_montage_result/v1','status':'prepared_result_montage',
            'path':str(final),'output_sha256':digest(final),'frames':n,'fps':30,'audio_tracks':0,
            'base_sha256':digest(base),'base_hold_frame':p['base_frame'],'mapping':mapping,'full_decode_exit':0,
            'automatic_semantic_or_rights_approval':False,'final_video_rendered':False,
            'boundary':'Labelled source excerpts over a fixed template, no chronology/navigation/timing claim. Protected rectangles are operator assertions. Codec may change pixels outside overlay. Font/publication rights, actual viewing/listening and native edit/export remain separate.'}
        save_new(out/'result.json',result);return result
    except BaseException:
        if not (out/'result.json').exists():save_new(out/'result.json',{'status':'failed_or_cancelled','final_approved':False,'partial_media_retained':tmp.exists()})
        raise
