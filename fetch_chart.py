"""ID-32 検証: 気象庁の天気図（実況・予想）・衛星赤外・アメダス実況・予報概況を取得する。

出力（data/<基準時刻>/ 配下。git 除外）:
  surface_asia.png   アジア太平洋 地上天気図（ASAS）
  surface_near.png   日本近海 地上天気図（着色）
  forecast_{near,asia}_{ft24,ft48}.png  予想天気図（24・48時間先）
  ir_japan.png       ひまわり赤外（B13）全球画像から日本周辺(z=4, 2x2タイル)を結合
  ir_nansei.png      九州〜南西諸島の拡大赤外（z=5, 経緯度格子つき）
  amedas.json        主要8地点＋全国アメダスの地域別集計（降水あり地点数・最大降水/風速）
  overview.json      府県予報の概況テキスト
  meta.json          取得元URLと時刻
出典: 気象庁ホームページ
"""
import io
import json
import math
import sys
from datetime import datetime, timedelta
from pathlib import Path

import requests
from PIL import Image, ImageDraw

BASE = "https://www.jma.go.jp/bosai"
UA = {"User-Agent": "weather-hackathon-spike (personal research)"}
DATA = Path(__file__).parent / "data"
COASTLINE = json.loads((Path(__file__).parent / "coastline_ea.json").read_text())
# fetch_ir のタイル範囲: z=4, x=13,14 / y=5,6（東経112〜157度・北緯22〜55度）
IR_Z, IR_X0, IR_Y0 = 4, 13, 5

# 実況の裏取りに使う主要地点（アメダス地点コード）
STATIONS = {
    "札幌": "14163", "新潟": "54232", "金沢": "56227", "東京": "44132",
    "名古屋": "51106", "大阪": "62078", "福岡": "82182", "那覇": "91197",
}
# 予報概況を取る府県予報区
OFFICES = {"東京都": "130000", "新潟県": "150000", "石川県": "170000", "北海道(石狩)": "016000"}


def get(url, **kw):
    r = requests.get(url, headers=UA, timeout=30, **kw)
    r.raise_for_status()
    return r


def latest_chart(kind):
    """list.json の now 配列の末尾（最新）を返す。"""
    files = get(f"{BASE}/weather_map/data/list.json").json()[kind]["now"]
    return files[-1]


def latest_forecast_chart(kind, ft):
    """予想天気図（ft24/ft48）の最新ファイル名を返す。"""
    return get(f"{BASE}/weather_map/data/list.json").json()[kind][ft][-1]


def chart_valid_utc(name):
    """天気図ファイル名から対象時刻(UTC, 14桁)を取り出す。"""
    return name.split("_")[6]


def lonlat_to_tile(lon, lat, z):
    """緯度経度 → スリッピータイル座標(浮動小数)。標準の Web Mercator タイル方式。"""
    xtile = (lon + 180.0) / 360.0 * (2 ** z)
    lat_rad = math.radians(lat)
    ytile = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * (2 ** z)
    return xtile, ytile


def draw_coastline(canvas, z, x0, y0, tile_px=256):
    """coastline_ea.json（Natural Earth 1:50m, パブリックドメイン）の線を canvas に描く。"""
    draw = ImageDraw.Draw(canvas)
    for line in COASTLINE["lines"]:
        pts = []
        for lon, lat in line:
            xt, yt = lonlat_to_tile(lon, lat, z)
            pts.append(((xt - x0) * tile_px, (yt - y0) * tile_px))
        if len(pts) >= 2:
            draw.line(pts, fill=(255, 255, 0), width=1)


def draw_grid(canvas, z, x0, y0, tile_px=256, step=5, scale=1):
    """緯度経度の格子線（step度刻み）とラベルを描く。天気図の経緯線と見比べるための補助線。"""
    draw = ImageDraw.Draw(canvas)
    w, h = canvas.size
    # 画像範囲の経緯度を逆算して、範囲内の step 度刻みの線だけを引く
    lon_min = (x0 / 2 ** z) * 360 - 180
    lon_max = ((x0 + w / scale / tile_px) / 2 ** z) * 360 - 180
    for lon in range(math.ceil(lon_min / step) * step, int(lon_max) + 1, step):
        px = (lonlat_to_tile(lon, 30, z)[0] - x0) * tile_px * scale
        draw.line([(px, 0), (px, h)], fill=(0, 200, 255), width=1)
        draw.text((px + 2, 2), f"{lon}E", fill=(0, 255, 255))
    for lat in range(20, 46, step):
        py = (lonlat_to_tile(130, lat, z)[1] - y0) * tile_px * scale
        if 0 <= py <= h:
            draw.line([(0, py), (w, py)], fill=(0, 200, 255), width=1)
            draw.text((2, py + 2), f"{lat}N", fill=(0, 255, 255))


def fetch_ir_zoom(out, target_utc, z=5, x0=27, y0=12, nx=2, ny=2, scale=2):
    """九州〜南西諸島を含む拡大赤外画像（z=5 の 2x2 タイル ≒ 東経124〜146度・北緯22〜41度）。

    z=4 の広域画像（約11px/度）では南西諸島の小さな雲域が潰れるため、約23px/度で別途作る。
    scale 倍に拡大して海岸線・緯経度の格子線を重ねる（VLM が雲域と気圧の谷を位置で照合できるように）。
    """
    times = get(f"{BASE}/himawari/data/satimg/targetTimes_fd.json").json()
    t = min(times, key=lambda x: abs(int(x["validtime"]) - int(target_utc)))
    base, valid = t["basetime"], t["validtime"]
    canvas = Image.new("RGB", (256 * nx, 256 * ny))
    for yi in range(ny):
        for xi in range(nx):
            url = f"{BASE}/himawari/data/satimg/{base}/fd/{valid}/B13/TBB/{z}/{x0 + xi}/{y0 + yi}.jpg"
            canvas.paste(Image.open(io.BytesIO(get(url).content)).convert("RGB"), (xi * 256, yi * 256))
    canvas = canvas.resize((canvas.width * scale, canvas.height * scale), Image.LANCZOS)
    # 海岸線は拡大後の座標系で描く（tile_px を scale 倍して流用）
    draw = ImageDraw.Draw(canvas)
    for line in COASTLINE["lines"]:
        pts = []
        for lon, lat in line:
            xt, yt = lonlat_to_tile(lon, lat, z)
            pts.append(((xt - x0) * 256 * scale, (yt - y0) * 256 * scale))
        if len(pts) >= 2:
            draw.line(pts, fill=(255, 255, 0), width=1)
    draw_grid(canvas, z, x0, y0, scale=scale)
    canvas.save(out)
    return {"basetime": base, "validtime": valid}


def fetch_ir(out, target_utc):
    """ひまわり赤外 B13 を全球(fd)の z=4 2x2 タイル(x=13,14 / y=5,6 ≒ 東経112〜157度・北緯22〜55度)で結合する。

    jp 域は斜めに欠けた範囲しかなく太平洋側が空白になるため fd を使う。
    生の赤外画像には海岸線が無く陸海の境目が分からないため、Natural Earth の海岸線を重ね描きする。
    """
    times = get(f"{BASE}/himawari/data/satimg/targetTimes_fd.json").json()
    # 天気図の対象時刻に最も近い観測を選ぶ（時刻ずれで解説と実況が食い違うのを防ぐ）
    t = min(times, key=lambda x: abs(int(x["validtime"]) - int(target_utc)))
    base, valid = t["basetime"], t["validtime"]
    canvas = Image.new("RGB", (512, 512))
    for yi, y in enumerate((IR_Y0, IR_Y0 + 1)):
        for xi, x in enumerate((IR_X0, IR_X0 + 1)):
            url = f"{BASE}/himawari/data/satimg/{base}/fd/{valid}/B13/TBB/{IR_Z}/{x}/{y}.jpg"
            canvas.paste(Image.open(io.BytesIO(get(url).content)).convert("RGB"), (xi * 256, yi * 256))
    draw_coastline(canvas, IR_Z, IR_X0, IR_Y0)
    canvas.save(out)
    return {"basetime": base, "validtime": valid}


# 地域区分（観測所番号の上2桁 = 府県・地方ブロック番号）。島しょ部は緯度で補正する
BLOCK_REGIONS = [
    (range(11, 25), "北海道"), (range(31, 37), "東北"), (range(40, 47), "関東"), (range(48, 50), "甲信"),
    (range(50, 54), "東海"), (range(54, 58), "北陸"), (range(60, 66), "近畿"), (range(66, 70), "中国"),
    ((81,), "中国"), (range(71, 75), "四国"), (range(82, 87), "九州北部"), ((87,), "九州南部"),
]


def region_of(code, lat):
    """アメダス地点コードと緯度から地域名を返す。"""
    b = int(code[:2])
    if b == 44 and lat < 34:
        return "伊豆・小笠原"
    if b >= 91:
        return "沖縄"          # 91=沖縄本島・先島、93=大東島
    if b == 88:
        return "鹿児島(本土・大隅)" if lat >= 30 else "奄美・トカラ"
    for rng, name in BLOCK_REGIONS:
        if b in rng:
            return name
    return "その他"


def summarize_amedas(m):
    """全国マップ(約1300地点)を地域ごとに集計する。VLMには生データでなく集計と上位地点だけ渡す。

    降水は precipitation1h（1時間降水量）が 0 超の地点を「降水あり」とする。
    """
    table = get(f"{BASE}/amedas/const/amedastable.json").json()
    regions, stations = {}, []
    for code, rec in m.items():
        info = table.get(code)
        if not info:
            continue
        lat = info["lat"][0] + info["lat"][1] / 60
        name = info["kjName"]
        prec = rec["precipitation1h"][0] if rec.get("precipitation1h") else None
        wind = rec["wind"][0] if rec.get("wind") else None
        r = regions.setdefault(region_of(code, lat), {"地点数": 0, "降水あり地点数": 0, "最大1時間降水量": None, "最大風速": None})
        r["地点数"] += 1
        if prec is not None:
            if prec > 0:
                r["降水あり地点数"] += 1
            if r["最大1時間降水量"] is None or prec > r["最大1時間降水量"]["mm"]:
                r["最大1時間降水量"] = {"mm": prec, "地点": name}
            stations.append((prec, name, region_of(code, lat)))
        if wind is not None and (r["最大風速"] is None or wind > r["最大風速"]["m/s"]):
            r["最大風速"] = {"m/s": wind, "地点": name}
    top = [{"地点": n, "地域": g, "1時間降水量mm": p} for p, n, g in sorted(stations, reverse=True)[:10] if p > 0]
    return {"全国": {"地点数": sum(r["地点数"] for r in regions.values()),
                     "降水あり地点数": sum(r["降水あり地点数"] for r in regions.values())},
            "地域別": regions, "降水上位": top}


def fetch_amedas(target_utc):
    """天気図の対象時刻(UTC→JST)のアメダス全国マップから、主要地点と全国の地域別集計を作る。"""
    jst = datetime.strptime(target_utc, "%Y%m%d%H%M%S") + timedelta(hours=9)
    stamp = jst.strftime("%Y%m%d%H%M%S")
    m = get(f"{BASE}/amedas/data/map/{stamp}.json").json()
    keys = ("temp", "wind", "windDirection", "precipitation1h", "precipitation10m", "humidity", "pressure", "normalPressure")
    out = {}
    for name, code in STATIONS.items():
        rec = m.get(code, {})
        # 値は [値, 品質フラグ] の形式
        out[name] = {k: rec[k][0] for k in keys if k in rec and rec[k]}
    # 構造: 主要地点（従来の8地点）＋ 全国の地域別集計
    return stamp, {"主要地点": out, **summarize_amedas(m)}


def fetch_all() -> Path:
    """天気図・衛星・アメダス・予報概況を一式取得し、保存先ディレクトリを返す。daily_update.py 等から再利用する。"""
    DATA.mkdir(exist_ok=True)
    asia, near = latest_chart("asia"), latest_chart("near")
    # 日本近海天気図(near)の対象時刻(UTC)に衛星・アメダスを揃える。アジア図は 6 時間毎のため直近を併記
    base_utc = chart_valid_utc(near)
    out = DATA / base_utc
    out.mkdir(exist_ok=True)
    meta = {"asia": asia, "near": near}
    (out / "surface_asia.png").write_bytes(get(f"{BASE}/weather_map/data/png/{asia}").content)
    (out / "surface_near.png").write_bytes(get(f"{BASE}/weather_map/data/png/{near}").content)
    # 予想天気図（24時間・48時間先）。解析時刻(token6)＋ft時間が予想の対象時刻
    for area in ("near", "asia"):
        for ft, hours in (("ft24", 24), ("ft48", 48)):
            name = latest_forecast_chart(area, ft)
            (out / f"forecast_{area}_{ft}.png").write_bytes(get(f"{BASE}/weather_map/data/png/{name}").content)
            init = datetime.strptime(chart_valid_utc(name), "%Y%m%d%H%M%S")
            valid_jst = init + timedelta(hours=hours + 9)
            meta.setdefault("forecast", {})[f"{area}_{ft}"] = {
                "file": name, "init_utc": chart_valid_utc(name), "valid_jst": valid_jst.strftime("%Y-%m-%d %H:%M"),
            }
    meta["ir"] = fetch_ir(out / "ir_japan.png", base_utc)
    meta["ir_zoom"] = fetch_ir_zoom(out / "ir_nansei.png", base_utc)
    stamp, amedas = fetch_amedas(base_utc)
    meta["amedas_time"] = stamp
    (out / "amedas.json").write_text(json.dumps(amedas, ensure_ascii=False, indent=2))
    overview = {}
    for name, code in OFFICES.items():
        try:
            overview[name] = get(f"{BASE}/forecast/data/overview_forecast/{code}.json").json()
        except Exception as e:  # noqa: BLE001 取得失敗は記録して続行
            overview[name] = {"error": str(e)}
    (out / "overview.json").write_text(json.dumps(overview, ensure_ascii=False, indent=2))
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return out


def main():
    out = fetch_all()
    print(out)
    print((out / "meta.json").read_text())


if __name__ == "__main__":
    sys.exit(main())
