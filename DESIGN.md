# Kit workshop 0.16 design

Native Windows / Python CustomTkinter. Operate mode. The user chose the Punk prototype on 18 October 2026. The real app applies its cut-paper, ink, acid-selection and condensed-title language across the editor, batch page, Connections and SP transfer window.

NI synth previews are automatic. The former opt-in checkbox is a fixed status
label in the export area; batch export states the same policy. The inspector names
the matched preset, while confirmed DrumSynth pads explicitly say they are empty.
Unmatched plug-in previews require replacement rather than silent export. Saved
banks can gain verified previews without replacing user-assigned or cleared pads.

## Art direction: the groovebox panel

Premise: the workshop is the top panel of a hardware sampler, not a form. One matte
charcoal plate carries everything and the sounds are the light source. Reference: a
modern pad controller (matte keys with a dark bevel, small caps labels with a dimmer
second line, an OLED readout, a 4x4 grid of glowing pads). The anatomy is borrowed;
nothing is traced.

Plate and keys. Charcoal plate (#2A2A2D, deeper #141416 header and #18181A insets);
keys are square matte faces (#35353A) with a near-black bevel border and a lighter face
on hover. Text is #E9E9EB with a #8E8E95 secondary line. The one lit control colour is
teal (#35D0C6) and is used only for the primary action and focus. A light variant
(graphite-silver plate, dark ink) follows the same structure and is the Theme toggle.

Type. Bahnschrift (the DIN-style face shipped with Windows) for every label; Cascadia
Mono for the OLED readout. Labels stay in the sentence case the app already uses, so
nothing that screen readers, tests or the status line quote is rewritten.

Pads are lit tiles: a radial glow (bright core, darker edge, thin top highlight and
lower shadow) in the colour of the sound type: kick red-orange, snare amber, clap
yellow, rim lime, closed hat teal, open hat aqua, cymbal azure, perc blue, bass violet,
pad orchid, lead pink, chord indigo, vocal coral, FX lime, loops green, NI previews
lavender, unknown steel. Pad number top-left, type tag top-right, name below, state
last. Empty pads are unlit; a pad that needs a sound is dim with a red rim; the
selected pad has a white rim and a stronger glow; a locked pad shows LOCK and a bright
bar along its lower edge. Colour never carries meaning alone: every state is also text.

Readout. The kit name and pad status sit in a black OLED-style panel (white mono text)
in place of the old yellow masthead strip.

Avoid: paper textures, rotated controls, neon on every control, and anything that
reduces legibility of a sound name.

Fonts: Bahnschrift and Cascadia Mono are system fonts; Oswald and Space Grotesk stay
bundled for compatibility with earlier resources and the Windows font fallback.
All controls keep keyboard focus and the existing save, undo, candidate and MIDI behaviour.

Layout is unchanged: persistent sound library on the left; two mode buttons across the
workspace; the 4x4 pad grid and waveform beside the selected-sound inspector; export
destination and readiness directly below.

Review against the brief: the pad grid is the defining object and carries the style;
check native captures at the default size and 1120x800 with real names, missing pads,
locked pads, selected banks and the batch page.

## Design review: groovebox panel, round 2

Scored against the brief (the pads are the light source) and the reference pad controller, from
window-only captures at 1380x920 and the 1120x800 minimum, both themes.

| Note | Finding | Resolution |
|---|---|---|
| 1 Hierarchy (deal-breaker) | At the minimum size ~270 px of chrome sat above the pads; pads were wide and flat. | Mode tabs 44 to 36 px, tighter header and tab margins, the two grey status lines folded into the readout; pad gutters 8 to 12 px. Pads are 80+ px tall at the default size. |
| 2 Pastel wash (deal-breaker) | The legibility pass bleached colours: saturation 0.09 to 0.27 for pink, orchid, lavender; nearest pair 15 RGB apart. | Legibility now works in HSV (value first, saturation kept) with a brighter tile edge; deep hues stay deep with light ink. Saturation 0.42 to 0.76; nearest pair 31 apart. Tests hold both. |
| 3 Second accent | A violet mode tab competed with the pad colours. | The active tab uses the one lit-key colour (teal). |
| 4 Readout | Kit name only, plus two grey status lines. | Second line follows the selected pad: "A05 . Kick Samidare 2" and "edited". |
| 5 Gutters | 8 px, tiles blurred together. | 12 px. |
| 6 Disabled keys | Dark-teal disabled text landed on a grey face and vanished. | Disabled text follows the face the key is wearing (lit or soft), checked in both themes. |
| Found in review | Lock pad and Swap unlocked were the last inspector row and fell off-screen at 1120x800. | Moved under Hear pad / Compare original. Test: reachable without scrolling at the minimum size. |
| Found in review | Compact captions cut after the first word ("Shaker..."). | Whole name if it fits, otherwise cut at the pad width. |

Known gaps (accepted): keys are flat (CustomTkinter has no bevel or highlight; the reference
has both), the plate has no gradient, and unlit DrumSynth pads are dim by design.

## Make kits tab

The first and default tab. One purpose: review ready-made drum kits fast. Eight lit pads in the MPC1000 layout
(pad 1 bottom-left, top row pads 5-8), the kit name and source in the OLED readout, five large keys under the pads
(Hear beat, Keep, Skip, Swap hit, Undo) and a side panel naming the selected hit and the current program's progress.
The editor's library rail and file keys are hidden here. Keys: Space/Return hear, Y keep, N skip, 1-8 pick a pad,
R swap, U undo; ignored while typing. Workers never touch Tk: results arrive through a queue polled every 60 ms.

## Interaction contract

Browse â†’ play â†’ select pad â†’ hear alternatives â†’ compare â†’ keep â†’ export.

All library matches remain reachable, with expansion, Favorites, Recent and previous/next. NI Expansions Location and Rescan sit under Sound library. Original NI demo is a persistent toggle that follows browser selection. Favorites exposes removal immediately.

MPC Aâ€“D each retain 16 pads. Mode changes keep page instances, pad assignments, Undo, candidates and active bank intact. Save/recovery and a single MPC PGM retain all 64 positions. SP export uses the current bank. All MPC hardware banks audition the visible software bank; MIDI configuration is independent of export destination.

Candidates stay temporary until Keep. Compare and Discard do not alter committed sounds; exports require a decision. Drops are undoable assignments. Fill empty protects occupied positions. Separate working-kit recovery survives restart; Undo remains session-only.

Export missing as empty defaults off. Unresolved source pads block a single export or skip batch kits. Opting in leaves MPC assignments empty and creates SP silence WAVs. Intentional empty pads are always allowed. Batch and single-kit policy controls share state. Preview clips may contain a phrase and do not imply effect/synth rendering.

Mouse/keyboard and MIDI share a preloaded polyphonic audio engine. Background scanning/export does not gate unchanged pad triggers. Focus is visible, Enter/Space activate modes and controls, arrows select physical pad neighbours. Check native captures at the default size and 1120Ã—800, including real names, missing pads, selected banks and batch scope.

## SP destination window

The transfer window uses the same paper/ink/acid/lavender world and bundled typography. It shows the detected project/USB connection, explicit bank buttons Aâ€“J, a physical 4Ã—4 destination map, and one Replace existing pads switch. A scrollable body handles shorter windows while the named Send action, progress, status and Cancel/Stop stay pinned below. No backup path form or redundant review checkbox.

Opening the window reads the device; it never writes audio. One SP needs no USB selection. Multiple SPs require an explicit choice. Current bank comes from a validated status response (project/bank/pad), not note guesses or the software's MPC bank. Unknown bank requires user selection. Manual bank overrides survive refresh; changing the bank resets replacement consent. Automatic backups remain mandatory for replacement. The final named Send action accepts the reviewed immutable plan; backend identity, occupied-pad snapshot and bank-protection checks remain enforced.

MPC pad input accepts the 64 program-note assignments across hardware banks Aâ€“D and folds each into its physical position in the visible bank. Software bank selection remains an editing choice; hardware-bank mismatch never filters otherwise valid pad hits. Custom program note maps are imported as metadata, validated before replacing preferences, and remain independent of exported PGM mappings. JJ controller input assignments are not used as outgoing-pad mappings.

MPC input offers separate MIDI track (chromatic notes 36â€“99) and drum program profiles. Both fold hardware banks onto the visible sixteen pads. Program imports affect only the drum program profile; choosing MIDI track preserves imported maps for later use. Profile selection is explicit because their note ranges overlap.
