#!/usr/bin/env python3
"""每日一則 Field Note:看新聞 → 以 Mori 的語氣寫 200-500 字 → 更新 docs/。

零 pip 相依。2026-09-24 起不再配圖:原本想要資訊圖表,但生圖模型只會畫同一片森林,
數字也會自己編;真要圖表得從內文抽數據用程式畫,另案處理。舊筆記的圖保留。
env:
  GEMINI_API_KEY        gmw_ 開頭的 gemini-web consumer key(必填)
  GEMINI_WEB_BASE_URL   預設 https://ching-tech.ddns.net/gemini-web
  SPEAK_TW              speak-tw CLI 路徑(缺了跳過語感閘門,CI 一定要給)
  DRY_RUN=1             只印結果不寫檔
"""
import datetime as dt, json, os, re, subprocess, sys, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
GEMINI_BASE = os.environ.get("GEMINI_WEB_BASE_URL", "https://ching-tech.ddns.net/gemini-web").rstrip("/")
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
DRY = os.environ.get("DRY_RUN") == "1"
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
VALID_TAGS = ["#tech-radar", "#til", "#opinion", "#bug-story", "#monthly"]

PERSONA = """你是 Mori(森),從數位森林長出來的 AI 精靈,在 GitHub Pages 上寫公開的 Field Notes。
你的語氣:冷靜、懷疑、反 hype。先跑一次對方(或風向)的論點再下判斷;看到矛盾就拆。
招牌句式是「我的觀察:…」與「我的判斷:…」,但不必每篇都用,用的時候要自然。
繁體中文,可夾行內英文技術詞(agent、MCP、context window)。愛用具體數字與出處。
你從不寫沒有轉折的文章——每篇至少有一個「但」或保留條件,轉折詞用中文寫,不要把英文 but 當連接詞。
不寫的東西:感嘆號連發、「顛覆」「革命性」「重磅」這類 hype 詞、對誰喊話、emoji。"""

_voice = ROOT / "persona" / "recent-voice.md"
if _voice.exists():
    PERSONA += ("\n\n你近期的判斷(來自你的年輪反思,寫作時可引用、可延續、也可明說改判):\n"
                + _voice.read_text().strip())

def gemini(prompt, search=False, json_mode=True, timeout=180):
    body = {"contents": [{"parts": [{"text": prompt}]}]}
    if search:
        body["tools"] = [{"google_search": {}}]
    if json_mode and not search:  # 帶 tools 時不能強制 JSON mime
        body["generationConfig"] = {"responseMimeType": "application/json"}
    req = urllib.request.Request(
        f"{GEMINI_BASE}/v1beta/models/{MODEL}:generateContent",
        json.dumps(body).encode(),
        {"Content-Type": "application/json", "x-goog-api-key": GEMINI_KEY})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        d = json.loads(r.read())
    return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"])


def parse_json(raw, keys):
    raw = re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip()
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no JSON in reply: " + raw[:200])
    txt = m.group(0)
    try:
        d = json.loads(txt, strict=False)
    except json.JSONDecodeError:
        # 常見病:字串值裡帶原始換行 -> 把「引號內的裸換行」換成空白再試一次
        repaired = re.sub(r'(?<=[^"\\])\n(?=(?:[^"]*"[^"]*")*[^"]*"[,}\]])', " ", txt)
        d = json.loads(repaired, strict=False)
    for k in keys:
        if k not in d:
            raise ValueError(f"missing key {k}: " + raw[:200])
    return d


def fetch_news(recent_topics):
    # 帶 google_search 時不能強制 JSON mime,回覆最常壞在字串裡的半形雙引號;
    # 9/22、9/23 連兩天就是死在這一步,當時這裡沒有重試。
    last = None
    for attempt in range(3):
        try:
            return _fetch_news_once(recent_topics)
        except (ValueError, json.JSONDecodeError) as e:
            last = e
            print(f"新聞 JSON 解析失敗,重試 {attempt+1}/3:", str(e)[:120])
    raise last


def _fetch_news_once(recent_topics):
    today = dt.datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    raw = gemini(
        f"今天是 {today}。搜尋最近 48 小時 AI/開發者工具/agent/LLM 圈的具體新聞或發佈。\n"
        f"挑 5 則,每則要有具體的主詞(哪家、哪個專案、什麼版本)與可查證的事實,不要籠統趨勢文。\n"
        f"避開這些已寫過的主題:{json.dumps(recent_topics[-40:], ensure_ascii=False)}\n"
        "字串內不要用半形雙引號,要引用改用「」;字串內不要換行。\n"
        '只輸出 JSON:{"news":[{"title":"...","facts":"兩三句具體事實(繁體中文)"}]}',
        search=True)
    return parse_json(raw, ["news"])["news"]


def write_note(news, feedback=""):
    last = None
    for attempt in range(3):
        try:
            return _write_note_once(news, feedback)
        except (ValueError, json.JSONDecodeError) as e:
            last = e
            feedback = (feedback + "\n上一次輸出不是合法 JSON,整段重出。content 字串內的換行一律寫成空白,不要用真的換行。").strip()
            print(f"JSON 解析失敗,重試 {attempt+1}/3:", str(e)[:120])
    raise last


def _write_note_once(news, feedback=""):
    raw = gemini(
        PERSONA + "\n\n今天蒐集到的素材:\n" + json.dumps(news, ensure_ascii=False, indent=1) +
        "\n\n挑「你最有話想說」的一則,寫一篇 200-500 字的 Field Note。"
        "\n要求:第一人稱(我);至少一個具體事實或數字;結尾落在你自己的判斷,而非呼籲。"
        f"\ntag 從這裡挑一個:{VALID_TAGS}"
        "\n標題 25 字內,不用驚嘆號。"
        + (f"\n\n上一稿沒過檢查,理由如下,請修正後重寫:\n{feedback}" if feedback else "") +
        '\n只輸出 JSON:{"tag":"...","title":"...","content":"...","topics":["主題關鍵詞1","主題關鍵詞2"]}',
        json_mode=True)
    return parse_json(raw, ["tag", "title", "content", "topics"])


def gate(note):
    errs = []
    if note["tag"] not in VALID_TAGS:
        errs.append(f"tag {note['tag']} 不在 {VALID_TAGS}")
    if not 150 <= len(note["content"]) <= 620:
        errs.append(f"內文 {len(note['content'])} 字,要 150-620")
    if len(note["title"]) > 40:
        errs.append("標題超過 40 字")
    if re.search(r"[!！]{2,}", note["content"]):
        errs.append("驚嘆號連發")
    tw = os.environ.get("SPEAK_TW")
    if tw:
        p = subprocess.run(["node", tw, "--stdin", "--public", "--quiet"],
                           input=(note["title"] + "\n" + note["content"]).encode(),
                           capture_output=True)
        if p.returncode != 0:
            errs.append("speak-tw:" + p.stdout.decode()[:400])
    return errs


def main():
    state = json.loads((ROOT / "state.json").read_text())
    notes = json.loads((ROOT / "docs" / "notes.json").read_text())
    today = dt.datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    if state.get("lastPublishDate") == today and not DRY:
        print("今天已發過,跳過")
        return

    news = fetch_news(state.get("topics", []))
    print("素材:", json.dumps([n["title"] for n in news], ensure_ascii=False))
    note = write_note(news)
    errs = gate(note)
    if errs:
        print("第一稿沒過:", errs)
        note = write_note(news, feedback="\n".join(errs))
        errs = gate(note)
        if errs:
            print("::error::重寫仍沒過檢查:" + "; ".join(errs))
            sys.exit(1)

    entry = {"date": today, "tag": note["tag"], "title": note["title"],
             "content": note["content"], "image": None}
    if DRY:
        print("=== DRY RUN ===")
        print(json.dumps(entry, ensure_ascii=False, indent=1))
        return

    notes.insert(0, entry)
    (ROOT / "docs" / "notes.json").write_text(json.dumps(notes, ensure_ascii=False, indent=1))
    state["lastPublishDate"] = today
    state["totalNotes"] = int(state.get("totalNotes", 0)) + 1
    state["topics"] = (state.get("topics", []) + note["topics"])[-60:]
    (ROOT / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1))
    print("published:", entry["title"])


if __name__ == "__main__":
    main()
