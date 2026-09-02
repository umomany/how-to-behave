"""Render every clip in audio/ with edge-tts, keyed off the manifest.

Filenames are the manifest keys, which index.html recomputes in JS — never rename.
Resumable: existing mp3s are skipped, so re-running finishes an interrupted batch.
"""
import json, os, re, sys, time, asyncio, subprocess
import edge_tts

VOICE = "ar-SA-HamedNeural"
RATE = "-10%"
MANIFEST = "manifest_tashkeel.json"   # audio/manifest.json for the undiacritized text
OUT = "audio"
CONCURRENCY = 5
RETRIES = 3

def prep(s):
    s = s.replace("«", "").replace("»", "").replace("﴿", "").replace("﴾", "")
    return re.sub(r"\s+", " ", s).strip()

async def render(key, text, sem, failed, done):
    mp3 = f"{OUT}/{key}.mp3"
    if os.path.exists(mp3):
        done.append(key)
        return
    async with sem:
        for attempt in range(RETRIES):
            try:
                buf = b""
                async for ch in edge_tts.Communicate(prep(text), VOICE, rate=RATE).stream():
                    if ch["type"] == "audio":
                        buf += ch["data"]
                if not buf:
                    raise RuntimeError("no audio received")
                break
            except Exception as e:
                if attempt == RETRIES - 1:
                    failed[key] = f"{type(e).__name__}: {e}"
                    return
                await asyncio.sleep(2 ** attempt)          # 1s, 2s
        # Encode to a temp name and rename, so an interrupted run never leaves a
        # truncated mp3 that the skip-check above would later treat as complete.
        tmp = f"{OUT}/.{key}.part.mp3"
        p = await asyncio.create_subprocess_exec(
            "ffmpeg", "-v", "error", "-y", "-i", "pipe:0",
            "-af", "silenceremove=start_periods=1:start_threshold=-45dB,loudnorm=I=-16:TP=-1.5:LRA=11",
            "-c:a", "libmp3lame", "-b:a", "48k", "-ar", "22050", tmp,
            stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        _, err = await p.communicate(buf)
        if p.returncode != 0:
            failed[key] = f"ffmpeg: {err.decode()[:120]}"
            return
        os.replace(tmp, mp3)
        done.append(key)
        if len(done) % 40 == 0:
            print(f"  {len(done)}/{TOTAL}  elapsed={(time.time() - T0) / 60:.1f}min", flush=True)

async def main():
    man = json.load(open(MANIFEST, encoding="utf-8"))
    sem, failed, done = asyncio.Semaphore(CONCURRENCY), {}, []
    globals().update(TOTAL=len(man), T0=time.time())
    print(f"{len(man)} clips | {VOICE} @ {RATE} | from {MANIFEST}", flush=True)
    await asyncio.gather(*(render(k, v, sem, failed, done) for k, v in man.items()))

    mp3s = sorted(f for f in os.listdir(OUT) if f.endswith(".mp3"))
    small = [f for f in mp3s if os.path.getsize(f"{OUT}/{f}") < 3072]
    size = sum(os.path.getsize(f"{OUT}/{f}") for f in mp3s)
    dur = sum(float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                    "-of", "csv=p=0", f"{OUT}/{f}"], capture_output=True, text=True).stdout or 0)
              for f in mp3s)
    print(f"\nfiles: {len(mp3s)} (expected {len(man)}) {'OK' if len(mp3s) == len(man) else 'MISMATCH'}")
    print(f"under 3KB: {len(small)}{' -> ' + ', '.join(small) if small else ''}")
    print(f"total size: {size / 1e6:.1f} MB | total duration: {dur / 60:.1f} min")
    print(f"missing keys: {sorted(set(man) - {f[:-4] for f in mp3s}) or 'none'}")
    if failed:
        print(f"FAILED {len(failed)}:")
        for k, e in failed.items():
            print(f"  {k}  {e}")
    return 1 if (failed or small or len(mp3s) != len(man)) else 0

sys.exit(asyncio.run(main()))
