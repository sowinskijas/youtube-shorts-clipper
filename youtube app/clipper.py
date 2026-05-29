import os
import subprocess
import json
import shutil
import wave
import sys
import re
import numpy as np
from pathlib import Path


YT_DLP = sys.executable  # run as: python3 -m yt_dlp


def _find_peaks(energies, distance, height):
    """Peak detection without scipy dependency."""
    peaks = []
    n = len(energies)
    for i in range(1, n - 1):
        if energies[i] < height:
            continue
        if energies[i] <= energies[i - 1] or energies[i] <= energies[i + 1]:
            continue
        if peaks and i - peaks[-1] < distance:
            if energies[i] > energies[peaks[-1]]:
                peaks[-1] = i
        else:
            peaks.append(i)
    return np.array(peaks) if peaks else np.array([], dtype=int)


class ClipGenerator:
    def __init__(self, output_dir: str, job_id: str):
        self.output_dir = output_dir
        self.job_id = job_id
        self.job_dir = os.path.join(output_dir, job_id)
        os.makedirs(self.job_dir, exist_ok=True)

    def _emit(self, q, msg: dict):
        q.put(msg)

    def generate(self, url, max_clips, clip_duration, start_time, end_time, q):
        try:
            # Disk space guard (need at least 3 GB)
            free_gb = shutil.disk_usage(self.output_dir).free / (1024 ** 3)
            if free_gb < 3:
                self._emit(q, {
                    'type': 'error',
                    'message': f'Not enough disk space ({free_gb:.1f} GB free). Need at least 3 GB.'
                })
                return

            # Download
            self._emit(q, {'type': 'status', 'step': 'download', 'message': 'Downloading video (720p)...'})
            video_path = self._download(url, start_time, end_time, q)
            if not video_path:
                return

            # Video metadata
            self._emit(q, {'type': 'status', 'step': 'analyze', 'message': 'Reading video info...'})
            info = self._get_video_info(video_path)
            duration = float(info.get('duration', 0))
            width = int(info.get('width', 1920))
            height = int(info.get('height', 1080))

            if duration == 0:
                self._emit(q, {'type': 'error', 'message': 'Could not determine video duration.'})
                return

            self._emit(q, {
                'type': 'info',
                'duration': duration,
                'width': width,
                'height': height,
                'message': f'Video: {width}x{height}, {duration/60:.1f} min'
            })

            # Extract audio for analysis
            self._emit(q, {'type': 'status', 'step': 'audio', 'message': 'Extracting audio for analysis...'})
            audio_path = os.path.join(self.job_dir, 'audio.wav')
            if not self._extract_audio(video_path, audio_path):
                self._emit(q, {'type': 'error', 'message': 'Failed to extract audio from video.'})
                return

            # Find exciting moments
            self._emit(q, {'type': 'status', 'step': 'detect', 'message': 'Finding exciting moments...'})
            timestamps = self._find_moments(audio_path, duration, max_clips, clip_duration)

            if not timestamps:
                # Fallback: evenly spaced clips
                step = duration / (max_clips + 1)
                timestamps = [step * (i + 1) for i in range(max_clips) if step * (i + 1) + clip_duration <= duration]

            self._emit(q, {
                'type': 'status',
                'step': 'detect',
                'message': f'Found {len(timestamps)} moments to clip'
            })

            # Generate each clip
            clips = []
            for i, ts in enumerate(timestamps):
                self._emit(q, {
                    'type': 'status',
                    'step': 'clip',
                    'message': f'Creating clip {i + 1}/{len(timestamps)}...'
                })
                clip_path = self._make_clip(video_path, ts, clip_duration, width, height, i + 1)
                if clip_path:
                    clips.append({
                        'path': f'{self.job_id}/clip_{i + 1:03d}.mp4',
                        'timestamp': round(ts, 1),
                        'index': i + 1
                    })
                self._emit(q, {'type': 'progress', 'current': i + 1, 'total': len(timestamps)})

            # Cleanup large source files, keep clips
            try:
                os.remove(video_path)
                os.remove(audio_path)
            except OSError:
                pass

            self._emit(q, {'type': 'done', 'clips': clips, 'job_id': self.job_id})

        except Exception as e:
            self._emit(q, {'type': 'error', 'message': str(e)})

    def _download(self, url, start_time, end_time, q):
        video_path = os.path.join(self.job_dir, 'source.mp4')

        cmd = [
            YT_DLP, '-m', 'yt_dlp',
            '--format',
            'bestvideo[height<=720][ext=mp4]+bestaudio[ext=m4a]/best[height<=720][ext=mp4]/22/18/best[height<=720]/best',
            '--merge-output-format', 'mp4',
            '--extractor-args', 'youtube:player_client=ios,web_creator,web',
            '--output', video_path,
            '--no-playlist',
            '--no-part',
            '--newline',
            url
        ]

        if start_time and end_time:
            cmd.extend(['--download-sections', f'*{start_time}-{end_time}'])
        elif start_time:
            cmd.extend(['--download-sections', f'*{start_time}-inf'])

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )

            for line in proc.stdout:
                line = line.strip()
                if not line:
                    continue
                if '[download]' in line and '%' in line:
                    m = re.search(r'(\d+\.?\d*)%', line)
                    if m:
                        q.put({
                            'type': 'download_progress',
                            'percent': float(m.group(1)),
                            'message': line
                        })
                elif 'ERROR' in line:
                    q.put({'type': 'status', 'step': 'download', 'message': line})

            proc.wait()

            if proc.returncode != 0:
                q.put({
                    'type': 'error',
                    'message': (
                        'Download failed. The video may be private, age-restricted, geo-blocked, '
                        'or unavailable. Try a different URL or check that it\'s publicly accessible.'
                    )
                })
                return None

            # yt-dlp may write with a different extension if merge failed
            if not os.path.exists(video_path):
                candidates = sorted(Path(self.job_dir).glob('source.*'))
                if candidates:
                    return str(candidates[0])
                q.put({'type': 'error', 'message': 'Download seemed to complete but no file was found.'})
                return None

            return video_path

        except FileNotFoundError:
            q.put({'type': 'error', 'message': 'yt-dlp not found. Run: pip install yt-dlp'})
            return None

    def _get_video_info(self, video_path):
        cmd = [
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            '-show_streams', '-show_format',
            video_path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError:
            return {}

        info = {}
        for stream in data.get('streams', []):
            if stream.get('codec_type') == 'video':
                info['width'] = stream.get('width', 1920)
                info['height'] = stream.get('height', 1080)
                dur = stream.get('duration') or data.get('format', {}).get('duration', '0')
                info['duration'] = float(dur or 0)
                break

        if 'duration' not in info:
            info['duration'] = float(data.get('format', {}).get('duration', 0))

        return info

    def _extract_audio(self, video_path, audio_path):
        cmd = [
            'ffmpeg', '-i', video_path,
            '-vn',
            '-ar', '16000',
            '-ac', '1',
            '-sample_fmt', 's16',
            '-f', 'wav',
            '-y', audio_path
        ]
        result = subprocess.run(cmd, capture_output=True)
        return result.returncode == 0 and os.path.exists(audio_path)

    def _find_moments(self, audio_path, duration, max_clips, clip_duration):
        try:
            with wave.open(audio_path, 'rb') as wf:
                n_frames = wf.getnframes()
                sample_rate = wf.getframerate()
                n_channels = wf.getnchannels()
                raw = wf.readframes(n_frames)

            if n_channels == 1:
                samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
            else:
                interleaved = np.frombuffer(raw, dtype=np.int16).astype(np.float32)
                samples = interleaved[::n_channels]

            samples /= 32768.0

            # Sliding RMS energy
            window_sec = 0.5
            hop_sec = 0.25
            window_size = int(sample_rate * window_sec)
            hop_size = int(sample_rate * hop_sec)

            energies = []
            times = []
            for i in range(0, len(samples) - window_size, hop_size):
                chunk = samples[i:i + window_size]
                rms = float(np.sqrt(np.mean(chunk ** 2)))
                energies.append(rms)
                times.append(i / sample_rate)

            if not energies:
                return []

            energies = np.array(energies)
            times = np.array(times)

            # Smooth
            k = min(20, len(energies) // 4 or 1)
            kernel = np.ones(k) / k
            smoothed = np.convolve(energies, kernel, mode='same')

            # Threshold: mean + 0.3 std — catches more stream moments without too much noise
            threshold = float(np.mean(smoothed) + 0.3 * np.std(smoothed))
            min_distance = int(clip_duration / hop_sec)

            peaks = _find_peaks(smoothed, distance=min_distance, height=threshold)

            if len(peaks) == 0:
                return []

            # Sort by energy, take top candidates
            peak_energies = smoothed[peaks]
            sorted_idx = np.argsort(peak_energies)[::-1]
            peaks = peaks[sorted_idx[:max_clips * 2]]

            # Build clip start times (peak minus 5s for context)
            lead_in = 5
            used = []
            for p in peaks:
                pt = float(times[p])
                start = max(0.0, pt - lead_in)
                end = start + clip_duration
                if end > duration:
                    start = max(0.0, duration - clip_duration)

                # Skip if overlaps an already-chosen clip
                overlap = any(abs(start - s) < clip_duration * 0.75 for s in used)
                if not overlap:
                    used.append(start)
                if len(used) >= max_clips:
                    break

            return sorted(used)

        except Exception:
            return []

    def _make_clip(self, video_path, start_time, duration, width, height, index):
        output_path = os.path.join(self.job_dir, f'clip_{index:03d}.mp4')

        # Crop to 9:16 portrait — center crop
        target_ratio = 9 / 16
        current_ratio = width / height

        if current_ratio > target_ratio:
            # Landscape → crop width to 9/16 of height
            crop_h = height
            crop_w = int(height * target_ratio)
            crop_x = (width - crop_w) // 2
            crop_y = 0
        else:
            # Portrait or squarish → crop height
            crop_w = width
            crop_h = int(width / target_ratio)
            crop_x = 0
            crop_y = (height - crop_h) // 2

        # Codec requires even dimensions
        crop_w -= crop_w % 2
        crop_h -= crop_h % 2

        cmd = [
            'ffmpeg',
            '-ss', str(start_time),
            '-i', video_path,
            '-t', str(duration),
            '-vf', f'crop={crop_w}:{crop_h}:{crop_x}:{crop_y},scale=1080:1920',
            '-c:v', 'libx264',
            '-preset', 'fast',
            '-crf', '23',
            '-c:a', 'aac',
            '-b:a', '128k',
            '-movflags', '+faststart',
            '-y',
            output_path
        ]

        result = subprocess.run(cmd, capture_output=True)
        if result.returncode == 0 and os.path.exists(output_path):
            return output_path
        return None
