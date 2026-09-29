"""ID-32 デモの毎日更新（GitHub Actions から実行）。

fetch_chart.py で今日の天気図・衛星・実況・予報を取得 → Claude API で気圧配置を判定
（一般向け・こども向けの両方の解説文を1回の呼び出しで取得）→ validate.py でガードレール
検証 → webui/history/<YYYY-MM-DD>/ に画像＋data.json を保存し、10日より古い履歴を削除する。

使い方: python daily_update.py
必要な環境変数: ANTHROPIC_API_KEY（GitHub Actions の場合はリポジトリ Secrets から）
出典: 気象庁ホームページ（天気図・衛星・アメダス・予報概況）
"""
import json
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

import anthropic

import fetch_chart
import validate
from vlm_read import b64

MODEL = "claude-sonnet-5"
HERE = Path(__file__).parent
HISTORY_DIR = HERE / "webui" / "history"
KEEP_DAYS = 10

SYSTEM = """あなたは気象庁の地上天気図・衛星赤外画像を読む VLM（視覚言語モデル）役です。
渡された画像（地上天気図2枚：アジア太平洋・日本近海／ひまわり赤外1枚、海岸線つき）と、
同時刻のアメダス実況・府県予報概況（JSONテキスト）をもとに、気圧配置を判定してください。

出力は次のスキーマに**厳密に一致する JSON のみ**を返すこと（説明文・コードフェンス・前置きは一切不要）。
"today"/"tomorrow"/"evidence"/"check_points" は一般向け、"kids" 以下はそれを小学生にも分かる言葉で言い換えたもの。

{
  "chart_time_jst": "YYYY-MM-DD HH:MM",
  "pattern": "冬型 | 南岸低気圧 | 梅雨前線 | 秋雨前線 | 太平洋高気圧 | 台風 | 移動性高気圧 | 日本海低気圧 | その他 のいずれか一つ",
  "secondary_patterns": ["主パターン以外に見える要素があれば自由記述で列挙（無ければ空配列）"],
  "confidence": 0.0から1.0の数値,
  "evidence": ["天気図・衛星画像から読み取れる根拠を箇条書きで"],
  "chart_values": {"対象（例: 高気圧(日本海)）": "示度(hPa)"},
  "claims": {
    "precip_now": "none | some | heavy のいずれか（アメダス実況の降水量から判断。画像から推定しない）",
    "wind_now": "weak | moderate | strong のいずれか（アメダス実況の風速から判断）",
    "front_near_japan_now": true または false,
    "typhoon_affects_japan_now": true または false
  },
  "today": "実況・気圧配置から言える今日の天気の解説文（一般向け）",
  "tomorrow": "気圧配置の推移から言える明日の見通し文（一般向け）",
  "check_points": ["予報が変わりうる不確実要素（一般向け）"],
  "caveats": "画像の読み取りに基づく推定である旨の断り書き",
  "kids": {
    "evidence": ["evidence を小学生にも分かる言葉で3点程度に言い換え"],
    "today": "today を小学生にも分かる言葉で言い換え",
    "tomorrow": "tomorrow を小学生にも分かる言葉で言い換え",
    "check_points": ["check_points を小学生にも分かる言葉で言い換え"]
  }
}

厳守事項:
- pattern は許可リストの語のみ使う。
- chart_values 以外の数値は、渡されたアメダス実況JSONか予報テキストに実在する値のみを today/tomorrow に書く。
- confidence は正直に見積もる。kids の内容は事実を変えず言葉だけ易しくする。
"""


def classify(root: Path) -> dict:
    amedas = (root / "amedas.json").read_text()
    overview = (root / "overview.json").read_text()
    meta = json.loads((root / "meta.json").read_text())

    images = [
        ("地上天気図（アジア太平洋）", root / "surface_asia.png"),
        ("地上天気図（日本近海・着色）", root / "surface_near.png"),
        ("ひまわり赤外(B13)・海岸線つき", root / "ir_japan.png"),
    ]
    content = []
    for label, path in images:
        content.append({"type": "text", "text": f"[{label}]"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64(path)}})
    content.append({
        "type": "text",
        "text": (
            f"アメダス実況（主要8地点）:\n{amedas}\n\n"
            f"府県予報概況:\n{overview}\n\n"
            f"取得メタ情報:\n{json.dumps(meta, ensure_ascii=False)}\n\n"
            "上記画像と実況・予報から、指定スキーマの JSON を出力してください。"
        ),
    })

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL, max_tokens=4096, system=SYSTEM,
        output_config={"effort": "low"},
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "max_tokens":
        print("警告: max_tokens で打ち切られました", file=sys.stderr)
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(f"text ブロックが無い（stop_reason={response.stop_reason}）")
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    out = json.loads(text)
    print(f"input_tokens={response.usage.input_tokens} output_tokens={response.usage.output_tokens}", file=sys.stderr)
    return out


def guardrail_checks(out: dict, root: Path) -> list[dict]:
    """validate.py の check() を実行し、webui 用の {verdict, label, detail} 形式に変換する。"""
    results = validate.check(out, root)
    return [{"verdict": v, "label": k, "detail": d} for v, k, d in results]


def build_entry(date_str: str, out: dict, checks: list[dict], root: Path, dest: Path):
    """webui/history/<date>/ に画像＋data.json を書き出す。"""
    dest.mkdir(parents=True, exist_ok=True)
    img_dir = dest / "images"
    img_dir.mkdir(exist_ok=True)
    shutil.copy(root / "surface_near.png", img_dir / "near.png")
    shutil.copy(root / "surface_asia.png", img_dir / "asia.png")
    shutil.copy(root / "ir_japan.png", img_dir / "ir.png")

    data = {
        "date": date_str,
        "chart_time_jst": out["chart_time_jst"],
        "pattern": out["pattern"],
        "secondary_patterns": out.get("secondary_patterns", []),
        "confidence": out["confidence"],
        "images": [
            {"id": "near", "label": "地上天気図（日本近海）", "src": f"history/{date_str}/images/near.png"},
            {"id": "asia", "label": "地上天気図（アジア太平洋）", "src": f"history/{date_str}/images/asia.png"},
            {"id": "ir", "label": "ひまわり赤外(B13)", "src": f"history/{date_str}/images/ir.png"},
        ],
        "general": {
            "evidence": out["evidence"],
            "today": out["today"],
            "tomorrow": out["tomorrow"],
            "check_points": out["check_points"],
        },
        "kids": out.get("kids", {}),
        "caveats": out.get("caveats", ""),
        "guardrail_checks": checks,
        "source": "出典: 気象庁ホームページ（天気図・衛星・アメダス・府県予報概況）",
    }
    (dest / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))


def prune_and_index():
    """KEEP_DAYS より古い履歴を削除し、history/index.json を新しい順で書き直す。"""
    dates = sorted((p.name for p in HISTORY_DIR.iterdir() if p.is_dir()), reverse=True)
    for old in dates[KEEP_DAYS:]:
        shutil.rmtree(HISTORY_DIR / old)
    dates = dates[:KEEP_DAYS]
    (HISTORY_DIR / "index.json").write_text(json.dumps({"dates": dates}, ensure_ascii=False, indent=2))


def main():
    jst_today = datetime.now(timezone.utc) + timedelta(hours=9)
    date_str = jst_today.strftime("%Y-%m-%d")

    root = fetch_chart.fetch_all()
    out = classify(root)
    checks = guardrail_checks(out, root)
    ng = sum(1 for c in checks if c["verdict"] == "NG")
    print(f"pattern={out['pattern']} confidence={out['confidence']} NG={ng}/{len(checks)}")

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    build_entry(date_str, out, checks, root, HISTORY_DIR / date_str)
    prune_and_index()
    print(f"→ {HISTORY_DIR / date_str}")


if __name__ == "__main__":
    sys.exit(main())
