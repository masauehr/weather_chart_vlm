"""ID-32 検証: 気象庁の天気図（実況・予想）・衛星赤外・アメダス実況・予報概況を取得する。

出力（data/<基準時刻>/ 配下。git 除外）:
  surface_asia.png   アジア太平洋 地上天気図（ASAS）
  surface_near.png   日本近海 地上天気図（着色）
  forecast_{near,asia}_{ft24,ft48}.png  予想天気図（24・48時間先）
  ir_japan.png       ひまわり赤外（B13）全球画像から日本周辺(z=4, 2x2タイル)を結合
  amedas.json        主要地点のアメダス実況（気温・風・降水）
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


def fetch_amedas(target_utc):
    """天気図の対象時刻(UTC→JST)のアメダス全国マップから主要地点を抜き出す。"""
    jst = datetime.strptime(target_utc, "%Y%m%d%H%M%S") + timedelta(hours=9)
    stamp = jst.strftime("%Y%m%d%H%M%S")
    m = get(f"{BASE}/amedas/data/map/{stamp}.json").json()
    keys = ("temp", "wind", "windDirection", "precipitation1h", "precipitation10m", "humidity", "pressure", "normalPressure")
    out = {}
    for name, code in STATIONS.items():
        rec = m.get(code, {})
        # 値は [値, 品質フラグ] の形式
        out[name] = {k: rec[k][0] for k in keys if k in rec and rec[k]}
    return stamp, out


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
