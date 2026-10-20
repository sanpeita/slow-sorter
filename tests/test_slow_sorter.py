"""Tests for slow-sorter. The fake server is a transport fixture, not a model result."""
import json
import os
import time
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import slow_sorter as ss


class _Handler(BaseHTTPRequestHandler):
    reply = {"folder": "Documents", "rename": "groceries-and-calls.txt", "why": "a to-do list"}

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        payload = {"choices": [{"message": {"content": json.dumps(_Handler.reply)}}]}
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class FakeModelServer:
    def __enter__(self):
        self.server = HTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


def make_files(root: Path, names):
    for name in names:
        (root / name).write_bytes(b"x")


class RuleTests(unittest.TestCase):
    def test_known_extensions_map_without_the_model(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_files(root, ["a.pdf", "b.jpg", "c.mp3", "d.zip", "e.py"])
            plan = ss.build_plan(root, None)
            folders = {d.path.name: d.folder for d in plan.decisions}
            self.assertEqual(folders["a.pdf"], "Documents")
            self.assertEqual(folders["b.jpg"], "Images")
            self.assertEqual(folders["c.mp3"], "Audio")
            self.assertEqual(folders["d.zip"], "Archives")
            self.assertEqual(folders["e.py"], "Code")
            self.assertTrue(all(d.source == "rule" for d in plan.decisions))

    def test_ambiguous_extension_falls_back_to_rules_without_a_model(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_files(root, ["mystery.dat"])
            d = ss.build_plan(root, None).decisions[0]
            self.assertEqual(d.folder, "Misc")
            self.assertEqual(d.source, "rule")


class ModelPathTests(unittest.TestCase):
    def test_model_can_reroute_an_ambiguous_file(self):
        with TemporaryDirectory() as tmp, FakeModelServer() as endpoint:
            root = Path(tmp)
            (root / "notes.txt").write_text("buy milk\ncall the plumber\n", encoding="utf-8")
            model = ss.Model(endpoint=endpoint)
            d = ss.build_plan(root, model).decisions[0]
            self.assertEqual(d.folder, "Documents")
            self.assertEqual(d.source, "model")
            self.assertEqual(d.rename, "groceries-and-calls.txt")
            self.assertEqual(model.calls, 1)

    def test_known_extension_never_calls_the_model(self):
        with TemporaryDirectory() as tmp, FakeModelServer() as endpoint:
            root = Path(tmp)
            make_files(root, ["a.jpg"])
            model = ss.Model(endpoint=endpoint)
            ss.build_plan(root, model)
            self.assertEqual(model.calls, 0)

    def test_unusable_model_answer_is_marked_fallback(self):
        with TemporaryDirectory() as tmp, FakeModelServer() as endpoint:
            root = Path(tmp)
            (root / "weird.bin").write_bytes(b"\x00\x01")
            _Handler.reply = {"folder": "Please tidy this", "rename": "", "why": ""}
            try:
                model = ss.Model(endpoint=endpoint)
                d = ss.build_plan(root, model).decisions[0]
            finally:
                _Handler.reply = {"folder": "Documents", "rename": "groceries-and-calls.txt",
                                  "why": "a to-do list"}
            self.assertEqual(d.source, "fallback")
            self.assertEqual(d.folder, "Misc")

    def test_dead_endpoint_degrades_to_rules(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "x.txt").write_text("hello", encoding="utf-8")
            model = ss.Model(endpoint="http://127.0.0.1:9", timeout=1.0)
            d = ss.build_plan(root, model).decisions[0]
            self.assertEqual(d.source, "fallback")
            self.assertFalse(model.available)
            self.assertTrue(model.last_error)


class NameTests(unittest.TestCase):
    def test_slashes_and_empties_are_rejected(self):
        self.assertEqual(ss.sanitize_name("a/b.txt", "x.txt"), "a_b.txt")
        self.assertIsNone(ss.sanitize_name("", "x.txt"))
        self.assertIsNone(ss.sanitize_name("x.txt", "x.txt"))
        self.assertIsNone(ss.sanitize_name(".hidden", "x.txt"))

    def test_extension_is_reapplied(self):
        self.assertEqual(ss.sanitize_name("shopping list", "notes.txt"), "shopping list.txt")


class ApplyUndoTests(unittest.TestCase):
    def test_apply_moves_then_undo_restores(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_files(root, ["a.pdf", "b.jpg"])
            before = sorted(p.name for p in root.iterdir())
            plan = ss.build_plan(root, None)
            moves = ss.apply_plan(plan, root)
            self.assertEqual(len(moves), 2)
            self.assertTrue((root / "Documents" / "a.pdf").exists())
            self.assertTrue((root / "Images" / "b.jpg").exists())
            self.assertTrue((root / ss.UNDO_NAME).exists())
            self.assertEqual(ss.undo(root), 0)
            files = sorted(p.name for p in root.iterdir() if p.is_file())
            self.assertEqual(files, before)          # 中身は元通り
            self.assertEqual(list((root / "Documents").iterdir()), [])   # 空フォルダは残る（仕様）

    def test_name_collision_gets_a_suffix(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_files(root, ["a.pdf"])
            (root / "Documents").mkdir()
            (root / "Documents" / "a.pdf").write_bytes(b"older")
            ss.apply_plan(ss.build_plan(root, None), root)
            self.assertTrue((root / "Documents" / "a-1.pdf").exists())

    def test_dotfiles_are_left_alone(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_files(root, [".hidden", "a.pdf"])
            plan = ss.build_plan(root, None)
            self.assertEqual([d.path.name for d in plan.decisions], ["a.pdf"])

    def test_dry_run_moves_nothing(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_files(root, ["a.pdf"])
            ss.main(["--dry-run", str(root)])
            self.assertTrue((root / "a.pdf").exists())
            self.assertFalse((root / "Documents").exists())


class NightTests(unittest.TestCase):
    """One file a night. Only provable junk gets binned."""

    def _old(self, path: Path, days: int = 10):
        stamp = time.time() - days * 86400
        os.utime(path, (stamp, stamp))

    def test_recent_files_are_not_candidates(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            fresh = root / "fresh.txt"
            fresh.write_text("just edited", encoding="utf-8")
            self.assertEqual(ss.night_candidates(root, 3.0), [])
            self._old(fresh)
            self.assertEqual([p.name for p in ss.night_candidates(root, 3.0)], ["fresh.txt"])

    def test_oldest_comes_first(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            a, b = root / "a.txt", root / "b.txt"
            a.write_text("a", encoding="utf-8")
            b.write_text("b", encoding="utf-8")
            self._old(a, 20)
            self._old(b, 5)
            self.assertEqual([p.name for p in ss.night_candidates(root, 3.0)], ["a.txt", "b.txt"])

    def test_empty_file_is_binned(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty = root / "empty.txt"
            empty.write_bytes(b"")
            self._old(empty)
            self.assertEqual(ss.night(root, None, 3.0, 1), 0)
            self.assertFalse(empty.exists())
            self.assertTrue((root / ss.TRASH_NAME / "empty.txt").exists())

    def test_duplicate_is_binned_and_original_kept(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            keep, copy = root / "notes.md", root / "notes copy.md"
            keep.write_text("same bytes", encoding="utf-8")
            copy.write_text("same bytes", encoding="utf-8")
            self._old(keep, 20)
            self._old(copy, 10)          # 古い方が原本。新しい方が bin される
            ss.night(root, None, 3.0, 2)
            self.assertTrue(keep.exists())                                  # 原本は残る
            self.assertFalse(copy.exists())                                 # 複製は消える
            self.assertTrue((root / ss.TRASH_NAME / "notes copy.md").exists())

    def test_unique_note_is_kept_not_binned(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            note = root / "idea.md"
            note.write_text("an idea worth keeping", encoding="utf-8")
            self._old(note)
            ss.night(root, None, 3.0, 1)
            self.assertTrue(note.exists())
            self.assertFalse((root / ss.TRASH_NAME).exists())

    def test_scratch_suffix_is_binned(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            tmpfile = root / "render.tmp"
            tmpfile.write_text("half a render", encoding="utf-8")
            self._old(tmpfile)
            ss.night(root, None, 3.0, 1)
            self.assertFalse(tmpfile.exists())

    def test_hidden_files_and_trash_are_never_candidates(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            hidden = root / ".secret"
            hidden.write_text("x", encoding="utf-8")
            (root / ss.TRASH_NAME).mkdir()
            inside = root / ss.TRASH_NAME / "old.txt"
            inside.write_text("x", encoding="utf-8")
            self._old(hidden)
            self._old(inside)
            self.assertEqual(ss.night_candidates(root, 3.0), [])

    def test_max_limits_how_many_are_touched(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            for index in range(4):
                path = root / f"x{index}.tmp"
                path.write_text(f"content {index}", encoding="utf-8")
                self._old(path, 30 - index)
            ss.night(root, None, 3.0, 1)
            left = sorted(p.name for p in root.iterdir() if p.is_file() and not p.name.startswith("."))
            self.assertEqual(left, ["x1.tmp", "x2.tmp", "x3.tmp"])

    def test_binned_file_can_be_restored(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            empty = root / "empty.txt"
            empty.write_bytes(b"")
            self._old(empty)
            ss.night(root, None, 3.0, 1)
            self.assertTrue((root / ss.TRASH_NAME / "empty.txt").exists())
            self.assertEqual(ss.undo(root), 0)
            self.assertTrue(empty.exists())


if __name__ == "__main__":
    unittest.main()
