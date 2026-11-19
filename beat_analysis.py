"""Isolated PyMusicLooper runner used by Longplay's cancellable analyzer."""
import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / 'vendor'))
from pymusiclooper.core import MusicLooper


def analyze(path):
    looper = MusicLooper(path)
    pairs = looper.find_loop_pairs()
    return [
        {
            'start': float(looper.samples_to_seconds(pair.loop_start)),
            'end': float(looper.samples_to_seconds(pair.loop_end)),
            'score': round(float(pair.score), 6),
            'note_distance': round(float(pair.note_distance), 6),
            'loudness_difference': round(float(pair.loudness_difference), 6),
        }
        for pair in pairs[:3]
    ]


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    print(json.dumps(analyze(sys.argv[1]), separators=(',', ':')))
