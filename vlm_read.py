"""ID-32 検証: fetch_chart.py が取得した画像・実況を実際に Claude API (VLM) に読ませる。

vlm_output_20260925.json は「Claude Code セッション自身が目視して作成」した代役出力だったが、
本スクリプトは外部 API を実際に呼び、同じスキーマの構造化出力を得る。
出力は validate.py にそのまま渡せる形式。

使い方:
  cp .env.example .env  # ANTHROPIC_API_KEY を記入済みであること
  python vlm_read.py data/20260925030000
  → data/20260925030000/vlm_output_api.json に保存

出典: 気象庁ホームページ（画像・実況・予報）
"""
import base64
import json
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import anthropic

# コスト重視のため Sonnet 5 を使用（画像読解＋構造化JSON出力には十分な精度、$2/$10 per MTok）
MODEL = "claude-sonnet-5"

SCHEMA_INSTRUCTION = """あなたは気象庁の地上天気図・衛星赤外画像を読む VLM（視覚言語モデル）役です。
渡された画像（地上天気図2枚：アジア太平洋・日本近海／ひまわり赤外1枚）と、
同時刻のアメダス実況・府県予報概況（JSONテキスト）をもとに、気圧配置を判定してください。

出力は次のスキーマに**厳密に一致する JSON のみ**を返すこと（説明文・コードフェンス・前置きは一切不要）。

{
  "chart_time_jst": "YYYY-MM-DD HH:MM",
  "pattern": "冬型 | 南岸低気圧 | 梅雨前線 | 秋雨前線 | 太平洋高気圧 | 台風 | 移動性高気圧 | 日本海低気圧 | その他 のいずれか一つ",
  "secondary_patterns": ["主パターン以外に見える要素があれば自由記述で列挙（無ければ空配列）"],
  "confidence": 0.0から1.0の数値,
  "evidence": ["天気図・衛星画像から読み取れる根拠を箇条書きで（等圧線の形・間隔、前線位置、雲の帯状構造など）"],
  "chart_values": {"対象（例: 高気圧(日本海)）": "示度(hPa) を天気図上の数字からそのまま書き写す"},
  "claims": {
    "precip_now": "none | some | heavy のいずれか（渡されたアメダス実況の降水量から判断。画像から推定しない）",
    "wind_now": "weak | moderate | strong のいずれか（アメダス実況の風速から判断）",
    "front_near_japan_now": true または false,
    "typhoon_affects_japan_now": true または false
  },
  "today": "実況・気圧配置から言える今日の天気の解説文",
  "tomorrow": "気圧配置の推移から言える明日の見通し文",
  "check_points": ["予報が変わりうる不確実要素"],
  "caveats": "画像の読み取りに基づく推定である旨の断り書き"
}

厳守事項:
- pattern は許可リストの語のみ使う（末尾に「（西高東低）」等の補足を括弧で付けるのは可）。
- chart_values 以外の数値（気温・風速・降水量）は、渡されたアメダス実況JSONか予報テキストに実在する値のみを today/tomorrow に書く。実況にない数値を書かない。
- confidence は画像から気圧配置を確信できる度合いを正直に見積もる。
"""


def b64(path: Path) -> str:
    return base64.standard_b64encode(path.read_bytes()).decode("utf-8")


def main():
    if len(sys.argv) != 2:
        print("使い方: python vlm_read.py data/<基準時刻>", file=sys.stderr)
        return 1
    root = Path(sys.argv[1])

    amedas = (root / "amedas.json").read_text()
    overview = (root / "overview.json").read_text()
    meta = json.loads((root / "meta.json").read_text())

    images = [
        ("地上天気図（アジア太平洋）", root / "surface_asia.png"),
        ("地上天気図（日本近海・着色）", root / "surface_near.png"),
        ("ひまわり赤外(B13)", root / "ir_japan.png"),
    ]

    content = []
    for label, path in images:
        content.append({"type": "text", "text": f"[{label}]"})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64(path)},
        })
    content.append({
        "type": "text",
        "text": (
            f"アメダス実況（主要8地点）:\n{amedas}\n\n"
            f"府県予報概況:\n{overview}\n\n"
            f"取得メタ情報（対象時刻など）:\n{json.dumps(meta, ensure_ascii=False)}\n\n"
            "上記画像と実況・予報から、指定スキーマの JSON を出力してください。"
        ),
    })

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL,
        max_tokens=4096,
        system=SCHEMA_INSTRUCTION,
        output_config={"effort": "low"},  # 単純な分類+JSON出力なので低coverageで十分・コスト抑制
        messages=[{"role": "user", "content": content}],
    )

    if response.stop_reason == "max_tokens":
        print("警告: max_tokens で打ち切られました。出力が不完全な可能性があります。", file=sys.stderr)

    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        print("エラー: text ブロックがありません。stop_reason=", response.stop_reason, file=sys.stderr)
        return 1
    # コードフェンス付きで返ってきた場合に備えて剥がす
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    out = json.loads(text)

    out_path = root / "vlm_output_api.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2))

    print(f"input_tokens={response.usage.input_tokens} output_tokens={response.usage.output_tokens}")
    cost = response.usage.input_tokens * 2 / 1e6 + response.usage.output_tokens * 10 / 1e6
    print(f"概算コスト: ${cost:.4f}")
    print(f"→ {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
