import subprocess
import time
import os

from pathlib import Path

from utils.constants import FFMPEG_PATH
from utils.logger import setup_logger

logger = setup_logger(__name__)


def convert_audio_file_with_cancel(input_path, output_dir, output_format, quality, cancel_event):
    input_path = Path(input_path)

    if not os.path.exists(input_path):
        logger.error(f"Input file not found: {input_path}")
        return None

    output_name = input_path.stem + f'.{output_format}'
    output_path = Path(output_dir) / output_name

    cmd = [FFMPEG_PATH, '-i', str(input_path), '-vn']

    if output_format == 'mp3':
        cmd += [
            '-acodec',
            'libmp3lame',
            '-b:a',
            f'{quality}k'
        ]

    elif output_format == 'wav':
        fmt_map = {
            '16': 'pcm_s16le',
            '24': 'pcm_s24le',
            '32': 'pcm_s32le'
        }

        cmd += [
            '-acodec',
            fmt_map[quality]
        ]

    elif output_format == 'flac':
        depth, comp = quality.split(':')
        comp = int(comp)

        if depth == '16':
            cmd += [
                '-acodec',
                'flac',
                '-sample_fmt',
                's16',
                '-compression_level',
                str(comp)
            ]

        elif depth == '24':
            cmd += [
                '-acodec',
                'flac',
                '-sample_fmt',
                's32',
                '-bits_per_raw_sample',
                '24',
                '-compression_level',
                str(comp)
            ]

    elif output_format == 'ogg':
        cmd += [
            '-acodec',
            'libvorbis',
            '-b:a',
            f'{quality}k'
        ]

    cmd += ['-y', str(output_path)]

    try:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        
        while True:
            if cancel_event.is_set():
                process.terminate()
                process.wait(timeout=2)
                return None
            
            retcode = process.poll()
            if retcode is not None:
                if retcode == 0:
                    return str(output_path)
                
                else:
                    return None
                
            time.sleep(0.1)

    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        return None