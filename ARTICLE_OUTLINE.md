# slow-sorter — DEV article outline (Week 3: Slow and Steady)

Deadline: 2026-10-26 15:59 JST. Judging: Writing Quality is weighted heavily.
Target length: 900–1,300 words. Language: English.

## Working title
**One file a night: building a folder tidier for a 942 MB computer**

## Opening (the hook — do not start with "AI is transforming...")
I have a Raspberry Pi 2 from 2015. 900 MHz, four cores, 942 MB of RAM, no Wi-Fi.
I also have a folder full of junk, and a 150M-parameter language model that runs on that board
at nine tokens per second.

The contest asked for something slow and steady. I already had the hardware.

## 1. The obvious design, and why I threw it out
- Obvious: scan the folder, classify every file with the model, sort into folders, done.
- On the Pi that is ~14–31 s per question (measured on a 0.5B model at 1.65 tok/s), and
  on my notes folder it means letting a 150M model decide the fate of 40,000 files at once.
- The failure mode is not "wrong folder". It is "gone".
- So: one file per night. One decision per night. Fortieth junk file takes forty nights.

## 2. What the model is allowed to do (almost nothing)
- The model describes. The rules decide.
- `RULES` maps extensions to folders — 20 lines you can argue with.
- The model's only job is one line of prose so I can see what it thought it was looking at.
- Evidence: I asked the 150M model to bin things on its own. It agreed with itself.
  Both-option test: 0/6. Yes/no test: 0/16. Logprobs: "yes" 20/20 with almost constant delta.
  The model has no opinion. It has a prior. (See `BINARY_CHOICE_RESULT.md` in the Pi lab.)

## 3. Delete only what you can prove
Three proofs, no judgement calls:
1. **Empty.** Zero bytes.
2. **Duplicate.** Same SHA-256 as an older file. The older one wins; the copy goes.
3. **Scratch.** `.tmp .bak .log .part .crdownload` — by definition unfinished work.
Everything else prints `kept (cannot prove it is safe to bin)` and is left for tomorrow.
Include: hidden files, directories, and the trash folder are never candidates.
Include: the undo log is JSON, and `--undo` puts it back.

## 4. The bug that made the article worth writing
First real run on the Pi:

```
$ python3 slow_sorter.py --nightly ~/inbox --quiet-days 3 --max 1
family: move | why: scratch suffix | file: render.tmp
model: no preamble
```

The model answered the instruction instead of the file. `no preamble` — that string was in my
prompt, not in the file. A 150M model will happily echo the task and call it an answer.

Fix: treat instruction echo as failure, fall back to the file's own first line.

```
model: abandoned draft from september
```

Lesson: on small models, output validation is not defensive programming, it is the feature.

## 5. Numbers (all measured, none estimated)
| | |
|---|---|
| Device | Raspberry Pi 2 Model B, 900 MHz, 4 cores, 942 MB RAM |
| Model | llm-jp-3-150m-instruct3 Q8_0 |
| Generation | ~9 tok/s |
| Full nightly pass, 1 file, model call included | 4.4 s |
| Bigger model (Qwen2.5-0.5B q4_0) | 1.65–1.85 tok/s, 14–31 s per question, no quality gain |
| Tests | 24, no dependencies, `python3 -m unittest discover -s tests` |

The wall on this board is not RAM. It is 900 MHz times four.

## 6. What I would tell someone starting this
- Pick the slowest machine you own. It makes the design honest.
- Never let the model hold the delete key.
- One file a night is not a limitation, it is the product.
- The undo log is the spec.

## Close
Forty junk files, forty nights, nobody watching. That is what "slow and steady" is
supposed to mean, and it is the only version of this tool I would actually run on my own notes.

## Submission checklist
- [ ] Post on DEV with the required tag/canon
- [ ] Link the repo: https://github.com/sanpeita/slow-sorter
- [ ] Demo video or GIF (optional but boosts judging)
- [ ] Submit via the challenge form / DevRelay
