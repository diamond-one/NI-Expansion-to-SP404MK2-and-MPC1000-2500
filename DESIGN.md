# Kit workshop 0.11 design

Native Windows / Python CustomTkinter. Operate mode. The user explicitly selected Neo-Brutalism; this replaces the former charcoal/gold world.

## Visual plan and decision

Paper #FFFFFF, ink #101010, canvas #E7E8E2, selection yellow #FFE34D, action cobalt #2547EB, missing peach #FFC8BE. Heavy black outlines and square corners describe actionable controls. A hard black offset under the two workspace modes provides the requested Neo-Brutalist depth. Yellow identifies active mode / selected pad; cobalt identifies actions. Missing pads also have text, so colour never carries their meaning alone.

Space Grotesk is bundled locally for headings and mode controls, with its SIL license. Segoe UI carries dense sample names and controls. Type steps are 12 / 14 / 18 / 22 / 28 / 32. No remote font dependency. Left alignment keeps repeated scanning predictable.

Layout: persistent sound library on the left; two large mode buttons across the workspace; 4×4 physical pad grid and waveform beside the selected-sound inspector; export destination and readiness directly below. The inspector scrolls independently at smaller heights. Batch export has a current-filter scope and a separate all-expansions scope, with preview/missing policy on the same page and global progress/cancel always visible.

Review against brief: a bright primary-colour instrument workspace, not a generic rounded dashboard. Keep the physical 4×4 pad layout as the defining object. Hard shadow belongs to navigation, not every control. No decorative knobs, meters, unrelated illustrations, animations or marketing panels. Batch scope copy states actual SP WAV output; edited MPC programs stay in Play & export.

## Interaction contract

Browse → play → select pad → hear alternatives → compare → keep → export.

All library matches remain reachable, with expansion, Favorites, Recent and previous/next. NI Expansions Location and Rescan sit under Sound library. Original NI demo is a persistent toggle that follows browser selection. Favorites exposes removal immediately.

MPC A–D each retain 16 pads. Mode changes keep page instances, pad assignments, Undo, candidates and active bank intact. Save/recovery and a single MPC PGM retain all 64 positions. SP export uses the current bank. Standard MPC MIDI mapping follows the selected bank; MIDI configuration is independent of export destination.

Candidates stay temporary until Keep. Compare and Discard do not alter committed sounds; exports require a decision. Drops are undoable assignments. Fill empty protects occupied positions. Separate working-kit recovery survives restart; Undo remains session-only.

Export missing as empty defaults off. Unresolved source pads block a single export or skip batch kits. Opting in leaves MPC assignments empty and creates SP silence WAVs. Intentional empty pads are always allowed. Batch and single-kit policy controls share state. Preview clips may contain a phrase and do not imply effect/synth rendering.

Mouse/keyboard and MIDI share a preloaded polyphonic audio engine. Background scanning/export does not gate unchanged pad triggers. Focus is visible, Enter/Space activate modes and controls, arrows select physical pad neighbours. Check native captures at the default size and 1120×800, including real names, missing pads, selected banks and batch scope.

## SP destination window

The transfer window uses the same paper/ink/yellow/cobalt world and bundled typography. It shows the detected project/USB connection, explicit bank buttons A–J, a physical 4×4 destination map, and one Replace existing pads switch. A scrollable body handles shorter windows while the named Send action, progress, status and Cancel/Stop stay pinned below. No backup path form or redundant review checkbox.

Opening the window reads the device; it never writes audio. One SP needs no USB selection. Multiple SPs require an explicit choice. Current bank comes from a validated status response (project/bank/pad), not note guesses or the software's MPC bank. Unknown bank requires user selection. Manual bank overrides survive refresh; changing the bank resets replacement consent. Automatic backups remain mandatory for replacement. The final named Send action accepts the reviewed immutable plan; backend identity, occupied-pad snapshot and bank-protection checks remain enforced.
