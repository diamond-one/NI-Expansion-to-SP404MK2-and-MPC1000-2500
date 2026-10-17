# Validation — Kit workshop 0.11

Verified 17 October 2026.

- Full regression suite: 132 tests passed.
- Final transfer suite: 12 tests passed, including two additional engine checks for failed backups and changed device projects. These use temporary generated audio and mocked device transport.
- The five workspace checks were rerun after clarifying the source-bank label and passed.
- Packaged runtime check: 13 checks passed, using generated fixtures and no device writes.
- The actual connected SP was read during native review. Current-bank detection, project identity, occupied-pad display and the replacement-off send block were verified. Turning replacement on enabled the named Send action and changed occupied pads to replacement status. No Send action was executed; hardware audio and settings were not changed.
- Native review covered the new Neo-Brutalist destination window. Its body scrolls at smaller sizes; status, Cancel/Stop, progress and the Send action stay below it. Minimum-size action bounds are also tested.
- Unreadable or ambiguous current-bank data never becomes a guessed destination. Multiple SPs require explicit device selection. Protected destinations and stale/failed transfer plans block sending.
- Replacing occupied pads remains opt-in. The existing engine requires validated recovery backups before import and rechecks device identity and destination contents. Backup restoration is manual; cancellation cannot undo completed writes.
- The main bank heading describes the software source bank, not the connected hardware destination. The destination window displays the actual SP bank.

Physical SP writes and MPC/JJOS loading were not performed during this release verification. Maschine instruments, effects, EQ and envelopes are not rendered by direct conversion. No NI audio, presets, personal connection settings or catalogue databases are included in the repository or release.
