"""Procedural audio: a licence-free music bed and sound effects, plus optional offline voice.

Nothing here is a recording or a third-party asset. The music is synthesised with the
standard library (a four-chord pad, bass, kick and hats at 118 BPM) and so is every sound
effect; the result is original and carries no licence obligation. Voice-over uses a local
engine only (Piper or espeak-ng); without one the video simply has captions and music.
"""

from __future__ import annotations

import array
import json
import math
import random
import subprocess
import wave
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from apps.distribution.video import capabilities

SAMPLE_RATE = 44_100
BPM = 118
BEAT_S = 60 / BPM
CHORDS = ((57, 60, 64), (53, 57, 60), (48, 52, 55), (55, 59, 62))  # Am F C G
TTS_TIMEOUT = 25
LICENCE = (
    "Generated procedurally by this application; no third-party audio; free to use."
)


def _hz(note: float) -> float:
    return 440.0 * 2 ** ((note - 69) / 12)


def _loop(seed: int) -> array.array:
    """Eight seconds-ish of music: four chords, one chord per two bars of four beats."""
    rng = random.Random(seed)
    chord_s = BEAT_S * 4
    frames = int(chord_s * len(CHORDS) * SAMPLE_RATE)
    out = array.array("f", bytes(4 * frames))
    for index, chord in enumerate(CHORDS):
        base = int(index * chord_s * SAMPLE_RATE)
        length = int(chord_s * SAMPLE_RATE)
        freqs = [_hz(n + 12) for n in chord]
        bass = _hz(chord[0] - 12)
        for i in range(length):
            t = i / SAMPLE_RATE
            env = min(1.0, t / 0.5) * min(1.0, (chord_s - t) / 0.6)
            pad = sum(
                math.sin(2 * math.pi * f * t)
                + 0.35 * math.sin(4 * math.pi * f * 1.004 * t)
                for f in freqs
            )
            beat_t = t % BEAT_S
            kick = (
                math.sin(2 * math.pi * (46 + 90 * math.exp(-beat_t * 28)) * beat_t)
                * math.exp(-beat_t * 14)
                * 0.62
            )
            sub = (
                math.sin(2 * math.pi * bass * t)
                * 0.16
                * (0.55 + 0.45 * math.exp(-beat_t * 5))
            )
            off_t = (t + BEAT_S / 2) % BEAT_S
            hat = (rng.random() * 2 - 1) * math.exp(-off_t * 90) * 0.07
            out[base + i] += pad * 0.045 * env + kick * 0.5 + sub + hat
    return out


def _whoosh(seed: int, length_s: float = 0.5) -> array.array:
    rng = random.Random(seed)
    frames = int(length_s * SAMPLE_RATE)
    out = array.array("f", bytes(4 * frames))
    low = 0.0
    for i in range(frames):
        x = i / frames
        alpha = 0.02 + 0.55 * x
        low += alpha * ((rng.random() * 2 - 1) - low)
        out[i] = low * math.sin(math.pi * x) ** 1.6 * 0.55
    return out


def _pop() -> array.array:
    frames = int(0.14 * SAMPLE_RATE)
    out = array.array("f", bytes(4 * frames))
    for i in range(frames):
        t = i / SAMPLE_RATE
        out[i] = (
            math.sin(2 * math.pi * (520 + 360 * math.exp(-t * 40)) * t)
            * math.exp(-t * 26)
            * 0.4
        )
    return out


def _mix(
    target: array.array, source: array.array, at_s: float, gain: float = 1.0
) -> None:
    start = int(at_s * SAMPLE_RATE)
    for i, value in enumerate(source):
        j = start + i
        if j >= len(target):
            break
        target[j] += value * gain


def write_bed(path: Path, duration_ms: int, seed: int, cues_ms: Sequence[int]) -> Path:
    """Music bed of exactly ``duration_ms`` with a whoosh at every cue; returns ``path``."""
    frames = int(duration_ms / 1000 * SAMPLE_RATE)
    loop = _loop(seed)
    bed = array.array("f", bytes(4 * frames))
    for i in range(frames):
        bed[i] = loop[i % len(loop)]
    whoosh, pop = _whoosh(seed), _pop()
    for n, cue in enumerate(cues_ms):
        _mix(bed, whoosh, max(0.0, cue / 1000 - 0.28), 0.8)
        _mix(bed, pop, cue / 1000 + 0.18, 0.7 if n else 0.0)
    fade_in, fade_out = int(0.4 * SAMPLE_RATE), int(1.4 * SAMPLE_RATE)
    pcm = array.array("h", bytes(2 * frames))
    for i in range(frames):
        gain = min(1.0, i / fade_in) * min(1.0, (frames - i) / fade_out)
        pcm[i] = int(max(-1.0, min(1.0, bed[i] * 0.8 * gain)) * 32767)
    with wave.Wave_write(str(path)) as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes(pcm.tobytes())
    return path


# ---------------------------------------------------------------------- voice ----


@dataclass(frozen=True, slots=True)
class Voice:
    beat_id: str
    path: Path
    ms: int


def wav_ms(path: Path) -> int:
    with wave.open(str(path), "rb") as handle:
        return int(handle.getnframes() / handle.getframerate() * 1000)


def _speak(engine: str, text: str, path: Path) -> bool:
    try:
        if engine == "piper":
            binary, model = capabilities.piper_path()
            run = subprocess.run(
                [binary, "--model", model, "--output_file", str(path)],
                input=text.encode(),
                capture_output=True,
                timeout=TTS_TIMEOUT,
                check=False,
            )
        else:
            run = subprocess.run(
                [
                    capabilities.espeak_path(),
                    "-v",
                    "en-us",
                    "-s",
                    "158",
                    "-w",
                    str(path),
                    text,
                ],
                capture_output=True,
                timeout=TTS_TIMEOUT,
                check=False,
            )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return run.returncode == 0 and path.exists() and path.stat().st_size > 64


def synthesize_voice(beats: Sequence[tuple[str, str]], folder: Path) -> list[Voice]:
    """One WAV per beat from the local engine; an empty list when there is none."""
    engine = capabilities.tts_engine()
    if not engine:
        return []
    found: list[Voice] = []
    for beat_id, text in beats:
        path = folder / f"voice-{beat_id}.wav"
        if not _speak(engine, text, path):
            return []  # a half-voiced video is worse than none
        try:
            found.append(Voice(beat_id, path, wav_ms(path)))
        except (wave.Error, EOFError, OSError):
            return []
    return found


# --------------------------------------------------------------------- master ----

LOUD_I, LOUD_TP, LOUD_LRA = -14.0, -1.5, 11.0


class MasterError(RuntimeError):
    pass


def _run(command: list[str], timeout: float) -> subprocess.CompletedProcess[bytes]:
    try:
        run = subprocess.run(
            command, capture_output=True, timeout=max(5.0, timeout), check=False
        )
    except subprocess.TimeoutExpired as error:
        raise MasterError("Audio mastering timed out.") from error
    except OSError as error:
        raise MasterError(f"ffmpeg could not run: {error}") from error
    if run.returncode != 0:
        raise MasterError(run.stderr.decode(errors="replace")[-300:])
    return run


def _measure(binary: str, source: Path, timeout: float) -> dict[str, str]:
    """First loudnorm pass: the programme's measured loudness as strings (or {})."""
    run = _run(
        [
            binary,
            "-hide_banner",
            "-nostats",
            "-i",
            str(source),
            "-af",
            f"loudnorm=I={LOUD_I}:TP={LOUD_TP}:LRA={LOUD_LRA}:print_format=json",
            "-f",
            "null",
            "-",
        ],
        timeout,
    )
    text = run.stderr.decode(errors="replace")
    start, end = text.rfind("{"), text.rfind("}")
    if start < 0 or end < start:
        return {}
    try:
        data = json.loads(text[start : end + 1])
    except ValueError:
        return {}
    keys = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    return {k: str(data[k]) for k in keys if k in data}


def master(
    binary: str,
    plan: Mapping[str, Any],
    work: Path,
    voices: Sequence[Voice],
    timeout: float,
) -> Path:
    """Mix music, effects and optional voice (music ducks under speech), then normalise to
    -14 LUFS integrated with a two-pass loudnorm; returns ``work/final.wav``."""
    bed = work / "bed.wav"
    mix = work / "mix.wav"
    inputs = ["-i", str(bed)]
    for voice in voices:
        inputs += ["-i", str(voice.path)]
    if voices:
        starts = {b["id"]: b["start"] for b in plan["beats"]}
        delays = [
            f"[{n + 1}:a]adelay={starts.get(v.beat_id, 0)}|{starts.get(v.beat_id, 0)},apad[v{n}]"
            for n, v in enumerate(voices)
        ]
        labels = "".join(f"[v{n}]" for n in range(len(voices)))
        graph = ";".join(
            [
                *delays,
                f"{labels}amix=inputs={len(voices)}:normalize=0:duration=longest[vox]",
                "[vox]asplit=2[vox1][vox2]",
                "[0:a][vox1]sidechaincompress=threshold=0.04:ratio=9:attack=20:release=450[duck]",
                "[duck][vox2]amix=inputs=2:normalize=0:weights=1 1.5,aformat=channel_layouts=stereo[mixed]",
            ]
        )
        _run(
            [
                binary,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                *inputs,
                "-filter_complex",
                graph,
                "-map",
                "[mixed]",
                "-t",
                f"{plan['total'] / 1000:.3f}",
                "-ar",
                "44100",
                str(mix),
            ],
            timeout,
        )
    else:
        _run(
            [
                binary,
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                *inputs,
                "-af",
                "aformat=channel_layouts=stereo",
                "-t",
                f"{plan['total'] / 1000:.3f}",
                "-ar",
                "44100",
                str(mix),
            ],
            timeout,
        )
    measured = _measure(binary, mix, timeout)
    final = work / "final.wav"
    target = f"loudnorm=I={LOUD_I}:TP={LOUD_TP}:LRA={LOUD_LRA}"
    if len(measured) == 5:
        target += (
            f":measured_I={measured['input_i']}:measured_TP={measured['input_tp']}"
            f":measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}"
            f":offset={measured['target_offset']}:linear=true"
        )
    _run(
        [
            binary,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            str(mix),
            "-af",
            target,
            "-ar",
            "44100",
            str(final),
        ],
        timeout,
    )
    return final
