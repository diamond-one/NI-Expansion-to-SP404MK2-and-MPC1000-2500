"""Reviewed, per-pad USB transfers through SparkyMK2's official ``sp404`` CLI.

Protocol/CLI formats were checked against upstream main.rs blob
1ef12986a4fa1a622dd92c26cc300c3c2c468f67. Upstream currently has no JSON mode.
Only the explicit send() entry point imports audio; discovery and probe are read-only.
https://github.com/TuscanPL/SparkyMK2/blob/main/crates/sp404-cli/src/main.rs
"""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import uuid

import soundfile as sf

from .model import is_sp_target, kit_count, role_names, sp_pad_number

_DEVICE_LOCK = threading.RLock()
BANKS = "ABCDEFGHIJ"
_PAD = re.compile(r"^([A-J])([1-9]|1[0-6])$")


class TransferError(RuntimeError):
    def __init__(self, message, report=None):
        super().__init__(message)
        self.report = report or {}


@dataclass(frozen=True)
class TransferEntry:
    logical_slot: int
    pad_number: int
    target_pad: str
    sample_id: str
    source_path: str
    source_sha256: str
    name: str
    role: str
    occupied: bool
    previous_name: str


@dataclass(frozen=True)
class TransferPlan:
    project: int
    project_name: str
    port: str
    device_serial: str
    bank: str
    kit: int
    kit_size: int
    seed: int
    entries: tuple[TransferEntry, ...]
    overwrites: tuple[str, ...]
    bank_snapshot: tuple[tuple[str, str], ...]

    def to_dict(self):
        return asdict(self)


def discover_cli(configured=None):
    """Find a configured or bundled CLI, without starting it or accessing USB."""
    def candidate(path):
        if not path:
            return None
        path = Path(os.path.expandvars(str(path))).expanduser()
        if path.is_dir():
            path = path / ("sp404.exe" if os.name == "nt" else "sp404")
        return path.resolve() if path.is_file() else None
    if configured:
        return candidate(configured)
    explicit = os.environ.get("NI_TO_SP_SP404_CLI")
    if explicit:
        return candidate(explicit)
    roots = [Path(sys.executable).parent, Path(__file__).resolve().parents[1]]
    if getattr(sys, "_MEIPASS", None):
        roots.insert(0, Path(sys._MEIPASS))
    for root in roots:
        for directory in ("", "tools", "bin", "resources", "_internal/tools", "_internal/bin"):
            for filename in ("sp404.exe", "sp404"):
                found = candidate(root / directory / filename)
                if found:
                    return found
    return candidate(shutil.which("sp404"))


def _run(cli, arguments, port=None, timeout=90):
    command = [str(cli)]
    if port:
        command.extend(["--port", str(port)])
    command.extend(str(arg) for arg in arguments)
    try:
        result = subprocess.run(command, shell=False, capture_output=True, text=True,
                                encoding="utf-8", errors="strict", timeout=timeout,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
        raise TransferError(f"SP connection command failed: {exc}. Disconnect SparkyMK2 and the Roland app before trying again.") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout or "Unknown connection error").strip()
        raise TransferError(f"sp404 {arguments[0]} failed: {detail[:1200]}")
    return result.stdout


def list_ports(cli):
    """Return upstream's VID/PID-filtered device ports; never choose between devices."""
    output = _run(cli, ["ports"], timeout=30)
    if output.strip() == "no SP-404MKII found":
        return []
    ports = []
    for line in output.splitlines():
        if not line.strip():
            continue
        fields = line.split("\t")
        if len(fields) != 3 or not fields[0].strip():
            raise TransferError("Could not read SP port information from this CLI version.")
        ports.append({"port": fields[0].strip(), "product": fields[1].strip(), "serial": fields[2].strip()})
    return ports


def parse_status(output):
    """Parse the exact upstream status fields; unknown/missing protection fails closed."""
    result = {"banks": {}}
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith("port:"):
            if "port" in result:
                raise TransferError("Ambiguous SP status: duplicate port.")
            result["port"] = stripped.split(":", 1)[1].strip()
        match = re.fullmatch(r"project:\s+(\d+)\s+\((.*)\)", stripped)
        if match:
            if "project" in result:
                raise TransferError("Ambiguous SP status: duplicate project.")
            result["project"], result["project_name"] = int(match[1]), match[2]
        match = re.fullmatch(r"bank ([A-J]):\s+tempo\s+(\d+(?:\.\d+)?)\s+volume\s+(\d+)(\s+protected)?", stripped)
        if match:
            if match[1] in result["banks"]:
                raise TransferError("Ambiguous SP status: duplicate bank.")
            result["banks"][match[1]] = {"tempo": float(match[2]), "volume": int(match[3]), "protected": bool(match[4])}
    if (not result.get("port") or not 1 <= result.get("project", 0) <= 16
            or set(result["banks"]) != set(BANKS)):
        raise TransferError("Could not identify the current SP project and all bank protection states. No transfer is permitted.")
    return result


def parse_pads(output):
    """The successful empty stdout is upstream's representation of an empty project."""
    occupied = {}
    pattern = re.compile(r"^\s*([A-J](?:[1-9]|1[0-6]))\s+(\d+)\s+frames\s+(?:(mono|stereo)\s+)?level\s+(\d+)\s+bpm\s+(\d+(?:\.\d+)?)\s*(.*?)\s*$")
    for line in output.splitlines():
        if not line.strip():
            continue
        match = pattern.fullmatch(line)
        if not match or match[1] in occupied:
            raise TransferError("Could not determine occupied SP pads from this CLI output. No transfer is permitted.")
        occupied[match[1]] = {"frames": int(match[2]), "channels": match[3] or "unknown",
                              "level": int(match[4]), "bpm": float(match[5]), "name": match[6],
                              "signature": " ".join(line.split())}
    return occupied


def _identity(status):
    return (status["port"].casefold(), status["project"], status["project_name"])


def parse_selection(output):
    """Read only a validated 7E status reply; never infer a bank from MIDI notes.

    Upstream sp404-device/control.rs identifies project, bank and selected pad
    at offsets 1, 3 and 5. The bundled CLI exposes this through its raw read.
    Unknown layouts leave the bank unknown rather than defaulting to A.
    """
    states = set()
    for line in output.splitlines():
        match = re.fullmatch(r"< ch05 (?:short|long\s*) dst=[0-9a-fA-F]{2} ((?:[0-9a-fA-F]{2}\s*)+)", line.strip())
        if not match:
            continue
        raw = bytes.fromhex(match[1])
        if not raw or raw[0] != 0x7E:
            continue
        if len(raw) < 6 or raw[1] >= 16 or raw[3] >= 10 or raw[5] >= 16:
            return None
        states.add((raw[1] + 1, BANKS[raw[3]], raw[5] + 1))
    if len(states) != 1:
        return None
    project, bank, pad = states.pop()
    return {"project": project, "bank": bank, "pad": pad}


def probe(cli, port=None):
    """Read device identity, current project, bank protection and all occupied pads."""
    with _DEVICE_LOCK:
        ports = list_ports(cli)
        if not ports:
            raise TransferError("No SP-404MKII USB connection found. Connect the sampler and disconnect other librarian apps.")
        if port:
            matches = [item for item in ports if item["port"].casefold() == str(port).casefold()]
            if len(matches) != 1:
                raise TransferError("The selected SP USB port is no longer present. Check the connection again.")
            selected = matches[0]
        elif len(ports) == 1:
            selected = ports[0]
        else:
            raise TransferError("More than one SP-404MKII is connected. Select an explicit USB port.")
        first = parse_status(_run(cli, ["status"], selected["port"]))
        occupied = parse_pads(_run(cli, ["pads"], selected["port"], timeout=180))
        try:
            selection = parse_selection(_run(cli,
                ["raw", "--channel", "5", "--wait", "300", "fe", "66", "00"],
                selected["port"], timeout=15))
        except TransferError:
            selection = None
        second = parse_status(_run(cli, ["status"], selected["port"]))
        if (_identity(first) != _identity(second) or first["banks"] != second["banks"]
                or second["port"].casefold() != selected["port"].casefold()):
            raise TransferError("The SP project or bank settings changed while checking it. Check the connection again.")
        if selection and selection["project"] != second["project"]:
            raise TransferError("The SP project changed while reading the selected bank. Check the connection again.")
        return {**second, "device_serial": selected["serial"], "occupied_pads": occupied,
                "occupancy_known": True, "cli": str(Path(cli).resolve()),
                "current_bank": selection["bank"] if selection else None}


def _bank_snapshot(bank, occupied):
    return tuple((f"{bank}{n}", occupied.get(f"{bank}{n}", {}).get("signature", "")) for n in range(1, 17))


def make_transfer_plan(session, kit, destbank, probe_result):
    """Capture a frozen, reviewable plan. This function never contacts the device."""
    if not is_sp_target(session.target):
        raise TransferError("Select SP-404MKII as the sampler before preparing a USB transfer.")
    if not 0 <= kit < kit_count(session):
        raise TransferError("The selected kit is outside this session.")
    bank = str(destbank).strip().upper()
    if bank not in BANKS or len(bank) != 1:
        raise TransferError("Choose a destination bank from A to J.")
    if not probe_result.get("occupancy_known") or not isinstance(probe_result.get("occupied_pads"), dict):
        raise TransferError("Pad occupancy is unknown. Check the SP connection before reviewing a transfer.")
    if not 1 <= probe_result.get("project", 0) <= 16 or not probe_result.get("port"):
        raise TransferError("The SP project identity is incomplete.")
    bank_info = probe_result.get("banks", {}).get(bank)
    if bank_info is None or bank_info.get("protected") is not False:
        raise TransferError(f"SP bank {bank} is protected or its protection state is unknown. Unprotect it on the sampler, then check again.")
    occupied = probe_result["occupied_pads"]
    samples, entries = session.by_id, []
    for slot, pad in enumerate(session.pads[kit * session.kit_size:(kit + 1) * session.kit_size]):
        if not pad.sample_id:
            continue
        sample = samples.get(pad.sample_id)
        if sample is None or sample.kind == "excluded" or not sample.features.get("available", True):
            raise TransferError(f"Kit slot {slot + 1} has an unavailable or excluded source. Replace it before sending.")
        number = sp_pad_number(slot)
        target = f"{bank}{number}"
        entries.append(TransferEntry(slot, number, target, sample.id, sample.path, sample.fingerprint,
                                     sample.features.get("name") or Path(sample.path).stem, role_names(session.kit_size)[slot],
                                     target in occupied, occupied.get(target, {}).get("name", "")))
    if not entries:
        raise TransferError("This kit has no samples to send.")
    return TransferPlan(probe_result["project"], probe_result.get("project_name", ""), probe_result["port"],
                        probe_result.get("device_serial", ""), bank, kit, session.kit_size, session.seed,
                        tuple(entries), tuple(entry.target_pad for entry in entries if entry.occupied),
                        _bank_snapshot(bank, occupied))


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path, value):
    path = Path(path)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, ensure_ascii=False)
        handle.flush()
        os.fsync(handle.fileno())
    for attempt in range(6):
        try:
            temporary.replace(path)
            break
        except PermissionError:
            if attempt == 5:
                raise
            # Windows antivirus/sync software can briefly hold a just-written journal.
            time.sleep(min(.025 * 2 ** attempt, .2))


def _export_files(plan, folder):
    folder = Path(folder).expanduser().resolve()
    if not (folder / "COMPLETE.json").is_file() or (folder / "INCOMPLETE.txt").exists():
        raise TransferError("Prepare a complete, verified SP WAV export before sending.")
    complete = json.loads((folder / "COMPLETE.json").read_text(encoding="utf-8"))
    recipe = json.loads((folder / "KIT_RECIPE.json").read_text(encoding="utf-8"))
    if (complete.get("complete") is not True or complete.get("validated") is not True
            or not is_sp_target(recipe.get("target", "")) or recipe.get("kit_size") != plan.kit_size
            or recipe.get("kit") != plan.kit + 1 or recipe.get("seed") != plan.seed):
        raise TransferError("The export does not match the kit that was reviewed. Export and review it again.")
    rows = recipe.get("pads", [])
    if len(rows) != len(plan.entries) or len({r.get("logical_slot") for r in rows}) != len(rows):
        raise TransferError("The export's pad assignments do not match the reviewed plan.")
    by_slot, files = {row["logical_slot"] - 1: row for row in rows}, {}
    for entry in plan.entries:
        row = by_slot.get(entry.logical_slot, {})
        if (row.get("sample_id") != entry.sample_id or row.get("source_sha256") != entry.source_sha256
                or row.get("pad") != entry.pad_number):
            raise TransferError("The kit changed after review. Export and review it again.")
        path = (folder / row.get("file", "")).resolve()
        if path.parent != folder or not path.is_file() or not row.get("output_sha256") or _sha256(path) != row["output_sha256"]:
            raise TransferError(f"The prepared WAV for {entry.target_pad} is missing, changed or outside the export folder.")
        info = sf.info(path)
        if info.samplerate != 48000 or info.subtype != "PCM_16" or info.channels not in (1, 2) or info.frames < 4800:
            raise TransferError(f"The prepared WAV for {entry.target_pad} is not a validated SP sample.")
        files[entry.target_pad] = path
    return folder, files


def _verify_target(plan, current):
    expected = (plan.port.casefold(), plan.project, plan.project_name)
    if _identity(current) != expected or current.get("device_serial", "") != plan.device_serial:
        raise TransferError("The connected SP or selected project changed after review. Check and review the destination again.")
    if current["banks"][plan.bank]["protected"]:
        raise TransferError(f"Destination bank {plan.bank} is now protected. No further samples were sent.")
    if _bank_snapshot(plan.bank, current["occupied_pads"]) != plan.bank_snapshot:
        raise TransferError("Destination pad contents changed after review. Check and review the destination again.")


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise TransferError("Transfer cancelled between pads. Already completed pads remain on the sampler.")


def send(plan, cli, exportfolder, *, reviewed=False, allow_overwrite=False, backup_dir=None,
         progress=lambda message, value: None, cancel=None):
    """Execute only an explicitly reviewed plan. Never initializes or restores a project.

    Cancellation waits for the current CLI command to end. A failed import may have
    changed its target pad; the report records this and never claims a rollback.
    """
    report = {"status": "not_started", "completed_pads": [], "failed_pad": None,
              "failed_pad_may_be_modified": False, "backup_dir": None, "journal": None,
              "plan": plan.to_dict(), "verification": "CLI import acknowledgements; hardware playback not verified"}
    journal = None
    attempted = False
    with _DEVICE_LOCK:
        try:
            if reviewed is not True:
                raise TransferError("Review the destination project, bank and pad mapping before sending.")
            if plan.overwrites and allow_overwrite is not True:
                raise TransferError("This plan replaces occupied pads. Explicitly allow those replacements in the review first.")
            if plan.overwrites and backup_dir is None:
                raise TransferError("Choose a backup folder before replacing occupied SP pads.")
            _check_cancel(cancel)
            folder, files = _export_files(plan, exportfolder)
            current = probe(cli, port=plan.port)
            _verify_target(plan, current)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            base = Path(backup_dir).expanduser().resolve() if backup_dir else folder / "SP_TRANSFERS"
            backup = base / f"PROJECT_{plan.project:02}_BANK_{plan.bank}_{stamp}_{uuid.uuid4().hex[:8]}"
            backup.mkdir(parents=True)
            journal = backup / "TRANSFER_REPORT.json"
            report.update(status="backing_up", backup_dir=str(backup), journal=str(journal))
            _write_json(journal, report)
            remote = f"ROLAND/SP-404MKII/PROJECT_{plan.project:02}"
            # Save the project pad configuration even for an empty destination bank.
            local = backup / "PADCONF.BIN"
            _run(cli, ["get", f"{remote}/PADCONF.BIN", local], plan.port, timeout=180)
            if not local.is_file() or local.stat().st_size == 0:
                raise TransferError("The SP pad-configuration backup was not saved. Nothing was sent.")
            configuration = _run(cli, ["padconf", local], timeout=90)
            if not any(line.startswith("project name:") for line in configuration.splitlines()):
                raise TransferError("The SP pad-configuration backup could not be validated. Nothing was sent.")
            backups = [{"file": local.name, "sha256": _sha256(local)}]
            if plan.overwrites:
                live = backup / "live-state"
                _run(cli, ["dump-state", live], plan.port, timeout=180)
                required = [live / "status.bin", live / "settings.bin"] + [live / f"pad-{entry.target_pad}.bin" for entry in plan.entries if entry.occupied]
                if any(not path.is_file() or path.stat().st_size == 0 for path in required):
                    raise TransferError("The live pad-settings backup is incomplete. Nothing was sent.")
                backups.extend({"file": str(path.relative_to(backup)), "sha256": _sha256(path)} for path in required)
            for number, entry in enumerate(e for e in plan.entries if e.occupied):
                _check_cancel(cancel)
                progress(f"Backing up occupied pad {entry.target_pad}", .05 + .25 * (number + 1) / max(1, len(plan.overwrites)))
                filename = f"BANK{BANKS.index(plan.bank) + 1}-{entry.pad_number:02}.SMP"
                local = backup / filename
                _run(cli, ["get", f"{remote}/SMPL/{filename}", local], plan.port, timeout=300)
                if not local.is_file() or local.stat().st_size < 4:
                    raise TransferError(f"The sample backup for {entry.target_pad} is incomplete. Nothing was sent.")
                with local.open("rb") as handle:
                    if handle.read(4) != b"RFWV":
                        raise TransferError(f"The sample backup for {entry.target_pad} is not a valid SMP file. Nothing was sent.")
                info = _run(cli, ["smp-info", local], timeout=90)
                if "digest ok: true" not in info.splitlines():
                    raise TransferError(f"The sample backup for {entry.target_pad} failed its digest check. Nothing was sent.")
                backups.append({"pad": entry.target_pad, "file": filename, "sha256": _sha256(local)})
            _write_json(backup / "BACKUP_COMPLETE.json", {"complete": True, "plan": plan.to_dict(), "files": backups,
                        "note": "Raw occupied samples, PADCONF and live parameter blocks are preserved. Restoration is manual; no automatic rollback is attempted."})
            _check_cancel(cancel)
            # A long backup must never make an old review valid for a newly selected project.
            _verify_target(plan, probe(cli, port=plan.port))
            report["status"] = "sending"
            _write_json(journal, report)
            for number, entry in enumerate(plan.entries):
                _check_cancel(cancel)
                current = parse_status(_run(cli, ["status"], plan.port))
                if _identity(current) != (plan.port.casefold(), plan.project, plan.project_name) or current["banks"][plan.bank]["protected"]:
                    raise TransferError("The SP project or bank protection changed. No further samples were sent.")
                report["failed_pad"] = entry.target_pad
                report["failed_pad_may_be_modified"] = False
                _write_json(journal, report)
                progress(f"Sending {entry.target_pad}: {entry.name}", .3 + .7 * number / len(plan.entries))
                attempted = True
                report["failed_pad_may_be_modified"] = True
                _write_json(journal, report)
                output = _run(cli, ["import", entry.target_pad, files[entry.target_pad], "--project", plan.project,
                                    "--name", entry.name], plan.port, timeout=300)
                if not re.search(rf"^{re.escape(entry.target_pad)}: imported \d+ frames as .* into project {plan.project}$", output, re.MULTILINE):
                    raise TransferError(f"The CLI did not confirm the import of {entry.target_pad}. Its state must be checked on the sampler.")
                report["completed_pads"].append(entry.target_pad)
                report["failed_pad"], report["failed_pad_may_be_modified"] = None, False
                attempted = False
                _write_json(journal, report)
            report["status"] = "complete"
            _write_json(journal, report)
            progress("Kit sent to the SP. Pad imports were acknowledged.", 1)
            return report
        except Exception as exc:
            report["status"] = "cancelled" if cancel is not None and cancel.is_set() else "failed"
            report["failed_pad_may_be_modified"] = bool(attempted)
            report["error"] = str(exc)
            if journal is not None:
                try:
                    _write_json(journal, report)
                except OSError:
                    pass
            raise TransferError(str(exc), report) from exc
