"""Find musically useful loop points, then create a periodic lossless loop."""
import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path

import numpy as np

HIGH_QUALITY_MAX_SECONDS = 30 * 60

async def command(*args, timeout=900, return_stderr=False):
    proc = await asyncio.create_subprocess_exec(*args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout)
    except BaseException:
        if proc.returncode is None:
            proc.kill()
            await proc.wait()
        raise
    if proc.returncode:
        raise ValueError('Could not analyze or prepare the audio. Try another video.')
    return err if return_stderr else out

async def probe_duration(source):
    duration = float((await command('ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(source))).strip())
    if not np.isfinite(duration) or duration < .1:
        raise ValueError('The audio is too short to loop.')
    return duration


async def _run_beat_analysis(proxy):
    output = await command(
        sys.executable,
        str(Path(__file__).with_name('beat_analysis.py')),
        str(proxy),
        timeout=600,
    )
    return json.loads(output)


async def find_beat_loop(source, duration):
    """Run beat/chroma analysis on a compatible, memory-bounded audio proxy."""
    if duration < 10:
        raise ValueError('Source is too short for beat-aware analysis.')
    if duration > HIGH_QUALITY_MAX_SECONDS:
        raise ValueError('Source is too long for beat-aware analysis.')

    with tempfile.TemporaryDirectory(prefix='longplay-analysis-') as folder:
        proxy = Path(folder) / 'analysis.wav'
        await command(
            'ffmpeg', '-v', 'error', '-y', '-i', str(source), '-vn',
            '-ac', '1', '-ar', '22050', '-c:a', 'pcm_s16le', str(proxy),
            timeout=900,
        )
        candidates = await _run_beat_analysis(proxy)

    options = rank_options(candidates, duration)
    return {**options[0], 'method': 'beat-aware', 'candidates': options}


def rank_options(candidates, duration):
    """Prefer a meaningful length gain within a narrow musical-match band."""
    valid = []
    for candidate in candidates:
        start, end, score = (candidate[k] for k in ('start', 'end', 'score'))
        if not all(np.isfinite([start, end, score])) or not 0 <= start < end <= duration + .05:
            continue
        end = min(end, duration)
        if end - start >= .25:
            valid.append(dict(candidate, start=start, end=end))
    if not valid:
        raise ValueError('No usable beat-aware loop candidates were found.')

    def length(option):
        return option['end'] - option['start']

    valid.sort(key=lambda option: option['score'], reverse=True)
    strongest = valid[0]
    close = [option for option in valid
             if strongest['score'] - option['score'] <= .01
             and option.get('loudness_difference', 0) <= strongest.get('loudness_difference', 0) + .25]
    longest = max(close, key=length)
    recommended = longest if length(longest) >= length(strongest) * 1.10 else strongest
    choices = []
    for option in [recommended, *valid]:
        if any(abs(option['start'] - x['start']) < 1 and abs(option['end'] - x['end']) < 1
               for x in choices):
            continue
        choices.append({key: round(option[key], 6) for key in ('start', 'end', 'score')})
        choices[-1]['crossfade'] = round(min(.12, length(option) / 8), 3)
        if len(choices) == 3:
            break
    return choices


async def preview_join(source, destination, points):
    start, end, fade = (points[k] for k in ('start', 'end', 'crossfade'))
    window = min(5 + fade, (end - start) / 2)
    filters = (f'[0:a]aresample=44100,atrim=duration={window},asetpts=PTS-STARTPTS[t];'
               f'[1:a]aresample=44100,atrim=duration={window},asetpts=PTS-STARTPTS[h];'
               f'[t][h]acrossfade=d={fade}:c1=tri:c2=tri[out]')
    await command('ffmpeg', '-v', 'error', '-y', '-ss', str(end-window), '-i', str(source),
                  '-ss', str(start), '-i', str(source), '-filter_complex', filters,
                  '-map', '[out]', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart',
                  str(destination))


async def find_spectral_loop(source, duration=None):
    """Fast fallback that compares spectral windows near both track edges."""
    duration = duration if duration is not None else await probe_duration(source)
    fade = min(2., duration / 8)
    edge = min(60., duration * .2)
    # Short clips still receive a crossfade; feature matching needs several seconds.
    start, end = 0., duration
    if duration >= 10:
        async def excerpt(offset):
            data = await command('ffmpeg', '-v', 'error', '-ss', str(offset), '-i', str(source),
                '-t', str(edge), '-vn', '-ac', '1', '-ar', '2000', '-f', 'f32le', 'pipe:1')
            samples = np.frombuffer(data, dtype='<f4')
            frames = samples[:len(samples)//200*200].reshape(-1, 200)
            # Loudness plus spectral energy distinguish tone, texture, and silence.
            power = np.abs(np.fft.rfft(frames * np.hanning(200), axis=1))**2
            bands = np.stack([power[:, a:b].mean(axis=1) for a,b in [(0,4),(4,10),(10,20),(20,40),(40,70),(70,101)]], axis=1)
            return np.log1p(bands * 1000)
        head, tail = await asyncio.gather(excerpt(0), excerpt(duration-edge))
        count = max(1, round(fade/.1))
        def windows(features):
            return np.array([features[i:i+count].reshape(-1) for i in range(len(features)-count+1)])
        a, b = windows(head), windows(tail)
        if len(a) and len(b):
            # Compare the entire overlapping passage, not just one quiet sample.
            costs = ((a*a).sum(1)[:,None] + (b*b).sum(1)[None,:] - 2*a@b.T) / a.shape[1]
            energy_a, energy_b = a.mean(1), b.mean(1)
            audible = max(float(energy_a.max()), float(energy_b.max()))
            if audible > .1:
                costs += ((energy_a < audible*.15)[:,None] | (energy_b < audible*.15)[None,:]) * 100
            i, j = np.unravel_index(np.argmin(costs), costs.shape)
            start = float(i)*.1
            end = min(duration, duration-edge+float(j)*.1+fade)
    return {
        'start': round(start, 3),
        'end': round(end, 3),
        'crossfade': round(fade, 3),
        'method': 'spectral-fallback',
        'candidates': [],
    }


async def find_loop(source):
    """Prefer musical beat/chroma analysis and fall back to edge matching."""
    duration = await probe_duration(source)
    try:
        return await find_beat_loop(source, duration)
    except Exception as error:
        logging.warning('Beat-aware analysis unavailable; using spectral fallback: %s', error)
        return await find_spectral_loop(source, duration)

async def prepare_loop(source, target, points):
    s, e, f = (points[k] for k in ('start','end','crossfade'))
    # The periodic file starts after the head overlap; the blended tail ends
    # exactly there. This avoids a second hard cut when the file repeats.
    filters = (
        '[0:a]aresample=44100,aformat=sample_fmts=fltp,asplit=3[m][t][h];'
        f'[m]atrim=start={s+f}:end={e-f},asetpts=PTS-STARTPTS[mid];'
        f'[t]atrim=start={e-f}:end={e},asetpts=PTS-STARTPTS[tail];'
        f'[h]atrim=start={s}:end={s+f},asetpts=PTS-STARTPTS[head];'
        f'[tail]afade=t=out:st=0:d={f}[tailfade];'
        f'[head]afade=t=in:st=0:d={f}[headfade];'
        '[tailfade][headfade]amix=inputs=2:duration=longest:normalize=0[blend];'
        '[mid][blend]concat=n=2:v=0:a=1[out]')
    # Float WavPack preserves decoded peaks above full scale until export gain is applied.
    codec = ['-c:a', 'wavpack', '-sample_fmt', 'fltp'] if target.suffix == '.wv' else ['-c:a', 'flac']
    await command('ffmpeg', '-v', 'error', '-y', '-i', str(source), '-filter_complex', filters,
                  '-map', '[out]', *codec, str(target))
