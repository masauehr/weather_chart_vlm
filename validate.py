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
# 地域名 → 解説文中でその地域を指しうる語（雨域の言及漏れ検査用）
REGION_KEYWORDS = {
    "鹿児島(本土・大隅)": ["鹿児島", "九州南部", "南九州", "九州", "屋久島", "種子島", "大隅", "薩南", "南西諸島"],
    "九州南部": ["宮崎", "九州南部", "南九州", "九州", "鹿児島"],
    "九州北部": ["福岡", "長崎", "佐賀", "熊本", "大分", "九州北部", "九州"],
    "奄美・トカラ": ["奄美", "トカラ", "南西諸島", "鹿児島"],
    "沖縄": ["沖縄", "先島", "南西諸島"],
    "伊豆・小笠原": ["伊豆", "小笠原", "八丈", "伊豆諸島"],
    "北海道": ["北海道"], "東北": ["東北"], "関東": ["関東", "東京"], "甲信": ["甲信", "長野", "山梨"],
    "東海": ["東海", "静岡", "愛知"], "北陸": ["北陸", "新潟", "石川", "富山", "福井"],
    "近畿": ["近畿", "大阪", "関西"], "中国": ["中国", "山口", "広島"], "四国": ["四国"],
}
# 前線通過域（fronts[].reach_ft48 など）の地方名 → 解説文中でその地方を指しうる語
REACH_KEYWORDS = {
    "北海道": ["北海道"],
    "東北": ["東北", "秋田", "山形", "青森", "岩手", "宮城", "福島"],
    "北陸": ["北陸", "新潟", "石川", "富山", "福井"],
    "関東甲信": ["関東", "東京", "甲信", "長野", "山梨"],
    "東海": ["東海", "静岡", "愛知", "岐阜", "三重"],
    "近畿": ["近畿", "関西", "大阪", "京都", "兵庫"],
    "山陰": ["山陰", "鳥取", "島根"],
    "山陽": ["山陽", "中国地方", "岡山", "広島", "山口"],
    "四国": ["四国"],
    "九州北部": ["九州北部", "北九州", "福岡", "佐賀", "長崎", "九州"],
    "九州南部": ["九州南部", "南九州", "鹿児島", "宮崎", "九州"],
    "沖縄": ["沖縄", "南西諸島", "先島"],
}
# 「本州の日本海側」「西日本」など、まとめた表記が含む地方（まとめた表記で書かれていれば言及ありとみなす）
BROAD_PHRASES = {
    "全国": set(REACH_KEYWORDS),
    "北日本": {"北海道", "東北"},
    "東日本": {"関東甲信", "北陸", "東海", "東北"},
    "西日本": {"近畿", "山陰", "山陽", "四国", "九州北部", "九州南部"},
    "本州の日本海側": {"東北", "北陸", "山陰", "近畿"},
    "日本海側": {"北海道", "東北", "北陸", "山陰", "九州北部", "近畿"},
    "本州": {"東北", "関東甲信", "北陸", "東海", "近畿", "山陰", "山陽"},
}
# 前線通過域の地方名 → 府県予報概況を取っている地方区分（fetch_chart.OFFICE_GROUPS のキー）。無い地方は概況突合しない
REACH_TO_GROUP = {
    "北海道": "北海道", "東北": "東北", "北陸": "北陸", "関東甲信": "関東",
    "東海": "東海", "近畿": "近畿", "山陰": "山陰", "九州北部": "九州北部",
    "九州南部": "九州南部", "沖縄": "沖縄",
}
# 概況に天気の崩れが書かれているかを見る語
WEATHER_BREAK_WORDS = ["雨", "雪", "雷", "荒れ", "崩れ", "あられ", "ひょう", "強風"]
# 予報テキストと突き合わせるキーワード（VLM 出力側の表現 → 予報側の表現）
CONCEPTS = ["高気圧", "低気圧", "前線", "台風", "気圧の谷"]


def overview_by_group(overview):
    """overview.json から {地方区分: 概況本文} を作る。新形式 {地方区分: {府県: JSON}} と
    旧形式 {府県: JSON} の両方を読める（旧形式は府県名をそのまま区分名にする）。"""
    out = {}
    for name, v in overview.items():
        if not isinstance(v, dict):
            continue
        if "text" in v or "error" in v:
            out[name] = v.get("text", "")
        else:
            out[name] = "".join(x.get("text", "") for x in v.values() if isinstance(x, dict))
    return out


def mentioned(region, text):
    """解説文が地方 region に言及しているか。地名のほか「本州の日本海側」「西日本」などのまとめた表記も認める。"""
    if any(k in text for k in REACH_KEYWORDS[region]):
        return True
    return any(p in text and region in members for p, members in BROAD_PHRASES.items())


def numbers_in(text):
    """本文中の「数値＋単位」を取り出す（気温・風速・雨量・確率）。"""
    return re.findall(r"(\d+(?:\.\d+)?)\s*(℃|度|m/s|メートル|mm|ミリ|%|％)", text)


def check(out, root):
    """検証結果を [(判定, 項目, 詳細)] で返す。判定は OK / NG / 注意。"""
    res = []
    amedas_all = json.loads((root / "amedas.json").read_text())
    # 新形式は {"主要地点": {...}, "全国": ..., "地域別": ...}。旧形式（8地点のみ）も読めるようにする
    amedas = amedas_all.get("主要地点", amedas_all)
    regions = amedas_all.get("地域別", {})
    overview = json.loads((root / "overview.json").read_text())
    group_text = overview_by_group(overview)
    official = "".join(group_text.values())
    typhoon_path = root / "typhoon.json"
    typhoon = json.loads(typhoon_path.read_text()) if typhoon_path.exists() else None

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
    # 地域別集計・降水上位の数値（降水量・風速・地点数）も引用してよい出典に含める
    nums = re.findall(r"\d+(?:\.\d+)?", json.dumps(amedas_all, ensure_ascii=False))
    known |= set(nums)
    # 「約20mm」のような四捨五入・切り捨ての引用も許す（20.5 → 20 / 21）
    known |= {str(int(float(x))) for x in nums} | {str(round(float(x))) for x in nums}
    for key in ("today", "tomorrow"):
        for num, unit in numbers_in(out[key]):
            ok = num in known or num in official or float(num).is_integer() and str(int(float(num))) in known
            res.append(("OK" if ok else "NG", f"数値引用[{key}]", f"{num}{unit} は出典に{'あり' if ok else 'なし → VLM推定の疑い'}"))

    # 4) 実況突合（claims を機械的に検証）
    cl = out.get("claims", {})
    # 新形式は全国約1300地点の最大、旧形式は主要8地点の最大
    if regions:
        prec = max((r["最大1時間降水量"]["mm"] for r in regions.values() if r["最大1時間降水量"]), default=0)
        wind = max((r["最大風速"]["m/s"] for r in regions.values() if r["最大風速"]), default=0)
        scope = "全国アメダス"
    else:
        prec = max(st.get("precipitation1h", 0) for st in amedas.values())
        wind = max(st.get("wind", 0) for st in amedas.values())
        scope = "主要8地点"
    if "precip_now" in cl:
        # 判定基準はプロンプトと同じ: 0mm=none / 0mm超〜30mm未満=some / 30mm以上=heavy（2026-10-13 に基準を明文化）
        want = "none" if prec == 0 else ("heavy" if prec >= 30 else "some")
        ok = cl["precip_now"] == want
        res.append(("OK" if ok else "NG", "実況突合:降水", f"claim={cl['precip_now']} / {scope}の1時間降水量最大={prec}mm（基準では {want}）"))
    if "wind_now" in cl:
        # 風速はすべて平均風速（10分間平均）。最大瞬間風速では判定しない。
        # 区切りは気象庁「風の強さと吹き方」の表に合わせる: 10m/s未満=weak（予報用語なし）、10以上20未満=moderate
        # （やや強い風10〜15・強い風15〜20）、20以上=strong（非常に強い風20〜30・猛烈な風30〜）。
        # 全国最大は離島・山岳を含むため、weak になる日は少ない（風の判定は地域別で見るほうが意味が強い）
        want = "weak" if wind < 10 else ("strong" if wind >= 20 else "moderate")
        ok = cl["wind_now"] == want
        res.append(("OK" if ok else "NG", "実況突合:風", f"claim={cl['wind_now']} / {scope}の風速最大={wind}m/s（基準では {want}）"))

    # 4b) 雨域の言及漏れ: 降水あり地点が3地点以上の地域を解説文が一切挙げていなければ「注意」
    text_today = out["today"] + out["tomorrow"] + "".join(out.get("evidence", []))
    for name, r in regions.items():
        if r["降水あり地点数"] >= 3 and r["最大1時間降水量"]["mm"] >= 1:
            kws = REGION_KEYWORDS.get(name, [name])
            ok = any(k in text_today for k in kws)
            res.append(("OK" if ok else "注意", f"雨域の言及:{name}",
                        f"降水あり{r['降水あり地点数']}地点・最大{r['最大1時間降水量']['mm']}mm({r['最大1時間降水量']['地点']}) / 解説文={'言及あり' if ok else '言及なし → 雨域の省略の疑い'}"))

    # 5) 予報突合: VLM 出力中の概念が公式概況にも現れるか
    vlm_text = out["today"] + out["tomorrow"] + "".join(out["evidence"])
    for w in CONCEPTS:
        v, o = w in vlm_text, w in official
        if v or o:
            verdict = "OK" if v == o else "注意"
            detail = f"VLM={'言及' if v else '言及なし'} / 公式概況={'言及' if o else '言及なし'}"
            # 台風は府県概況（今日・明日の地方予報）に出ないことが多い。気象庁の公式台風情報に台風が実在し、
            # VLMがそれを書いているなら誤りではないので注意にしない（概況に言及が無いだけの誤検知を避ける）
            if w == "台風" and v and not o and isinstance(typhoon, list) and typhoon:
                verdict = "OK"
                detail += f" / 公式台風情報に台風{len(typhoon)}個あり（概況に無くても妥当）"
            res.append((verdict, f"予報突合:{w}", detail))

    # 6) 前線通過域の突合: 予想図で前線が陸地にかかる地方を、解説文が落としていないか／公式概況に天気の崩れがあるか
    tomorrow_text = out["tomorrow"] + "".join(out.get("check_points", []))
    for key, label in (("reach_ft24", "24時間先"), ("reach_ft48", "48時間先")):
        reach = []
        for f in out.get("fronts", []):
            for r in f.get(key, []):
                if r in REACH_KEYWORDS and r not in reach:
                    reach.append(r)
        for r in reach:
            ok = mentioned(r, tomorrow_text)
            res.append(("OK" if ok else "注意", f"前線通過域の言及[{label}]:{r}",
                        f"{label}の予想図で前線が{r}にかかる / 解説文={'言及あり' if ok else '言及なし → 前線通過域の省略の疑い'}"))
            grp = REACH_TO_GROUP.get(r)
            if grp and grp in group_text:
                broken = [w for w in WEATHER_BREAK_WORDS if w in group_text[grp]]
                res.append(("OK" if broken else "注意", f"前線通過域と概況[{label}]:{r}",
                            f"前線が{r}にかかる / 公式概況（{grp}）に天気の崩れの記載={'あり（' + '・'.join(broken[:3]) + '）' if broken else 'なし → 前線の影響を過大に書いていないか確認'}"))
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
