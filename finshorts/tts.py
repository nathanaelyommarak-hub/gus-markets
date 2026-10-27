"""TTS (Kokoro-82M, Apache-2.0) with word-level timestamps. Fallbacks: Piper + faster-whisper alignment."""
import re, subprocess, numpy as np, soundfile as sf
from pathlib import Path

SR = 24000
_PIPE = {}


def _merge_tokens(tokens, offset):
    """Kokoro tokens -> words [{w, s, e}], gluing punctuation and '$' to neighbours."""
    words, pending = [], ""
    for t in tokens:
        txt = t.text
        if t.start_ts is None or t.end_ts is None or re.fullmatch(r"[$€£]", txt):
            if re.fullmatch(r"[^\w$€£]+", txt) and words:
                words[-1]["w"] += txt
            else:
                pending += txt
            continue
        if re.fullmatch(r"[^\w$€£%]+", txt) and words:
            words[-1]["w"] += txt
            words[-1]["e"] = offset + t.end_ts
            continue
        words.append({"w": pending + txt, "s": offset + t.start_ts, "e": offset + t.end_ts})
        pending = ""
    return words


def kokoro_tts(text, out_wav, voice="am_michael", speed=1.0, lang_code="a", repo_id="hexgrad/Kokoro-82M"):
    import torch
    from kokoro import KPipeline
    torch.set_num_threads(max(1, __import__("os").cpu_count() or 2))
    if lang_code not in _PIPE:
        _PIPE[lang_code] = KPipeline(lang_code=lang_code, repo_id=repo_id)
    pipe = _PIPE[lang_code]
    chunks, words, off = [], [], 0.0
    for r in pipe(text, voice=voice, speed=speed, split_pattern=r"\n+"):
        a = r.audio.cpu().numpy() if hasattr(r.audio, "cpu") else np.asarray(r.audio)
        words += _merge_tokens(r.tokens or [], off)
        chunks.append(a)
        off += len(a) / SR
    audio = np.concatenate(chunks)
    sf.write(out_wav, audio, SR)
    return words


def piper_tts(text, out_wav, model_path, length_scale=1.0, piper_bin="piper"):
    subprocess.run([piper_bin, "-m", model_path, "--length-scale", str(length_scale), "-f", out_wav],
                   input=text.encode(), check=True, capture_output=True)
    return None


def whisper_words(wav, model="base.en"):
    from faster_whisper import WhisperModel
    m = WhisperModel(model, device="cpu", compute_type="int8")
    segs, _ = m.transcribe(wav, word_timestamps=True, language="en")
    return [{"w": w.word.strip(), "s": w.start, "e": w.end} for s in segs for w in s.words]


def synthesize(cfg, text, out_wav):
    t = cfg["tts"]
    words = None
    if t["engine"] == "kokoro":
        k = t["kokoro"]
        words = kokoro_tts(text, out_wav, k["voice"], k["speed"], k["lang_code"], k["repo_id"])
    else:
        p = t["piper"]
        piper_tts(text, out_wav, p["model"], p["length_scale"])
    if t.get("subtitles") == "whisper" or not words:
        words = whisper_words(out_wav)
    return words
