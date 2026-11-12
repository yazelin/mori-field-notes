import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("GEMINI_API_KEY", "unit-test-key")

from scripts import daily_note


OLD_JUDGMENT = "沒有邏輯約束的高速推論只是加速呈現錯誤與隨機噪音。"
OPENING = "原文把測試條件列得很清楚，這是理解結果的起點。"
ENDING = "我的判斷是，這種限制條件比單一效能數字更值得保留。"


def make_note(title, content):
    return {
        "tag": "#opinion",
        "title": title,
        "paragraphs": [content],
        "topics": ["測試"],
        "chart": None,
    }


def long_content_773():
    middle_size = 773 - len(OPENING) - len(ENDING)
    base, extra = divmod(middle_size, 8)
    middle = ["中" * (base - 1 + (i < extra)) + "。" for i in range(8)]
    content = OPENING + "".join(middle) + ENDING
    assert len(content) == 773
    return content


class DailyNoteGateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        (self.root / "docs").mkdir()
        (self.root / "state.json").write_text("{}", encoding="utf-8")
        (self.root / "docs" / "notes.json").write_text("[]", encoding="utf-8")

    def run_main_with_replies(self, replies):
        prompts = []

        def fake_gemini(prompt, **_kwargs):
            prompts.append(prompt)
            return json.dumps(replies[min(len(prompts) - 1, len(replies) - 1)], ensure_ascii=False)

        material = {
            "id": 123,
            "title": "測試素材",
            "url": "https://example.invalid/article",
            "hn": "https://example.invalid/discussion",
            "article": "測試原文",
            "top_comments": [],
        }
        output = io.StringIO()
        with patch.object(daily_note, "ROOT", self.root), \
             patch.object(daily_note, "DRY", False), \
             patch.object(daily_note, "VOICE_LINES", [OLD_JUDGMENT]), \
             patch.object(daily_note, "fetch_hn", return_value=material), \
             patch.object(daily_note, "pick_voice", return_value=[OLD_JUDGMENT]), \
             patch.object(daily_note, "gemini", side_effect=fake_gemini), \
             patch.dict(os.environ, {"SPEAK_TW": ""}), \
             contextlib.redirect_stdout(output):
            try:
                daily_note.main()
                exit_code = 0
            except SystemExit as exc:
                exit_code = exc.code
        return exit_code, output.getvalue(), prompts

    def test_sequential_failures_end_with_trimmed_published_note(self):
        copied = OLD_JUDGMENT + "我再回到原文確認條件，" + "這些背景補充讓測試脈絡更完整。" * 8
        title_too_long = "很長的標題" * 9
        too_long = long_content_773()
        replies = [
            make_note("測試標題", copied),
            make_note(title_too_long, "我先核對原文條件。" + "這段補充保持在規定長度之內。" * 12),
            make_note("保留原文條件", too_long),
        ]
        replies.extend(make_note("保留原文條件", too_long) for _ in range(daily_note.MAX_REWRITES))

        exit_code, output, prompts = self.run_main_with_replies(replies)

        self.assertEqual(exit_code, 0)
        self.assertIn("已截短", output)
        state_notes = json.loads((self.root / "docs" / "notes.json").read_text(encoding="utf-8"))
        self.assertEqual(len(state_notes), 1)
        content = state_notes[0]["content"]
        self.assertTrue(content.startswith(OPENING))
        self.assertTrue(content.endswith(ENDING))
        self.assertLessEqual(len(content), daily_note.CONTENT_MAX_CHARS)
        self.assertGreaterEqual(len(content), daily_note.CONTENT_MIN_CHARS)
        self.assertIn("標題長度：目前 45 字；上限 40 字，請刪除約 5 字。", prompts[2])
        self.assertIn("內文長度：目前 773 字；範圍 150 至 620 字，請刪除約 153 字。", prompts[3])

    def test_rewrite_that_still_copies_old_judgment_is_not_published(self):
        copied = OLD_JUDGMENT + "我再回到原文確認條件，" + "這些背景補充讓測試脈絡更完整。" * 8
        reply = make_note("測試標題", copied)

        exit_code, output, _prompts = self.run_main_with_replies([reply])

        self.assertEqual(exit_code, 1)
        self.assertIn("內文照抄了舊判斷", output)
        self.assertNotIn("已截短", output)
        self.assertEqual(json.loads((self.root / "docs" / "notes.json").read_text(encoding="utf-8")), [])

    def test_rewrite_prompt_includes_all_current_gate_failures(self):
        initial = make_note("標題" * 21, OLD_JUDGMENT)
        initial["tag"] = "#unknown"
        corrected = make_note("修正後標題", "我先核對原文條件。" + "這段補充交代測試背景與具體做法。" * 10)

        exit_code, _output, prompts = self.run_main_with_replies([initial, corrected])

        self.assertEqual(exit_code, 0)
        self.assertIn("內文照抄了舊判斷", prompts[1])
        self.assertIn("標籤「#unknown」不在允許清單內", prompts[1])
        self.assertIn("內文長度：目前", prompts[1])
        self.assertIn("標題長度：目前 42 字；上限 40 字", prompts[1])

    def test_gate_reports_every_independent_failure_with_length_details(self):
        note = {
            "tag": "#unknown",
            "title": "標題" * 21,
            "content": OLD_JUDGMENT,
        }
        with patch.object(daily_note, "VOICE_LINES", [OLD_JUDGMENT]), \
             patch.dict(os.environ, {"SPEAK_TW": ""}):
            errors = daily_note.gate(note)

        self.assertTrue(any("內文照抄了舊判斷" in error for error in errors))
        self.assertTrue(any("標籤「#unknown」" in error for error in errors))
        self.assertTrue(any("內文長度：目前" in error and "請補寫約" in error for error in errors))
        self.assertTrue(any("標題長度：目前 42 字；上限 40 字，請刪除約 2 字。" == error for error in errors))


if __name__ == "__main__":
    unittest.main()
