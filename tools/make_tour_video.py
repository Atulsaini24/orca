"""Record the guided tour as a narrated video with an original score.

    python tools/make_tour_video.py                 # record + narrate + score + mix -> docs/orca-tour.mp4
    python tools/make_tour_video.py --skip-record   # reuse the last recording, redo the sound only

Pipeline
  1. Record: headless Chromium (Playwright, GPU via ANGLE) opens the control room in its
     deterministic capture mode and steps the guided tour frame by frame at 30 fps.
     The engine runs on the bundled sample of real NYC TLC trips. Every caption that
     appears on screen is logged with its time, so the narration says what the video shows.
  2. Narrate: one voice-over line per chapter, spoken by a neural voice (edge-tts) when it
     is installed, otherwise Google TTS (gTTS). Each line is fitted inside its chapter.
  3. Score: an original 100-second soundtrack synthesised here (pads, bass, drums, arpeggio,
     risers) whose sections follow the tour's chapters. No third-party music.
  4. Mix: the score is ducked under the voice, the mix is loudness-normalised to -16 LUFS,
     and muxed with the video as AAC. WebVTT captions and a poster frame are written too.

Needs: playwright (with Chromium), imageio-ffmpeg, numpy, scipy, and gTTS or edge-tts.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy.io import wavfile
from scipy.signal import fftconvolve

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
WORK = ROOT / "build-test" / "tour"
SR, FPS, SIZE = 48_000, 30, (1280, 720)


def ffmpeg() -> str:
    import imageio_ffmpeg
    return imageio_ffmpeg.get_ffmpeg_exe()


# ----------------------------------------------------------------------------- 1. record
def record(out: Path) -> list[dict]:
    from playwright.sync_api import sync_playwright
    url = (ROOT / "frontend" / "index.html").resolve().as_uri() + "?capture"
    enc = subprocess.Popen([ffmpeg(), "-v", "error", "-y", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
                            "-c:v", "libx264", "-preset", "slow", "-crf", "18", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)],
                           stdin=subprocess.PIPE)
    chapters: list[dict] = []
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-angle=d3d11", "--enable-gpu", "--ignore-gpu-blocklist"])
        pg = b.new_page(viewport={"width": SIZE[0], "height": SIZE[1]})
        pg.goto(url)
        pg.wait_for_function("document.title === 'capture-ready'", timeout=180_000)
        total = pg.evaluate("__orca.total")
        pg.evaluate("__orca.startTour()")
        n = round(total * FPS)
        for i in range(n):
            pg.evaluate("__orca.step(1/30)")
            cap = pg.evaluate("[document.querySelector('#capCh').textContent, document.querySelector('#capT').textContent, document.querySelector('#capB').textContent]")
            if cap[0] and (not chapters or chapters[-1]["ch"] != cap[0]):
                chapters.append(dict(ch=cap[0], title=cap[1], body=cap[2], t=i / FPS, history=[]))
            if chapters and (not chapters[-1]["history"] or chapters[-1]["history"][-1][1] != cap[2]):
                chapters[-1]["history"].append((i / FPS, cap[2]))     # live captions (the result) change in-chapter
            enc.stdin.write(pg.screenshot(type="jpeg", quality=94))
            if i % 300 == 0:
                print(f"  recorded {i / FPS:5.1f}s / {total}s")
        b.close()
    enc.stdin.close()
    enc.wait()
    return chapters


# ----------------------------------------------------------------------------- 2. narrate
SCRIPT = [   # one line per chapter; None = derived from what the caption shows (data-driven chapters)
    "This is ORCA: twelve hundred cars on Manhattan's real streets, with real New York taxi demand.",
    "Every request goes to two dispatchers: the nearest-car rule, and ORCA. Same riders, same traffic.",
    "Yellow cars head to pickups. Blue cars carry riders. Grey cars are free. Rings are waiting riders.",
    "ORCA just assigned this rider. It scored ten nearby cars on drive time, acceptance, GPS freshness, and the demand around them.",
    None,
    "Every three seconds, ORCA solves all waiting requests together, as one optimal match.",
    "Now a network outage. Cars in the red circle go silent. The nearest-car rule keeps offering them rides. ORCA works around them.",
    "Then a demand spike. A learned forecast sees it coming, and ORCA moves spare cars in before they're needed.",
    None,
    "Real streets, real buildings, real riders, in hand-written WebGL. Now it's your turn: open the control room.",
]


def spoken(text: str) -> str:
    """Make on-screen caption text read naturally."""
    text = re.sub(r"\bCar 0*(\d+)", r"car \1", text)
    text = re.sub(r"\b(\d+):(\d\d)\b", lambda m: (f"{int(m[1])} minute{'s' if int(m[1]) != 1 else ''} " if int(m[1]) else "") + f"{int(m[2])} seconds", text)
    text = text.replace("%", " percent")
    return text


def line_for(i: int, cap: dict) -> str:
    if SCRIPT[i]:
        return SCRIPT[i]
    body = cap["body"]
    for t, b in cap.get("history", []):          # what the caption says about 3 s in, when the voice gets there
        if t <= cap["t"] + 3:
            body = b
    if i == 8:   # the result: say the headline number shown on screen
        m = re.search(r"(\d+)% less", body)
        return (f"The result: {m[1]} percent shorter waits than nearest-car dispatch, on the very same requests." if m
                else "The result: on the very same requests, ORCA riders wait less.")
    body = (body.replace("would have sent", "would send").replace(", shown in grey", "")
            .replace("high-demand zone", "busy zone").replace("It is one of", "But it's one of"))
    return spoken(body)


def tts(text: str, path: Path, engine: str, attempts: int = 5):
    """Synthesise one line; online speech services fail transiently, so retry with exponential backoff."""
    import time as _time
    for k in range(attempts):
        try:
            if engine == "edge":
                import edge_tts
                asyncio.run(edge_tts.Communicate(text, "en-US-AndrewMultilingualNeural", rate="+4%").save(str(path)))
            else:
                from gtts import gTTS
                gTTS(text, lang="en", tld="com").save(str(path))
            if path.exists() and path.stat().st_size > 1000:
                return
        except Exception as exc:  # network or service error
            if k == attempts - 1:
                raise RuntimeError(f"speech synthesis failed after {attempts} attempts: {exc}") from exc
        _time.sleep(2 ** k)
    raise RuntimeError("speech synthesis returned no audio")


def load_audio(path: Path, tempo: float = 1.0) -> np.ndarray:
    wav = path.with_suffix(f".{tempo:.2f}.wav")
    af = ["silenceremove=start_periods=1:start_threshold=-45dB:stop_periods=-1:stop_threshold=-45dB:stop_duration=0.35"]
    if abs(tempo - 1) > 1e-3:
        af.append(f"atempo={tempo:.3f}")
    af.append("highpass=f=70,acompressor=threshold=-20dB:ratio=3:attack=5:release=80")
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", str(path), "-af", ",".join(af), "-ar", str(SR), "-ac", "1", str(wav)], check=True)
    _sr, x = wavfile.read(wav)
    x = x.astype(np.float32) / 32768.0
    return x / max(1e-6, np.abs(x).max()) * 0.89


def narrate(chapters: list[dict], durations: list[float], engine: str):
    voice = np.zeros(int(SR * sum(durations)), np.float32)
    cues = []
    start = 0.0
    for i, (cap, dur) in enumerate(zip(chapters, durations)):
        text = line_for(i, cap)
        mp3 = WORK / f"vo{i:02d}.mp3"
        tts(text, mp3, engine)
        x, budget = load_audio(mp3), dur - 0.9
        tempo = 1.0
        if len(x) / SR > budget:
            tempo = min(1.35, (len(x) / SR) / budget)
            x = load_audio(mp3, tempo)
        if len(x) / SR > budget:
            raise SystemExit(f"Chapter {i + 1} narration is {len(x) / SR:.1f}s for a {budget:.1f}s slot even at 1.35x: shorten it.\n  {text}")
        t0 = start + 0.45
        a = int(t0 * SR)
        voice[a:a + len(x)] += x
        cues.append((t0, t0 + len(x) / SR, text))
        print(f"  ch{i + 1:2d}  {len(x) / SR:4.1f}s of {dur:4.1f}s  x{tempo:.2f}  {text[:70]}…")
        start += dur
    return voice, cues


# ----------------------------------------------------------------------------- 3. score
def note(n: str) -> float:
    names = {"C": -9, "C#": -8, "D": -7, "D#": -6, "E": -5, "F": -4, "F#": -3, "G": -2, "G#": -1, "A": 0, "A#": 1, "B": 2}
    return 440.0 * 2 ** ((names[n[:-1]] + 12 * (int(n[-1]) - 4)) / 12)


def score(total: float, marks: list[float], seed: int = 7) -> np.ndarray:
    """An original ambient-electronic cue, 96 BPM, Am-F-C-G, sections following the tour chapters."""
    rng = np.random.default_rng(seed)
    n = int(SR * total)
    t = np.arange(n) / SR
    L, R = np.zeros(n, np.float32), np.zeros(n, np.float32)
    beat = 60 / 96
    bar = 4 * beat
    chords = [["A2", "A3", "C4", "E4", "B4"], ["F2", "F3", "A3", "C4", "E4"], ["C3", "G3", "C4", "E4", "G4"], ["G2", "G3", "B3", "D4", "A4"]]
    m = marks + [total]                       # chapter starts
    sec = lambda k: m[k]                      # start of chapter k (0-based)

    def intensity(x):                          # 0..1 energy curve over the tour
        pts = [(0, .15), (sec(2), .4), (sec(3), .55), (sec(6), .7), (sec(7), .95), (sec(8) - .1, 1.0), (sec(8), .8), (sec(9), .45), (total, .0)]
        xs, ys = zip(*pts)
        return np.interp(x, xs, ys)

    def add(sig, start, pan=0.0, gain=1.0):
        a = int(start * SR)
        if a >= n:
            return
        sig = sig[: n - a] * gain
        L[a:a + len(sig)] += sig * np.sqrt(0.5 * (1 - pan))
        R[a:a + len(sig)] += sig * np.sqrt(0.5 * (1 + pan))

    # pads: detuned additive tones, two bars per chord, soft attack and release
    for k, s0 in enumerate(np.arange(0, total, 2 * bar)):
        ch = chords[k % 4]
        d = 2 * bar + 1.2
        tt = np.arange(int(d * SR)) / SR
        env = np.minimum(1, tt / 1.1) * np.minimum(1, (d - tt) / 1.4)
        for _ni, nn in enumerate(ch[1:]):
            f = note(nn)
            for det, pan in ((-0.004, -0.6), (0.004, 0.6)):
                w = sum(np.sin(2 * np.pi * f * (1 + det) * h * tt + rng.random() * 6.28) / h ** 1.6 for h in range(1, 6))
                add((w * env * 0.05).astype(np.float32), s0, pan)
    # sub bass: root on beats 1 and 3, enters with chapter 2
    for k, s0 in enumerate(np.arange(0, total, bar)):
        if s0 < sec(1) - 0.01:
            continue
        f = note(chords[(k // 2) % 4][0])
        for b in (0, 2):
            tt = np.arange(int(beat * 2 * SR)) / SR
            env = np.exp(-tt * 2.2) * np.minimum(1, tt / 0.01)
            add(((np.sin(2 * np.pi * f * tt) + 0.25 * np.sin(4 * np.pi * f * tt)) * env * 0.24).astype(np.float32), s0 + b * beat)
    # drums from chapter 3: kick on the beat, hats on the off-beat, ghost snare on 2 and 4 in the build
    tt = np.arange(int(0.35 * SR)) / SR
    kick = (np.sin(2 * np.pi * (45 * tt + 75 / 18 * (1 - np.exp(-18 * tt)))) * np.exp(-tt * 9)).astype(np.float32)
    ht = np.arange(int(0.06 * SR)) / SR
    hat = (np.diff(rng.standard_normal(len(ht) + 1)) * np.exp(-ht * 70)).astype(np.float32)
    st = np.arange(int(0.2 * SR)) / SR
    snare = ((rng.standard_normal(len(st)) * 0.6 + np.sin(2 * np.pi * 190 * st)) * np.exp(-st * 22)).astype(np.float32)
    for j, s0 in enumerate(np.arange(sec(2), sec(9), beat)):
        e = float(intensity(s0))
        add(kick, s0, 0, 0.42 * e)
        add(hat, s0 + beat / 2, 0.3, 0.09 * e)
        if s0 >= sec(6) and j % 2 == 1:
            add(snare, s0, -0.1, 0.13 * e)
        if s0 >= sec(7):
            add(hat, s0 + beat / 4, -0.3, 0.05 * e)
            add(hat, s0 + 3 * beat / 4, -0.3, 0.05 * e)
    # arpeggio: plucked chord tones in eighths, chapters 4-9
    pt = np.arange(int(0.45 * SR)) / SR
    for j, s0 in enumerate(np.arange(sec(3), sec(9), beat / 2)):
        ch = chords[int(s0 // (2 * bar)) % 4]
        f = note(ch[2 + j % 3]) * 2
        pl = sum(np.sin(2 * np.pi * f * h * pt) / h ** 2 for h in (1, 2, 3)) * np.exp(-pt * 9)
        add(pl.astype(np.float32), s0, 0.5 if j % 2 else -0.5, 0.05 * float(intensity(s0)))
    # risers into the outage and the spike, a swell into the result
    for k in (6, 7, 8):
        d = 3.0
        rt = np.arange(int(d * SR)) / SR
        noise = rng.standard_normal(len(rt)).astype(np.float32)
        noise = fftconvolve(noise, np.ones(24) / 24, mode="same")      # soften
        add((noise * (rt / d) ** 2.5 * 0.12).astype(np.float32), sec(k) - d, 0, 1.0)
        ct = np.arange(int(2.5 * SR)) / SR
        crash = np.diff(rng.standard_normal(len(ct) + 1)) * np.exp(-ct * 2.4)
        add((crash * 0.07).astype(np.float32), sec(k), 0, 1.0)
    # reverb: stereo exponentially decaying noise impulse
    ir_t = np.arange(int(2.2 * SR)) / SR
    irL = (rng.standard_normal(len(ir_t)) * np.exp(-ir_t * 3)).astype(np.float32)
    irR = (rng.standard_normal(len(ir_t)) * np.exp(-ir_t * 3)).astype(np.float32)
    irL /= np.sqrt((irL ** 2).sum()); irR /= np.sqrt((irR ** 2).sum())
    wl, wr = fftconvolve(L, irL)[:n], fftconvolve(R, irR)[:n]
    L, R = L + 0.35 * wl, R + 0.35 * wr
    fade = np.minimum(1, t / 2.0) * np.minimum(1, (total - t) / 3.5)
    mix = np.stack([L, R], 1) * fade[:, None]
    mix = np.tanh(mix * 1.4)
    return (mix / np.abs(mix).max() * 0.9).astype(np.float32)


# ----------------------------------------------------------------------------- 4. mix
def mix(voice: np.ndarray, music: np.ndarray) -> np.ndarray:
    env = np.abs(voice)
    k = int(0.03 * SR)
    env = np.convolve(env, np.ones(k) / k, mode="same")
    active = (env > 0.02).astype(np.float32)
    att, rel = np.exp(-1 / (0.08 * SR)), np.exp(-1 / (0.6 * SR))
    from scipy.signal import lfilter
    up = lfilter([1 - att], [1, -att], active)                       # follow quickly when the voice starts
    duck = np.maximum(up, lfilter([1 - rel], [1, -rel], active))     # release slowly after it stops
    gain = 0.55 - 0.36 * np.clip(duck * 1.6, 0, 1)                   # -5 dB bed, about -15 dB under speech
    out = music * gain[:, None] + voice[:, None] * 0.95
    return out / max(1.0, np.abs(out).max() / 0.97)


def vtt(cues, path: Path):
    ts = lambda s: f"{int(s // 3600):02d}:{int(s % 3600 // 60):02d}:{s % 60:06.3f}"
    path.write_text("WEBVTT\n\n" + "\n\n".join(f"{i + 1}\n{ts(a)} --> {ts(b)}\n{txt}" for i, (a, b, txt) in enumerate(cues)) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--skip-record", action="store_true")
    ap.add_argument("--tts", choices=["auto", "edge", "gtts"], default="auto")
    a = ap.parse_args()
    WORK.mkdir(parents=True, exist_ok=True)
    silent, meta = WORK / "tour-silent.mp4", WORK / "chapters.json"
    if not a.skip_record:
        print("1/4 recording the tour on real NYC demand")
        meta.write_text(json.dumps(record(silent), indent=2))
    chapters = json.loads(meta.read_text())
    marks = [c["t"] for c in chapters]
    probe = subprocess.run([ffmpeg(), "-i", str(silent)], capture_output=True, text=True).stderr
    h, mi, s = re.search(r"Duration: (\d+):(\d+):([\d.]+)", probe).groups()
    total = int(h) * 3600 + int(mi) * 60 + float(s)
    durations = [b - a_ for a_, b in zip(marks, marks[1:] + [total])]
    engine = a.tts
    if engine == "auto":
        try:
            import edge_tts  # noqa: F401
            engine = "edge"
        except ImportError:
            engine = "gtts"
    print(f"2/4 narration ({'neural voice, edge-tts' if engine == 'edge' else 'Google TTS'})")
    voice, cues = narrate(chapters, durations, engine)
    print("3/4 composing the score")
    music = score(total, marks)
    print("4/4 mixing and encoding")
    out = mix(voice[: len(music)], music)
    wavfile.write(WORK / "mix.wav", SR, (out * 32767).astype(np.int16))
    wavfile.write(WORK / "score.wav", SR, (music * 32767).astype(np.int16))
    DOCS.mkdir(exist_ok=True)
    # Loudness-normalise once, then encode two deliverables from the same master audio.
    master = WORK / "mix-norm.wav"
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", str(WORK / "mix.wav"), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                    "-ar", str(SR), str(master)], check=True)
    # H.264 + AAC: plays everywhere (Safari, iOS, old browsers).
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", str(silent), "-i", str(master),
                    "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "veryslow", "-crf", "27", "-tune", "film",
                    "-profile:v", "high", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
                    "-shortest", "-movflags", "+faststart", str(DOCS / "orca-tour.mp4")], check=True)
    # VP9 + Opus in WebM: roughly half the size at the same quality, used by Chrome, Edge and Firefox.
    webm = DOCS / "orca-tour.webm"
    vp9 = ["-c:v", "libvpx-vp9", "-b:v", "1500k", "-maxrate", "2400k", "-bufsize", "3000k", "-row-mt", "1", "-tile-columns", "2", "-deadline", "good",
           "-cpu-used", "2", "-pix_fmt", "yuv420p"]
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", str(silent), *vp9, "-pass", "1", "-an", "-passlogfile",
                    str(WORK / "vp9"), "-f", "null", "-"], check=True)
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", str(silent), "-i", str(master), "-map", "0:v", "-map", "1:a", *vp9,
                    "-pass", "2", "-passlogfile", str(WORK / "vp9"), "-c:a", "libopus", "-b:a", "128k", "-shortest",
                    str(webm)], check=True)
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-i", str(WORK / "score.wav"), "-af", "loudnorm=I=-20:TP=-2",
                    "-c:a", "libmp3lame", "-b:a", "160k", str(DOCS / "orca-tour-score.mp3")], check=True)
    subprocess.run([ffmpeg(), "-v", "error", "-y", "-ss", "5", "-i", str(silent), "-frames:v", "1", "-q:v", "3",
                    str(DOCS / "orca-tour-poster.jpg")], check=True)
    vtt(cues, DOCS / "orca-tour.vtt")
    mb = lambda f: (DOCS / f).stat().st_size / 1e6
    print(f"Wrote docs/orca-tour.mp4 ({mb('orca-tour.mp4'):.1f} MB), orca-tour.webm ({mb('orca-tour.webm'):.1f} MB), "
          "orca-tour.vtt, orca-tour-poster.jpg, orca-tour-score.mp3")


if __name__ == "__main__":
    sys.exit(main())
