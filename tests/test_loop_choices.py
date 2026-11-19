import asyncio
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import app as server
from looping import rank_options


class LoopChoiceTests(unittest.TestCase):
    def test_length_preference_has_quality_and_loudness_limits(self):
        def option(end, score, mismatch):
            return dict(start=1, end=end, score=score, loudness_difference=mismatch)

        strongest = option(51, .98, .05)
        similar = option(101, .976, .08)
        weak = option(141, .90, .07)
        unbalanced = option(151, .978, .45)
        choices = rank_options([weak, strongest, unbalanced, similar], 160)
        self.assertEqual(choices[0]['end'], 101)
        self.assertEqual(choices[1]['end'], 51)
        self.assertEqual(rank_options([weak, strongest, unbalanced], 160)[0]['end'], 51)

    def test_preview_selection_and_peak_checked_export(self):
        async def run(folder):
            source = folder / 'hot.wav'
            output = folder / 'longplay.m4a'
            subprocess.run([
                'ffmpeg', '-v', 'error', '-f', 'lavfi', '-i',
                'aevalsrc=1.2*sin(2*PI*997*t)|1.2*sin(2*PI*997*t):d=4',
                '-c:a', 'pcm_f32le', str(source)
            ], check=True)
            options = [dict(start=.5, end=3.5, crossfade=.1, score=.99),
                       dict(start=1., end=3., crossfade=.1, score=.98)]
            points = dict(options[0], candidates=options, method='beat-aware')
            job = {'compare_loops': True}
            ident = folder.name
            with patch.object(server, 'DATA', folder.parent), patch.dict(server.jobs, {ident: job}), \
                    patch.object(server, 'find_loop', return_value=points):
                task = asyncio.create_task(server.render(source, output, 7, job))
                try:
                    async with asyncio.timeout(30):
                        while job.get('status') != 'awaiting_choice':
                            if task.done():
                                await task
                            await asyncio.sleep(.02)
                    response = await server.get_preview(ident, 1)
                    self.assertGreater(Path(response.path).stat().st_size, 0)
                    await server.choose_loop(ident, server.LoopChoice(index=1))
                    await task
                finally:
                    if not task.done():
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
            self.assertEqual(job['loop']['start'], 1.)
            self.assertLess(job['gain_db'], 0)
            self.assertLessEqual(await server.encoded_peak(output), -1.1)
        with tempfile.TemporaryDirectory() as folder:
            asyncio.run(run(Path(folder)))
