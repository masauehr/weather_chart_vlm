"""ID-32 検証: 複数事例でVLMの気圧配置分類の正答率を測る。

fetch_hibiten.py で切り出した「天気図のみ」の画像を Claude API に渡し、pattern を判定させる。
正解ラベルは気象庁「日々の天気図」の見出し（人間の予報官が書いた要約）から、
validate.py の許可リストに機械的に対応づけたもの（下記 EVENTS）。

過去日のためアメダス実況・府県予報は使えない（bosaiは直近のみ）。画像だけで判定する点が
vlm_read.py（当日・実況/予報つき）との違い。

使い方: python eval_historical.py
出典: 気象庁ホームページ「日々の天気図」
"""
import base64
import json
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import anthropic

MODEL = "claude-sonnet-5"
DATA = Path(__file__).parent / "data" / "hibiten"

# (年月日, 正解ラベル, 気象庁見出し原文) — 見出しに明示されたラベルのみ採用（曖昧なものは除外）
EVENTS = [
    (2025, 12, 4, "冬型", "冬型の気圧配置"),
    (2025, 12, 31, "冬型", "冬型の気圧配置"),
    (2026, 1, 8, "冬型", "冬型の気圧配置が強まる"),
    (2026, 1, 20, "冬型", "冬型の気圧配置が強まる"),
    (2025, 12, 14, "南岸低気圧", "南岸低気圧が発達"),
    (2025, 6, 22, "梅雨前線", "梅雨前線停滞"),
    (2025, 6, 24, "梅雨前線", "梅雨前線停滞"),
    (2025, 6, 17, "太平洋高気圧", "太平洋高気圧強まる"),
    (2025, 7, 24, "台風", "台風第7号沖縄に接近"),
    (2025, 9, 5, "台風", "台風第15号愛媛県に上陸"),
    (2025, 10, 13, "台風", "台風第23号伊豆諸島接近"),
    (2025, 12, 10, "移動性高気圧", "移動性高気圧に覆われる"),
    (2025, 12, 29, "移動性高気圧", "日本の南に移動性高気圧"),
    (2025, 6, 14, "日本海低気圧", "低気圧が日本海を東進"),
    (2026, 1, 15, "日本海低気圧", "低気圧が日本海を東進"),
]

SYSTEM = """あなたは気象庁の地上天気図（日本近海）を読むVLM役です。
渡された1枚の天気図画像から、気圧配置パターンを判定してください。
アメダス実況や予報テキストは渡されません。画像だけから判断してください。

出力は次のスキーマに厳密に一致するJSONのみ（説明文・コードフェンス不要）:
{
  "pattern": "冬型 | 南岸低気圧 | 梅雨前線 | 秋雨前線 | 太平洋高気圧 | 台風 | 移動性高気圧 | 日本海低気圧 | その他 のいずれか一つ",
  "confidence": 0.0から1.0,
  "evidence": ["等圧線の形・間隔、前線位置、高低気圧の位置と示度など、画像から読み取れる根拠"],
  "chart_values": {"対象（例: 高気圧(大陸)）": "示度(hPa)"}
}
pattern は許可リストの語のみ（補足は括弧書き可）。画像に写っている月日から季節も考慮すること。
"""


def b64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode("utf-8")


def classify(client, image_path: Path) -> dict:
    response = client.messages.create(
        model=MODEL,
        max_tokens=1500,
        system=SYSTEM,
        output_config={"effort": "low"},
        messages=[{
            "role": "user",
            "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64(image_path)}},
                {"type": "text", "text": "この天気図の気圧配置パターンをJSONで判定してください。"},
            ],
        }],
    )
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        return {"pattern": None, "error": f"stop_reason={response.stop_reason}"}
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    out = json.loads(text)
    out["_usage"] = {"input_tokens": response.usage.input_tokens, "output_tokens": response.usage.output_tokens}
    return out


def main():
    client = anthropic.Anthropic()
    results = []
    total_cost = 0.0
    for year, month, day, truth, headline in EVENTS:
        img = DATA / f"{year}{month:02d}" / f"{day:02d}.png"
        if not img.exists():
            print(f"スキップ（画像なし）: {year}-{month:02d}-{day:02d}", file=sys.stderr)
            continue
        out = classify(client, img)
        pred = (out.get("pattern") or "").split("（")[0]
        ok = pred == truth
        usage = out.pop("_usage", {"input_tokens": 0, "output_tokens": 0})
        cost = usage["input_tokens"] * 2 / 1e6 + usage["output_tokens"] * 10 / 1e6
        total_cost += cost
        results.append({
            "date": f"{year}-{month:02d}-{day:02d}", "truth": truth, "headline": headline,
            "pred": out.get("pattern"), "confidence": out.get("confidence"), "correct": ok,
            "evidence": out.get("evidence"),
        })
        print(f"{'OK ' if ok else 'NG '} {year}-{month:02d}-{day:02d} 正解={truth:8s} 予測={out.get('pattern')} (conf={out.get('confidence')}) 見出し「{headline}」")

    n = len(results)
    n_ok = sum(r["correct"] for r in results)
    print(f"\n--> 正答 {n_ok}/{n}（{n_ok/n:.0%}）  概算コスト合計 ${total_cost:.3f}")

    out_path = Path(__file__).parent / "results_historical.json"
    out_path.write_text(json.dumps({"accuracy": n_ok / n, "n": n, "cost_usd": round(total_cost, 4), "results": results}, ensure_ascii=False, indent=2))
    print(f"→ {out_path}")


if __name__ == "__main__":
    main()
