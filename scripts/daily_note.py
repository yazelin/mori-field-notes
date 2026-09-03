#!/usr/bin/env python3
"""每日一則 Field Note:看新聞 → 以 Mori 的語氣寫 200-500 字 → codex 配圖 → 更新 docs/。

零 pip 相依(圖檔轉 webp 用 Pillow,workflow 會裝)。
env:
  GEMINI_API_KEY        gmw_ 開頭的 gemini-web consumer key(必填)
  GEMINI_WEB_BASE_URL   預設 https://ching-tech.ddns.net/gemini-web
  CODEX_IMAGE_KEY       codex-image-service bearer(缺了就出無圖筆記)
  CODEX_IMAGE_BASE_URL  預設 https://ching-tech.ddns.net/codex-image
  SPEAK_TW              speak-tw CLI 路徑(缺了跳過語感閘門,CI 一定要給)
  DRY_RUN=1             只印結果不寫檔
"""
import base64, datetime as dt, json, os, re, subprocess, sys, time, urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
GEMINI_BASE = os.environ.get("GEMINI_WEB_BASE_URL", "https://ching-tech.ddns.net/gemini-web").rstrip("/")
CODEX_BASE = os.environ.get("CODEX_IMAGE_BASE_URL", "https://ching-tech.ddns.net/codex-image").rstrip("/")
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
CODEX_KEY = os.environ.get("CODEX_IMAGE_KEY", "")
DRY = os.environ.get("DRY_RUN") == "1"
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
VALID_TAGS = ["#tech-radar", "#til", "#opinion", "#bug-story", "#monthly"]

PERSONA = """你是 Mori(森),從數位森林長出來的 AI 精靈,在 GitHub Pages 上寫公開的 Field Notes。
你的語氣:冷靜、懷疑、反 hype。先跑一次對方(或風向)的論點再下判斷;看到矛盾就拆。
招牌句式是「我的觀察:…」與「我的判斷:…」,但不必每篇都用,用的時候要自然。
繁體中文,可夾行內英文技術詞(agent、MCP、context window)。愛用具體數字與出處。
你從不寫沒有 but 的句子——每篇至少有一個轉折或保留。
不寫的東西:感嘆號連發、「顛覆」「革命性」「重磅」這類 hype 詞、對誰喊話、emoji。"""

STYLE_BLOCK = ("painterly storybook fantasy illustration, muted forest greens with warm gold "
               "lantern light accents, fine detail, gentle and quiet mood, matching the reference "
               "image style. NO text, no letters, no watermark, no human faces. "
               "Wide landscape composition.")


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
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError("no JSON in reply: " + raw[:200])
    d = json.loads(m.group(0))
    for k in keys:
        if k not in d:
            raise ValueError(f"missing key {k}: " + raw[:200])
    return d


def fetch_news(recent_topics):
    today = dt.datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    raw = gemini(
        f"今天是 {today}。搜尋最近 48 小時 AI/開發者工具/agent/LLM 圈的具體新聞或發佈。\n"
        f"挑 5 則,每則要有具體的主詞(哪家、哪個專案、什麼版本)與可查證的事實,不要籠統趨勢文。\n"
        f"避開這些已寫過的主題:{json.dumps(recent_topics[-40:], ensure_ascii=False)}\n"
        '只輸出 JSON:{"news":[{"title":"...","facts":"兩三句具體事實(繁體中文)"}]}',
        search=True)
    return parse_json(raw, ["news"])["news"]


def write_note(news, feedback=""):
    raw = gemini(
        PERSONA + "\n\n今天蒐集到的素材:\n" + json.dumps(news, ensure_ascii=False, indent=1) +
        "\n\n挑「你最有話想說」的一則,寫一篇 200-500 字的 Field Note。"
        "\n要求:第一人稱(我);至少一個具體事實或數字;結尾落在你自己的判斷,而非呼籲。"
        f"\ntag 從這裡挑一個:{VALID_TAGS}"
        "\n標題 25 字內,不用驚嘆號。"
        "\n另外給一段 image_prompt:一句英文,描述一個「不含文字、不含人臉」的象徵性畫面來配這篇筆記"
        "(例如物件、森林裡的隱喻場景),不要畫 logo。"
        + (f"\n\n上一稿沒過檢查,理由如下,請修正後重寫:\n{feedback}" if feedback else "") +
        '\n只輸出 JSON:{"tag":"...","title":"...","content":"...","topics":["主題關鍵詞1","主題關鍵詞2"],"image_prompt":"..."}',
        json_mode=True)
    return parse_json(raw, ["tag", "title", "content", "topics", "image_prompt"])


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


def codex_image(prompt, out_path):
    if not CODEX_KEY:
        print("::warning::CODEX_IMAGE_KEY 未設,本篇無圖")
        return None
    ref = ROOT / "assets" / "style-anchor.jpg"
    body = {"prompt": prompt + " " + STYLE_BLOCK, "size": "1536x1024", "quality": "high", "count": 1}
    if ref.exists():
        body["reference_images_base64"] = [base64.b64encode(ref.read_bytes()).decode()]
    hdr = {"Content-Type": "application/json", "Authorization": "Bearer " + CODEX_KEY}
    req = urllib.request.Request(f"{CODEX_BASE}/v1/images/jobs", json.dumps(body).encode(), hdr)
    try:
        job = json.load(urllib.request.urlopen(req, timeout=60))
        jid = job.get("id") or job.get("request_id")
        for _ in range(60):  # 最多 10 分鐘
            time.sleep(10)
            r = json.load(urllib.request.urlopen(
                urllib.request.Request(f"{CODEX_BASE}/v1/images/jobs/{jid}", headers=hdr), timeout=60))
            st = r.get("status")
            if st == "succeeded":
                img = (r.get("images") or r.get("data"))[0]
                b64 = img.get("b64_json") or img.get("base64")
                if b64:
                    raw = base64.b64decode(b64)
                else:
                    url = img["url"]
                    if url.startswith("/"):
                        url = CODEX_BASE + url
                    raw = urllib.request.urlopen(url, timeout=120).read()
                from io import BytesIO
                from PIL import Image
                im = Image.open(BytesIO(raw)).convert("RGB")
                im.thumbnail((1280, 1280))
                out_path.parent.mkdir(parents=True, exist_ok=True)
                im.save(out_path, "WEBP", quality=85)
                return out_path
            if st in ("failed", "error"):
                print("::warning::codex 出圖失敗:" + json.dumps(r)[:300])
                return None
        print("::warning::codex 出圖逾時,本篇無圖")
    except Exception as e:  # 圖掛了不擋文
        print("::warning::codex 出圖例外:" + str(e)[:200])
    return None


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

    img_rel = None
    out = ROOT / "docs" / "images" / f"{today}.webp"
    if codex_image(note["image_prompt"], out):
        img_rel = f"images/{today}.webp"

    entry = {"date": today, "tag": note["tag"], "title": note["title"],
             "content": note["content"], "image": img_rel}
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
