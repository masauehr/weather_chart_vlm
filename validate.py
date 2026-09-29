"""ID-32 検証: VLM 出力を「数値引用」「ラベル許可リスト」「実況突合」「予報突合」でチェックする。

使い方:
  python validate.py vlm_output_20260925.json data/20260925030000
  python validate.py --selftest data/20260925030000   # わざと壊した出力を検出できるか確認
出典: 気象庁ホームページ
"""
import json
import re
import sys
from pathlib import Path

# 気圧配置ラベルの許可リスト（自由記述を禁止する）
ALLOWED = {
    "冬型", "南岸低気圧", "梅雨前線", "秋雨前線", "太平洋高気圧", "台風",
    "移動性高気圧", "日本海低気圧", "その他",
}
# ラベルごとに出現しうる月（許可リストを通っても季節外れの誤答を弾く）
SEASON = {
    "冬型": {11, 12, 1, 2, 3},
    "南岸低気圧": {1, 2, 3, 4, 11, 12},
    "梅雨前線": {5, 6, 7},
    "秋雨前線": {9, 10, 11},
    "太平洋高気圧": {6, 7, 8, 9},
}
# 予報テキストと突き合わせるキーワード（VLM 出力側の表現 → 予報側の表現）
CONCEPTS = ["高気圧", "低気圧", "前線", "台風", "気圧の谷"]


def numbers_in(text):
    """本文中の「数値＋単位」を取り出す（気温・風速・雨量・確率）。"""
    return re.findall(r"(\d+(?:\.\d+)?)\s*(℃|度|m/s|メートル|mm|ミリ|%|％)", text)


def check(out, root):
    """検証結果を [(判定, 項目, 詳細)] で返す。判定は OK / NG / 注意。"""
    res = []
    amedas = json.loads((root / "amedas.json").read_text())
    overview = json.loads((root / "overview.json").read_text())
    official = "".join(v.get("text", "") for v in overview.values() if isinstance(v, dict))

    # 1) ラベル許可リスト
    base = out["pattern"].split("（")[0]
    res.append(("OK" if base in ALLOWED else "NG", "ラベル許可リスト", f"pattern={out['pattern']}"))

    # 1b) 季節整合（許可リスト内でも季節外れのラベルは NG）
    month = int(out["chart_time_jst"][5:7])
    ok = base not in SEASON or month in SEASON[base]
    res.append(("OK" if ok else "NG", "季節整合", f"{base} は {month} 月に{'出現しうる' if ok else '通常出ない → 誤読の疑い'}"))

    # 2) 確信度
    c = out["confidence"]
    res.append(("OK" if c >= 0.6 else "注意", "確信度", f"{c}（<0.6 は『判断が難しい』を前面に出す）"))

    # 3) 数値引用: 解説文の数値は実況JSONか予報テキストに存在しなければならない
    known = {str(v) for st in amedas.values() for v in st.values()}
    for key in ("today", "tomorrow"):
        for num, unit in numbers_in(out[key]):
            ok = num in known or num in official or float(num).is_integer() and str(int(float(num))) in known
            res.append(("OK" if ok else "NG", f"数値引用[{key}]", f"{num}{unit} は出典に{'あり' if ok else 'なし → VLM推定の疑い'}"))

    # 4) 実況突合（claims を機械的に検証）
    cl = out.get("claims", {})
    prec = max(st.get("precipitation1h", 0) for st in amedas.values())
    wind = max(st.get("wind", 0) for st in amedas.values())
    if "precip_now" in cl:
        ok = (cl["precip_now"] == "none") == (prec == 0)
        res.append(("OK" if ok else "NG", "実況突合:降水", f"claim={cl['precip_now']} / 主要8地点の1時間降水量最大={prec}mm"))
    if "wind_now" in cl:
        # 「弱い」は最大風速 8m/s 未満とみなす（簡易基準）
        ok = (cl["wind_now"] == "weak") == (wind < 8)
        res.append(("OK" if ok else "NG", "実況突合:風", f"claim={cl['wind_now']} / 主要8地点の風速最大={wind}m/s"))

    # 5) 予報突合: VLM 出力中の概念が公式概況にも現れるか
    vlm_text = out["today"] + out["tomorrow"] + "".join(out["evidence"])
    for w in CONCEPTS:
        v, o = w in vlm_text, w in official
        if v or o:
            verdict = "OK" if v == o else "注意"
            res.append((verdict, f"予報突合:{w}", f"VLM={'言及' if v else '言及なし'} / 公式概況={'言及' if o else '言及なし'}"))
    return res


def report(res):
    for v, k, d in res:
        print(f"[{v:2}] {k}: {d}")
    ng = sum(1 for v, *_ in res if v == "NG")
    print(f"--> NG {ng} 件 / 注意 {sum(1 for v, *_ in res if v == '注意')} 件 / 全 {len(res)} 件")
    return ng


def selftest(root):
    """わざと壊した出力を用意し、ガードレールが NG を出すか確認する。"""
    good = json.loads(Path(__file__).with_name("vlm_output_20260925.json").read_text())
    bad_cases = {
        "誤ラベル(冬型)": {"pattern": "冬型（西高東低）"},
        "数値の捏造(気温35℃)": {"today": "東京は気温35℃まで上がり猛暑になる。"},
        "実況と矛盾(雨/強風)": {"claims": {"precip_now": "heavy", "wind_now": "strong"}},
    }
    detected = 0
    for name, patch in bad_cases.items():
        res = check({**good, **patch}, root)
        hit = any(v == "NG" for v, *_ in res)
        detected += hit
        print(f"{'検出' if hit else '見逃し'}: {name}")
    print(f"--> {detected}/{len(bad_cases)} 検出")
    return 0 if detected == len(bad_cases) else 1


if __name__ == "__main__":
    if sys.argv[1] == "--selftest":
        sys.exit(selftest(Path(sys.argv[2])))
    sys.exit(1 if report(check(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]))) else 0)
