# slow-sorter

**One file a night. That's the whole idea.**

A folder-tidying tool that runs on a 10-year-old Raspberry Pi 2, uses a 150M-parameter
open-weight model to describe what it is looking at, and is allowed to delete exactly
one thing: what it can *prove* is junk.

Built for the [Hacktoberfest Open-Source AI Challenge: Week 3 — Slow and Steady](https://dev.to/challenges/hacktoberfest-week3-2026-10-19).

---

## Why slow

Every "AI cleanup" tool I have tried wants to do everything at once: scan 40,000 files,
classify them, move them, and ask for forgiveness. On a device as small as a Pi 2
(900 MHz, 4 cores, ~580 MB free) that is a fantasy, and on a device as personal as your
notes folder it is a bad idea anyway.

So this one is deliberately paced:

- It looks at **one file per night** (`--max`, default 1).
- It only considers files you have not touched for **3 days** (`--quiet-days`).
- It writes **one line** about the file, produced by a local model, so you can see what it
  thought it was looking at.
- It bins the file **only if the answer is provable** — an empty file, a byte-identical
  duplicate of an older file, or a scratch extension (`.tmp`, `.bak`, `.log`, `.part`,
  `.crdownload`).
- Everything else gets `kept (cannot prove it is safe to bin)` and is left for tomorrow.

Forty junk files take forty nights. Nobody has to sit and watch. That is the point.

## Why open

- The model is **open-weight** and runs on the same board as the logic. No API key, no
  account, no per-file cost, no network.
- The rules are 20 lines of dictionary. You can read them, disagree with them, and change
  them; the model is never the final authority.
- The undo log is a JSON file you can open. `--undo` restores what the last run moved.

## Install

Python 3.9+ and nothing else — no dependencies, no build step.

```sh
git clone https://github.com/sanpeita/slow-sorter
cd slow-sorter
python3 slow_sorter.py --demo ./junk     # a sample folder to play with
python3 slow_sorter.py --nightly ./junk --offline --quiet-days 0
```

`--offline` uses the rules only. Without it, `slow_sorter.py` talks to any
OpenAI-compatible endpoint on the loopback interface:

```sh
python3 slow_sorter.py --nightly ./junk --endpoint http://127.0.0.1:8899
```

That is `llama-server -m llm-jp-3-150m-instruct3-q8_0.gguf -c 512 -n 256 --jinja` on the Pi.

## Run it every night

```ini
# ~/.config/systemd/user/slow-sorter.service
[Unit]
Description=slow-sorter nightly pass

[Service]
Type=oneshot
ExecStart=%h/slow-sorter/run-nightly.sh
```

```sh
# ~/.config/systemd/user/slow-sorter.timer
[Unit]
Description=slow-sorter at 03:00

[Timer]
OnCalendar=*-*-* 03:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

## What it will not do

- It will not touch hidden files, directories, or its own trash folder.
- It will not bin a file it cannot explain.
- It will not bin the older of two identical files (the older one is treated as the original).
- It will not run without a folder argument, and `--dry-run` moves nothing.

## Measured on the actual hardware

| | |
|---|---|
| Device | Raspberry Pi 2 Model B, 900 MHz, 4 cores, 942 MB RAM |
| Model | llm-jp-3-150m-instruct3, Q8_0 GGUF |
| Generation | ~9 tokens/second |
| One describe call | 1.7–5.8 s |
| Nightly pass (1 file) | under 10 s including the model call |

The numbers are the reason for the design, not a limitation to apologise for.

## Tests

```sh
python3 -m unittest discover -s tests
```

21 tests: rule mapping, model routing, unusable answers, a dead endpoint, name
sanitising, collisions, dry-run, and the nightly bin/keep/undo path.

## License

MIT.
