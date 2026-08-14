"""Validate Codex judgments and render Markdown plus interactive HTML reports."""

from __future__ import annotations

import argparse
import html
import json
import re
from pathlib import Path


MODE = "DeepEval Faithfulness 规则复现（Codex 评审，非 DeepEval 官方运行）"
VALID_VERDICTS = {"supported", "unsupported"}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def normalized(text: str) -> str:
    # Compare visible source text while ignoring Markdown/HTML presentation marks.
    text = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("`", "").replace("**", "").replace("__", "")
    text = re.sub(r"(?m)^\s*(?:[-*+]\s+|\d+[.)]\s+|>\s*)", "", text)
    return " ".join(text.split())


def read_source_lines(path: Path) -> list[str]:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return path.read_text(encoding=encoding).splitlines()
        except UnicodeDecodeError:
            continue
    raise ValueError(f"Cannot decode evidence source: {path}")


def md_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\r", " ").replace("\n", "<br>")


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value).strip("-")
    return cleaned or "article"


def validate_case(prepared: dict, judgments: dict, judgment_path: Path) -> list[dict]:
    article_id = prepared["article_id"]
    if judgments.get("article_id") != article_id:
        raise ValueError(f"{judgment_path.name}: article_id does not match prepared JSON")
    if judgments.get("evaluation_mode") != MODE:
        raise ValueError(f"{judgment_path.name}: evaluation_mode must be the required non-official label")

    units = {unit["unit_id"]: unit for unit in prepared["article_units"]}
    source_paths = {str(Path(path).resolve()).lower() for path in prepared["knowledge_files"]}
    seen: set[str] = set()
    claims = judgments.get("claims")
    if not isinstance(claims, list):
        raise ValueError(f"{judgment_path.name}: claims must be an array")

    for index, claim in enumerate(claims, 1):
        claim_id = claim.get("claim_id")
        if not claim_id or claim_id in seen:
            raise ValueError(f"{judgment_path.name}: missing or duplicate claim_id at row {index}")
        seen.add(claim_id)
        unit = units.get(claim.get("unit_id"))
        if not unit:
            raise ValueError(f"{judgment_path.name}: {claim_id} has an invalid unit_id")
        if claim.get("article_line") != unit["line"]:
            raise ValueError(f"{judgment_path.name}: {claim_id} article_line does not match its unit")
        quote = claim.get("article_quote", "")
        if not quote or normalized(quote) not in normalized(unit["text"]):
            raise ValueError(f"{judgment_path.name}: {claim_id} article_quote is not verbatim in its unit")
        verdict = claim.get("verdict")
        if verdict not in VALID_VERDICTS:
            raise ValueError(f"{judgment_path.name}: {claim_id} has invalid verdict {verdict!r}")
        evidence = claim.get("evidence", [])
        if not isinstance(evidence, list):
            raise ValueError(f"{judgment_path.name}: {claim_id} evidence must be an array")
        if verdict == "supported" and not evidence:
            raise ValueError(f"{judgment_path.name}: supported {claim_id} must include evidence")
        for item in evidence:
            source_file = item.get("source_file", "")
            source_path = Path(source_file).resolve() if source_file else None
            if not source_path or str(source_path).lower() not in source_paths:
                raise ValueError(f"{judgment_path.name}: {claim_id} cites a file outside supplied knowledge context")
            if not item.get("quote"):
                raise ValueError(f"{judgment_path.name}: {claim_id} contains empty evidence quote")
            if not isinstance(item.get("line_start"), int) or not isinstance(item.get("line_end"), int):
                raise ValueError(f"{judgment_path.name}: {claim_id} evidence line numbers must be integers")
            line_start, line_end = item["line_start"], item["line_end"]
            source_lines = read_source_lines(source_path)
            if line_start < 1 or line_end < line_start or line_end > len(source_lines):
                raise ValueError(f"{judgment_path.name}: {claim_id} evidence line range is invalid")
            source_excerpt = "\n".join(source_lines[line_start - 1 : line_end])
            if normalized(item["quote"]) not in normalized(source_excerpt):
                raise ValueError(f"{judgment_path.name}: {claim_id} evidence quote is not verbatim at the stated lines")
    return claims


def evidence_markdown(claim: dict) -> str:
    if not claim.get("evidence"):
        return "—"
    parts = []
    for evidence in claim["evidence"]:
        location = f"{evidence['source_file']}:{evidence['line_start']}"
        if evidence["line_end"] != evidence["line_start"]:
            location += f"–{evidence['line_end']}"
        parts.append(f"“{evidence['quote']}”<br>{location}")
    return "<br><br>".join(parts)


def write_detail(case: dict, output_dir: Path) -> Path:
    prepared, claims = case["prepared"], case["claims"]
    supported = sum(claim["verdict"] == "supported" for claim in claims)
    total = len(claims)
    rate = supported / total * 100 if total else None
    lines = [
        f"# {prepared['article_id']} 文章知识库支持明细",
        "",
        f"> 评审模式：{MODE}",
        "",
        f"> 计算：{supported} ÷ {total} = {rate:.2f}%" if rate is not None else "> 计算：分母为 0，结果 N/A",
        "",
        f"- 文章：`{prepared['article_file']}`",
        *[f"- 知识文件：`{path}`" for path in prepared["knowledge_files"]],
        "",
        "| 主张 | 文章行 | 文章原文 | 原子事实主张 | 判断 | 知识库原文与位置 | 理由 |",
        "|---|---:|---|---|---|---|---|",
    ]
    for claim in claims:
        verdict = "支持" if claim["verdict"] == "supported" else "不支持"
        lines.append(
            "| "
            + " | ".join(
                md_cell(value)
                for value in (
                    claim["claim_id"],
                    claim["article_line"],
                    claim["article_quote"],
                    claim["claim"],
                    verdict,
                    evidence_markdown(claim),
                    claim.get("reason", ""),
                )
            )
            + " |"
        )
    output = output_dir / f"{safe_name(prepared['article_id'])}_faithfulness_details.md"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def summary_row(case: dict) -> dict:
    claims = case["claims"]
    supported = sum(claim["verdict"] == "supported" for claim in claims)
    total = len(claims)
    return {
        "article_id": case["prepared"]["article_id"],
        "title": case["prepared"]["article_title"],
        "supported": supported,
        "total": total,
        "unsupported": total - supported,
        "rate": supported / total * 100 if total else None,
        "mode": MODE,
    }


def write_summary(cases: list[dict], output_dir: Path) -> Path:
    lines = [
        "# 文章知识库支持率汇总",
        "",
        f"> 评审模式：{MODE}",
        "",
        "> 公式：知识库支持的事实主张数 ÷ 文章全部事实主张数。标题和各级小标题不计入。",
        "",
        "| 文章编号 | 文章标题 | 支持主张数（分子） | 全部事实主张数（分母） | 不支持主张数 | Faithfulness | 评审模式 |",
        "|---|---|---:|---:|---:|---:|---|",
    ]
    for case in cases:
        row = summary_row(case)
        rate = f"{row['rate']:.2f}%" if row["rate"] is not None else "N/A"
        lines.append(
            "| "
            + " | ".join(
                md_cell(value)
                for value in (
                    row["article_id"], row["title"], row["supported"], row["total"], row["unsupported"], rate, row["mode"]
                )
            )
            + " |"
        )
    output = output_dir / "faithfulness_summary.md"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def claim_card(claim: dict) -> str:
    supported = claim["verdict"] == "supported"
    label = "支持" if supported else "不支持"
    evidence = ""
    if claim.get("evidence"):
        blocks = []
        for item in claim["evidence"]:
            name = html.escape(Path(item["source_file"]).name)
            line = f"L{item['line_start']}" if item["line_start"] == item["line_end"] else f"L{item['line_start']}–L{item['line_end']}"
            blocks.append(
                f'<blockquote>{html.escape(item["quote"])}</blockquote>'
                f'<div class="source">{name} · {line}</div>'
            )
        evidence = "".join(blocks)
    else:
        evidence = '<div class="empty">提供的知识文件中未找到支持证据</div>'
    return f'''
    <details class="claim {claim['verdict']}" data-verdict="{claim['verdict']}" open>
      <summary><span class="badge">{label}</span><span class="claim-id">{html.escape(claim['claim_id'])}</span>{html.escape(claim['article_quote'])}</summary>
      <div class="claim-body">
        <div><strong>拆分后的事实主张</strong><p>{html.escape(claim['claim'])}</p></div>
        <div><strong>判断理由</strong><p>{html.escape(claim.get('reason', ''))}</p></div>
        <div><strong>对应知识库原文</strong>{evidence}</div>
      </div>
    </details>'''


def article_section(case: dict, index: int) -> str:
    prepared, claims = case["prepared"], case["claims"]
    by_line: dict[int, list[dict]] = {}
    for claim in claims:
        by_line.setdefault(claim["article_line"], []).append(claim)
    row = summary_row(case)
    rate = f"{row['rate']:.2f}%" if row["rate"] is not None else "N/A"
    body = []
    for item in prepared["article_lines"]:
        line_claims = by_line.get(item["line"], [])
        verdict_class = ""
        if line_claims:
            verdict_class = "has-unsupported" if any(c["verdict"] == "unsupported" for c in line_claims) else "has-supported"
        cards = "".join(claim_card(claim) for claim in line_claims)
        body.append(
            f'<section class="article-line {verdict_class}"><div class="line-no">L{item["line"]}</div>'
            f'<p class="article-text">{html.escape(item["text"])}</p>{cards}</section>'
        )
    active = " active" if index == 0 else ""
    return f'''
    <article class="article-panel{active}" id="panel-{index}">
      <header class="article-head">
        <div><div class="eyebrow">{html.escape(prepared['article_id'])}</div><h2>{html.escape(prepared['article_title'])}</h2></div>
        <div class="score"><strong>{rate}</strong><span>{row['supported']} / {row['total']} 个事实主张得到支持</span></div>
      </header>
      <div class="article-body">{"".join(body)}</div>
    </article>'''


def write_html(cases: list[dict], output_dir: Path) -> Path:
    tabs = "".join(
        f'<button class="tab{" active" if index == 0 else ""}" data-target="panel-{index}">{html.escape(case["prepared"]["article_id"])}</button>'
        for index, case in enumerate(cases)
    )
    panels = "".join(article_section(case, index) for index, case in enumerate(cases))
    rows = "".join(
        f'<tr><td>{html.escape(row["article_id"])}</td><td>{html.escape(row["title"])}</td><td>{row["supported"]}</td><td>{row["total"]}</td><td>{row["unsupported"]}</td><td><strong>{row["rate"]:.2f}%</strong></td></tr>'
        if row["rate"] is not None
        else f'<tr><td>{html.escape(row["article_id"])}</td><td>{html.escape(row["title"])}</td><td>0</td><td>0</td><td>0</td><td><strong>N/A</strong></td></tr>'
        for row in (summary_row(case) for case in cases)
    )
    document = f'''<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src data:;">
<title>文章知识库支持率可视化</title>
<style>
:root{{--bg:#f4f7fb;--card:#fff;--ink:#172033;--muted:#667085;--line:#dce3ec;--green:#117a4b;--green-bg:#e9f8f0;--red:#b42318;--red-bg:#fff0ee;--blue:#2856a3}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 Inter,"Segoe UI","Microsoft YaHei",sans-serif}}
.shell{{max-width:1180px;margin:auto;padding:32px 20px 64px}}h1{{margin:0 0 8px;font-size:30px}}.subtitle{{color:var(--muted);margin:0 0 24px}}
.notice{{background:#fff7df;border:1px solid #f0d78a;border-radius:12px;padding:13px 16px;margin-bottom:18px}}
.summary{{width:100%;border-collapse:collapse;background:var(--card);border-radius:14px;overflow:hidden;box-shadow:0 8px 24px #253b5b12}}
.summary th,.summary td{{padding:12px 14px;border-bottom:1px solid var(--line);text-align:left}}.summary th{{background:#eef3fa;color:#42526b;font-size:13px}}
.toolbar{{display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:24px 0 14px}}button{{border:1px solid var(--line);background:white;border-radius:999px;padding:8px 15px;cursor:pointer;color:var(--ink)}}button.active{{background:var(--blue);border-color:var(--blue);color:white}}
.filters{{margin-left:auto;display:flex;gap:8px}}.article-panel{{display:none}}.article-panel.active{{display:block}}
.article-head{{display:flex;justify-content:space-between;gap:22px;align-items:flex-start;background:var(--card);padding:22px;border-radius:16px 16px 0 0;border-bottom:1px solid var(--line)}}
.article-head h2{{margin:2px 0 0;font-size:24px}}.eyebrow{{color:var(--blue);font-weight:700;letter-spacing:.04em}}.score{{min-width:210px;text-align:right}}.score strong{{display:block;font-size:34px;color:var(--blue)}}.score span{{color:var(--muted)}}
.article-body{{background:var(--card);padding:8px 22px 28px;border-radius:0 0 16px 16px;box-shadow:0 8px 24px #253b5b12}}
.article-line{{position:relative;padding:18px 18px 16px 58px;border-bottom:1px solid #edf0f4}}.line-no{{position:absolute;left:8px;top:22px;color:#98a2b3;font:12px ui-monospace,monospace}}.article-text{{margin:0 0 12px;font-family:Georgia,"Times New Roman",serif;font-size:17px}}
.has-supported .article-text{{border-left:4px solid var(--green);padding-left:12px}}.has-unsupported .article-text{{border-left:4px solid var(--red);padding-left:12px}}
.claim{{margin:9px 0;border:1px solid var(--line);border-radius:10px;overflow:hidden}}.claim.supported{{border-color:#9bd5b6}}.claim.unsupported{{border-color:#efaaa3}}
.claim summary{{cursor:pointer;padding:10px 12px;background:#f9fafb;display:flex;gap:9px;align-items:flex-start}}.claim.supported summary{{background:var(--green-bg)}}.claim.unsupported summary{{background:var(--red-bg)}}
.badge{{border-radius:999px;padding:1px 8px;font-size:12px;font-weight:700;white-space:nowrap}}.supported .badge{{background:var(--green);color:white}}.unsupported .badge{{background:var(--red);color:white}}.claim-id{{font:12px ui-monospace,monospace;color:var(--muted);padding-top:2px}}
.claim-body{{padding:13px 15px;display:grid;grid-template-columns:1fr 1fr;gap:14px}}.claim-body>div:last-child{{grid-column:1/-1}}.claim-body p{{margin:3px 0}}blockquote{{margin:6px 0;padding:10px 13px;border-left:3px solid var(--blue);background:#f6f8fc}}.source{{color:var(--muted);font-size:12px;overflow-wrap:anywhere}}.empty{{color:var(--red);padding:8px 0}}
body[data-filter="supported"] .claim.unsupported,body[data-filter="unsupported"] .claim.supported{{display:none}}
@media(max-width:760px){{.article-head{{display:block}}.score{{text-align:left;margin-top:12px}}.filters{{margin-left:0}}.claim-body{{grid-template-columns:1fr}}.claim-body>div:last-child{{grid-column:auto}}.summary-wrap{{overflow:auto}}}}
</style></head><body data-filter="all"><main class="shell">
<h1>文章知识库支持率可视化</h1><p class="subtitle">文章原文、事实主张和知识库证据始终在同一位置对应显示。</p>
<div class="notice"><strong>{MODE}</strong><br>公式：知识库支持的事实主张数 ÷ 文章全部事实主张数。标题和各级小标题不计入。</div>
<div class="summary-wrap"><table class="summary"><thead><tr><th>文章</th><th>标题</th><th>支持（分子）</th><th>全部主张（分母）</th><th>不支持</th><th>Faithfulness</th></tr></thead><tbody>{rows}</tbody></table></div>
<div class="toolbar"><div>{tabs}</div><div class="filters"><button class="filter active" data-filter="all">全部</button><button class="filter" data-filter="supported">只看支持</button><button class="filter" data-filter="unsupported">只看不支持</button></div></div>
{panels}</main><script>
document.querySelectorAll('.tab').forEach(b=>b.addEventListener('click',()=>{{document.querySelectorAll('.tab,.article-panel').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.getElementById(b.dataset.target).classList.add('active')}}));
document.querySelectorAll('.filter').forEach(b=>b.addEventListener('click',()=>{{document.querySelectorAll('.filter').forEach(x=>x.classList.remove('active'));b.classList.add('active');document.body.dataset.filter=b.dataset.filter}}));
</script></body></html>'''
    output = output_dir / "faithfulness_highlight.html"
    output.write_text(document, encoding="utf-8")
    return output


def discover_cases(input_dir: Path) -> list[dict]:
    cases = []
    for judgment_path in sorted(input_dir.glob("*-judgments.json")):
        stem = judgment_path.name[: -len("-judgments.json")]
        prepared_path = input_dir / f"{stem}-prepared.json"
        if not prepared_path.exists():
            raise ValueError(f"Missing prepared JSON for {judgment_path.name}")
        prepared = load_json(prepared_path)
        judgments = load_json(judgment_path)
        claims = validate_case(prepared, judgments, judgment_path)
        cases.append({"prepared": prepared, "judgments": judgments, "claims": claims})
    if not cases:
        raise ValueError(f"No *-judgments.json files found in {input_dir}")
    return cases


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    cases = discover_cases(args.input_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    outputs = [str(write_detail(case, args.output_dir).resolve()) for case in cases]
    outputs.append(str(write_summary(cases, args.output_dir).resolve()))
    outputs.append(str(write_html(cases, args.output_dir).resolve()))
    print(json.dumps({"articles": len(cases), "outputs": outputs}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
