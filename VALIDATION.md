# Validation — Kit workshop 0.13

Verified 17 October 2026.

- Full regression suite: 140 tests passed. Fixtures generate temporary audio and mock device transport; no physical transfer is performed.
- Packaged runtime check: 13 checks passed. Bundled fonts, MIDI input backend, sample decoding, MPC program output, sample drop and workspace construction were checked.
- New input tests exercise all 64 MPC pad notes with all four selected software banks, velocity/channel filtering, learned overrides, custom PGM map reading and atomic preference validation. Notes 89–92 remain bank D pads rather than being mistaken for JJ remote bank commands.
- Twelve actual local MPC1000 programs were inspected read-only. All used the standard 64-pad program map. User programs, samples and setup files were not modified or included in the release.
- Native review of the packaged app covered a loaded 16-pad kit and the Connections dialog. The main-workspace capture is in docs/screenshots/main-workspace.png and embedded in the README. It contains no personal filesystem paths or connection settings.
- With the MPC track output changed from OFF to 1A, the read-only USB Midi 1 listener received 11 note-on hits and 11 releases on channel 1. The requested bank-start taps produced notes 36, 52 and 84, consistent with the MIDI-track chromatic layout rather than the drum-program table. Replaying those actual messages through the new MIDI-track profile routed every hit to visible pad 1 and ignored releases.
- New regression coverage checks every chromatic note 36–99 with all four software banks, range boundaries and release suppression. The Connections test saves both MPC profiles. The original program-map tests remain in place. Exported PGM note tables are unchanged.
- The packaged app opened USB Midi 1 and the default audio output with MIDI preview ON and a real NI kit loaded. Audible output and physical positions await the user's listening check.
- The vendor-source folder and two unused UI modules were removed. Matching SparkyMK2 source remains available as a release asset and is included in the Windows package under third-party-source. Runtime helper and license notices remain present.

Existing SP destination protections remain covered by the full suite: explicit device selection for multiple SPs, no guessed bank when selection is unknown, opt-in replacement, required recovery backups, and device/destination rechecks before writes. Backups require manual restoration; cancellation cannot undo completed writes.

Physical SP writes and MPC/JJOS program loading were not performed during this release verification. Maschine instruments, effects, EQ and envelopes are not rendered by direct conversion. No NI audio, presets, personal connection settings or catalogue databases are included in the repository or release.
