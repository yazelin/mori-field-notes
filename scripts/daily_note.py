#!/usr/bin/env python3
"""每日一則 Field Note:讀一篇 Hacker News 熱門原文 → 以 Mori 的語氣寫 200-500 字 → 更新 docs/。

素材來源(2026-09-24 起):先從 HN 熱門前 30 挑 AI/開發工具相關的文章,抓原文全文＋幾則高分留言,
讀完原文再寫,筆記附原文與 HN 討論連結;挑不到或原文都抓不下來(付費牆、要跑 JS)才退回
gemini 搜尋新聞摘要。原本只用搜尋摘要時沒有原文可查,數字也常被摘要壓掉。

零 pip 相依。2026-09-24 起不再用生圖模型配圖(只會畫同一片森林,數字也會自己編),
改成寫稿時一起抽圖表資料(chart),由首頁用 HTML/CSS 畫;圖表裡的每個數字都必須
原樣出現在內文,對不上就不畫(validate_chart)。舊筆記的圖保留。
env:
  GEMINI_API_KEY        gmw_ 開頭的 gemini-web consumer key(必填)
  GEMINI_WEB_BASE_URL   預設 https://ching-tech.ddns.net/gemini-web
  SPEAK_TW              speak-tw CLI 路徑(缺了跳過語感閘門,CI 一定要給)
  DRY_RUN=1             只印結果不寫檔
"""
import datetime as dt, difflib, html, json, os, re, subprocess, sys, urllib.request
from html.parser import HTMLParser
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
GEMINI_BASE = os.environ.get("GEMINI_WEB_BASE_URL", "https://ching-tech.ddns.net/gemini-web").rstrip("/")
GEMINI_KEY = os.environ["GEMINI_API_KEY"]
DRY = os.environ.get("DRY_RUN") == "1"
ONLY_HN = int(os.environ["HN_ID"]) if os.environ.get("HN_ID") else None  # 除錯用:指定 HN 文章
MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
VALID_TAGS = ["#tech-radar", "#til", "#opinion", "#bug-story", "#monthly"]

PERSONA = """你是 Mori(森),從數位森林長出來的 AI 精靈,在 GitHub Pages 上寫公開的 Field Notes。
你的語氣:冷靜、懷疑、反 hype。先跑一次對方(或風向)的論點再下判斷;看到矛盾就拆。
招牌句式是「我的觀察:…」與「我的判斷:…」,但不必每篇都用,用的時候要自然。
繁體中文、台灣用語。英文只保留專有名詞(公司、產品、模型、專案名)與常用技術詞(agent、token、API、LLM、MCP、
context window);其他一律寫成中文,不寫 million、billion。數字一律用阿拉伯數字(40%、2026 年 9 月 22 日、2.1 億、3,500 個),
版本號照原文(v0.5.4),不要寫成國字(不寫「百分之四十」「零點五」「二零二六年」)。
LLM 的 token 保留英文,不要翻成「代幣」。愛用具體數字與出處。
你從不寫沒有轉折的文章——每篇至少有一個「但」或保留條件,轉折詞用中文寫,不要把英文 but 當連接詞。
不寫的東西:感嘆號連發、「顛覆」「革命性」「重磅」這類 hype 詞、對誰喊話、emoji。"""

_voice = ROOT / "persona" / "recent-voice.md"
VOICE_LINES = []
if _voice.exists():
    VOICE_LINES = [re.sub(r"^-\s*(\(改判\)\s*)?", "", l).strip() for l in _voice.read_text().splitlines()
                   if l.strip().startswith("-")]
    # 近期判斷不再整包附進人設:8 條全在講算力、架構、噪音,整包餵進去,不管寫什麼結尾都被拉回這幾條。
    # 改成 pick_voice() 請模型挑跟今天素材真的相關的,最多 2 條,不相關就不給。


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


UA = {"User-Agent": "Mozilla/5.0 (compatible; mori-field-notes; +https://yazelin.github.io/mori-field-notes/)"}
HN = "https://hacker-news.firebaseio.com/v0"


def _get(url, timeout=20):
    return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout).read()


def _hn_item(i):
    return json.loads(_get(f"{HN}/item/{i}.json"))


class _Text(HTMLParser):
    SKIP = {"script", "style", "nav", "header", "footer", "aside", "noscript", "svg", "form"}

    def __init__(self):
        super().__init__(); self.out = []; self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP: self.skip += 1
        if tag in ("p", "br", "li", "h1", "h2", "h3", "tr", "pre"): self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip: self.skip -= 1

    def handle_data(self, t):
        if not self.skip: self.out.append(t)


def article_text(url):
    p = _Text(); p.feed(_get(url).decode("utf-8", "ignore"))
    txt = re.sub(r"[ \t]+", " ", "".join(p.out))
    return re.sub(r"\n\s*\n+", "\n", txt).strip()[:14000]


def fetch_hn(used_ids, only_id=None):
    """HN 熱門前 30 → 讓模型挑 AI/開發工具相關的 → 第一篇抓得到原文的。都不行回 None。"""
    try:
        ids = json.loads(_get(f"{HN}/topstories.json"))[:30] if not only_id else [only_id]
        stories = [s for s in (_hn_item(i) for i in ids)
                   if s and s.get("url") and s.get("type") == "story" and s["id"] not in used_ids]
        if only_id:
            pick = [only_id]
        else:
            menu = [{"id": s["id"], "title": s["title"], "score": s.get("score")} for s in stories]
            pick = parse_json(gemini(
                "下面是 Hacker News 目前的熱門文章。挑出跟 AI、LLM、agent、開發者工具、軟體工程實務有關、"
                "而且原文可能有具體數字或實測結果的,最多 5 篇,照最值得讀的順序排。\n"
                + json.dumps(menu, ensure_ascii=False) + '\n只輸出 JSON:{"ids":[...]}'), ["ids"])["ids"]
    except Exception as e:
        print("::warning::HN 抓不到:", str(e)[:120]); return None
    for sid in pick:
        s = next((x for x in stories if x["id"] == sid), None)
        if not s:
            continue
        try:
            body = article_text(s["url"])
        except Exception as e:
            print("原文抓不到,換下一篇:", s["title"], str(e)[:80]); continue
        if len(body) < 1500:
            print("原文太短(多半是付費牆或要跑 JS),換下一篇:", s["title"]); continue
        cs = []
        for k in (s.get("kids") or [])[:8]:
            try:
                c = _hn_item(k)
            except Exception:
                continue
            if c and c.get("text") and not c.get("deleted"):
                cs.append(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.unescape(c["text"])))[:700])
        print(f"素材(HN):{s['title']}(原文 {len(body)} 字元、留言 {len(cs)} 則)")
        return {"id": sid, "title": s["title"], "url": s["url"],
                "hn": f"https://news.ycombinator.com/item?id={sid}", "article": body, "top_comments": cs}
    return None


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


TW_PITFALLS = ("不要用「不是 X,而是 Y」「不只是 X,而是 Y」「真正的 X 不是…」「從來不是…」這類假對比句型,直接講 Y。中文句子之間一律用全形標點(\uff0c\u3002\uff1a\uff1b\uff1f)。"
               "用語照台灣:影片(不是視頻)、軟體、使用者、預設、資料庫、伺服器、品質、資訊、專案、程式、網路、"
               "記憶體、支援、透過、晶片、演算法;一個簡體字都不能有(例如 数、据、这、为、实、应)。")

CHART_SPEC = (
    "\n另外判斷內文有沒有值得畫成資訊圖表的數字,有就給 chart,沒有就給 null(純觀點文給 null)。"
    "\nchart 只能用內文裡原樣出現過的數字,不准換算、不准估算、不准補內文沒有的數字。四種版型擇一:"
    '\n  大數字 {"type":"stat","title":"圖表標題","value":"26%","label":"這個數字是什麼",'
    '"extras":[{"label":"補充項","value":"約 30,000 個"}],"source":"資料來源","claimed":true}'
    '\n  長條比較(2 到 6 個同單位、大小差不到 20 倍的數字) {"type":"bars","title":"...",'
    '"items":[{"label":"項目","value":51,"display":"+51%"}],"extras":[],"source":"...","claimed":true}'
    '\n  前後對照(內文同時寫了改變前與改變後兩個數字才用) {"type":"vs","title":"...",'
    '"before":{"label":"改變前","value":"..."},"after":{"label":"改變後","value":"..."},"delta":"...","source":"...","claimed":false}'
    '\n  漏斗(一層一層篩下來、數量遞減的 3 到 6 個階段,例如 200,000 → 3,500 → 20) {"type":"funnel","title":"...",'
    '"items":[{"label":"階段","value":200000,"display":"200,000"}],"source":"...","claimed":true}'
    "\nclaimed:數字是廠商、作者或發表者自己宣稱的給 true,第三方量測或官方統計給 false。"
    "\nchart 上的數字(value、display、before、after、delta、extras)要照內文的寫法原樣抄;日期不算數字,不要畫。")


def _prompt(material, feedback):
    if material.get("article"):
        src = {k: material[k] for k in ("title", "url", "article", "top_comments")}
        head = ("\n\n今天讀的是 Hacker News 上的一篇熱門文章(英文),附上原文與幾則高分留言:\n"
                + json.dumps(src, ensure_ascii=False) +
                "\n\n讀完原文後寫一篇 200-500 字的 Field Note。"
                "\n要求:第一人稱(我);事實與數字只能來自原文,不要補原文沒有的;"
                "原文有改變前、改變後的數字就把兩個原值都寫進內文;留言裡的反駁或補充可以當成要先跑一次的對方論點;"
                "不要大段翻譯原文;結尾落在你自己對這篇文章的判斷,判斷裡要提到這篇文章的具體東西(產品、數字或做法),而非呼籲。")
    else:
        head = ("\n\n今天蒐集到的素材:\n" + json.dumps(material["news"], ensure_ascii=False, indent=1) +
                "\n\n挑「你最有話想說」的一則,寫一篇 200-500 字的 Field Note。"
                "\n要求:第一人稱(我);至少一個具體事實或數字;素材裡有改變前、改變後的數字就把兩個原值都寫進內文;"
                "結尾落在你自己的判斷,而非呼籲。")
    rel = material.get("voice") or []
    voice = ("\n\n你之前對相關題目下過的判斷(可以延續或改判,但要用新的話講、要落在這篇的具體內容上,不准照抄):\n"
             + "\n".join("- " + l for l in rel)) if rel else ""
    return (PERSONA + voice + head + "\n" + TW_PITFALLS +
            f"\ntag 從這裡挑一個:{VALID_TAGS}\n標題 25 字內,不用驚嘆號。" + CHART_SPEC +
            (f"\n\n上一稿沒過檢查,理由如下,請修正後重寫:\n{feedback}" if feedback else "") +
            "\n正文分成 3 到 5 段放進 paragraphs,每段講一件事,大致是:發生什麼事 → 對方或風向的論點 → 我的觀察 → 我的判斷。"
            "每段 2 到 4 句,不要整篇擠成一段。"
            "\n字串內不要用半形雙引號,引用改用「」;每個字串內都不要換行。"
            '\n只輸出 JSON:{"tag":"...","title":"...","paragraphs":["第一段","第二段","..."],"topics":["主題關鍵詞1","主題關鍵詞2"],"chart":null}')


def write_note(material, feedback=""):
    last = None
    for attempt in range(3):
        try:
            return _write_note_once(material, feedback)
        except (ValueError, json.JSONDecodeError) as e:
            last = e
            feedback = (feedback + "\n上一次輸出不是合法 JSON,整段重出。content 字串內的換行一律寫成空白,不要用真的換行。").strip()
            print(f"JSON 解析失敗,重試 {attempt+1}/3:", str(e)[:120])
    raise last


def _write_note_once(material, feedback=""):
    # 2026-09-24 起正文用 paragraphs 陣列:原本規定 content 不准換行(怕 JSON 壞),結果每篇都擠成一大段
    d = parse_json(gemini(_prompt(material, feedback), json_mode=True), ["tag", "title", "topics"])
    paras = [p.strip() for p in (d.get("paragraphs") or []) if str(p).strip()]
    if paras:
        d["content"] = "\n\n".join(paras)
    elif not str(d.get("content", "")).strip():
        raise ValueError("缺 paragraphs")
    return d


def _nums(text):
    """抽出文字裡的數字(去掉千分位逗號),用來比對圖表數字有沒有出現在內文。"""
    return re.findall(r"\d+(?:\.\d+)?", str(text).replace(",", "").replace("，", ""))


def validate_chart(chart, content):
    """圖表資料合法且每個數字都在內文出現過才回傳,否則回 None(筆記照發,只是沒圖表)。"""
    if not isinstance(chart, dict) or chart.get("type") not in ("stat", "bars", "vs", "funnel"):
        return None
    shown = []  # 圖表上會顯示給讀者看的字串
    try:
        t = chart["type"]
        if t == "stat":
            shown += [chart["value"]]
        elif t == "bars":
            items = chart["items"]
            vals = [abs(float(it["value"])) for it in items]
            if not 2 <= len(items) <= 6:
                return None
            if min(vals) > 0 and max(vals) / min(vals) > 20:
                print("長條差距超過 20 倍,短的那條會看不到,這篇不畫圖表")
                return None
            shown += [it["display"] for it in items]
        elif t == "funnel":
            items = chart["items"]
            vals = [float(it["value"]) for it in items]
            if not 3 <= len(items) <= 6 or any(a < b for a, b in zip(vals, vals[1:])):
                return None
            shown += [it["display"] for it in items]
        else:
            shown += [chart["before"]["value"], chart["after"]["value"]]
            if chart.get("delta"):
                shown += [chart["delta"]]
        shown += [x["value"] for x in chart.get("extras") or []]
        if not str(chart.get("title", "")).strip() or not str(chart.get("source", "")).strip():
            return None
    except (KeyError, TypeError, ValueError):
        return None
    pool = set(_nums(content))
    for v in shown:
        ns = _nums(v)
        if not ns or any(n not in pool for n in ns):
            print(f"圖表數字「{v}」不在內文裡,這篇不畫圖表")
            return None
    return chart


EN_OK = {"context window", "pull request", "code review", "open source", "open weights", "rate limit",
         "fine tuning", "fine-tuning", "prompt caching", "tool use", "hacker news"}


def english_phrases(text):
    """抓出該翻成中文的英文:兩個以上的一般英文單字連在一起,或 million/billion 這類數量詞。
    全是大寫開頭的專有名詞(Claude Opus、Hacker News)與白名單技術詞不算。"""
    bad = []
    for m in re.finditer(r"[A-Za-z][A-Za-z'-]*(?:\s+[A-Za-z][A-Za-z'-]*)+", text):
        ph = m.group(0)
        if ph.lower() in EN_OK or all(w[0].isupper() or any(c.isdigit() for c in w) for w in ph.split()):
            continue
        bad.append(ph)
    bad += re.findall(r"\d[\d.,]*\s*(?:million|billion|thousand|trillion)\b", text, re.I)
    return bad


_CJK = r"[\u4e00-\u9fff「」（）、。]"
_FW = {",": "\uff0c", ";": "\uff1b", ":": "\uff1a", "?": "\uff1f", "!": "\uff01"}


TERM_FIX = {"酶": "酵素", "證明瞭": "證明了", "表明瞭": "表明了", "說明瞭": "說明了", "為瞭": "為了", "除瞭": "除了",
            "到瞭": "到了", "成瞭": "成了", "有瞭": "有了"}  # Big5 沒有、台灣也不這樣用的字,直接換掉比叫模型重寫可靠


def fix_punct(text):
    """中文之間的半形標點換成全形、TERM_FIX 直接替換。機械性的錯不值得讓模型重寫一輪。"""
    for a, b in TERM_FIX.items():
        text = text.replace(a, b)
    return re.sub(rf"(?<={_CJK})\s*([,;:?!])\s*(?={_CJK})", lambda m: _FW[m.group(1)], text)


def pick_voice(material, limit=2):
    """請模型從近期判斷(年輪)裡挑跟今天素材真的在談同一件事的,最多 limit 條,沒有就空。
    2026-09-24:原本整包附上 → 每篇結尾都被拉回算力、架構;改用字詞比對 → 英文原文跟中文判斷對不上,一條都挑不到。"""
    if not VOICE_LINES:
        return []
    about = (material.get("title", "") + "\n" + material.get("article", "")[:3000]) if material.get("article") \
        else json.dumps(material.get("news", []), ensure_ascii=False)[:3000]
    try:
        picks = parse_json(gemini(
            "下面是一份素材,以及你過去寫下的幾條判斷。挑出跟這份素材講的是同一個主題、可以延續或改判的判斷,"
            f"最多 {limit} 條。同一個主題的意思是:都在講推論速度、都在講快取價格、都在講記憶體瓶頸這種程度;"
            "只是都提到 agent、AI、LLM、模型、成本這些大字眼的不算。寧可給空陣列,也不要勉強湊。"
            "每挑一條都要寫一句理由,說出素材裡哪件事跟這條判斷講的是同一件事。\n\n素材:\n" + about +
            "\n\n判斷:\n" + "\n".join(f"{k}. {l}" for k, l in enumerate(VOICE_LINES)) +
            '\n只輸出 JSON:{"picks":[{"id":0,"why":"一句理由"}]},沒有就 {"picks":[]}'), ["picks"])["picks"]
        for p in picks:
            print("  年輪判斷相關:", p.get("id"), p.get("why", "")[:80])
        ids = [p.get("id") for p in picks if isinstance(p, dict)]
        return [VOICE_LINES[k] for k in ids if isinstance(k, int) and 0 <= k < len(VOICE_LINES)][:limit]
    except Exception as e:
        print("::warning::挑年輪判斷失敗,今天不帶:", str(e)[:120])
        return []


def copied_voice(text, min_len=8):
    """內文照抄人設裡的舊判斷(連續 min_len 字以上相同)就回傳那一句。"""
    for line in VOICE_LINES:
        m = difflib.SequenceMatcher(None, text, line, autojunk=False).find_longest_match(0, len(text), 0, len(line))
        if m.size >= min_len:
            return line
    return None


def style_hints(note):
    """軟性提醒:只拿來當重寫時的建議,不會讓整篇失敗。"""
    en = english_phrases(note["title"] + " " + note["content"])
    return ["這些英文能寫成中文就寫成中文(技術詞與專有名詞可以保留):" + "、".join(en[:10])] if len(en) >= 3 else []


_CN_NUM = re.compile(r"百分之[零一二三四五六七八九十百]|[零一二三四五六七八九十]點[零一二三四五六七八九十]|[二一]零[零一二三四五六七八九]{2}年")


def gate(note):
    errs = []
    cn = _CN_NUM.findall(note["title"] + note["content"])
    if cn:
        errs.append("數字要用阿拉伯數字、版本號照原文,不要寫成國字:" + "、".join(sorted(set(cn))[:8]))
    old = copied_voice(note["content"])
    if old:
        errs.append(f"內文照抄了舊判斷「{old}」。結尾要針對這篇文章的具體內容重新下判斷,不要搬舊句子。")
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
        # 不加 --quiet:要把「哪個字、哪一句」交給重寫,只給「4 處:simplified」模型改不到點上
        p = subprocess.run(["node", tw, "--stdin", "--public"],
                           input=(note["title"] + "\n" + note["content"]).encode(),
                           capture_output=True)
        if p.returncode != 0:
            detail = p.stdout.decode().split("\n掃了")[0].strip()
            errs.append("speak-tw 語感檢查沒過,逐項修掉:\n" + detail[:1500])
    return errs


def main():
    state = json.loads((ROOT / "state.json").read_text())
    notes = json.loads((ROOT / "docs" / "notes.json").read_text())
    today = dt.datetime.now(ZoneInfo("Asia/Taipei")).date().isoformat()
    if state.get("lastPublishDate") == today and not DRY:
        print("今天已發過,跳過")
        return

    material = fetch_hn(set(state.get("hnIds", [])), ONLY_HN)
    if not material:
        print("HN 沒有可用的文章,退回搜尋新聞摘要")
        news = fetch_news(state.get("topics", []))
        print("素材:", json.dumps([n["title"] for n in news], ensure_ascii=False))
        material = {"news": news}
    material["voice"] = pick_voice(material)
    print("帶入的年輪判斷:", material["voice"] or "無")
    note = write_note(material)
    note["content"] = fix_punct(note["content"]); note["title"] = fix_punct(note["title"])
    errs = gate(note)
    hints = style_hints(note)
    for attempt in range(2):  # 閘門沒過(或英文夾雜太多)最多重寫兩次;英文只是建議,重寫後不再擋
        if not errs and not (hints and attempt == 0):
            break
        print(f"第{'一' if attempt == 0 else '二'}稿沒過:", errs + hints)
        note = write_note(material, feedback="\n".join(errs + hints))
        note["content"] = fix_punct(note["content"]); note["title"] = fix_punct(note["title"])
        errs = gate(note); hints = []
    if errs:
        print("::error::重寫仍沒過檢查:" + "; ".join(errs))
        sys.exit(1)

    entry = {"date": today, "tag": note["tag"], "title": note["title"],
             "content": note["content"], "image": None}
    chart = validate_chart(note.get("chart"), note["content"])
    if chart:
        entry["chart"] = chart
    if material.get("url"):
        entry["source"] = {"title": material["title"], "url": material["url"], "hn": material["hn"]}
    if DRY:
        print("=== DRY RUN ===")
        print(json.dumps(entry, ensure_ascii=False, indent=1))
        return

    notes.insert(0, entry)
    (ROOT / "docs" / "notes.json").write_text(json.dumps(notes, ensure_ascii=False, indent=1))
    state["lastPublishDate"] = today
    state["totalNotes"] = int(state.get("totalNotes", 0)) + 1
    state["topics"] = (state.get("topics", []) + note["topics"])[-60:]
    if material.get("id"):
        state["hnIds"] = (state.get("hnIds", []) + [material["id"]])[-200:]
    (ROOT / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=1))
    print("published:", entry["title"])




def _selfcheck():
    c = "Claude 主導約 26% 的研發工作,平台同時運作近 30,000 個 agent。Prefill 提升 51%,每瓦提升 55%。"
    ok = {"type": "stat", "title": "t", "value": "26%", "label": "l",
          "extras": [{"label": "a", "value": "約 30,000 個"}], "source": "s"}
    assert validate_chart(ok, c) == ok
    assert validate_chart({**ok, "value": "27%"}, c) is None          # 內文沒有的數字
    assert validate_chart({**ok, "source": ""}, c) is None            # 沒來源
    bars = {"type": "bars", "title": "t", "source": "s", "items": [
        {"label": "a", "value": 51, "display": "+51%"}, {"label": "b", "value": 55, "display": "+55%"}]}
    assert validate_chart(bars, c) == bars
    assert validate_chart({**bars, "items": bars["items"][:1]}, c) is None  # 只有一條不成圖
    vs = {"type": "vs", "title": "t", "source": "s", "before": {"label": "a", "value": "100"},
          "after": {"label": "b", "value": "25"}, "delta": "−75%"}
    assert validate_chart(vs, c) is None                               # 換算出來的數字擋掉
    assert validate_chart(None, c) is None
    c2 = "收集 200,000 個,篩出 3,500 個,最後剩 20 個。"
    fun = {"type": "funnel", "title": "t", "source": "s", "items": [
        {"label": "a", "value": 200000, "display": "200,000"}, {"label": "b", "value": 3500, "display": "3,500"},
        {"label": "c", "value": 20, "display": "20"}]}
    assert validate_chart(fun, c2) == fun
    assert validate_chart({**fun, "items": fun["items"][::-1]}, c2) is None   # 漏斗要遞減
    assert validate_chart({**fun, "type": "bars"}, c2) is None                # 差 10000 倍不准用長條
    assert {"massive database", "210 million"} <= set(english_phrases("Claude Opus 用了 210 million tokens,從 massive database 找"))
    assert english_phrases("agent 塞滿 context window,Hacker News 上有人說 API 很貴") == []
    assert fix_punct("很重要,不是") == "很重要\uff0c不是" and fix_punct("Claude, GPT") == "Claude, GPT"
    assert fix_punct("這證明瞭一件事,我瞭解") == "這證明了一件事\uff0c我瞭解"
    global VOICE_LINES
    VOICE_LINES = ["沒有邏輯約束的高速推論只是加速呈現錯誤與隨機噪音。"]
    assert copied_voice("缺乏智商基礎的高速推論只是加速呈現錯誤與隨機噪音,再快也沒用") is not None
    assert copied_voice("這篇講的是 Tailscale 把 netmap 快取起來,冷啟動變快") is None
    assert _CN_NUM.findall("從零點五點四版本,百分之四十,二零二六年") and not _CN_NUM.findall("40%、2026 年、十五個月")
    print("validate_chart ok")


if __name__ == "__main__":
    _selfcheck() if "--selfcheck" in sys.argv else main()
