"""Explicit local preset narration preparation; not listening or rights approval.

The caller installs reviewed dependencies and downloads pinned public files
separately. The inference worker has no tokens, remote model code or download API.
"""
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys

from editing_agent.core import EditError, digest, fields, read_json, run, validate as edit_validate
from .core import bound_file, save_new
from .motion import private_new_directory

REPO = 'hexgrad/Kokoro-82M-v1.1-zh'
REVISION = '01e7505bd6a7a2ac4975463114c3a7650a9f7218'
PINNED = {
    'weights': 'b1d8410fa44dfb5c15471fd6c4225ea6b4e9ac7fa03c98e8bea47a9928476e2b',
    'config': 'bc333efa5ce4ceff433c8c8e5d027a1eca0166001e4e4a62bea2d26ff7a46890',
    'voice': '9bdc9a87e13e9bb1ea3e7803259c2ecbfebaeeb2ff80b5d0c76df1a464c1c962',
    'model_card': '02ed578bdd24c42f3e4ed0d88d33e923365b41e6a37e86d3a5055f75d456f8e6',
}


def validate_voice(path):
    pp = Path(path).resolve(); p = read_json(pp)
    fields(p, ['schema', 'id', 'edit_plan', 'repository', 'revision', 'voice_id', 'files', 'speeds'])
    if p['schema'] != 'local_preset_voice/v1' or (p['repository'], p['revision'], p['voice_id']) != (REPO, REVISION, 'zf_001'):
        raise EditError('Only the explicitly reviewed Kokoro Chinese preset/revision is supported')
    from editing_agent.core import identity
    identity(p['id'])
    ep = bound_file(p['edit_plan'], pp.parent); base, _ = edit_validate(ep)
    fields(p['files'], list(PINNED))
    paths = {}
    for key, expected in PINNED.items():
        paths[key] = bound_file(p['files'][key], pp.parent)
        if p['files'][key]['sha256'] != expected:
            raise EditError('Unreviewed model/config/voice/card bytes')
    if not isinstance(p['speeds'], dict) or set(p['speeds']) != set(base['script']['lines']):
        raise EditError('An explicit model speed is required for every frozen line')
    cue_ids = [c['line_id'] for c in base['subtitles']]
    if len(cue_ids) != len(set(cue_ids)) or set(cue_ids) != set(p['speeds']):
        raise EditError('One cue per line is required by this voice backend')
    for key, speed in p['speeds'].items():
        if type(speed) not in (int, float) or not math.isfinite(speed) or not 1 <= speed <= 1.35:
            raise EditError('Explicit model speed must be 1..1.35; no clipping or automatic rate retries')
        # This reviewed path does not provision an English G2P model. Fail rather
        # than silently dropping Latin names or unknown characters.
        if re.search(r'[A-Za-z]', base['script']['lines'][key]):
            raise EditError('Chinese-only narration: Latin text needs a separately reviewed pronunciation path')
    return p, base, paths


def place_utterance(timeline, audio, cue, sample_rate=24000):
    """Retain the complete utterance with 80 ms tail room, or reject it."""
    import numpy as np
    a = np.asarray(audio, dtype=np.float32)
    start = cue['in_frame'] * sample_rate // 30
    end = cue['out_frame'] * sample_rate // 30
    if (a.ndim != 1 or not a.size or not np.isfinite(a).all()
            or float(np.max(np.abs(a))) <= 1e-6 or float(np.max(np.abs(a))) > 1
            or a.size + round(.08 * sample_rate) > end - start):
        raise EditError(f'Invalid or overlong utterance: {a.size} samples for {end-start} cue samples including tail room; explicitly revise a new request')
    timeline[start:start + a.size] = a
    return {'samples': int(a.size), 'seconds': float(a.size / sample_rate),
            'cue_seconds': (end - start) / sample_rate, 'truncated': False}


def worker(request, output):
    p, base, paths = validate_voice(request)
    for package in ('kokoro', 'misaki'):
        if importlib.metadata.version(package) != '0.9.4':
            raise EditError('Install the explicitly reviewed kokoro/misaki 0.9.4 in an isolated environment')
    import numpy as np
    import soundfile as sf
    import torch
    from kokoro import KModel, KPipeline
    def write_wav(path, samples, subtype='PCM_16'):
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w+b') as sink:
            sf.write(sink, samples, 24000, format='WAV', subtype=subtype)
    torch.set_num_threads(2); torch.set_num_interop_threads(1)
    model = KModel(repo_id=REPO, config=str(paths['config']), model=str(paths['weights'])).to('cpu').eval()
    pipeline = KPipeline(lang_code='z', repo_id=REPO, model=model, device='cpu')
    # Official 0.9.4 uses weights_only=True for both KModel and local voice loads.
    voice = torch.load(str(paths['voice']), map_location='cpu', weights_only=True)
    timeline = np.zeros(base['output']['frames'] * 800, dtype=np.float32)
    rows = []; out = Path(output)
    for i, cue in enumerate(base['subtitles']):
        key = cue['line_id']; text = base['script']['lines'][key]
        gs, phonemes = pipeline.g2p(text)
        if not gs or '❓' in gs or len(gs) > 510 or any(model.vocab.get(c) is None for c in gs):
            raise EditError('Empty/unknown/overlong phonemes; do not allow implicit model truncation')
        # Generate from explicit phonemes to avoid sentence chunking/truncation.
        generated = list(pipeline.generate_from_tokens(gs, voice=voice, speed=p['speeds'][key]))
        if len(generated) != 1 or generated[0].audio is None:
            raise EditError('Expected exactly one complete generated utterance')
        audio = generated[0].audio.detach().cpu().numpy()
        path = out / f'cue-{i + 1:02d}.wav'
        # Preserve even an overlong model utterance in the failed private attempt,
        # never silently trim it. This is not a ready-to-import narration file.
        write_wav(path, audio, subtype='FLOAT')
        save_new(out / f'cue-{i + 1:02d}.json', {'line_id': key, 'speed': p['speeds'][key],
            'generated_samples': int(audio.size), 'sample_rate': 24000,
            'cue_frames': cue['out_frame'] - cue['in_frame'], 'approved': False})
        data = place_utterance(timeline, audio, cue)
        rows.append({'line_id': key, 'text': text, 'speed': p['speeds'][key], **data, 'sha256': digest(path)})
        print('prepared cue', i + 1, flush=True)
    # One common reduction preserves the relative cue levels; no up-normalizing,
    # compression, truncation or time stretch masquerading as a listening fix.
    peak = float(np.max(np.abs(timeline))); gain = min(1., .65 / peak)
    temp = out / 'narration.partial.wav'; final = out / 'local-narration.wav'
    write_wav(temp, timeline * gain)
    os.link(temp, final); temp.unlink(); final.chmod(0o600)
    save_new(out / 'worker-report.json', {'lines': rows, 'gain': gain, 'samples': int(timeline.size),
        'sample_rate': 24000, 'channels': 1, 'voice_id': 'zf_001', 'repository': REPO,
        'revision': REVISION, 'packages': {n: importlib.metadata.version(n) for n in
        ('kokoro', 'misaki', 'torch', 'transformers', 'soundfile', 'numpy')}})


def prepare_voice(request, output, timeout=300):
    if type(timeout) is not int or not 1 <= timeout <= 600:
        raise EditError('Worker timeout must be 1..600 seconds')
    p, base, paths = validate_voice(request)
    # Parent imports neither Torch nor the optional renderer packages.
    out = private_new_directory(output); process = None
    save_new(out / 'request.json', {'request': p, 'request_sha256': digest(request)})
    env = {k: os.environ[k] for k in ('PATH', 'LANG', 'LC_ALL', 'TMPDIR') if k in os.environ}
    env.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
               DO_NOT_TRACK='1', TOKENIZERS_PARALLELISM='false', OMP_NUM_THREADS='2')
    try:
        with (out / 'worker.stdout').open('xb') as stdout, (out / 'worker.stderr').open('xb') as stderr:
            process = subprocess.Popen([sys.executable, '-m', 'creative_agent.voice', str(Path(request).resolve()), str(out)],
                stdout=stdout, stderr=stderr, env=env, cwd=Path(__file__).resolve().parent.parent, start_new_session=True)
            if process.wait(timeout=timeout):
                raise EditError('Local voice worker failed; inspect this private attempt, do not import it')
        final = out / 'local-narration.wav'; report = read_json(out / 'worker-report.json')
        m = json.loads(run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file,pipe', '-show_streams', '-show_format', '-of', 'json', str(final)]))
        streams = m['streams']
        if (len(streams) != 1 or streams[0]['codec_type'] != 'audio' or streams[0].get('sample_rate') != '24000'
                or streams[0].get('channels') != 1 or abs(float(m['format']['duration']) - base['output']['frames'] / 30) > .001):
            raise EditError('Narration format/duration differs from frozen timeline')
        run(['ffmpeg', '-v', 'error', '-xerror', '-protocol_whitelist', 'file,pipe', '-i', str(final), '-f', 'null', '-'])
        # Revalidate bindings after worker execution; no successful manifest if
        # the request, frozen plan, model or voice changed while generating.
        if read_json(out / 'request.json')['request_sha256'] != digest(request):
            raise EditError('Voice request changed')
        validate_voice(request)
        result = {'schema': 'local_preset_voice_result/v1', 'status': 'prepared_not_listened',
            'path': str(final), 'sha256': digest(final), 'request_sha256': digest(request),
            'frames': base['output']['frames'], 'duration_seconds': base['output']['frames'] / 30,
            'model_files': {k: digest(v) for k, v in paths.items()}, 'worker_report': report,
            'full_decode_exit': 0, 'actual_listening_completed': False,
            'automatic_rights_approval': False, 'publication_approved': False,
            'scope': 'Locally generated preset voice, not a real person clone. Publisher license evidence retained; no automatic legal, pronunciation, listening or final-video approval.'}
        save_new(out / 'result.json', result); return result
    except BaseException as exc:
        if process is not None and process.poll() is None:
            try: os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError: pass
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try: os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                process.wait(timeout=5)
        if not (out / 'result.json').exists():
            save_new(out / 'result.json', {'status': 'failed_or_cancelled', 'publication_approved': False})
        if isinstance(exc, subprocess.TimeoutExpired):
            raise EditError('Local voice worker timed out and was reaped; this attempt is not approved') from exc
        raise


if __name__ == '__main__':
    worker(sys.argv[1], sys.argv[2])
