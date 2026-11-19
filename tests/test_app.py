import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from app import canonical_url, render

class ConversionTests(unittest.TestCase):
    def test_url_validation(self):
        expected = 'https://www.youtube.com/watch?v=abcdefghijk'
        for url in [expected, 'https://youtu.be/abcdefghijk?t=2', 'https://youtube.com/shorts/abcdefghijk']:
            self.assertEqual(canonical_url(url), expected)
        for url in ['https://example.com/a', 'http://youtu.be/abcdefghijk', 'https://youtube.com.evil.test/watch?v=abcdefghijk', 'https://youtube.com/watch?v=bad', 'https://user:pass@youtube.com/watch?v=abcdefghijk']:
            with self.assertRaises(ValueError): canonical_url(url)

    def test_loop_duration_and_codec(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'source.wav'
            dest = Path(folder)/'result.mp3'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=0.4',str(source)],check=True)
            asyncio.run(render(source, dest, 3, {}))
            info=json.loads(subprocess.check_output(['ffprobe','-v','quiet','-show_format','-show_streams','-of','json',str(dest)]))
            self.assertAlmostEqual(float(info['format']['duration']),3,delta=.1)
            self.assertEqual(info['streams'][0]['codec_name'],'mp3')
            self.assertEqual(info['streams'][0]['bit_rate'],'192000')

class SmartLoopTests(unittest.TestCase):
    def test_matches_shared_tone_instead_of_unrelated_edges(self):
        import numpy as np
        import wave
        from looping import find_loop
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'tones.wav'
            rate = 8000
            t = np.arange(3*rate)/rate
            samples = np.concatenate([.4*np.sin(2*np.pi*hz*t) for hz in [440,880,440,440,440,440,440,440,880,1320]])
            with wave.open(str(source),'wb') as audio:
                audio.setnchannels(1); audio.setsampwidth(2); audio.setframerate(rate)
                audio.writeframes((samples*32767).astype('<i2').tobytes())
            points=asyncio.run(find_loop(source))
            self.assertGreaterEqual(points['start'], 2.9)
            self.assertLessEqual(points['end'], 27.1)
            self.assertGreaterEqual(points['end'], 25.9)

    def test_periodic_crossfade_removes_boundary_jump(self):
        import numpy as np
        import wave
        from looping import prepare_loop
        with tempfile.TemporaryDirectory() as folder:
            source, loop = Path(folder)/'ramp.wav', Path(folder)/'loop.flac'
            rate=44100
            samples=np.linspace(-.6,.6,rate*4,endpoint=False)
            with wave.open(str(source),'wb') as audio:
                audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(rate)
                audio.writeframes((samples*32767).astype('<i2').tobytes())
            asyncio.run(prepare_loop(source,loop,{'start':0,'end':4,'crossfade':.5}))
            raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(loop),'-f','f32le','pipe:1'])
            result=np.frombuffer(raw,dtype='<f4')
            self.assertAlmostEqual(len(result)/rate,3.5,delta=.002)
            self.assertLess(abs(float(result[-1]-result[0])),.001)
            self.assertLess(float(np.abs(np.diff(result)).max()),.001)

    def test_export_preserves_intro_and_smooth_repeats(self):
        import numpy as np
        import wave
        with tempfile.TemporaryDirectory() as folder:
            source, dest = Path(folder)/'ramp.wav', Path(folder)/'longplay.mp3'
            rate=48000
            samples=np.linspace(-.5,.5,rate*4,endpoint=False)
            with wave.open(str(source),'wb') as audio:
                audio.setnchannels(2); audio.setsampwidth(2); audio.setframerate(rate)
                stereo=np.column_stack((samples,samples*.5))
                audio.writeframes((stereo*32767).astype('<i2').tobytes())
            job={}
            asyncio.run(render(source,dest,12,job))
            raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(dest),'-ac','1','-ar','44100','-f','f32le','pipe:1'])
            result=np.frombuffer(raw,dtype='<f4')
            self.assertAlmostEqual(len(result)/44100,12,delta=.01)
            self.assertLess(float(np.abs(np.diff(result[2205:-2205])).max()),.02)
            self.assertLess(float(result[4410]),-.2)  # Original negative intro remains.
            self.assertIn('loop',job)

class IPhoneExportTests(unittest.TestCase):
    def test_m4a_codec_metadata_and_fast_start(self):
        import struct
        from app import Request
        from pydantic import ValidationError
        self.assertEqual(Request(url='https://youtu.be/abcdefghijk').format,'m4a')
        with self.assertRaises(ValidationError):
            Request(url='https://youtu.be/abcdefghijk', format='exe')
        with tempfile.TemporaryDirectory() as folder:
            source, dest=Path(folder)/'source.wav',Path(folder)/'result.m4a'
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=0.4',str(source)],check=True)
            asyncio.run(render(source,dest,3,{'title':'Rain at night'}))
            info=json.loads(subprocess.check_output(['ffprobe','-v','quiet','-show_format','-show_streams','-of','json',str(dest)]))
            stream=info['streams'][0]
            self.assertEqual(stream['codec_name'],'aac')
            self.assertEqual(stream['profile'],'LC')
            self.assertEqual(stream['channels'],2)
            self.assertEqual(stream['sample_rate'],'44100')
            self.assertAlmostEqual(float(info['format']['duration']),3,delta=.1)
            self.assertEqual(info['format']['tags']['title'],'Rain at night')
            data=dest.read_bytes(); offset=0; atoms=[]
            while offset<len(data):
                size, kind=struct.unpack('>I4s',data[offset:offset+8])
                self.assertGreater(size,0)
                atoms.append(kind);offset+=size
            self.assertLess(atoms.index(b'moov'),atoms.index(b'mdat'))

    def test_transfer_manifest_and_download_headers(self):
        from unittest.mock import patch
        import app as server
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); (root/'test').mkdir();(root/'test'/'longplay.m4a').write_bytes(b'test')
            job={'status':'ready','minutes':60,'format':'m4a','title':'Rain'}
            with patch.object(server,'DATA',root),patch.dict(server.jobs,{'test':job},clear=True):
                manifest=asyncio.run(server.list_exports())
                self.assertEqual(manifest[0]['mime_type'],'audio/mp4')
                self.assertEqual(manifest[0]['filename'],'Rain.m4a')
                self.assertEqual(manifest[0]['bytes'],4)
                response=asyncio.run(server.get_download('test',True))
                self.assertEqual(response.headers['content-type'],'audio/mp4')
                self.assertIn('inline;',response.headers['content-disposition'])
                self.assertIn('Rain.m4a',response.headers['content-disposition'])
                response=asyncio.run(server.get_download('test'))
                self.assertIn('attachment;',response.headers['content-disposition'])


class AudioFilenameTests(unittest.TestCase):
    def test_title_based_names_are_portable(self):
        from app import audio_filename
        cases = [
            ('Rain at night', 'Rain at night'),
            ('Artist: Song / Live?', 'Artist Song Live'),
            ('Beyoncé – Déjà Vu', 'Beyoncé – Déjà Vu'),
            ('Rain\r\nContent-Type: text/html', 'Rain Content-Type text html'),
            ('CON', '_CON'),
            ('NUL.song', '_NUL.song'),
            ('lpt1', '_lpt1'),
            ('... ', 'Longplay'),
            (None, 'Longplay'),
            ('x' * 120, 'x' * 80),
        ]
        for title, expected in cases:
            for fmt in ('m4a', 'mp3'):
                with self.subTest(title=title, fmt=fmt):
                    self.assertEqual(audio_filename(title, fmt), expected + '.' + fmt)
