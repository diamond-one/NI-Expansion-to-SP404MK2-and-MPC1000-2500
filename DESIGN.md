# Kit workshop 0.15 design

Native Windows / Python CustomTkinter. Operate mode. The user chose the Punk prototype on 18 October 2026. The real app applies its cut-paper, ink, acid-selection and condensed-title language across the editor, batch page, Connections and SP transfer window.

## Visual plan and decision

Light and dark are paired versions of this interface. The top-bar theme button
switches all live controls in place; the library list and waveform follow the
same palette. Dark mode uses charcoal surfaces, warm light text, subdued selected
pad fills and acid primary actions. The saved choice is independent of kits and
never rebuilds the player, library or bank state.

Paper #F6F3E8, ink #18171B, canvas #E7E5DC, acid selection #E5F26A, lavender mode selection #B4A4D0 and warning #F2C1BD. Heavy black rules and square paper panels keep the workspace direct. The masthead is an acid paper strip; kit names sit on an ink label. Primary actions use acid lettering on ink. Selected pads use acid with an outline; missing states retain explicit text. No raster textures or fake distressed controls obscure sound names.

Oswald is bundled locally for headings and mode titles, with its SIL Open Font Licence. Segoe UI carries dense sound names and controls. Space Grotesk is retained for compatibility with earlier resources. Type steps are 12 / 14 / 18 / 22 / 27 / 30 / 32. Fonts load privately for the process. All controls keep keyboard focus and the existing save, undo, candidate and MIDI behavior.

Layout: persistent sound library on the left; two large mode buttons across the workspace; 4Ã—4 physical pad grid and waveform beside the selected-sound inspector; export destination and readiness directly below. The inspector scrolls independently at smaller heights. Batch export has a current-filter scope and a separate all-expansions scope, with preview/missing policy on the same page and global progress/cancel always visible.

Review against brief: use the approved Punk prototype as the visual authority. Native widgets preserve legibility and focus; the paper strips, lavender navigation, Oswald titles and black/acid actions carry the style without rotating interactive controls. The 4×4 pad layout remains the defining object. The installed MIDI-track and program-pad profiles remain independent of visual styling.

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
