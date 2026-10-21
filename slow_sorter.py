#!/usr/bin/env python3
"""slow-sorter: tidy a folder overnight, one file at a time, on a slow local model.

The model proposes; the rules decide. Every decision is explainable, and every
move is undoable. No network except the loopback inference endpoint.

Usage:
    python3 slow_sorter.py --dry-run ~/Downloads
    python3 slow_sorter.py --apply   ~/Downloads
    python3 slow_sorter.py --undo    ~/Downloads
    python3 slow_sorter.py --demo ./junk      # build a sample folder to play with
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

FOLDERS = ("Documents", "Images", "Audio", "Video", "Archives", "Code", "Misc")

# The floor: deterministic, explainable, and never wrong about extensions.
RULES = {
    ".pdf": "Documents", ".doc": "Documents", ".docx": "Documents", ".odt": "Documents",
    ".xls": "Documents", ".xlsx": "Documents", ".ods": "Documents", ".ppt": "Documents",
    ".pptx": "Documents", ".csv": "Documents", ".epub": "Documents",
    ".png": "Images", ".jpg": "Images", ".jpeg": "Images", ".gif": "Images",
    ".webp": "Images", ".svg": "Images", ".heic": "Images", ".bmp": "Images",
    ".mp3": "Audio", ".wav": "Audio", ".flac": "Audio", ".m4a": "Audio", ".ogg": "Audio",
    ".mp4": "Video", ".mkv": "Video", ".mov": "Video", ".webm": "Video", ".avi": "Video",
    ".zip": "Archives", ".tar": "Archives", ".gz": "Archives", ".7z": "Archives",
    ".rar": "Archives", ".tgz": "Archives",
    ".py": "Code", ".js": "Code", ".ts": "Code", ".c": "Code", ".h": "Code", ".cpp": "Code",
    ".rb": "Code", ".go": "Code", ".rs": "Code", ".sh": "Code", ".ipynb": "Code",
    ".json": "Code", ".yaml": "Code", ".yml": "Code", ".toml": "Code", ".css": "Code",
    ".html": "Code", ".md": "Documents",
}

# Extensions where a filename alone is not enough to decide.
AMBIGUOUS = {".txt", ".dat", ".bin", ".log", ".tmp", ".bak", ""}

UNDO_NAME = ".slow-sorter-undo.json"
TRASH_NAME = ".slow-sorter-trash"
TEXT_SNIFF_BYTES = 600
DISPOSABLE_SUFFIXES = {".tmp", ".bak", ".log", ".part", ".crdownload"}


@dataclass
class Decision:
    path: Path
    folder: str
    rename: str | None = None
    source: str = "rule"          # rule | model | fallback
    why: str = ""
    seconds: float = 0.0

    @property
    def target(self) -> Path:
        name = self.rename or self.path.name
        return self.path.parent / self.folder / name


@dataclass
class Model:
    """Loopback chat endpoint (llama.cpp server or anything OpenAI-compatible)."""
    endpoint: str = "http://127.0.0.1:8899"
    timeout: float = 45.0
    calls: int = 0
    tokens_seen: int = 0
    seconds: float = 0.0
    last_error: str = ""
    available: bool = True

    def propose(self, filename: str, size: int, sniff: str) -> dict | None:
        prompt = (
            "You tidy a folder. Pick ONE folder from: " + ", ".join(FOLDERS) + ".\n"
            f"file: {filename}\nbytes: {size}\n"
            + (f"first bytes:\n{sniff}\n" if sniff else "no preview (binary)\n")
            + 'Answer with JSON only: {"folder": "...", "rename": "...", "why": "..."}\n'
            "rename must be a short descriptive filename without slashes, or empty."
        )
        body = json.dumps({
            "model": "local",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 120,
            "response_format": {"type": "json_object"},
        }).encode()
        request = urllib.request.Request(
            self.endpoint.rstrip("/") + "/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"},
        )
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            self.available = False
            self.last_error = f"{type(error).__name__}: {error}"
            return None
        finally:
            self.seconds += time.time() - started
        self.calls += 1
        try:
            text = payload["choices"][0]["message"]["content"]
            self.tokens_seen += len(str(text).split())
            return json.loads(text)
        except (KeyError, IndexError, TypeError, ValueError):
            return None

    def describe(self, filename: str, sniffed: str) -> str:
        """One honest line about what this file is. Short on purpose."""
        prompt = (
            "Describe this file in one short line (max 12 words), plain and concrete. "
            "No quotes, no preamble.\n"
            f"file: {filename}\n"
            + (f"first bytes:\n{sniffed}\n" if sniffed else "no preview (binary)\n")
        )
        body = json.dumps({
            "model": "local",
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.0,
            "max_tokens": 48,
        }).encode()
        request = urllib.request.Request(
            self.endpoint.rstrip("/") + "/v1/chat/completions",
            data=body, headers={"Content-Type": "application/json"},
        )
        started = time.time()
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            text = payload["choices"][0]["message"]["content"]
        except (urllib.error.URLError, TimeoutError, OSError, ValueError,
                KeyError, IndexError, TypeError):
            self.available = False
            self.last_error = "describe failed"
            return ""
        finally:
            self.seconds += time.time() - started
        self.calls += 1
        line = " ".join(str(text).split())[:90]
        # 150M は指示文をそのまま返すことがある（実測: "no preamble"）。その場合は失敗扱い。
        echo = ("preamble", "no quotes", "describe this", "file:", "max 12 words")
        if not line or any(marker in line.lower() for marker in echo):
            return ""
        return line


@dataclass
class Plan:
    decisions: list[Decision] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)


def sniff(path: Path) -> str:
    try:
        head = path.open("rb").read(TEXT_SNIFF_BYTES)
    except OSError:
        return ""
    if b"\0" in head:
        return ""
    try:
        return head.decode("utf-8").strip()[:300]
    except UnicodeDecodeError:
        return ""


def sanitize_name(name: str, original: str) -> str | None:
    if not name:
        return None
    name = name.strip().strip('"').replace("/", "_").replace("\\", "_")
    stem, suffix = os.path.splitext(original)
    if not os.path.splitext(name)[1]:
        name += suffix
    if name == original or len(name) > 80 or name.startswith("."):
        return None
    return name


def decide(path: Path, model: Model | None) -> Decision:
    suffix = path.suffix.lower()
    size = path.stat().st_size
    if suffix not in AMBIGUOUS:
        folder = RULES.get(suffix, "Misc")
        return Decision(path, folder, source="rule", why=f"extension {suffix or '(none)'}")
    if model is None:
        return Decision(path, RULES.get(suffix, "Misc"), source="rule", why="ambiguous, rules only")
    started = time.time()
    proposal = model.propose(path.name, size, sniff(path))
    elapsed = time.time() - started
    folder = str((proposal or {}).get("folder", "")).strip().capitalize()
    if folder not in FOLDERS:
        return Decision(path, RULES.get(suffix, "Misc"), source="fallback",
                        why="model answer unusable", seconds=elapsed)
    return Decision(path, folder,
                    rename=sanitize_name(str((proposal or {}).get("rename", "")), path.name),
                    source="model",
                    why=str(proposal.get("why", ""))[:80] or "model proposal",
                    seconds=elapsed)


def build_plan(root: Path, model: Model | None) -> Plan:
    plan = Plan()
    for path in sorted(root.iterdir()):
        if path.name.startswith(".") or path.is_dir() or path.name == UNDO_NAME:
            continue
        plan.decisions.append(decide(path, model))
    return plan


def show(plan: Plan, root: Path) -> None:
    width = max((len(d.path.name) for d in plan.decisions), default=10)
    for d in plan.decisions:
        rename = f" -> {d.rename}" if d.rename else ""
        print(f"  {d.path.name:<{width}}  {d.folder:<9} [{d.source}]{rename}")
        if d.why:
            print(f"  {'':<{width}}  {d.why}")
    if not plan.decisions:
        print("  (nothing to sort)")


def apply_plan(plan: Plan, root: Path) -> list[dict]:
    moves: list[dict] = []
    for d in plan.decisions:
        target = d.target
        target.parent.mkdir(parents=True, exist_ok=True)
        candidate, counter = target, 1
        while candidate.exists():
            candidate = target.with_name(f"{target.stem}-{counter}{target.suffix}")
            counter += 1
        shutil.move(str(d.path), str(candidate))
        moves.append({"from": str(d.path), "to": str(candidate)})
    if moves:
        (root / UNDO_NAME).write_text(json.dumps(moves, ensure_ascii=False, indent=2), encoding="utf-8")
    return moves


def undo(root: Path) -> int:
    log = root / UNDO_NAME
    if not log.exists():
        print("no undo log here")
        return 1
    moves = json.loads(log.read_text(encoding="utf-8"))
    restored = 0
    for move in reversed(moves):
        src, dst = Path(move["from"]), Path(move["to"])
        if dst.exists():
            src.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(src))
            restored += 1
    log.unlink()
    print(f"restored {restored}/{len(moves)} files")
    return 0


DEMO_FILES = {
    "invoice_2026_10.pdf": b"%PDF-1.4 demo",
    "notes.txt": "buy milk\ncall the plumber\nfinish the slides\n".encode(),
    "IMG_4821.jpg": b"\xff\xd8\xff\xe0demo",
    "screen-recording.mp4": b"\x00\x00\x00\x18ftypmp42",
    "backup.tar": b"demo archive",
    "main.py": b"print('hello')\n",
    "data.bin": b"\x01\x02\x03\x04",
    "voice-memo.m4a": b"\x00\x00\x00 ftypM4A",
}


def make_demo(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for name, payload in DEMO_FILES.items():
        (root / name).write_bytes(payload)
    print(f"created {len(DEMO_FILES)} files in {root}")


def _sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            digest.update(block)
    return digest.hexdigest()


def night_candidates(root: Path, quiet_days: float = 3.0, now: float | None = None) -> list[Path]:
    """Files untouched for `quiet_days`, oldest first. Hidden files are never candidates."""
    now = time.time() if now is None else now
    cutoff = now - quiet_days * 86400
    found: list[Path] = []
    for path in root.iterdir():
        if path.is_dir() or path.name.startswith("."):
            continue
        try:
            if path.stat().st_mtime > cutoff:
                continue
        except OSError:
            continue
        found.append(path)
    return sorted(found, key=lambda item: item.stat().st_mtime)


def duplicate_of(path: Path, root: Path) -> Path | None:
    try:
        size = path.stat().st_size
    except OSError:
        return None
    if size == 0:
        return None
    mine = _sha256_of(path)
    for other in root.iterdir():
        if other == path or other.is_dir() or other.name.startswith("."):
            continue
        try:
            if other.stat().st_size != size:
                continue
        except OSError:
            continue
        if _sha256_of(other) == mine:
            return other
    return None


def why_disposable(path: Path, root: Path) -> str | None:
    """Only bin what can be proven. Anything else is kept and left for tomorrow."""
    try:
        if path.stat().st_size == 0:
            return "empty file"
    except OSError:
        return None
    twin = duplicate_of(path, root)
    if twin is not None:
        # 原本を残す: 双子の方が古ければ、こちらは後から来た複製とみなして bin。
        try:
            if twin.stat().st_mtime < path.stat().st_mtime:
                return f"byte-identical duplicate of {twin.name}"
        except OSError:
            return None
        return None
    if path.suffix.lower() in DISPOSABLE_SUFFIXES:
        return f"scratch file ({path.suffix})"
    return None


def bin_to_trash(path: Path, root: Path) -> Path:
    trash = root / TRASH_NAME
    trash.mkdir(exist_ok=True)
    target, counter = trash / path.name, 1
    while target.exists():
        target = trash / f"{path.stem}-{counter}{path.suffix}"
        counter += 1
    shutil.move(str(path), str(target))
    log = root / UNDO_NAME
    moves = json.loads(log.read_text(encoding="utf-8")) if log.exists() else []
    moves.append({"from": str(path), "to": str(target)})
    log.write_text(json.dumps(moves, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def describe(path: Path, model: "Model | None") -> str:
    preview = sniff(path)
    if model is not None and model.available:
        line = model.describe(path.name, preview)
        if line:
            return line
    return preview.splitlines()[0][:60] if preview else "(no preview)"


def night(root: Path, model: "Model | None", quiet_days: float, limit: int) -> int:
    """Walk the oldest files in order and bin the first `limit` provable ones.

    A file that cannot be proven junk never blocks the queue: it is reported once and
    the next night starts again from the top of the list.
    """
    candidates = night_candidates(root, quiet_days)
    if not candidates:
        print(f"nothing untouched for {quiet_days:g} days. see you tomorrow.")
        return 0

    binned = 0
    looked = 0
    explained_kept = 0
    for path in candidates[:20]:
        if binned >= limit:
            break
        reason = why_disposable(path, root)
        if reason is None:
            looked += 1
            if explained_kept < 1:          # 1行だけ「何に見えたか」を残す
                print(f"{path.name}\n  {describe(path, model)}\n"
                      f"  -> kept (cannot prove it is safe to bin)")
                explained_kept += 1
            continue
        looked += 1
        line = describe(path, model)
        landed = bin_to_trash(path, root)
        binned += 1
        print(f"{path.name}\n  {line}\n  -> binned ({reason}) -> {landed.relative_to(root)}")

    if binned == 0:
        print(f"looked at {looked} old file(s), binned nothing. slow and steady.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="tidy a folder with a slow local model")
    parser.add_argument("target", nargs="?", help="folder to tidy")
    parser.add_argument("--dry-run", action="store_true", help="show the plan, move nothing")
    parser.add_argument("--apply", action="store_true", help="move the files")
    parser.add_argument("--undo", action="store_true", help="restore the last run")
    parser.add_argument("--demo", metavar="DIR", help="create a sample folder")
    parser.add_argument("--offline", action="store_true", help="rules only, no model")
    parser.add_argument("--nightly", action="store_true",
                        help="look at the oldest untouched file; bin only what is provably junk")
    parser.add_argument("--quiet-days", type=float, default=3.0,
                        help="how many days a file must be untouched (default 3)")
    parser.add_argument("--max", type=int, default=1,
                        help="how many files to look at per night (default 1)")
    parser.add_argument("--endpoint", default=os.environ.get("SLOW_SORTER_ENDPOINT",
                                                            "http://127.0.0.1:8899"))
    args = parser.parse_args(argv)

    if args.demo:
        make_demo(Path(args.demo))
        return 0
    if not args.target:
        parser.error("target folder is required")
    root = Path(args.target).expanduser().resolve()
    if not root.is_dir():
        print(f"not a folder: {root}")
        return 2
    if args.undo:
        return undo(root)

    if args.nightly:
        model = None if args.offline else Model(endpoint=args.endpoint)
        return night(root, model, args.quiet_days, args.max)

    model = None if args.offline else Model(endpoint=args.endpoint)
    started = time.time()
    plan = build_plan(root, model)
    elapsed = time.time() - started

    print(f"slow-sorter  {root}")
    show(plan, root)
    if model is not None:
        if model.available:
            rate = model.calls / elapsed if elapsed else 0
            print(f"model: {model.calls} calls, {elapsed:.1f}s total, {rate:.2f} files/s")
        else:
            print(f"model unavailable ({model.last_error}) -> rules only")
    if args.dry_run or not args.apply:
        print("\n(dry run: nothing moved. add --apply to move.)")
        return 0
    moves = apply_plan(plan, root)
    print(f"\nmoved {len(moves)} files. undo with: --undo {root}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
