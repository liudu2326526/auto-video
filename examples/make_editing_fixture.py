"""Generate a nonprivate source and a 12-second Jianying plan. Nothing is uploaded."""
import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=False)
    source = root / 'source.mp4'
    subprocess.run(['ffmpeg', '-nostdin', '-v', 'error', '-f', 'lavfi', '-i',
                    'testsrc2=size=1280x720:rate=30:duration=20', '-vf', 'hue=h=15*t',
                    '-an', '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '20',
                    '-pix_fmt', 'yuv420p', '-n', str(source)], check=True)
    lines = {'line-01': '这是剪辑测试，先看第一段。', 'line-02': '再看第二段，字幕配音都要保留。'}
    plan = {
        'schema': 'jianying_edit_plan/v1', 'plan_id': 'auto-video-edit-smoke-v1',
        'script': {'version': 'script-smoke-v1', 'lines': lines},
        'output': {'width': 1280, 'height': 720, 'fps': 30, 'frames': 360},
        'assets': {'source': {'path': 'source.mp4', 'sha256': hashlib.sha256(source.read_bytes()).hexdigest(),
                              'rights': 'first_party_owned', 'rights_basis': 'Locally generated FFmpeg test pattern; no private footage.'}},
        'video': [
            {'id': 'shot-01', 'asset_id': 'source', 'source_in_frame': 30, 'source_out_frame': 210,
             'timeline_in_frame': 0, 'timeline_out_frame': 180},
            {'id': 'shot-02', 'asset_id': 'source', 'source_in_frame': 330, 'source_out_frame': 510,
             'timeline_in_frame': 180, 'timeline_out_frame': 360}],
        'subtitles': [{'line_id': key, 'text': text, 'in_frame': i * 180, 'out_frame': (i + 1) * 180}
                      for i, (key, text) in enumerate(lines.items())],
        'narration': {'mode': 'macos_say', 'voice': 'Tingting', 'rate': 190},
        'transitions': [{'at_frame': 180, 'kind': 'dissolve', 'duration_frames': 15, 'timing': 'preserve_total'}]}
    (root / 'edit_plan.json').write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    print(root / 'edit_plan.json')


if __name__ == '__main__':
    main()
