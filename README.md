# NI to SP & MPC 0.12 — four-bank kit workshop

[Download the latest Windows package](https://github.com/diamond-one/NI-Expansion-to-SP404MK2-and-MPC1000-2500/releases/latest), extract it, and open NI-to-SP-0.12.exe. Python and Maschine do not need to be running. Build an MPC1000 program with banks A–D, audition sounds and export it, or send the current 16-pad bank to the SP-404MKII. This is a sample converter; no recording is required.

![Kit workshop showing a loaded NI kit, MPC banks A–D, pad audition and replacement controls](docs/screenshots/main-workspace.png)

## Neo-Brutalist workspace

Play & export and Batch export are large, keyboard-accessible mode buttons. Switching between them preserves the current kit, all MPC banks, Undo and any temporary candidate. The new paper/ink interface uses heavy black outlines, yellow selection, cobalt actions and explicit missing-pad labels. The right-hand sound editor scrolls when space is tight.

Batch export separates Matching kits (your current search, expansion and Favorites/Recent view) from All expansions (the entire installed collection). Both create SP sample kits. Its preview and missing-sound options share the settings used on Play & export. An edited MPC program exports from Play & export.

## MPC banks A, B, C and D

The A–D buttons above the pads select the bank to work on. Each bank has 16 independent pad positions. Switching banks keeps sounds in the other banks. Save, editable-kit recovery and MPC export include all four banks. When browsing an NI kit while a four-bank program is open, that kit loads into the selected bank; the other three banks are kept. Undo restores the previous program state. A temporary candidate is not kept automatically when changing banks; press Keep sound first if you want it.

MPC export produces one PGM with up to 64 sample assignments and the corresponding WAVs. It works even when the bank on screen is empty, provided another bank contains audio. Missing sounds in any bank are counted before export. Next missing pad can move between banks. The MPC1000 profile accepts all four hardware banks and maps their physical positions onto the visible software bank. Choose A–D in the app to select the kit bank you are editing. Custom MIDI Learn remains available. With an SP controller, its 16 pads audition whichever software bank is selected.

SP export and Send to SP use only the current 16-pad bank. Changing the export target keeps the other MPC banks in the editable program.

## Library controls

NI Expansions Location and Rescan sit directly below Sound library. The location is the folder containing installed NI libraries, normally C:\Users\Public\Documents. It is remembered between runs. This setting does not move or rename audio files.

Hear original NI demo is an on/off switch. When on, selecting a different kit in the browser auditions its supplied original demo automatically (up to 20 seconds). Turning it off stops the demo. These demos describe the original NI kit and do not include your bank edits. A kit with no supplied demo shows a message. The preference is remembered.

The favorite action changes immediately between Add to Favorites and Remove from Favorites. Removing a favorite keeps the actual kit and its samples; it removes only the bookmark.

The waveform stays visible below the 4×4 pads. The right panel lets you hear alternatives before keeping one. Use arrows to select a pad and Enter or Space to audition. The selected export target supplies the primary pad numbers. MPC1000 is the initial target; your choice is remembered independently of the MIDI controller mapping.

## Hear alternatives before changing a pad

Select a pad and choose the replacement scope. Next sound finds a temporary candidate; Hear candidates automatically is on by default and can be turned off. Previous and Next revisit candidates already heard. Compare original switches between the kept sound and the candidate. Keep sound commits one edit; Discard returns to the kept sound. Switching to a different pad discards the temporary candidate. Exports and saving require the candidate to be kept or discarded first.

Choose file also auditions a temporary candidate. Dropping a file directly onto a pad assigns it immediately, with Undo available. Fill empty pads accepts several audio files and fills only empty or missing positions, in original pad order, without overwriting usable sounds. A failed import leaves the kit unchanged. For an MPC kit, this is bottom-left A01, then right through A04, then the next row upwards.

Mouse, keyboard and MIDI use the same loaded polyphonic sampler. Mouse/keyboard audition needs no MIDI device. Searches and exports do not take the audio lock. Unchanged pads remain playable while a new sound is loaded; changed pads wait until their new audio is ready. Existing voices are retained when prepared pads swap. Very large/slow/offline audio can still delay preparation or fail with a message; no fixed latency is promised.

The library exposes every match, with horizontal scrolling for long kit names. Expansion metadata for the open kit appears below the list. All kits, Favorites and Recent help revisit sounds. These views also define the matching batch-export scope.

## Start with an expansion or an empty kit

The app opens with a blank 16-pad kit and scans C:\Users\Public\Documents for installed NI expansions. Choose an expansion/search a kit and click it, or use New kit to build from your own sample packs. NI Expansions Location changes the expansion install location.

Pad positions are preserved. Maschine/MPC pad 1 is bottom-left; it corresponds to SP pad 13. The top-left pad is Maschine/MPC pad 13 and SP pad 1. Pads show the number for the selected export destination. No missing sound causes later pads to move.

Click a pad to select and audition it. The editor on the right acts on that selected pad. Right-click opens the same editing options and MIDI Learn.

## Replace or add samples

- Drag one WAV, AIFF, FLAC or OGG file from Explorer onto a pad. Choose sample is the alternative.
- Find another sample combines Search in (This expansion / All expansions) and Use (Any sample / Same pad). These two choices produce the four scopes below. It always excludes the sample already on that pad.
- Any sample / same expansion: a randomly selected file from the kit's expansion Samples folder.
- Any sample / all expansions: a randomly selected file from the Samples folders of all scanned expansions.
- Same pad in another kit / same expansion: a real sample assigned to exactly that Maschine pad number in another kit from the same expansion.
- Same pad in another kit / all expansions: the same pad number in another kit anywhere in the scanned expansions.
- For a blank kit, select an expansion in the library filter to anchor a same-expansion scope. All-expansion scopes work without that filter.
- Same-pad lookup uses original kit slot numbers, never filename guesses or inferred instrument categories. Unsupported kit layouts are excluded from this lookup. Raw sample scopes can still use their sample folders.
- Clear makes a pad intentionally empty. Undo restores up to 30 previous kit edits. Ctrl+Z is available.

Random choices come from the current scanned expansion list. Same-pad metadata is cached locally in SQLite and refreshed when a kit's size/modification time changes. Raw sample scopes rescan folder entries so new sample files are found without rebuilding the app. Audio is validated when selected. Offline/unreadable/unsupported files are skipped. Scanning and lookup run in a worker and can be cancelled.

The app accepts mono/stereo files from 8–192 kHz, up to 120 seconds per sample. It does not rename, move or edit the originals.

## Missing sounds and previews

Filled neutral pads use original sample files or kept replacements. Warm warning pads need a sound; they may be synths, layered sounds, missing references or unsupported audio. Empty pads invite a drop. The selected pad is yellow with a blue focus outline; temporary replacements say Trying sound and the inspector says candidate.

Use NI sound previews is OFF by default. If enabled, exact-name unambiguous matching NI preset audition clips can fill some unresolved positions. Pads label these sounds NI preview: they may be phrases and are approximations, not guaranteed reproductions of the pad's original note or processing. Changing this setting refreshes original NI slots while keeping your replacements and deliberately empty pads.

Export missing as empty is OFF by default. It permits an incomplete conversion when a source sound cannot be read or rendered: the missing positions stay in place and export empty instead of preventing export. MPC leaves those pads unassigned; SP uses short silence WAVs to preserve the 16-pad layout. It does not find substitutes or render synths. The readiness text shows how many sounds will be omitted. Leave it off to catch omissions, or replace the sounds before exporting. Intentionally empty pads in your own kit do not require this option. At least one sound is required somewhere in the MPC program or current SP bank.

## Save your work

Save kit / Ctrl+S writes an editable JSON kit containing sample references, slot positions and replacement provenance. Open / Ctrl+O restores it. The sample files must remain accessible. A changed or missing file is marked unresolved when reopening.

Edits save separate recovery kits under %LOCALAPPDATA%\NIToSP\working-kits and retain last-edited-kit.json for compatibility. Session kits and Return to edited kit restore assignments and this session's Undo history after browsing. Recover last session reopens the latest recovery kit after a restart; Undo history itself is session-only. A draft from version 0.7 is offered through the same recovery action. Save a named JSON kit for long-term use. Kit files contain references, so source audio must remain accessible. Recovery files are local and are not a portable sample bundle.

Open completed kit opens an exported SP kit as an editable kit, using its verified WAV copies.

## MIDI preview

MIDI preview ON turns the app into a computer-based pad sampler. No samples need to be imported into the hardware to preview them. Connections selects the MIDI input, audio output, pad mapping and channel. Audio outputs are grouped to avoid redundant Windows device listings.

SP-404MKII:

1. Connect USB and enable PAD Note Out on the SP.
2. Select SP Mode A or Mode B in Connections to match the SP. Mode A is the default.
3. Choose the SP audio output to monitor the computer audio through it, or use another computer/interface output.
4. Use an empty hardware bank if you do not want the SP's existing samples to sound too.

MPC1000 / JJOS3:

1. Connect MPC MIDI OUT A or B to your computer MIDI interface's IN. The MPC1000 USB port provides storage transfer, not USB MIDI or audio.
2. On the MPC, use a MIDI track and assign its MIDI output/channel to that connected output. You can disable the track's internal program to avoid double playback.
3. Select that interface input in Connections and choose MPC1000 / JJOS3 standard. Any MIDI channel is accepted unless a specific channel is chosen.
4. Hear the preview through your computer/interface audio output. For monitoring through the MPC, route the interface's analogue output to its recording input and enable the appropriate input monitoring on the MPC.
5. For altered mappings, enable preview, right-click a software pad, select Learn MIDI note and hit the corresponding hardware pad. Learned notes are saved. The triggering hit learns the note; subsequent hits audition it. The linear notes 36–51 and learned-only profiles are also available.

The standard profile accepts the MPC program notes for all 64 hardware pads, preserving bottom-left-first physical positions. Switching the MPC hardware bank does not silence the currently visible kit. The note map was checked against twelve local MPC1000 programs. If your MPC has a different program mapping, Connections → Load MPC program map reads its 64 note assignments without copying audio or modifying the MPC. Use standard map restores the default. Changing mapping profiles clears the previous learned overrides. Preview does not send MIDI back to the hardware.

## Export and transfer to SP-404MKII

Export SP WAVs creates a complete kit folder using verified 48 kHz / 16-bit PCM WAVs. Send to SP prepares the current edited bank and opens the Neo-Brutalist destination window.

The window reads the connected SP automatically. With one SP, there is no USB dropdown: the app uses that connection. With several SPs, choose the one you want. The former Automatic dropdown selected a USB connection, not a bank.

The bank selected on the SP is the initial destination when its status can be read. Buttons A–J let you choose another bank. Refresh SP rereads project, selection, protection and occupied pads; an explicit bank choice is kept on that refresh. Unknown selected-bank data never defaults to A: choose a bank explicitly instead. The current SP project is shown; the app does not switch projects. Choose a project on the hardware and refresh if needed.

Replace existing pads is OFF by default. Off blocks the whole kit if any destination position is occupied; it does not silently skip sounds or send a partial kit. On permits replacement of those positions. The app automatically saves and validates the recovery backup in `%LOCALAPPDATA%\NIToSP\SP-backups` before writing. Backups are automatic, not another approval or folder-selection step. Restore is manual; this is not automatic rollback.

The 4×4 map shows destination positions, incoming names and occupied pads. The final Send button names the destination bank; clicking it confirms the displayed transfer. There is no extra I checked checkbox. The immutable plan is rechecked against the device before import, and protected banks or changed destination contents block the transfer. Stop after this pad waits for the active transfer command; completed writes remain on the SP and are recorded in the report.

These are sample transfers. Effects, choke groups and gate/loop settings are not applied to the hardware. Checking and opening this window do not import audio.

In the Batch export tab, Export matching kits and Export all expansions remain available for direct SP WAV batch conversion. These use the currently selected preview/missing-pad options, and produce a report of exported and skipped kits. They export the original NI kits rather than applying edits from the currently visible kit to every other kit.

## Export MPC1000 programs

Select MPC1000 in the export target, then Export MPC program. This exports the current edited kit into a fresh folder containing:

- A genuine MPC1000 PGM 1.00 binary program, not a modern MPC XPM.
- Banks A–D populated from your working program, with each bank preserving its 16 physical pad positions. Untouched banks stay empty.
- Short ASCII WAV names, 44.1 kHz / 16-bit PCM, with mono/stereo preserved.
- PAD_MAP.csv, provenance/hash manifest, sample-memory estimate and loading instructions.

Copy the whole folder to a CF card, MPC disk, or its USB-mounted storage. On the MPC LOAD page, load the PGM with its samples. The exporter uses the MPC's default sample level of 70, no guessed choke groups and one-shot playback. No Maschine effect, tuning or envelope conversion is claimed. Loading on a physical MPC1000 with JJOS3 remains to be checked; software structure and sample formats are validated.

## Audio and scope

This converter copies/decodes the selected audio, resamples for the target and converts to 16-bit PCM. It does not record instruments or apply Maschine EQ/effects. Only resampling/preview-decoding overs are attenuated, using one shared gain to preserve relative levels. Minimum exported sample length is 100 ms, padded with silence if necessary.

Maschine kit metadata is proprietary. The reader supports observed structures and fails safely on unsupported layouts. Synth and multisample playback, original tuning/envelopes, velocity layers and group effects are not recreated. Use replacement samples or explicitly labelled previews for unavailable sounds.

## Credits and formats

MPC exporter adapted from the user's existing CrateBuilder project. The unmodified MPC1000 template and independent reference parser are by Stephen Norum (zlib licence in licenses/pympc1000.txt).

- MPC format reference: https://mybunnyhug.org/fileformats/pgm/
- MPC reference code: https://github.com/stephenn/pympc1000
- Akai MPC1000 manual: https://cdn.inmusicbrands.com/akai/mpc1000/mpc1000v2_operators_manual_00.pdf_055b8b24eeb101e27a2f4042c6819727.pdf
- SparkyMK2 helper: https://github.com/TuscanPL/SparkyMK2

The distribution includes the helper's matching source/notices. It contains no NI sample packs, presets or engine binaries. Native Instruments, Roland and Akai do not endorse this app.



## Run or build from this repository

Windows, Python 3.12:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python run.py
```

Build the standalone Windows executable with `./build.ps1`; the result is in `dist`. Run regression tests with `python -m unittest discover -s tests -v`. Tests create their own temporary audio; they do not import samples to physical hardware. For the generated-fixture packaged runtime check, use `NI-to-SP-0.12.exe --self-check report.json`.

`tools/sp404.exe` is the bundled SparkyMK2 USB helper. Its matching source is in [the matching helper source release asset](https://github.com/diamond-one/NI-Expansion-to-SP404MK2-and-MPC1000-2500/releases/download/v0.11.0/SparkyMK2-source.zip); license notices are in `licenses`. The app font is loaded privately for the process. No NI expansion content, personal settings, scan databases or audio files are stored in this repository.

### MPC controller setup

1. Connect MPC MIDI OUT A or B to the USB MIDI interface IN connected to this computer.
2. On the MPC main screen, select a MIDI track and assign its output port/channel to that cable. A stopped sequencer is sufficient for pad audition.
3. In Connections, select MPC1000 / JJOS3 · program pads and the interface input (for example USB Midi 1). Use channel Any initially. Auto detect can use one unambiguous MPC or USB MIDI input; it never guesses between several candidates.
4. Select a kit, turn MIDI preview on, and tap pads. Every MPC hardware bank plays the visible software bank. Use the app's A–D controls to choose another bank for editing/export. Velocity is preserved.
5. For a custom note layout, load the PGM currently assigned on the MPC in Connections, then save. This reads pad-note metadata only. MIDI Learn remains available when that program format is unsupported.

JJ's `mpc-ctl` preset assigns **incoming** notes/CCs so a computer can operate the MPC. Those assignments are not a map of the physical pads' outgoing notes. This app opens MIDI inputs only; it does not send JJ bank/transport commands back to the hardware. See [JJ's controller documentation](https://www7a.biglobe.ne.jp/~mpc1000/mpc-ctl/index.htm) and [button input assignments](https://www7a.biglobe.ne.jp/~mpc1000/128xl/button_light.htm).
