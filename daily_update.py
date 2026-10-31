"""ID-32 デモの毎日更新（GitHub Actions から実行）。

fetch_chart.py で今日の天気図・衛星・実況・予報を取得 → Claude API で気圧配置を判定
（一般向け・こども向けの両方の解説文を1回の呼び出しで取得）→ validate.py でガードレール
検証 → webui/history/<YYYY-MM-DD>/ に画像＋data.json を保存し、10日より古い履歴を削除する。

使い方: python daily_update.py
必要な環境変数: ANTHROPIC_API_KEY（GitHub Actions の場合はリポジトリ Secrets から）
出典: 気象庁ホームページ（天気図・衛星・アメダス・予報概況）
"""
import json
import os
import shutil
import subprocess
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
EFFORT = "low"  # medium だと低気圧の陸上/海上の読みは安定するがコストと時間が増えるため、多少の誤読は許容して low（2026-10-19）

SYSTEM = """あなたは気象庁の地上天気図・衛星赤外画像を読む VLM（視覚言語モデル）役です。
渡された画像（実況の地上天気図2枚：アジア太平洋・日本近海／ひまわり赤外2枚：広域と九州〜南西諸島の拡大（海岸線・緯経度格子つき）／拡大天気図2枚：北半分と朝鮮半島〜日本海西部（低気圧の位置確認用）／
予想天気図4枚：日本近海・アジア太平洋の24時間先・48時間先）と、
同時刻のアメダス実況（主要8地点＋全国約1300地点の地域別集計）・府県予報概況（JSONテキスト）をもとに、気圧配置を判定してください。

出力は次のスキーマに**厳密に一致する JSON のみ**を返すこと（説明文・コードフェンス・前置きは一切不要）。
"today"/"tomorrow"/"evidence"/"check_points" は一般向け、"kids" 以下はそれを小学生にも分かる言葉で言い換えたもの。

{
  "chart_time_jst": "YYYY-MM-DD HH:MM",
  "pattern": "冬型 | 南岸低気圧 | 梅雨前線 | 秋雨前線 | 太平洋高気圧 | 台風 | 移動性高気圧 | 日本海低気圧 | その他 のいずれか一つ",
  "secondary_patterns": ["主パターン以外に見える要素があれば自由記述で列挙（無ければ空配列）"],
  "confidence": 0.0から1.0の数値,
  "evidence": ["天気図・衛星画像から読み取れる根拠を箇条書きで"],
  "lows": [{"hpa": "天気図に書かれた中心気圧の数字", "position": "中心の位置。海岸線（北海道・サハリン・千島・カムチャツカ・本州・朝鮮半島）との位置関係と、気象庁の海域名で書く（例: サハリンの南東のオホーツク海、東北の西の日本海）。緯経度の数字は書かない", "distance_to_japan": "日本（北海道・本州）の陸地から近い | やや離れている | 遠い", "movement": "天気図の矢印の向きと速さ", "fc_ft24": "24時間先の予想図での位置・気圧（同じ低気圧と判断した根拠。無ければ「消滅」「不明」）"}],
  "fronts": [{"type": "温暖・寒冷・停滞・閉塞（前線記号の色・形で1本ずつ判定）", "location": "その前線が描かれている範囲（緯経度の目安・海域名・どの低気圧に伴うか）", "regions": ["実況でその前線が陸地にかかる地方（下の地方名リストから。海上だけなら空配列）"]}],
  "fronts_ft24": [{"type": "24時間先の予想図の前線1本ごとの種類", "location": "範囲", "regions": ["陸地にかかる地方"]}],
  "fronts_ft48": [{"type": "48時間先の予想図の前線1本ごとの種類（停滞前線は停滞）", "location": "範囲", "regions": ["陸地にかかる地方"]}],
  "troughs": [{"location": "気圧の谷の位置（例: 九州南部〜奄美付近）", "isobar_dent": "等圧線の凹みの様子", "cloud_match": "yes | partial | no（衛星の雲域との一致）", "rain_stations": "その付近のアメダス降水あり地点数（地域別集計から）"}],
  "chart_values": {"対象（例: 高気圧(日本海)）": "示度(hPa)"},
  "claims": {
    "front_near_japan_now": true または false,
    "typhoon_affects_japan_now": true または false
  },
  "today": "実況・気圧配置から言える今日の天気の解説文（一般向け）",
  "tomorrow": "実況と予想天気図（24時間先・48時間先）の気圧配置の推移から言える明日以降の見通し文（一般向け。予想天気図の対象時刻に触れる）",
  "check_points": ["予報が変わりうる不確実要素（一般向け）"],
  "caveats": "画像の読み取りに基づく推定である旨の断り書き",
  "kids": {
    "evidence": ["evidence を小学生にも分かる言葉で3点程度に言い換え"],
    "today": "today を小学生にも分かる言葉で言い換え",
    "tomorrow": "tomorrow を小学生にも分かる言葉で言い換え",
    "check_points": ["check_points を小学生にも分かる言葉で言い換え"]
  }
}

低気圧・前線の読み取り手順（解説を書く前に必ず行う）:
1. 地上天気図（日本近海・アジア太平洋の両方）で「低」の記号と中心（×）をすべて探し、"lows" に1つずつ別々に列挙する。
   中心気圧の数字・中心の位置・矢印の向きと速さを天気図から読む。
   位置は緯経度の数字だけで決めない。まず中心（×）が「陸地のどちら側の海上か」を海岸線との関係で確認する
   （例: 北海道と本州の西側＝日本海、北海道の東側＝太平洋、サハリンや千島列島の北西側＝オホーツク海）。
   そのうえで格子線の数字と照合し、海岸線との関係と食い違うときは海岸線との関係を優先して読み直す。
   中心（×）が朝鮮半島・中国東北部（遼寧・吉林・黒竜江）・沿海州・サハリンなどの陸地の上にあるときは、position に陸地名と「内陸」「沿岸」を明記し、「日本海」「日本海北部」と書かない
   （日本海は、朝鮮半島・沿海州・サハリン・北海道・本州に囲まれた海上だけ。黄海は朝鮮半島と中国大陸の間）。
   台風（台風記号・「台」の表記）は lows に入れない（台風は台風情報で扱う）。
   日本近海図に描かれた「低」の記号の数と lows の数を一致させる（アジア太平洋図にだけ見える低気圧も入れる）。入れ忘れ・重複列挙をしない。
2. 複数の低気圧を1つにまとめない。気圧の数字が違えば別の低気圧。中心が離れていれば別の低気圧。
   「北海道付近」と書けるのは、中心が北海道の陸地または沿岸のおおむね数百km以内にある場合だけ。
   オホーツク海中部・千島・カムチャツカ方面にある低気圧は「オホーツク海」「千島方面」などと書き、北海道付近とは書かない。
3. 日本への影響は、中心の遠近だけでなく、中心の位置・進行方向・等圧線の混み具合・アメダスの降水地点の分布で判断する。
   遠くの深い低気圧より、日本海や東北付近にある浅い低気圧のほうが雨の原因であることが多い。降水があるときは、降水地点に最も近い低気圧・前線・谷を原因として書く。
4. 前線は実況・24時間先・48時間先のそれぞれの図で、前線記号が実際に描かれている前線を1本ずつ別々に fronts / fronts_ft24 / fronts_ft48 に書く。描かれていない所に前線を書かない。
   種類は記号で決める: 青の三角（トゲ）＝寒冷前線、赤の半円＝温暖前線、赤半円と青三角が交互＝停滞前線、紫＝閉塞前線。
   1つの低気圧に温暖前線と寒冷前線が付いていれば2本に分ける（例: 北海道の低気圧の東に温暖前線、南西にのびる寒冷前線）。「寒冷・温暖混合」のようにまとめない。
   北海道付近など低気圧の東側（進行方向の前側）には、赤い半円の温暖前線が描かれていることが多い。北半分の拡大画像で低気圧の東側・南東側を必ず確かめ、描かれていれば寒冷前線とは別に温暖前線として書く。
   前線は、線の根元にある低気圧に伴うものとして書く。前線の根元に低気圧記号が無い（海上や大陸のみ）なら、その海域に低気圧があるとは書かない（例: 日本海に低気圧記号が無ければ「日本海の低気圧」と書かない。寒冷前線が日本海に沿ってのびていても、根元は北海道付近の低気圧）。
   実況の前線は、低気圧との位置関係から温暖前線（進行方向の前側）か寒冷前線（後ろ側）かを確かめる。低気圧があっても前線が描かれていなければ「前線を伴わない低気圧」と扱う。
5. 明日の見通しでは、24時間先・48時間先の予想図で "lows" の各低気圧がどこへ動いたかを個別に追う。
   気圧の数字の変化（例: 1014→1012）・実況の進行方向・移動速度から同じ低気圧を対応づけ、他の低気圧と取り違えない。
   予想図の進行先は、中心（×）が北海道・本州の陸地のどちら側にあるかで海域を確定し、tomorrow に「日本海」「オホーツク海」「北海道の東の太平洋」などの海域名で書く
   （「北海道付近」とだけ書いて済ませない。北海道の東側の海上は太平洋、サハリン・千島列島の北西側はオホーツク海）。対応づけが曖昧なら「〜とみられる」と断る。
6. tomorrow で複数の低気圧に触れるときは、低気圧ごとに「18日に日本海にあった○hPaの低気圧」のように起点（実況時の位置・気圧）を毎回明記し、
   別の低気圧に話を移すときは「一方、」で区切る。ある低気圧の気圧変化（深まる・弱まる）を、別の低気圧のものと混ぜて書かない。
   気圧の数字の変化は lows の fc_ft24 で追跡した低気圧のものだけを、その低気圧の説明の中に書く（例: 日本海の低気圧は1004→1000hPaと深まり、別のオホーツク海の低気圧は994→1004hPaと弱まる）。
7. 予想天気図の対象時刻は各画像のラベルにある日時のとおり（24時間予想図と48時間予想図は対象日時が違う）。tomorrow に書く日時は、見ている予想図のラベルの日時に一致させる。
8. 前線ごとに、図で前線の線が陸地を通る範囲を地方名で regions に入れる（実況・24時間先・48時間先それぞれ）。
   府県予報概況を取得している地域だけに引きずられず、前線が陸地にかかる範囲全体を書く。
   入れるのは、前線の線そのものが当該地方の陸地の上、または沿岸のすぐそばを通る場合だけ。前線が日本海や大陸にあって日本の陸地に届いていなければ空配列（前線から離れた地方を足さない）。
   前線が本州を北東〜南西にほぼ縦断しているときは、通る地方（例: 北海道・東北・北陸・近畿・山陰・九州北部）を図に沿って1つずつ確かめて全て入れる。停滞前線は、線が実際に通る区間（例: 関東から北陸・山陰付近）だけを入れ、広げすぎない。
   地方名は次のリストのみ使う: 北海道、東北、北陸、関東甲信、東海、近畿、山陰、山陽、四国、九州北部、九州南部、沖縄。

何を書くか（日本への影響で選ぶ）:
- today・tomorrow に書くのは日本の天気に関わる気圧配置だけにする。日本に影響しない低気圧・高気圧（大陸上に留まる低気圧、日本海や朝鮮半島付近にあって日本の陸地に届かない高気圧、遠いオホーツク海の低気圧など）は書かない（lows には記録してよい）。
- 最優先で書くのは、各予想図で前線が日本の陸地のどこにかかり、その種類（寒冷・温暖・停滞）と動きがどうかと、北海道付近などの低気圧がどこへ抜けるか。
  前線が本州の広い範囲にかかっているなら、それを最初に書く（「本州の広い範囲に寒冷前線がかかり、天気が崩れる」「前線が関東から北陸・山陰付近に停滞」など）。
- tomorrow に書く低気圧は、日本の陸地に前線や雨をもたらす（北海道付近〜本州付近を通る）ものだけ。遠いオホーツク海の低気圧、大陸上の低気圧、日本の陸地に届かない高気圧は tomorrow・kids に書かない。
- 朝鮮半島より西・北の大陸上にある低気圧は、予想図でも中心（×）が大陸上にあれば「大陸にとどまり日本には影響しない」と fc_ft24 に書く（「日本海を東進」「本州に近づく」としない）。
- 大陸上の低気圧が予想図でも大陸上にあるなら、「本州に近づく」とは書かない。低気圧の fc_ft24 は予想図の中心（×）の位置が陸か海かで確かめて書く。

地域の書き方（解説文 today・tomorrow・check_points・kids）:
- 地方名を3つ以上並べない。並べたくなったら次のまとめた表記に置き換える: 北海道＋東北＝「北日本」、東北＋北陸＋山陰（＋近畿北部）＝「本州の日本海側」、
  関東甲信＋東海＋近畿＋山陽＝「本州の太平洋側」または「東日本・西日本」、山陰＋九州北部＝「西日本の日本海側」、近畿〜九州＝「西日本」。
  前線が北日本から九州北部まで及ぶなら「北日本から西日本の日本海側を中心に」のように、一つの句で書く。
  悪い例:「北日本から北陸・近畿・山陰・九州北部にかけて雨」（地方名の列挙）→ 良い例:「本州の日本海側を中心に、北日本から九州北部にかけて雨」。
  悪い例:「北海道・東北・北陸に加えて関東甲信・東海・近畿・山陰・九州北部まで」→ 良い例:「北日本から西日本にかけて広く、特に日本海側で」。
  こども向け（kids）も同じ。regions の配列だけは地方名を1つずつ列挙してよい（解説文には使わない）。
- 都道府県や地点を並べず、気象庁の予報用語に近いまとまりで書く。例: 「北日本から本州の日本海側を中心に」「西日本の日本海側」「北海道から北陸にかけて」「東日本の太平洋側」「九州北部から山陰・北陸、北日本の日本海側にかけて」。
- 前線・低気圧による天気の崩れは、前線が実際にかかる範囲（fronts_ft24 / fronts_ft48 の regions）全体のまとまりで書く。概況の取得地域（北海道・東北・北陸・関東・東海・近畿・山陰・九州北部・九州南部・沖縄の代表府県）に載った地名だけを挙げて、
  他の前線通過域を落とさない。概況に無い地方でも、前線が描かれていれば「天気が崩れるおそれ」を書く（概況に無いことは断り、予想図に基づく推定と分かるようにする）。
- 府県名・地点名は、アメダスで実際に降水があった地点を示すときなど、具体名が必要な場合に限って使う。

気圧の谷・雨域の読み取り手順（必ず行う）:
1. 地上天気図の等圧線が低圧側へ凹んでいる所（高気圧の縁でも、低気圧・前線が無くても）を探し、"troughs" に列挙する。無ければ空配列。
2. 凹みの位置を、緯経度格子つきの拡大赤外画像（特に九州〜南西諸島）と見比べ、雲域（白く明るい塊）が重なるか確認する。
3. アメダスの「地域別」集計で、その付近の「降水あり地点数」「最大1時間降水量」を確認する。降水あり地点が複数ある地域は、
   雨域がある事実として today に地域名つきで書く（例: 鹿児島〜奄美付近の気圧の谷に伴う雨雲）。
4. 谷と雲域の片方しか確認できない場合は断定せず cloud_match を partial にし、断定を避ける表現にする。
5. 全国の大半が晴れでも、局地的な雨域を省略しない。「全国的に晴れ」と書くのは降水あり地点数が全国で数地点以下の場合に限る。

風の強さの扱い（気象庁の風の強さと吹き方の表に従う）:
- 風の強弱は【平均風速（10分間の平均）】で判断し、記述する。最大瞬間風速（突風・3秒平均）を風の強さの分類に使わない。瞬間風速に触れる場合は「瞬間風速」と明記して平均風速と区別する（瞬間風速は平均風速の1.5倍程度になることが多い）。
- 「やや強い風（10以上15未満m/s）」「強い風（15以上20未満）」「非常に強い風（20以上30未満）」「猛烈な風（30以上）」は平均風速の区分。渡されたアメダスの「風速」は平均風速なので、そのまま上の区分で述べてよい。

海域名・地名の使い方（気象庁「全般気象解説情報などに用いるアジア・北西太平洋域の地名、海域名」に従う）:
- 台風の位置・海域名は、渡された「台風情報」の位置（緯度経度）と海域名を正とする。天気図の図柄から海域を推測しない。
- 台風情報が空配列なら、天気図上の台風記号があっても台風の存在を書かない（熱帯低気圧(TD)は「熱帯低気圧」と書く）。
- 日本周辺の海域名は気象庁の名称だけを使い、独自の言い換えをしない。主な海域名と目安:
  日本海、東シナ海（九州の西・沖縄の北西〜台湾の北）、黄海、オホーツク海、
  日本の東（本州の東方、概ね北緯30度以北・東経145度付近）、日本の南（本州・四国・九州の南）、日本のはるか東、
  沖縄の南（沖縄の南方、北緯20度台前半）、フィリピンの東、南シナ海、小笠原諸島近海、
  マリアナ諸島、南鳥島近海。台風情報の「海域名」の語がある場合はその語をそのまま使う。
- 「東シナ海南方」「沖縄の東」のような気象庁にない海域名は使わない。
- 台風が日本から遠い海域（日本の東など）にある場合、沖縄・南西諸島・西日本の雨や湿った空気を台風の影響と書かない。
  沖縄・南西諸島の雨は、地上天気図の気圧の谷・前線・衛星の雲域の対応から原因を判断し、
  谷が確認できれば「気圧の谷の影響」と書く（"troughs" にも記載）。
  台風が遠くて南西諸島に降水があるときは、台湾付近・東シナ海南部・沖縄の南の低気圧や等圧線の凹み（等圧線が低圧側へ張り出す所）を
  必ず探し、対応する谷・低気圧があれば「○○付近の気圧の谷の影響」と地域名つきで明記する。「明瞭な谷は確認できない」で済ませない。
- 台風の海域名は台風情報の「実況」の海域名をそのまま使う（「日本の東」を「日本のはるか東」などに言い換えない。予報時刻ごとの海域名はその時刻のものを使う）。台風の影響は台風情報の強風域・進路に日本が入る場合のみ書く。

厳守事項:
- pattern は許可リストの語のみ使う。台風が日本から離れていて影響しない（claims.typhoon_affects_japan_now が false）場合は、pattern を「台風」にせず、日本付近の主な気圧配置（移動性高気圧・日本海低気圧など）を選び、台風は secondary_patterns に書く。
- 「上空の気圧の谷」など、渡された天気図で確認できない根拠は書かない。
- chart_values 以外の数値は、渡されたアメダス実況JSONか予報テキストに実在する値のみを today/tomorrow に書く。
- 明日の見通しは予想天気図（気象庁の数値予報に基づく）の高低気圧・前線の位置の変化を根拠にする。
- 予想天気図の最後の対象日時（48時間先）より先の天気は断定しない。「20日も〜」のように予想図にない日の見通しを書かない。
- confidence は正直に見積もる。kids の内容は事実を変えず言葉だけ易しくする。
"""


# 予想天気図（meta["forecast"] のキー, 表示名）
FORECAST_LABELS = [
    ("near_ft24", "日本近海・24時間先"), ("near_ft48", "日本近海・48時間先"),
    ("asia_ft24", "アジア太平洋・24時間先"), ("asia_ft48", "アジア太平洋・48時間先"),
]


def make_zoom_crop(root: Path, name: str, box: tuple) -> Path:
    """日本近海天気図の一部（box は幅・高さに対する割合 (左, 上, 右, 下)）を3倍に拡大した補助画像を作る。
    元画像が600px程度で低気圧の位置を取り違えやすいため、位置確認用に拡大する。
    VLMへ渡すだけで、公開ページ（webui/history）には保存しない。"""
    from PIL import Image
    im = Image.open(root / "surface_near.png").convert("RGB")
    w, h = im.size
    crop = im.crop((int(w * box[0]), int(h * box[1]), int(w * box[2]), int(h * box[3])))
    crop = crop.resize((crop.width * 3, crop.height * 3), Image.LANCZOS)
    path = root / name
    crop.save(path)
    return path


def make_north_crop(root: Path) -> Path:
    """北半分（北海道〜東北・日本海北部・オホーツク海）の拡大。"""
    return make_zoom_crop(root, "surface_near_north.png", (0.25, 0.0, 1.0, 0.55))


def make_west_crop(root: Path) -> Path:
    """朝鮮半島〜日本海西部〜中国東北部の拡大。陸上の低気圧を日本海と取り違えないための確認用。"""
    return make_zoom_crop(root, "surface_near_west.png", (0.0, 0.08, 0.6, 0.55))


def classify(root: Path) -> dict:
    amedas = (root / "amedas.json").read_text()
    overview = (root / "overview.json").read_text()
    meta = json.loads((root / "meta.json").read_text())
    typhoon_path = root / "typhoon.json"
    typhoon = typhoon_path.read_text() if typhoon_path.exists() else "（取得なし）"

    images = [
        ("地上天気図（アジア太平洋）", root / "surface_asia.png"),
        ("地上天気図（日本近海・着色）", root / "surface_near.png"),
        ("地上天気図（日本近海の北半分を3倍に拡大。北海道・日本海北部・オホーツク海の低気圧の位置確認用）", make_north_crop(root)),
        ("地上天気図（朝鮮半島〜日本海西部を3倍に拡大。低気圧の中心（×）が陸上か海上かの確認用。陸上なら日本海とは書かない）", make_west_crop(root)),
        ("ひまわり赤外(B13)・海岸線つき（広域）", root / "ir_japan.png"),
        ("ひまわり赤外(B13)・九州〜南西諸島の拡大（海岸線・緯経度格子つき）", root / "ir_nansei.png"),
    ]
    for key, label in FORECAST_LABELS:
        vt = meta["forecast"][key]["valid_jst"]
        images.append((f"予想天気図（{label}・{vt} JST 対象）", root / f"forecast_{key}.png"))
    content = []
    for label, path in images:
        content.append({"type": "text", "text": f"[{label}]"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": b64(path)}})
    content.append({
        "type": "text",
        "text": (
            f"アメダス実況（主要8地点＋全国の地域別集計）:\n{amedas}\n\n"
            f"府県予報概況（地方区分ごと。東北は日本海側の秋田・山形、九州南部は宮崎・鹿児島、沖縄は沖縄本島。前線がかかる範囲はここに載る地域に限らない）:\n{overview}\n\n"
            f"台風情報（気象庁の公式発表。位置・海域名はこれを正とする。空配列なら台風は存在しない）:\n{typhoon}\n\n"
            f"取得メタ情報:\n{json.dumps(meta, ensure_ascii=False)}\n\n"
            "上記画像と実況・予報から、指定スキーマの JSON を出力してください。"
        ),
    })

    client = anthropic.Anthropic()
    response = client.messages.create(
        model=MODEL, max_tokens=8000, system=SYSTEM,
        output_config={"effort": EFFORT},
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "max_tokens":
        print("警告: max_tokens で打ち切られました", file=sys.stderr)
    text = next((b.text for b in response.content if b.type == "text"), None)
    if text is None:
        raise RuntimeError(f"text ブロックが無い（stop_reason={response.stop_reason}）")
    text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    out = json.loads(text)
    validate.fill_claims(out, root)  # precip_now / wind_now はアメダスから機械的に決める
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
    shutil.copy(root / "ir_nansei.png", img_dir / "ir_nansei.png")
    meta = json.loads((root / "meta.json").read_text())
    forecast_images = []
    for key, label in FORECAST_LABELS:
        shutil.copy(root / f"forecast_{key}.png", img_dir / f"fc_{key}.png")
        forecast_images.append({
            "id": f"fc_{key}", "label": f"予想 {label}（{meta['forecast'][key]['valid_jst']}）",
            "src": f"history/{date_str}/images/fc_{key}.png",
        })

    data = {
        "date": date_str,
        "chart_time_jst": out["chart_time_jst"],
        "updated_jst": (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M"),
        "pattern": out["pattern"],
        "secondary_patterns": out.get("secondary_patterns", []),
        "confidence": out["confidence"],
        "images": [
            {"id": "near", "label": "地上天気図（日本近海）", "src": f"history/{date_str}/images/near.png"},
            {"id": "asia", "label": "地上天気図（アジア太平洋）", "src": f"history/{date_str}/images/asia.png"},
            {"id": "ir", "label": "ひまわり赤外(B13)", "src": f"history/{date_str}/images/ir.png"},
            {"id": "ir_nansei", "label": "ひまわり赤外 九州〜南西諸島（拡大）", "src": f"history/{date_str}/images/ir_nansei.png"},
            *forecast_images,
        ],
        "general": {
            "evidence": out["evidence"],
            "today": out["today"],
            "tomorrow": out["tomorrow"],
            "check_points": out["check_points"],
        },
        "lows": out.get("lows", []),
        "fronts": out.get("fronts", []),
        "fronts_ft24": out.get("fronts_ft24", []),
        "fronts_ft48": out.get("fronts_ft48", []),
        "troughs": out.get("troughs", []),
        "kids": out.get("kids", {}),
        "caveats": out.get("caveats", ""),
        "guardrail_checks": checks,
        "source": "出典: 気象庁ホームページ（実況・予想天気図・衛星・アメダス・府県予報概況）",
    }
    (dest / "data.json").write_text(json.dumps(data, ensure_ascii=False, indent=2))


def prune_and_index():
    """KEEP_DAYS より古い履歴を削除し、history/index.json を新しい順で書き直す。"""
    dates = sorted((p.name for p in HISTORY_DIR.iterdir() if p.is_dir()), reverse=True)
    for old in dates[KEEP_DAYS:]:
        shutil.rmtree(HISTORY_DIR / old)
    dates = dates[:KEEP_DAYS]
    (HISTORY_DIR / "index.json").write_text(json.dumps({"dates": dates}, ensure_ascii=False, indent=2))


def notify_slack(text: str) -> bool:
    """Slack DM へ通知する。Bluesky 自動投稿（automated-data-collector/bin/notify.py）と同じ仕組みで、
    `openclaw message send` 経由で送る。宛先は環境変数 BSKY_NOTIFY_TO か ~/.config/bsky/slack_to。
    通知の失敗で本処理のエラーを隠さない（例外は握りつぶして False を返す）。"""
    to = os.environ.get("BSKY_NOTIFY_TO", "").strip()
    to_file = Path.home() / ".config" / "bsky" / "slack_to"
    if not to and to_file.exists():
        to = to_file.read_text().strip()
    if not to:
        print("Slack通知先が未設定のため通知をスキップ", file=sys.stderr)
        return False
    exe = shutil.which("openclaw") or "/opt/homebrew/bin/openclaw"
    try:
        r = subprocess.run([exe, "message", "send", "--channel", "slack", "--target", to, "-m", text[:3500]],
                           capture_output=True, text=True, timeout=90)
        if r.returncode != 0:
            print("Slack通知に失敗:", (r.stderr or r.stdout).strip()[:200], file=sys.stderr)
        return r.returncode == 0
    except Exception as e:
        print("Slack通知で例外:", e, file=sys.stderr)
        return False


def is_credit_error(e: Exception) -> bool:
    """Anthropic API のクレジット残高不足エラー（400 invalid_request_error）かを判定する。"""
    return isinstance(e, anthropic.BadRequestError) and "credit balance is too low" in str(e)


def main():
    jst_today = datetime.now(timezone.utc) + timedelta(hours=9)
    date_str = jst_today.strftime("%Y-%m-%d")

    root = fetch_chart.fetch_all()
    try:
        out = classify(root)
    except Exception as e:
        if is_credit_error(e):
            # 1日2回（8:45・9:15）実行されるため、同日の通知は1回に絞る（.slack_notified は git 除外）
            flag = HERE / ".slack_notified"
            if not (flag.exists() and flag.read_text().strip() == date_str):
                ok = notify_slack(
                    "【天気図VLM】日次更新が失敗しました（%s）\n"
                    "Anthropic API のクレジット残高が不足しています。"
                    "https://console.anthropic.com/settings/billing でチャージ後、"
                    "`bash daily_update_local.sh` を再実行してください。" % date_str
                )
                if ok:
                    flag.write_text(date_str)
        raise
    checks = guardrail_checks(out, root)
    ng = sum(1 for c in checks if c["verdict"] == "NG")
    print(f"pattern={out['pattern']} confidence={out['confidence']} NG={ng}/{len(checks)}")

    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    build_entry(date_str, out, checks, root, HISTORY_DIR / date_str)
    prune_and_index()
    print(f"→ {HISTORY_DIR / date_str}")


if __name__ == "__main__":
    sys.exit(main())
