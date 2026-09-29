"""ID-32 検証: 気象庁「日々の天気図」（月次PDF、2002年8月〜）から、指定日の地上天気図だけを切り出す。

過去の気圧配置パターン（冬型・南岸低気圧・梅雨前線・秋雨前線・太平洋高気圧・台風・移動性高気圧 等）で
VLMの正答率を測るための、複数事例・正解ラベル付きデータセットを作る土台。

レイアウト（標準版PDF、A4縦・1ページ4x4グリッド）:
  - 1ページ目: セル0(左上)が表紙(号数・月間まとめ)、セル1〜15が1〜15日
  - 2ページ目: セル0〜15が16日〜末日（存在する日数分）
  - 各セルは「天気図（上部）＋日付・見出し・解説文（下部）」で、
    見出し行の文字位置(top)を使って「天気図だけ」を機械的に切り出す（解説文＝正解ラベルを画像に含めないため）。

出典: 気象庁ホームページ「日々の天気図」https://www.data.jma.go.jp/fcd/yoho/hibiten/index.html
利用は個人の研究用途。商用・大量再配布はしない。

使い方:
  python fetch_hibiten.py 2025 12          # その月の全日を切り出し
  python fetch_hibiten.py 2025 12 26       # 26日だけ
"""
import re
import sys
from pathlib import Path

import pdfplumber
import requests
from pdf2image import convert_from_path

UA = {"User-Agent": "weather-hackathon-spike (personal research)"}
DATA = Path(__file__).parent / "data" / "hibiten"
DPI = 300
ROWS, COLS = 4, 4
PAGE_W_PT, PAGE_H_PT = 595.22, 842.0
ROW_H_PT = 198.35  # タイトル行の間隔（実測: 157.5, 355.8, 554.2, 752.5 の差）
COL_W_PT = 143.23  # タイトル列の間隔（実測: 27.8, 171.0, 314.3, 457.5 の差）
CHART_MARGIN_PT = 4  # タイトル文字の少し上でクロップ（解説文を画像に含めないため）
CHART_LEFT_MARGIN_PT = 20  # 列境界のずれで隣の天気図が少し映り込むのを防ぐ


def pdf_path(year: int, month: int) -> Path:
    DATA.mkdir(parents=True, exist_ok=True)
    p = DATA / f"{year}{month:02d}.pdf"
    if not p.exists():
        url = f"https://www.data.jma.go.jp/yoho/data/hibiten/{year}/{year % 100:02d}{month:02d}.pdf"
        r = requests.get(url, headers=UA, timeout=30)
        r.raise_for_status()
        p.write_bytes(r.content)
    return p


def day_cell(day: int):
    """day(1-indexed) -> (page_index, row, col)。ページ0のセル0は表紙。"""
    cell_index = day  # ページ0: セル0=表紙, セル1..15=1..15日 / ページ1: セル0..15=16..31日
    if cell_index <= 15:
        page, idx = 0, cell_index
    else:
        page, idx = 1, cell_index - 16
    return page, idx // COLS, idx % COLS


def extract_titles(pdf) -> dict:
    """pdfplumber から「N日(曜)見出し」を全部取り、日付→(ページ, 見出し全文, タイトルのtop座標, 全単語リスト) の辞書にする。"""
    out = {}
    for page_idx, page in enumerate(pdf.pages):
        words = page.extract_words()
        for w in words:
            m = re.match(r"^(\d{1,2})日", w["text"])
            if m:
                out[int(m.group(1))] = {"page": page_idx, "title": w["text"], "title_top": w["top"], "words": words}
    return out


def chart_top_pt(day: int, titles: dict) -> float:
    """このセルの天気図の上端(pt)。行0はページ上端、行1以降は「1つ上の行・同じ列」の
    見出し＋解説文の最下端(その行の実測)のすぐ下にする（固定間隔だと解説文の長さで前の行にはみ出すため）。
    """
    page_idx, row, col = day_cell(day)
    if row == 0:
        return 0.0
    col_x0 = col * COL_W_PT
    col_x1 = (col + 1) * COL_W_PT
    info = titles[day]
    prev_title_top = row * ROW_H_PT - (ROW_H_PT - 157.5)  # 同列・1つ上の行のタイトルtop（実測間隔から逆算）
    # 同ページ・同列で、直前行の「タイトル〜解説文」に属する単語（top >= 直前行タイトルtop）の最下端を取る
    bottoms = [
        w["bottom"] for w in info["words"]
        if col_x0 - 5 <= w["x0"] < col_x1 + 5 and prev_title_top - 5 <= w["top"] < info["title_top"] - 5
    ]
    if not bottoms:
        return row * ROW_H_PT  # フォールバック: 固定間隔
    return max(bottoms) + 6  # 解説文の下に少し余白


def crop_day(year: int, month: int, day: int, titles: dict, page_images: list) -> tuple[Path, str]:
    info = titles[day]
    page_idx, row, col = day_cell(day)
    scale = DPI / 72
    x0 = col * COL_W_PT * scale + CHART_LEFT_MARGIN_PT * scale
    x1 = min((col + 1) * COL_W_PT * scale, PAGE_W_PT * scale)
    y0 = chart_top_pt(day, titles) * scale
    y1 = (info["title_top"] - CHART_MARGIN_PT) * scale
    img = page_images[page_idx].crop((x0, y0, x1, y1))

    out_dir = DATA / f"{year}{month:02d}"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"{day:02d}.png"
    img.save(out_path)
    return out_path, info["title"]


def main():
    if len(sys.argv) not in (3, 4):
        print("使い方: python fetch_hibiten.py <year> <month> [day]", file=sys.stderr)
        return 1
    year, month = int(sys.argv[1]), int(sys.argv[2])
    days = [int(sys.argv[3])] if len(sys.argv) == 4 else None

    pdf_file = pdf_path(year, month)
    with pdfplumber.open(pdf_file) as pdf:
        titles = extract_titles(pdf)
    page_images = convert_from_path(pdf_file, dpi=DPI)

    target_days = days or sorted(titles.keys())
    labels = {}
    for d in target_days:
        if d not in titles:
            print(f"警告: {d}日 の見出しが見つかりません（スキップ）", file=sys.stderr)
            continue
        out_path, title = crop_day(year, month, d, titles, page_images)
        labels[d] = title
        print(f"{year}-{month:02d}-{d:02d}: {title} -> {out_path}")

    labels_path = DATA / f"{year}{month:02d}" / "titles.json"
    import json
    existing = json.loads(labels_path.read_text()) if labels_path.exists() else {}
    existing.update({str(k): v for k, v in labels.items()})
    labels_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
