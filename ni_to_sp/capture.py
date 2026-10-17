"""Deterministic MIDI capture schedule and checked splitting of a Maschine master render.

Maschine remains the renderer. No original WAV extraction can reproduce its
instrument engines, envelopes, layers, sound effects and group processing.
"""
import hashlib
import json
import math
import re
import uuid
from pathlib import Path

import mido
import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from .model import Assignment, Sample, Session, sp_pad_number

DOCUMENTATION = "https://docs.native-instruments.com/ni-tech-manuals/maschine-software-manual/en/working-with-patterns-and-clips"


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(name):
    name = re.sub(r'[^\w .()-]', "_", str(name)).strip(" .")[:80] or "NI kit"
    if name.casefold() in {"con", "prn", "aux", "nul", *[f"com{i}" for i in range(1, 10)], *[f"lpt{i}" for i in range(1, 10)]}:
        name = "Kit " + name
    return name


def load_capture(folder):
    path = Path(folder) / "CAPTURE.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    if value.get("schema") != 1 or len(value.get("slots", [])) != 16:
        raise ValueError("This is not a supported NI to SP capture job.")
    _validate_job(value)
    return value


def create_capture(kit, destination, spacing=8, hold=.5, velocity=100, bpm=120):
    spacing, hold, velocity, bpm = int(spacing), float(hold), int(velocity), int(bpm)
    if spacing not in (4, 8, 16, 32) or not 40 <= bpm <= 240 or not .05 <= hold <= min(8, spacing * 120 / bpm - 1) or not 1 <= velocity <= 127:
        raise ValueError("Choose 4/8/16/32 seconds between pads, a shorter note hold and velocity 1–127.")
    source = Path(kit["path"]).resolve(strict=True)
    if source.suffix.casefold() != ".mxgrp":
        raise ValueError("Choose an installed Maschine group kit.")
    # Private output must never be placed in an installed library.
    destination = Path(destination).expanduser().resolve()
    if destination.is_relative_to(Path(kit["root"]).resolve()):
        raise ValueError("Capture jobs must be saved outside the installed NI library.")
    destination.mkdir(parents=True, exist_ok=True)
    folder = destination / (safe_name(kit["name"]) + "-" + uuid.uuid4().hex[:8])
    folder.mkdir()
    setting, lead, interval = spacing, 240 / bpm, spacing * 120 / bpm
    slots = [{"ni_pad": i + 1, "sp_pad": sp_pad_number(i), "start": lead + i * interval,
              "end": lead + (i + 1) * interval, "note": 36 + i} for i in range(16)]
    job = {"schema": 1, "kit": dict(kit), "kit_sha256": file_hash(source), "bpm": bpm,
           "root_note": 36, "synth_note": 60, "velocity": velocity, "hold": hold,
           "spacing_setting": setting, "spacing": interval, "lead_seconds": lead, "duration": lead + 16 * interval, "slots": slots,
           "render_method": "Maschine master output, one isolated hit per sound slot"}
    midi = mido.MidiFile(type=0, ticks_per_beat=480)
    track = mido.MidiTrack()
    midi.tracks.append(track)
    track.append(mido.MetaMessage("track_name", name="NI to SP - 16 pad capture"))
    track.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm)))
    track.append(mido.MetaMessage("time_signature", numerator=4, denominator=4))
    events = []
    for slot in slots:
        tick = (4 + (slot["ni_pad"] - 1) * setting * 2) * 480
        events.extend([(tick, mido.Message("note_on", channel=0, note=slot["note"], velocity=velocity)),
                       (tick + round(hold * 480 * bpm / 60), mido.Message("note_off", channel=0, note=slot["note"], velocity=0))])
    last = 0
    for tick, message in sorted(events, key=lambda event: event[0]):
        track.append(message.copy(time=tick - last))
        last = tick
    track.append(mido.MetaMessage("end_of_track", time=(4 + 16 * setting * 2) * 480 - last))
    midi.save(folder / "CAPTURE.mid")
    (folder / "CAPTURE.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
    (folder / "MASCHINE_STEPS.txt").write_text(render_instructions(job), encoding="utf-8")
    return folder, job


def render_instructions(job):
    return f"""NI TO SP — CAPTURE IN MASCHINE

Kit: {job['kit']['name']} / {job['kit']['pack']}
Original kit: {job['kit']['path']}

1. Use a new, unsaved Maschine project. Open the original kit in Group A1.
   Load its plug-ins successfully. Do not save over the installed kit.
2. Select a NEW EMPTY pattern. Set the project tempo to {job['bpm']} BPM.
   In Group Input / MIDI, set Root Note to C1 (MIDI 36).
3. Right-click the GROUP name > Import MIDI, then choose CAPTURE.mid.
   Import to the GROUP, not to one Sound. There must be one hit on each of
   the 16 sound lanes, in order, starting at {job['lead_seconds']:.3f} seconds and spaced {job['spacing']:.3f} seconds apart.
   NI's single-channel group import triggers each sound at its default C3.
   Synths use one note at C3 (MIDI 60), held for {job['hold']:g}s, velocity {job['velocity']}.
   Set PATTERN LENGTH to {1 + 8 * job['spacing_setting']}:0:0 (bars:beats:ticks).
   Maschine can shorten a MIDI import to its last note and drop end padding.
4. Put this capture pattern in a scene. Solo this group and keep its original
   sound/group effects. Set the render range to start at zero and include
   the whole {job['duration']:.3f}s / {job['duration'] * job['bpm'] / 240:g}-bar capture pattern.
5. File > Export Audio: MASTER output, WAV, 48000 Hz, 32-bit float preferred.
   NORMALIZE OFF. LOOP OPTIMIZE OFF. Preserve the original kit's gain.
   Master output captures group and master effects; exporting individual
   Sound outputs does not capture the complete downstream chain.
6. Export to this capture folder, then select that WAV in NI to SP.
   Confirm you rendered this kit/pattern at {job['bpm']} BPM from time zero.
   Audition each resulting pad before transferring.

If tails reach the next hit, create a new job with wider spacing and render
again. If a pad is silent, resolve missing plug-ins or explicitly identify it
as an intentionally empty pad. Do not substitute the raw sample for a synth.

Fidelity: these are fixed recordings of one note/velocity. They preserve that
captured hit, not live synthesis, velocity layers, round robins, patterns,
choke settings or the way shared effects react to simultaneous playing.

NI MIDI import rules: {DOCUMENTATION}
"""


def _validate_job(job):
    setting, bpm = job.get("spacing_setting"), job.get("bpm")
    if job.get("schema") != 1 or setting not in (4, 8, 16, 32) or not isinstance(bpm, int) or not 40 <= bpm <= 240:
        raise ValueError("The capture schedule is invalid. Create a new capture job.")
    lead, spacing = 240 / bpm, setting * 120 / bpm
    if job.get("spacing") != spacing or job.get("lead_seconds") != lead or job.get("duration") != lead + 16 * spacing:
        raise ValueError("The capture tempo or timing changed. Create a new job.")
    expected = [(i + 1, sp_pad_number(i), lead + i * spacing, lead + (i + 1) * spacing) for i in range(16)]
    actual = [(s.get("ni_pad"), s.get("sp_pad"), s.get("start"), s.get("end")) for s in job.get("slots", [])]
    if actual != expected:
        raise ValueError("Capture pad order or timing has changed. Create a new job.")


def _job_digest(job):
    return hashlib.sha256(json.dumps(job, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def inspect_render(job, path, empty_pads=(), confirmed=False, progress=None, cancel=None):
    """Inspect one bounded window at a time; never infer kit identity from audio."""
    _validate_job(job)
    if confirmed is not True:
        raise ValueError("Confirm the selected kit, capture pattern, capture tempo and export from time zero.")
    empties = set(empty_pads)
    if any(isinstance(i, bool) or not isinstance(i, int) or i not in range(1, 17) for i in empties):
        raise ValueError("Intentional empty pads must be Maschine pad numbers 1–16.")
    path = Path(path).expanduser().resolve(strict=True)
    if path.suffix.casefold() not in (".wav", ".aif", ".aiff", ".flac"):
        raise ValueError("Choose the full audio render from Maschine.")
    before = path.stat()
    rows, problems = [], []
    with sf.SoundFile(path) as audio:
        rate, channels = audio.samplerate, audio.channels
        if channels not in (1, 2) or not 8000 <= rate <= 192000:
            raise ValueError("The render must be mono/stereo audio at a supported sample rate.")
        if len(audio) < round(job["duration"] * rate):
            raise ValueError(f"Render too short: need {job['duration']:.3f} seconds from time zero. Check {job['bpm']} BPM and the full capture range.")
        if len(audio) > (job["duration"] + 120) * rate:
            raise ValueError("This render is much longer than the capture pattern. Check its range and tempo.")
        lead = audio.read(rate, dtype="float32", always_2d=True)
        if not np.isfinite(lead).all():
            raise ValueError("Invalid audio in the render.")
        floor = float(np.sqrt(np.mean(lead.astype(np.float64) ** 2)))
        for index, slot in enumerate(job["slots"]):
            if cancel and cancel.is_set():
                raise InterruptedError("Render check cancelled")
            if progress:
                progress(f"Checking Maschine pad {index + 1}/16…", index / 16)
            audio.seek(round(slot["start"] * rate))
            data = audio.read(round(job["spacing"] * rate), dtype="float32", always_2d=True)
            if not np.isfinite(data).all():
                raise ValueError(f"Invalid audio at Maschine pad {index + 1}.")
            peak = float(np.max(np.abs(data)))
            tail = float(np.sqrt(np.mean(data[-round(.1 * rate):].astype(np.float64) ** 2)))
            silent = peak < max(1e-5, floor * 2)
            threshold = max(peak * .001, floor * 4, 1e-6)
            issues = []
            if silent and index + 1 not in empties:
                issues.append("Silent pad: check the plug-in, mapping and capture, or mark it intentionally empty.")
            if index + 1 in empties and not silent:
                issues.append("Marked empty but audible: check the pad number or render.")
            if peak > 1:
                issues.append("Audio exceeds 0 dBFS: lower the Maschine master and render again; automatic normalization is off.")
            if tail > threshold and not silent:
                issues.append("Effect tail reaches the next capture boundary. Increase spacing and render again.")
            rows.append({**slot, "peak": peak, "tail_rms": tail, "silent": silent,
                         "intentional_empty": index + 1 in empties, "issues": issues})
            problems.extend(f"Pad {index + 1}: {issue}" for issue in issues)
    digest = file_hash(path)
    if (before.st_size, before.st_mtime_ns) != (path.stat().st_size, path.stat().st_mtime_ns):
        raise ValueError("The render changed while being checked. Wait for export to finish.")
    return {"schema": 1, "ok": not problems, "path": str(path), "size": before.st_size,
            "mtime_ns": before.st_mtime_ns, "sha256": digest, "rate": rate, "job_sha256": _job_digest(job),
            "channels": channels, "pads": rows, "problems": problems,
            "kit_identity": "Confirmed by user; not inferred or acoustically verified"}


def prepare_kit(job, report, destination, progress=None, cancel=None):
    _validate_job(job)
    if report.get("ok") is not True or len(report.get("pads", [])) != 16:
        raise ValueError("Resolve the capture issues before preparing an SP kit.")
    if report.get("job_sha256") != _job_digest(job):
        raise ValueError("The capture job changed. Check the render again.")
    path = Path(report["path"]).resolve(strict=True)
    if file_hash(path) != report["sha256"]:
        raise ValueError("The render changed since checking. Check it again.")
    if file_hash(job["kit"]["path"]) != job["kit_sha256"]:
        raise ValueError("The original kit changed after the capture job was made. Create a new capture.")
    destination = Path(destination).resolve()
    if destination.is_relative_to(Path(job["kit"]["root"]).resolve()):
        raise ValueError("Prepared kits must stay outside the installed NI library.")
    destination.mkdir(parents=True, exist_ok=True)
    output = destination / (safe_name(job["kit"]["name"]) + "-" + uuid.uuid4().hex[:8])
    output.mkdir()
    marker = output / "INCOMPLETE.txt"
    marker.write_text("Kit preparation is incomplete. Do not import.\n", encoding="utf-8")
    samples, assignments, rows = [], [Assignment() for _ in range(16)], []
    try:
        with sf.SoundFile(path) as audio:
            rate = audio.samplerate
            for i, slot in enumerate(job["slots"]):
                if cancel and cancel.is_set():
                    raise InterruptedError("Kit preparation cancelled")
                if progress:
                    progress(f"Preparing SP pad {slot['sp_pad']:02d} / Maschine {i + 1}…", i / 16)
                audio.seek(round(slot["start"] * rate))
                data = audio.read(round(job["spacing"] * rate), dtype="float32", always_2d=True)
                if report["pads"][i]["intentional_empty"]:
                    data = np.zeros((4800, audio.channels), dtype=np.float32)
                elif rate != 48000:
                    divisor = math.gcd(rate, 48000)
                    data = resample_poly(data, 48000 // divisor, rate // divisor, axis=0)
                if not np.isfinite(data).all() or float(np.max(np.abs(data))) > 1:
                    raise ValueError("Audio clips after sample-rate conversion. Render at 48 kHz with more headroom.")
                number = slot["sp_pad"]
                name = f"{number:02d}_NI_{i + 1:02d}.wav"
                target = output / name
                sf.write(target, data, 48000, subtype="PCM_16")
                info = sf.info(target)
                if (info.samplerate, info.subtype, info.channels, info.frames) != (48000, "PCM_16", audio.channels, len(data)):
                    raise ValueError("Prepared WAV failed validation.")
                digest = file_hash(target)
                sample_id = hashlib.sha256(f"{digest}:{i}".encode()).hexdigest()[:20]
                samples.append(Sample(sample_id, str(target), digest))
                assignments[i] = Assignment(sample_id)
                rows.append({"logical_slot": i + 1, "pad": number, "sample_id": sample_id,
                             "source_sha256": digest, "output_sha256": digest, "file": name,
                             "ni_pad": i + 1, "intentional_empty": report["pads"][i]["intentional_empty"]})
        if file_hash(path) != report["sha256"]:
            raise ValueError("The render changed during preparation.")
        recipe = {"schema": 1, "kit": 1, "kit_size": 16, "target": "sp404", "seed": 1000,
                  "pads": rows, "capture_job": job, "capture_report": report,
                  "fidelity": "One recorded note/velocity per pad; not a live Maschine instrument or effects engine"}
        (output / "KIT_RECIPE.json").write_text(json.dumps(recipe, indent=2), encoding="utf-8")
        (output / "PAD_MAP.csv").write_text("Maschine pad,SP pad,File\n" + "".join(
            f"{row['ni_pad']},{row['pad']},{row['file']}\n" for row in rows), encoding="utf-8-sig")
        (output / "COMPLETE.json").write_text(json.dumps({"complete": True, "validated": True, "files": 16}), encoding="utf-8")
        marker.unlink()
        return output, Session(samples, assignments)
    except Exception as exc:
        marker.write_text(f"Incomplete kit. Do not import.\n{exc}\n", encoding="utf-8")
        raise


def load_prepared(folder):
    folder = Path(folder).resolve(strict=True)
    if (folder / "INCOMPLETE.txt").exists() or not (folder / "COMPLETE.json").is_file():
        raise ValueError("Choose a completed, verified NI to SP kit.")
    complete = json.loads((folder / "COMPLETE.json").read_text(encoding="utf-8"))
    if complete.get("complete") is not True or complete.get("validated") is not True or complete.get("files") != 16:
        raise ValueError("The kit completion record is invalid.")
    recipe = json.loads((folder / "KIT_RECIPE.json").read_text(encoding="utf-8"))
    rows = recipe.get("pads", [])
    if (recipe.get("target"), recipe.get("kit_size"), recipe.get("kit")) != ("sp404", 16, 1) or len(rows) != 16:
        raise ValueError("The prepared kit does not contain a complete 16-pad layout.")
    samples, pads = [], [Assignment() for _ in range(16)]
    if sorted(row["logical_slot"] for row in rows) != list(range(1, 17)):
        raise ValueError("The prepared kit has invalid pad positions.")
    for row in rows:
        path = (folder / row["file"]).resolve(strict=True)
        if path.parent != folder or file_hash(path) != row["output_sha256"]:
            raise ValueError("A prepared WAV is missing, changed or outside the kit folder.")
        index = row["logical_slot"] - 1
        if row["pad"] != sp_pad_number(index):
            raise ValueError("The prepared pad map has changed.")
        samples.append(Sample(row["sample_id"], str(path), row["source_sha256"],
                              features={"available": True, "name": row.get("name", path.stem)}))
        pads[index] = Assignment(row["sample_id"])
    return Session(samples, pads), recipe
