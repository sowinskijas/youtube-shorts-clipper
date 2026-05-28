import os
import json
import time
import queue
import threading
from flask import Flask, render_template, request, Response, jsonify, send_from_directory
from clipper import ClipGenerator

app = Flask(__name__)

CLIPS_DIR = os.path.join(os.path.dirname(__file__), 'clips')
os.makedirs(CLIPS_DIR, exist_ok=True)

# Active job queues: job_id -> Queue
_jobs: dict[str, queue.Queue] = {}


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/generate', methods=['POST'])
def generate():
    data = request.get_json(force=True)
    url = (data.get('url') or '').strip()
    if not url:
        return jsonify({'error': 'URL is required'}), 400

    max_clips = max(1, min(20, int(data.get('max_clips', 8))))
    clip_duration = max(10, min(59, int(data.get('clip_duration', 30))))
    start_time = (data.get('start_time') or '').strip()
    end_time = (data.get('end_time') or '').strip()

    job_id = str(int(time.time() * 1000))
    q: queue.Queue = queue.Queue()
    _jobs[job_id] = q

    def run():
        gen = ClipGenerator(CLIPS_DIR, job_id)
        gen.generate(url, max_clips, clip_duration, start_time, end_time, q)

    threading.Thread(target=run, daemon=True).start()
    return jsonify({'job_id': job_id})


@app.route('/progress/<job_id>')
def progress(job_id):
    q = _jobs.get(job_id)
    if q is None:
        return Response('data: {"type":"error","message":"Job not found"}\n\n',
                        mimetype='text/event-stream')

    def stream():
        while True:
            try:
                msg = q.get(timeout=25)
                yield f'data: {json.dumps(msg)}\n\n'
                if msg.get('type') in ('done', 'error'):
                    _jobs.pop(job_id, None)
                    break
            except queue.Empty:
                yield 'data: {"type":"ping"}\n\n'

    return Response(stream(), mimetype='text/event-stream',
                    headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})


@app.route('/clips/<path:filename>')
def serve_clip(filename):
    return send_from_directory(CLIPS_DIR, filename)


if __name__ == '__main__':
    print('\n  Shorts Clipper running → http://localhost:5050\n')
    app.run(debug=False, port=5050, threaded=True)
