"""Find similar audio at both ends, then create a periodic lossless loop."""
import asyncio
import numpy as np

async def command(*args, timeout=900):
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
    return out

async def find_loop(source):
    duration = float((await command('ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(source))).strip())
    if not np.isfinite(duration) or duration < .1:
        raise ValueError('The audio is too short to loop.')
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
    return {'start': round(start, 3), 'end': round(end, 3), 'crossfade': round(fade, 3)}

async def prepare_loop(source, target, points):
    s, e, f = (points[k] for k in ('start','end','crossfade'))
    # The periodic file starts after the head overlap; the blended tail ends
    # exactly there. This avoids a second hard cut when the file repeats.
    filters = (
        '[0:a]aresample=44100,asplit=3[m][t][h];'
        f'[m]atrim=start={s+f}:end={e-f},asetpts=PTS-STARTPTS[mid];'
        f'[t]atrim=start={e-f}:end={e},asetpts=PTS-STARTPTS[tail];'
        f'[h]atrim=start={s}:end={s+f},asetpts=PTS-STARTPTS[head];'
        f'[tail]afade=t=out:st=0:d={f}[tailfade];'
        f'[head]afade=t=in:st=0:d={f}[headfade];'
        '[tailfade][headfade]amix=inputs=2:duration=longest:normalize=0[blend];'
        '[mid][blend]concat=n=2:v=0:a=1[out]')
    await command('ffmpeg','-v','error','-y','-i',str(source),'-filter_complex',filters,
                  '-map','[out]','-c:a','flac',str(target))
