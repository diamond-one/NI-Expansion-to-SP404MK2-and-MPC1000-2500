"""The audition beat: a two-bar pattern played from a kit's eight hits, so a kit is judged in a few seconds."""
import numpy as np

RATE = 44100
# slot order matches the house layout: KICK SNARE CL-HAT OP-HAT CLAP RIM TOM CYMBAL
KICK, SNARE, CLHAT, OPHAT, CLAP, RIM, TOM, CYMBAL = range(8)
LEVEL = {KICK: 1.0, SNARE: .9, CLHAT: .5, OPHAT: .55, CLAP: .6, RIM: .55, TOM: .8, CYMBAL: .5}
# step -> [(slot, velocity)] over 16 sixteenth-note steps; bar one, then bar two with a fill
BAR_ONE = {0: [(KICK, 1), (CYMBAL, 1)], 2: [(CLHAT, .7)], 4: [(SNARE, 1), (CLAP, .5), (CLHAT, .8)], 6: [(CLHAT, .7), (KICK, .8)],
           8: [(CLHAT, .8)], 10: [(KICK, .9), (CLHAT, .7)], 12: [(SNARE, 1), (CLAP, .5), (CLHAT, .8)], 14: [(OPHAT, .9)]}
BAR_TWO = {0: [(KICK, 1)], 2: [(CLHAT, .7)], 4: [(SNARE, 1), (CLAP, .5), (CLHAT, .8)], 6: [(CLHAT, .7), (KICK, .7)],
           7: [(RIM, .8)], 8: [(KICK, .9), (CLHAT, .8)], 10: [(CLHAT, .7)], 12: [(SNARE, 1), (CLHAT, .8)],
           13: [(TOM, .8)], 14: [(TOM, .9)], 15: [(TOM, 1)]}


def _stereo(hit):
    return np.repeat(hit, 2, axis=1) if hit.shape[1] == 1 else hit


def render_groove(hits, bpm=92, rate=RATE):
    """Mix the beat. `hits` is 8 arrays (n, 1|2) at `rate` or None for an empty slot. Returns float32 (n, 2)."""
    if len(hits) != 8:
        raise ValueError('A kit has eight slots.')
    step = 60 / bpm / 4
    step_frames = int(round(step * rate))
    longest = max((len(h) for h in hits if h is not None), default=0)
    total = 32 * step_frames + longest + int(.1 * rate)
    out = np.zeros((total, 2), dtype=np.float32)
    closed_times = []
    for bar, pattern in enumerate((BAR_ONE, BAR_TWO)):
        for index, events in pattern.items():
            if CLHAT in [slot for slot, _ in events]:
                closed_times.append((bar * 16 + index) * step_frames)
    for bar, pattern in enumerate((BAR_ONE, BAR_TWO)):
        for index, events in pattern.items():
            at = (bar * 16 + index) * step_frames
            for slot, velocity in events:
                hit = hits[slot]
                if hit is None:
                    continue
                clip = _stereo(hit) * (LEVEL[slot] * velocity)
                if slot == OPHAT:                       # a closed hat shuts the open one, as on the MPC
                    later = [t for t in closed_times if t > at]
                    if later:
                        clip = clip[:max(1, later[0] - at)].copy()
                        fade = min(len(clip), int(.005 * rate))
                        clip[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)[:, None]
                end = min(total, at + len(clip))
                out[at:end] += clip[:end - at]
    peak = float(np.max(np.abs(out)))
    if peak > .9:
        out *= .9 / peak                                  # one gain for the whole beat, so levels stay relative
    return out
