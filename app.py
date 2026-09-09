
import os, re, math, wave, struct, shutil, subprocess, threading, uuid
from pathlib import Path
from flask import Flask, render_template, request, jsonify, send_file

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024 * 1024  # 2 GB

BASE = Path(__file__).resolve().parent
WORK = Path(os.environ.get("AC_WORK", BASE / "work"))
OUTPUT = Path(os.environ.get("AC_OUTPUT", BASE / "output"))
WORK.mkdir(parents=True, exist_ok=True)
OUTPUT.mkdir(parents=True, exist_ok=True)

JOBS = {}
LOCK = threading.Lock()
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}

def run(cmd):
    p = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if p.returncode:
        raise RuntimeError(p.stderr[-7000:])
    return p

def natural_key(path):
    return [int(x) if x.isdigit() else x.lower() for x in re.split(r"(\d+)", path.name)]

def media_duration(path):
    return float(run([
        "ffprobe","-v","error","-show_entries","format=duration",
        "-of","default=noprint_wrappers=1:nokey=1",str(path)
    ]).stdout.strip())

def audio_energy(path, buckets):
    wav_path = WORK / f"{uuid.uuid4().hex}.wav"
    run(["ffmpeg","-y","-i",str(path),"-ac","1","-ar","8000","-f","wav",str(wav_path)])
    with wave.open(str(wav_path),"rb") as f:
        raw = f.readframes(f.getnframes())
    wav_path.unlink(missing_ok=True)
    if not raw:
        return [0.5] * buckets
    samples = struct.unpack("<" + "h" * (len(raw)//2), raw)
    step = max(1, len(samples)//buckets)
    vals = []
    for i in range(buckets):
        chunk = samples[i*step:min(len(samples),(i+1)*step)]
        vals.append(math.sqrt(sum(v*v for v in chunk)/len(chunk))/32768.0 if chunk else 0.0)
    lo, hi = min(vals), max(vals)
    return [0.5]*buckets if hi-lo < 1e-8 else [(v-lo)/(hi-lo) for v in vals]

def make_scene_durations(n, total, energies):
    if n <= 1:
        return [total]
    lo, hi = 2.0, 8.0
    if total < lo*n or total > hi*n:
        return [total/n]*n
    base = total/n
    d = []
    for i in range(n):
        e = energies[min(len(energies)-1, int((i+0.5)*len(energies)/n))]
        d.append(max(lo, min(hi, base*(1.20-0.40*e))))
    for _ in range(60):
        diff = total-sum(d)
        if abs(diff) < 0.005: break
        idx = [i for i,x in enumerate(d) if (diff>0 and x<hi-1e-6) or (diff<0 and x>lo+1e-6)]
        if not idx: break
        for i in idx:
            room = (hi-d[i]) if diff>0 else (d[i]-lo)
            delta = max(-room, min(room, diff/len(idx)))
            d[i] += delta
    return d

MOTIONS = [
    ("in", "min(zoom+0.0009,1.14)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    ("out", "max(zoom-0.0009,1.0)", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"),
    ("left", "1.08", "iw/2-(iw/zoom/2)-on*1.0", "ih/2-(ih/zoom/2)"),
    ("right", "1.08", "iw/2-(iw/zoom/2)+on*1.0", "ih/2-(ih/zoom/2)"),
    ("up", "1.08", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)-on*.7"),
    ("down", "1.08", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)+on*.7"),
    ("diag", "1.08", "iw/2-(iw/zoom/2)+on*.7", "ih/2-(ih/zoom/2)-on*.45"),
    ("diag2", "1.08", "iw/2-(iw/zoom/2)-on*.7", "ih/2-(ih/zoom/2)+on*.45"),
]

def render_chapter(images, audio, out, job_id, chapter_no):
    total = media_duration(audio)
    energies = audio_energy(audio, max(60, len(images)*2))
    durations = make_scene_durations(len(images), total, energies)
    chapter_dir = WORK / job_id / f"chapter_{chapter_no}"
    chapter_dir.mkdir(parents=True, exist_ok=True)
    clips = []
    for i, (image, seconds) in enumerate(zip(images, durations)):
        frames = max(1, round(seconds*30))
        _, z, x, y = MOTIONS[i % len(MOTIONS)]
        vf = (
            "scale=1920:1080:force_original_aspect_ratio=increase,"
            "crop=1920:1080,"
            f"zoompan=z='{z}':x='{x}':y='{y}':d={frames}:s=1920x1080:fps=30,setsar=1"
        )
        clip = chapter_dir / f"scene_{i:04d}.mp4"
        run([
            "ffmpeg","-y","-loop","1","-i",str(image),
            "-vf",vf,"-frames:v",str(frames),
            "-c:v","libx264","-preset","veryfast","-crf","20",
            "-pix_fmt","yuv420p","-an",str(clip)
        ])
        clips.append(clip)
        with LOCK:
            JOBS[job_id]["progress"] = min(90, 10 + int((i+1)/len(images)*70))
    concat = chapter_dir / "concat.txt"
    concat.write_text("".join(f"file '{str(c).replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'\n" for c in clips), encoding="utf-8")
    silent = chapter_dir / "silent.mp4"
    run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat),"-c","copy",str(silent)])
    muxed = chapter_dir / "chapter.mp4"
    run(["ffmpeg","-y","-i",str(silent),"-i",str(audio),"-map","0:v:0","-map","1:a:0","-c:v","copy","-c:a","aac","-b:a","192k","-shortest",str(muxed)])
    return muxed, total, len(images)

def worker(job_id, form_data, files):
    try:
        job_dir = WORK / job_id
        job_dir.mkdir(parents=True, exist_ok=True)
        chapters = []
        for ch in range(1, 8):
            audio = files.get(f"audio_{ch}")
            imgs = files.getlist(f"images_{ch}")
            text = form_data.get(f"text_{ch}", "").strip()
            valid_imgs = [x for x in imgs if x and x.filename]
            if not (audio and audio.filename) or not valid_imgs:
                continue
            cdir = job_dir / f"input_{ch}"
            cdir.mkdir(parents=True, exist_ok=True)
            ap = cdir / Path(audio.filename).name
            audio.save(ap)
            image_paths = []
            for idx, im in enumerate(valid_imgs, 1):
                ext = Path(im.filename).suffix.lower()
                if ext not in IMAGE_EXTS:
                    continue
                ip = cdir / f"{idx:04d}{ext}"
                im.save(ip)
                image_paths.append(ip)
            (cdir / "naskah.txt").write_text(text, encoding="utf-8")
            if not image_paths:
                continue
            out, secs, count = render_chapter(image_paths, ap, job_dir / f"chapter_{ch}.mp4", job_id, ch)
            chapters.append((ch, out, secs, count))
            with LOCK:
                JOBS[job_id]["chapters"] = [{"chapter": a, "scenes": c, "audio_seconds": round(s,2)} for a,_,s,c in chapters]
                JOBS[job_id]["progress"] = min(95, 10 + int(ch/7*80))
        if not chapters:
            raise RuntimeError("Tidak ada BAB yang berisi audio + gambar.")
        final = OUTPUT / f"AutoCinematic_{job_id}.mp4"
        concat = job_dir / "chapters.txt"
        concat.write_text("".join(f"file '{str(p)}'\n" for _,p,_,_ in chapters), encoding="utf-8")
        run(["ffmpeg","-y","-f","concat","-safe","0","-i",str(concat),"-c","copy",str(final)])
        final_secs = media_duration(final)
        with LOCK:
            JOBS[job_id].update(status="done", progress=100, url=f"/download/{job_id}", final_seconds=round(final_secs,2))
    except Exception as e:
        with LOCK:
            JOBS[job_id].update(status="error", progress=0, error=str(e))

@app.get("/")
def index():
    return render_template("index.html")

@app.post("/create")
def create():
    job_id = uuid.uuid4().hex
    with LOCK:
        JOBS[job_id] = {"status":"queued","progress":0,"chapters":[]}
    form_data = request.form.to_dict(flat=True)
    files = request.files
    t = threading.Thread(target=worker, args=(job_id, form_data, files), daemon=True)
    t.start()
    return jsonify({"job_id": job_id})

@app.get("/status/<job_id>")
def status(job_id):
    with LOCK:
        data = dict(JOBS.get(job_id, {"status":"missing"}))
    return jsonify(data)

@app.get("/download/<job_id>")
def download(job_id):
    path = OUTPUT / f"AutoCinematic_{job_id}.mp4"
    if not path.exists():
        return jsonify({"error":"Video belum siap."}), 404
    return send_file(path, as_attachment=True, download_name=path.name)

@app.get("/health")
def health():
    return jsonify({"ok": True})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, threaded=True)
