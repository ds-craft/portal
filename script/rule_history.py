#!/usr/bin/env python3
"""ルールの改正履歴・過去の版・条文比較(新旧対照表)を生成する。

サブモジュール _rules(ds-craft/rules)のGit履歴を読み、次を書き出す(どちらもコミットしない生成物)。
  _data/rule_history.json      ルールごとの版の一覧と、各版の条文比較のHTML
                               (条文ページの左の「改正履歴」と「条文比較」タブに使う)
  _rule_versions/<ルールID>/<改正ルールID>.md
                               過去の版の全文(コレクション rule_versions)
                               → /rule/<ルールID>/<改正ルールID>.html

版の区切り:
  - コミットの作者がルール番号(例:令和8年ルール第2号)で、Enforced トレーラーがあるコミットを1つの版とする。
    版の名前(URL)は改正したルールの番号をルールIDにしたもの(制定時はそのルール自身のID)。
  - それ以外のコミット(誤記の修正など)は版を増やさず、その時点の版の文面に反映する。
  - rules/repealed/ に移したコミットは廃止として扱い、版にはしない。
  - 最新の版(廃止したルールは廃止時点の版)の全文は、元のルールページ(/rule/<ルールID>.html)で表示する。

条文比較(新旧対照表)は、改正された条(見出しごとのかたまり)だけを、左に改正後・右に改正前で並べ、
変わった文字に傍線を引く。

使い方: python3 script/rule_history.py   (リポジトリのルートで実行。標準ライブラリのみ使用)
"""
import difflib
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RULES_REPO = ROOT / "_rules"
RULES_DIR = "rules"
REPEALED_DIR = "rules/repealed"
OUT_DIR = ROOT / "_rule_versions"
DATA_FILE = ROOT / "_data" / "rule_history.json"

KIND_CODE = {"ルール": "01", "ルール施行令": "02", "国内法": "10"}
NUMBER_RE = re.compile(r"^令和(\d+)年(ルール施行令|ルール|国内法)第(\d+)号$")
LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
RULE_LINK_RE = re.compile(r"\]\((R\d{7}\.html)")


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(RULES_REPO), *args], check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout


def number_to_id(number: str):
    m = NUMBER_RE.match(number)
    return f"R{int(m[1]):02d}{KIND_CODE[m[2]]}{int(m[3]):03d}" if m else None


def wareki(iso: str) -> str:
    y, m, d = (int(x) for x in iso.split("-"))
    if (y, m, d) < (2019, 5, 1):
        sys.exit(f"{iso}: 令和より前の日付には対応していません")
    return f"令和{y - 2018}年{m}月{d}日"


def split_front_matter(text: str):
    lines = text.replace("\r\n", "\n").split("\n")
    if lines[0] != "---":
        return {}, text
    end = lines.index("---", 1)
    fm = {}
    for line in lines[1:end]:
        key, sep, value = line.partition(":")
        if sep:
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            fm[key.strip()] = value
    return fm, "\n".join(lines[end + 1:])


# ---------------------------------------------------------------- 履歴の読み取り

def file_commits(rid: str, path: str):
    """そのルールのファイルを変えたコミットを古い順に返す。"""
    out = git("log", "--follow", "--format=%H%x1f%an%x1f%s%x1f%(trailers:key=Enforced,valueonly,separator=%x2C)%x1e",
              "--", path)
    commits = []
    for rec in out.split("\x1e"):
        rec = rec.strip()
        if not rec:
            continue
        sha, author, subject, enforced = (rec.split("\x1f") + [""])[:4]
        commits.append({"sha": sha, "author": author, "subject": subject, "enforced": enforced.strip()})
    commits.reverse()
    for c in commits:
        for p in (f"{REPEALED_DIR}/{rid}.md", f"{RULES_DIR}/{rid}.md"):
            if subprocess.run(["git", "-C", str(RULES_REPO), "cat-file", "-e", f"{c['sha']}:{p}"],
                              capture_output=True).returncode == 0:
                c["path"] = p
                c["text"] = git("show", f"{c['sha']}:{p}")
                break
    return commits


def rule_history(rid: str, path: str):
    versions = []
    repeal = None
    prev_path = None
    for c in file_commits(rid, path):
        amend_id = number_to_id(c["author"]) if c["enforced"] else None
        if c["path"].startswith(REPEALED_DIR) and (prev_path or "").startswith(RULES_DIR + "/R"):
            repeal = {"date_iso": c["enforced"], "by_number": c["author"], "by_id": amend_id}
        elif amend_id:
            versions.append({
                "vid": amend_id,
                "by_number": c["author"],
                "subject": c["subject"],
                "date_iso": c["enforced"],
                "text": c["text"],
            })
        elif versions:
            versions[-1]["text"] = c["text"]  # 誤記の修正など: その時点の版に反映する
        prev_path = c["path"]
    if not versions:
        sys.exit(f"{rid}: 版がありません(作者がルール番号のコミットがない)")
    return versions, repeal


# ---------------------------------------------------------------- 条文比較

def blocks(body: str):
    """本文を見出しごとのかたまりに分ける。kramdownの属性行({: ...})は除き、リンクは文字だけにする。"""
    result, cur = [], None
    for line in body.split("\n"):
        if line.startswith("#"):
            cur = {"heading": line.lstrip("#").strip(), "lines": []}
            result.append(cur)
        elif line.strip().startswith("{:"):
            continue
        else:
            if cur is None:
                cur = {"heading": "", "lines": []}
                result.append(cur)
            if line.strip():
                cur["lines"].append(LINK_RE.sub(r"\1", line))
    for b in result:
        b["key"] = b["heading"] + "\n" + "\n".join(b["lines"])
    return [b for b in result if b["heading"] or b["lines"]]


def mark_lines(old: str, new: str):
    """文字単位で比べ、変わった部分に印を付ける。結果は行ごとの(HTML, 変わったか)の一覧を(改正前, 改正後)で返す。"""
    sm = difflib.SequenceMatcher(None, old, new, autojunk=False)
    oseg, nseg = [], []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            oseg.append((old[i1:i2], False))
            nseg.append((new[j1:j2], False))
        else:
            if i2 > i1:
                oseg.append((old[i1:i2], True))
            if j2 > j1:
                nseg.append((new[j1:j2], True))
    return to_lines(oseg, "del"), to_lines(nseg, "ins")


def to_lines(segments, tag: str):
    """(文字列, 変わったか)の並びを行に分ける。印は行をまたがないよう行ごとに付け直す。"""
    lines, changed = [[]], [False]
    for text, marked in segments:
        for k, part in enumerate(text.split("\n")):
            if k > 0:
                lines.append([])
                changed.append(False)
            if part:
                lines[-1].append(f'<{tag} class="diff-mark">{html.escape(part)}</{tag}>' if marked else html.escape(part))
                changed[-1] = changed[-1] or marked
    return [("".join(l), c) for l, c in zip(lines, changed)]


SIMILAR = 0.45  # 改正前後の行を同じ項・号の改正とみなす似ている度合いの下限


def align(olds, news):
    """順序を保ったまま、似ている行どうしを組にする(似ている度合いの合計が最大になる組み合わせ)。
    組にならない行は片方だけ(None)にする。項の追加・削除で番号がずれても、内容の近い行どうしを組にするため。"""
    n, m = len(olds), len(news)
    sim = [[difflib.SequenceMatcher(None, o, w, autojunk=False).ratio() for w in news] for o in olds]
    best = [[0.0] * (m + 1) for _ in range(n + 1)]
    for i in range(n - 1, -1, -1):
        for j in range(m - 1, -1, -1):
            take = best[i + 1][j + 1] + sim[i][j] if sim[i][j] >= SIMILAR else -1
            best[i][j] = max(take, best[i + 1][j], best[i][j + 1])
    pairs, i, j = [], 0, 0
    while i < n and j < m:
        if sim[i][j] >= SIMILAR and best[i][j] == best[i + 1][j + 1] + sim[i][j]:
            pairs.append((olds[i], news[j]))
            i, j = i + 1, j + 1
        elif best[i][j] == best[i + 1][j]:
            pairs.append((olds[i], None))
            i += 1
        else:
            pairs.append((None, news[j]))
            j += 1
    return pairs + [(o, None) for o in olds[i:]] + [(None, w) for w in news[j:]]


def pair_blocks(old_blocks, new_blocks):
    """かたまりの並びを比べ、変わったものを(改正前, 改正後)の組にして返す。片方がないものはNone。"""
    pairs = []
    sm = difflib.SequenceMatcher(None, [b["key"] for b in old_blocks], [b["key"] for b in new_blocks], autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        olds, news = old_blocks[i1:i2], new_blocks[j1:j2]
        # 範囲の中では、見出しが同じものどうしを組にし、残りは順に組にする
        sub = difflib.SequenceMatcher(None, [b["heading"] for b in olds], [b["heading"] for b in news], autojunk=False)
        for t, a1, a2, b1, b2 in sub.get_opcodes():
            o, n = olds[a1:a2], news[b1:b2]
            for k in range(max(len(o), len(n))):
                pairs.append((o[k] if k < len(o) else None, n[k] if k < len(n) else None))
    return pairs


def compare_lines(old_lines, new_lines):
    """1つの条の項・号の行を比べ、対応する行どうしの組 (改正前のHTML|None, 改正後のHTML|None, 変わったか) を返す。
    片方にしかない行は反対側を None にする。組になった行が違えば「変わった」にする
    (e-Govと同じく、改正された項は両側とも白い箱に入れ、同じ高さに並べるため)。"""
    pairs = []
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            pairs += [(html.escape(l), html.escape(l), False) for l in new_lines[j1:j2]]
            continue
        for o, n in align(old_lines[i1:i2], new_lines[j1:j2]):
            (om, _), = mark_lines(o or "", n or "")[0]
            (nm, _), = mark_lines(o or "", n or "")[1]
            pairs.append((om if o is not None else None, nm if n is not None else None, True))
    return pairs


def line_div(content, changed: bool, heading: bool = False) -> str:
    """表の欄の中の1行。変わっていない行は灰色の背景のまま、変わった行は白い箱に入れる(CSSの .is-same)。"""
    cls = "diff-line" + (" diff-heading" if heading else "") + ("" if changed else " is-same")
    return f'<div class="{cls}">{content or "&#8203;"}</div>'


def none_div(label: str) -> str:
    return f'<div class="diff-none">{label}</div>'


def group_rows(pairs, heading_pair=None) -> list:
    """1つの条を、対応する行ごとの表の行(<tr>)にする。pairs は compare_lines の結果。
    heading_pair は見出しの (改正前, 改正後, 変わったか)。"""
    rows = []
    items = ([(heading_pair, True)] if heading_pair and (heading_pair[0] or heading_pair[1]) else []) + [(p, False) for p in pairs]
    for (o, n, changed), is_heading in items:
        # 改正前にない行(追加された項)は改正前の欄に「（新設）」、改正後にない行(削られた項)は改正後の欄に「（削除）」
        new_cell = line_div(n, changed, is_heading) if n is not None else none_div("（削除）")
        old_cell = line_div(o, changed, is_heading) if o is not None else none_div("（新設）")
        rows.append(f"<tr><td>{new_cell}</td><td>{old_cell}</td></tr>")
    return rows


def whole_rows(heading: str, lines, side: str) -> list:
    """条ごと新設・削除されたもの。反対側は条の全部の行にまたがる1つの欄にして「（新設）」「（削除）」と書く。"""
    tag = "ins" if side == "new" else "del"
    divs = []
    if heading:
        divs.append(line_div(f'<{tag} class="diff-mark">{html.escape(heading)}</{tag}>', True, heading=True))
    divs += [line_div(f'<{tag} class="diff-mark">{html.escape(l)}</{tag}>', True) for l in lines]
    other = none_div("（新設）" if side == "new" else "（削除）")
    rows = []
    for k, d in enumerate(divs):
        span = f'<td rowspan="{len(divs)}">{other}</td>' if k == 0 else ""
        rows.append(f"<tr><td>{d}</td>{span}</tr>" if side == "new" else f"<tr>{span}<td>{d}</td></tr>")
    return rows


def compare_html(old, new) -> str:
    """改正された条だけを、左に改正後・右に改正前で並べた表(新旧対照表)を返す。
    条ごとに <tbody> を分け、条の中は対応する項・号ごとに1行にして左右の高さを揃える。"""
    groups = []
    old_name, new_name = old["fm"].get("rule_name", ""), new["fm"].get("rule_name", "")
    if old_name != new_name:
        (om, _), = mark_lines(old_name, new_name)[0]
        (nm, _), = mark_lines(old_name, new_name)[1]
        groups.append(group_rows([(om, nm, True)], ("題名", "題名", False)))
    for ob, nb in pair_blocks(blocks(old["body"]), blocks(new["body"])):
        if ob and nb:
            (oh, _), = mark_lines(ob["heading"], nb["heading"])[0]
            (nh, _), = mark_lines(ob["heading"], nb["heading"])[1]
            heading = (oh, nh, ob["heading"] != nb["heading"])
            groups.append(group_rows(compare_lines(ob["lines"], nb["lines"]), heading))
        elif nb:
            groups.append(whole_rows(nb["heading"], nb["lines"], "new"))
        else:
            groups.append(whole_rows(ob["heading"], ob["lines"], "old"))
    if not groups:
        return '<p class="diff-empty">この改正で、条文の文言は変わっていません（リンクなど表示の変更のみ）。</p>'
    out = ['<table class="diff-table">',
           f'<thead><tr><th scope="col">改正後<span class="diff-date">（{new["date"]} 施行）</span></th>'
           f'<th scope="col">改正前<span class="diff-date">（{old["date"]} 施行）</span></th></tr></thead>']
    for rows in groups:
        out.append("<tbody>" + "".join(rows) + "</tbody>")
    out.append("</table>")
    return "\n".join(out)


# ---------------------------------------------------------------- 書き出し

def write_page(path: Path, front: dict, content: str):
    lines = ["---"] + [f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in front.items()] + ["---", content]
    path.write_text("\n".join(lines), encoding="utf-8", newline="\n")


def build_rule(rid: str, path: str, data: dict):
    versions, repeal = rule_history(rid, path)
    main_url = f"/rule/{rid}.html"
    for v in versions:
        v["fm"], v["body"] = split_front_matter(v["text"])
        v["date"] = wareki(v["date_iso"])

    entries = []
    for i, v in enumerate(versions):
        last = i == len(versions) - 1
        url = main_url if last else f"/rule/{rid}/{v['vid']}.html"
        if i + 1 < len(versions):
            next_by = versions[i + 1]["by_number"]
            end_iso = versions[i + 1]["date_iso"]
        else:
            next_by = repeal["by_number"] if repeal else None
            end_iso = repeal["date_iso"] if repeal else None
        entries.append({
            "vid": v["vid"],
            "kind": "制定" if i == 0 else "改正",
            "by_number": v["by_number"],
            "title": re.sub(r"\s*施行$", "", v["subject"]),
            "date_iso": v["date_iso"],
            "date": v["date"],
            "end_iso": end_iso,
            "end": wareki(end_iso) if end_iso else None,
            "next_by_number": next_by,
            "rule_name": v["fm"].get("rule_name", ""),
            "url": url,
            "latest": last,
            "compare_html": compare_html(versions[i - 1], v) if i > 0 else None,
        })
    data[rid] = {
        "rule_id": rid,
        "repealed": bool(repeal),
        "repeal": {**repeal, "date": wareki(repeal["date_iso"])} if repeal else None,
        "versions": entries,
    }

    out = OUT_DIR / rid
    for v, e in zip(versions, entries):
        if e["latest"]:
            continue
        fm = v["fm"]
        out.mkdir(parents=True, exist_ok=True)
        body = RULE_LINK_RE.sub(r"](../\1", v["body"])  # 一段下の階層に置くため、他のルールへのリンクを直す
        write_page(out / f"{v['vid']}.md", {
            "layout": "rule",
            "permalink": e["url"],
            "rule_id": rid,
            "rule_base": "../",
            "noindex": True,
            "version_id": v["vid"],
            "title": f"{fm.get('rule_name', '')}（{e['date']}施行の版）",
            "rule_name": fm.get("rule_name", ""),
            "rule_number": fm.get("rule_number", ""),
            "enforcement_date": fm.get("enforcement_date", ""),
            "category": fm.get("category", ""),
            "og_description": f"{fm.get('rule_name', '')}（{e['date']}施行の版）",
        }, body)


def main():
    if not (RULES_REPO / ".git").exists():
        sys.exit("_rules がありません(git submodule update --init を実行してください)")
    if git("rev-parse", "--is-shallow-repository").strip() == "true":
        git("fetch", "--quiet", "--unshallow")  # 浅いクローンでは改正履歴をたどれない
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir()
    DATA_FILE.parent.mkdir(exist_ok=True)

    data = {}
    files = sorted((RULES_REPO / RULES_DIR).glob("R*.md")) + sorted((RULES_REPO / REPEALED_DIR).glob("R*.md"))
    for f in files:
        build_rule(f.stem, f.relative_to(RULES_REPO).as_posix(), data)
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
    n = sum(len(r["versions"]) for r in data.values())
    print(f"ルール {len(data)} 件・版 {n} 件の改正履歴を生成しました")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
