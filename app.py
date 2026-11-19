import asyncio
import os
import re
import shutil
import sys
import time
import uuid
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse, parse_qs

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT / 'vendor'))
import yt_dlp
from looping import find_loop, prepare_loop

DATA = ROOT / 'data'
DATA.mkdir(exist_ok=True)
app = FastAPI(title='Longplay')
jobs = {}
lock = asyncio.Lock()

class Request(BaseModel):
    url: str
    format: Literal['m4a', 'mp3'] = 'm4a'
    minutes: int = Field(default=60, ge=1, le=480)

def canonical_url(value):
    parsed = urlparse(value.strip())
    host = (parsed.hostname or '').lower()
    if parsed.scheme != 'https' or parsed.username or parsed.password or parsed.port not in (None, 443):
        raise ValueError('Enter an HTTPS YouTube video link.')
    if host in ('youtube.com', 'www.youtube.com', 'm.youtube.com', 'music.youtube.com'):
        parts = parsed.path.strip('/').split('/')
        ident = parse_qs(parsed.query).get('v', [''])[0] if parsed.path == '/watch' else (parts[1] if len(parts) == 2 and parts[0] in ('shorts', 'live', 'embed') else '')
    elif host == 'youtu.be':
        ident = parsed.path.strip('/')
    else:
        raise ValueError('Please use a youtube.com or youtu.be video link.')
    import re
    if not re.fullmatch(r'[A-Za-z0-9_-]{11}', ident):
        raise ValueError('This link does not contain a valid YouTube video ID.')
    return 'https://www.youtube.com/watch?v=' + ident

def download(url, folder, job):
    def progress(info):
        if info['status'] == 'downloading':
            total = info.get('total_bytes') or info.get('total_bytes_estimate')
            job['progress'] = min(35, int(info.get('downloaded_bytes', 0) / total * 35)) if total else 5
    def reject(info, *, incomplete=False):
        if info.get('is_live'):
            return 'Live streams are not supported. Use a finished video.'
        if (info.get('duration') or 0) > 4 * 3600:
            return 'Source videos must be four hours or shorter.'
    opts = {'format': 'bestaudio/best', 'outtmpl': str(folder / 'source.%(ext)s'), 'noplaylist': True,
            'quiet': True, 'no_warnings': True, 'socket_timeout': 30, 'retries': 2,
            'max_filesize': 512 * 1024 * 1024, 'match_filter': reject, 'progress_hooks': [progress]}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if not info:
            raise ValueError('The video could not be downloaded.')
        job['title'] = info.get('track') or info.get('title') or 'Longplay'
        path = Path(ydl.prepare_filename(info))
        if not path.is_file():
            raise ValueError('No audio was downloaded. Check the source length and availability.')
        return path

async def render(source, destination, seconds, job):
    loop = destination.with_suffix('.loop.flac')
    job['status'] = 'analyzing'
    points = await find_loop(source)
    job['loop'] = points
    await prepare_loop(source, loop, points)
    job['status'] = 'rendering'
    encoding = (['-c:a', 'aac', '-profile:a', 'aac_low', '-movflags', '+faststart']
                if destination.suffix == '.m4a' else ['-c:a', 'libmp3lame'])
    process = await asyncio.create_subprocess_exec('ffmpeg', '-hide_banner', '-loglevel', 'error', '-y',
        '-i', str(source), '-stream_loop', '-1', '-i', str(loop), '-t', str(seconds), '-vn',
        '-filter_complex', f'[0:a]atrim=end={points["start"]+points["crossfade"]},asetpts=PTS-STARTPTS[intro];[intro][1:a]concat=n=2:v=0:a=1[out]', '-map', '[out]',
        *encoding, '-b:a', '192k', '-ar', '44100', '-ac', '2',
        '-metadata', 'title=' + job.get('title', 'Longplay'),
        '-metadata', 'artist=Longplay', '-metadata', 'album=Longplay Loops', '-progress', 'pipe:1', str(destination),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    async def read_progress():
        async for line in process.stdout:
            key, _, value = line.decode().strip().partition('=')
            if key == 'out_time_us':
                try: job['progress'] = min(99, 35 + int(int(value) / 1e6 / seconds * 64))
                except ValueError: pass
    try:
        await asyncio.wait_for(asyncio.gather(read_progress(), process.wait(), process.stderr.read()), timeout=3600)
    except BaseException:
        if process.returncode is None:
            process.kill()
            await process.wait()
        raise
    loop.unlink(missing_ok=True)
    if process.returncode:
        raise ValueError('Audio rendering failed. Try another video.')

async def work(ident, url, minutes):
    job = jobs[ident]
    folder = DATA / ident
    folder.mkdir()
    try:
        job['status'] = 'downloading'
        source = await asyncio.to_thread(download, url, folder, job)
        job['status'] = 'rendering'
        await render(source, folder / ('longplay.' + job.get('format', 'mp3')), minutes * 60, job)
        source.unlink(missing_ok=True)
        job.update(status='ready', progress=100)
    except Exception as error:
        # Keep downloader internals and local paths out of the browser.
        message = str(error) if isinstance(error, ValueError) else 'Conversion could not finish. YouTube may not be downloadable. It may be restricted or require sign-in. Try another public video.'
        job.update(status='error', error=message)
        shutil.rmtree(folder, ignore_errors=True)
    finally:
        job['finished'] = time.time()

@app.post('/api/jobs', status_code=202)
async def create_job(request: Request):
    try: url = canonical_url(request.url)
    except (ValueError, TypeError) as error: raise HTTPException(400, str(error))
    async with lock:
        for directory in DATA.iterdir():
            if directory.is_dir() and directory.stat().st_mtime < time.time() - 86400:
                shutil.rmtree(directory, ignore_errors=True)
        for ident, job in list(jobs.items()):
            if job.get('finished', time.time()) < time.time() - 86400:
                shutil.rmtree(DATA / ident, ignore_errors=True)
                del jobs[ident]
        if any(j['status'] in ('downloading', 'rendering', 'queued', 'analyzing') for j in jobs.values()):
            raise HTTPException(429, 'Another conversion is running. Please try again when it finishes.')
        if shutil.disk_usage(DATA).free < 2 * 1024**3:
            raise HTTPException(503, 'Not enough free disk space for a conversion.')
        ident = uuid.uuid4().hex
        jobs[ident] = {'status': 'queued', 'progress': 0, 'minutes': request.minutes, 'format': request.format}
        asyncio.create_task(work(ident, url, request.minutes))
        return {'id': ident}

@app.get('/api/jobs/{ident}')
async def get_job(ident: str):
    if ident not in jobs: raise HTTPException(404, 'Conversion not found. Start a new conversion.')
    result = dict(jobs[ident])
    if result['status'] == 'ready':
        result.update(export_info(ident))
    return result

@app.get('/api/jobs/{ident}/download')
async def get_download(ident: str, inline: bool = False):
    if ident not in jobs or jobs[ident]['status'] != 'ready': raise HTTPException(404, 'Audio is not ready.')
    info = export_info(ident)
    return FileResponse(DATA / ident / ('longplay.' + info['format']), media_type=info['mime_type'], filename=info['filename'], content_disposition_type='inline' if inline else 'attachment')

def audio_filename(title, fmt):
    # Preserve readable Unicode titles while supporting Windows and HTTP headers.
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', ' ', title or '')
    stem = re.sub(r'\s+', ' ', stem).strip(' .')[:80].rstrip(' .') or 'Longplay'
    base = stem.split('.')[0].rstrip().upper()
    if base in {'CON', 'PRN', 'AUX', 'NUL'} or re.fullmatch(r'(COM|LPT)[1-9¹²³]', base):
        stem = '_' + stem
    return f'{stem}.{fmt}'


def export_info(ident):
    job = jobs[ident]
    fmt = job.get('format', 'mp3')
    file = DATA / ident / ('longplay.' + fmt)
    return {'id': ident, 'format': fmt,
            'filename': audio_filename(job.get('title'), fmt),
            'mime_type': 'audio/mp4' if fmt == 'm4a' else 'audio/mpeg',
            'duration_seconds': job['minutes'] * 60,
            'bytes': file.stat().st_size,
            'download_url': f'/api/jobs/{ident}/download',
            'playback_url': f'/api/jobs/{ident}/download?inline=true'}

@app.get('/api/exports')
async def list_exports():
    return [dict(export_info(ident), title=job.get('title', 'Longplay'))
            for ident, job in jobs.items()
            if job['status'] == 'ready' and (DATA / ident / ('longplay.' + job.get('format', 'mp3'))).is_file()]

app.mount('/',  StaticFiles(directory=ROOT / 'static', html=True), name='static')
