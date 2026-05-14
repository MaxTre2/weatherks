#!/usr/bin/env python3
"""
Weather Proxy Server for KSmobile/CM Launcher
=============================================
Притворяется weather.ksmobile.com но берёт данные из OpenWeatherMap.

УСТАНОВКА:
  pip install flask requests

ЗАПУСК:
  python weather_proxy_server.py

КОНФИГУРАЦИЯ:
  Замените OWM_API_KEY на ваш ключ OpenWeatherMap (бесплатно на openweathermap.org)

ПАТЧ APK (после запуска этого сервера):
  1. apktool d app.apk -o app_decompiled
  2. Найдите в smali файлах строки "weather.ksmobile.com" и "weather.ksmobile.net"
  3. Замените оба на IP/домен вашего сервера (например 192.168.1.100:5000)
  4. apktool b app_decompiled -o app_patched.apk
  5. Подпишите APK: zipalign + apksigner

ТОЧНЫЕ ФАЙЛЫ SMALI для замены URL:
  - com/cmnow/weather/request/e/a.smali  → URL прогноза погоды
  - com/cmnow/weather/request/b/a.smali  → URL поиска города
  - com/cmnow/weather/request/b/c.smali  → URL поиска TWC (оставить как есть)

ФОРМАТ API (что ожидает лаунчер):
  GET /api/city/search?f=<город>&locale=ru-ru&lang=ru
  GET /api/forecasts?f=<city_code>&cc=<country>&lang=ru&u=m
  GET /api/city/iplocate?locale=ru-ru&lat=<lat>&lng=<lng>
"""

from flask import Flask, request, jsonify
import requests
from datetime import datetime, timezone

app = Flask(__name__)

# ══════════════════════════════════════════════════════════════════
# НАСТРОЙКИ — ЗАМЕНИТЕ ЗДЕСЬ
OWM_API_KEY = "8ab40a42ab7e2af856bb54cc3d9da233"  # ← бесплатный ключ с openweathermap.org
HOST = "0.0.0.0"
PORT = 5000
# ══════════════════════════════════════════════════════════════════

OWM_BASE = "https://api.openweathermap.org"


# ── Маппинг кодов погоды OWM → коды KSmobile ──────────────────
# KSmobile использует Weather Channel (TWC) коды 0-47
OWM_TO_WC = {
    # Гроза
    200: 4, 201: 4, 202: 4, 210: 4, 211: 4, 212: 4, 221: 4, 230: 4, 231: 4, 232: 4,
    # Морось
    300: 9, 301: 9, 302: 9, 310: 9, 311: 9, 312: 9, 313: 9, 314: 9, 321: 9,
    # Дождь
    500: 11, 501: 12, 502: 12, 503: 12, 504: 12, 511: 10, 520: 40, 521: 40, 522: 40,
    # Снег
    600: 14, 601: 16, 602: 41, 611: 6, 612: 6, 613: 6, 615: 5, 616: 5, 620: 14, 621: 16, 622: 41,
    # Туман
    701: 20, 711: 22, 721: 21, 731: 19, 741: 20, 751: 19, 761: 19, 762: 19, 771: 23, 781: 0,
    # Ясно
    800: 32,  # день
    # Облачно
    801: 34, 802: 30, 803: 28, 804: 26,
}

def owm_to_wc(owm_id, is_night=False):
    wc = OWM_TO_WC.get(owm_id, 32)
    if is_night and wc in (32, 34):
        wc = wc - 1  # 32 день→31 ночь, 34 день→33 ночь
    return wc


def c_to_f(c):
    return round(c * 9 / 5 + 32)


def wind_deg_to_dir(deg):
    dirs = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW']
    ix = round(deg / 22.5) % 16
    return dirs[ix]


# ── Поиск города ───────────────────────────────────────────────────
@app.route('/api/city/search')
def city_search():
    query = request.args.get('f', '')
    lang  = request.args.get('lang', 'en')
    
    r = requests.get(f"{OWM_BASE}/geo/1.0/direct", params={
        'q': query, 'limit': 10, 'appid': OWM_API_KEY
    }, timeout=10)
    
    if r.status_code != 200 or not r.json():
        return jsonify({"errno": 1, "data": []})
    
    results = []
    for city in r.json():
        local_name = city.get('local_names', {}).get(lang[:2], city.get('name', ''))
        results.append({
            "g": str(city['lat']),                   # широта
            "s": str(city['lon']),                   # долгота
            "c": f"{city['lat']},{city['lon']}",     # city_code (lat,lon)
            "n": local_name or city['name'],         # название
            "co": city.get('country', ''),           # страна
            "st": city.get('state', ''),             # штат/регион
            "tz": "UTC"
        })
    
    return jsonify({"errno": 0, "data": results})


# ── IP-геолокация ──────────────────────────────────────────────────
@app.route('/api/city/iplocate')
def ip_locate():
    lat = request.args.get('lat', '')
    lng = request.args.get('lng', '')
    lang = request.args.get('locale', 'en-us')[:2]
    
    if lat and lng:
        r = requests.get(f"{OWM_BASE}/geo/1.0/reverse", params={
            'lat': lat, 'lon': lng, 'limit': 1, 'appid': OWM_API_KEY
        }, timeout=10)
        if r.status_code == 200 and r.json():
            city = r.json()[0]
            local_name = city.get('local_names', {}).get(lang, city.get('name', ''))
            return jsonify({"errno": 0, "data": {
                "g": str(city['lat']),
                "s": str(city['lon']),
                "c": f"{city['lat']},{city['lon']}",
                "n": local_name or city['name'],
                "co": city.get('country', ''),
                "tz": "UTC"
            }})
    
    return jsonify({"errno": 1, "data": {}})


# ── Прогноз погоды ─────────────────────────────────────────────────
@app.route('/api/forecasts')
def forecasts():
    """
    Параметр f= — city_code в формате "lat,lon" который мы вернули в поиске.
    """
    city_code = request.args.get('f', '')
    lang = request.args.get('lang', 'en')
    units_param = request.args.get('u', 'm')  # m=metric, e=imperial
    units = 'metric' if units_param == 'm' else 'imperial'
    
    # Парсим lat/lon из city_code
    try:
        lat, lon = city_code.split(',')
        lat, lon = float(lat), float(lon)
    except Exception:
        return jsonify({"errno": 2, "data": {}})
    
    # OWM One Call API 3.0 (или 2.5)
    r = requests.get(f"{OWM_BASE}/data/2.5/forecast", params={
        'lat': lat, 'lon': lon, 'appid': OWM_API_KEY,
        'units': units, 'lang': lang[:2], 'cnt': 40
    }, timeout=10)
    
    r_curr = requests.get(f"{OWM_BASE}/data/2.5/weather", params={
        'lat': lat, 'lon': lon, 'appid': OWM_API_KEY,
        'units': units, 'lang': lang[:2]
    }, timeout=10)
    
    if r.status_code != 200 or r_curr.status_code != 200:
        return jsonify({"errno": 3, "data": {}})
    
    forecast_data = r.json()
    curr_data = r_curr.json()
    
    curr_weather = curr_data['weather'][0]
    curr_main = curr_data['main']
    curr_wind = curr_data.get('wind', {})
    curr_sys = curr_data.get('sys', {})
    
    now_ts = datetime.now(timezone.utc).timestamp()
    is_night = not (curr_sys.get('sunrise', 0) < now_ts < curr_sys.get('sunset', 1e12))
    
    # ── Текущая погода (td) ────────────────────────────────────────
    td = {
        "wc":  owm_to_wc(curr_weather['id'], is_night),
        "tn":  round(curr_main['temp']),
        "th":  round(curr_main['temp_max']),
        "tl":  round(curr_main['temp_min']),
        "fl":  round(curr_main.get('feels_like', curr_main['temp'])),
        "rh":  curr_main['humidity'],
        "wd":  wind_deg_to_dir(curr_wind.get('deg', 0)),
        "ws":  round(curr_wind.get('speed', 0) * 3.6),     # m/s → kph
        "kph": round(curr_wind.get('speed', 0) * 3.6),
        "mph": round(curr_wind.get('speed', 0) * 2.237),
        "p_mb": curr_main.get('pressure', 1013),
        "v_km": round(curr_data.get('visibility', 10000) / 1000),
        "up":  "",
        "aqi": -1,
        "date": datetime.fromtimestamp(curr_data['dt'], timezone.utc).strftime('%Y%m%d %H:%M'),
    }
    
    # ── Дневной прогноз (forecast) — агрегируем по дням ────────────
    daily = {}
    for item in forecast_data.get('list', []):
        day_key = datetime.fromtimestamp(item['dt'], timezone.utc).strftime('%Y%m%d')
        if day_key not in daily:
            daily[day_key] = {
                'temps': [], 'min': [], 'max': [],
                'wc': item['weather'][0]['id'],
                'ws': [], 'rh': [], 'pop': [],
                'date': day_key
            }
        daily[day_key]['temps'].append(item['main']['temp'])
        daily[day_key]['min'].append(item['main']['temp_min'])
        daily[day_key]['max'].append(item['main']['temp_max'])
        daily[day_key]['ws'].append(item.get('wind', {}).get('speed', 0))
        daily[day_key]['rh'].append(item['main']['humidity'])
        daily[day_key]['pop'].append(item.get('pop', 0) * 100)
    
    forecast_list = []
    for day_key in sorted(daily.keys())[:10]:
        d = daily[day_key]
        avg_temp = sum(d['temps']) / len(d['temps'])
        is_night_day = (avg_temp < sum(d['min']) / len(d['min']) + 2)
        forecast_list.append({
            "date":  day_key + " 12:00",
            "wc":    owm_to_wc(d['wc']),
            "wctd":  owm_to_wc(d['wc']),
            "tn":    round(avg_temp),
            "tl":    round(min(d['min'])),
            "th":    round(max(d['max'])),
            "fl":    round(avg_temp - 2),
            "rh":    round(sum(d['rh']) / len(d['rh'])),
            "kph":   round(sum(d['ws']) / len(d['ws']) * 3.6),
            "mph":   round(sum(d['ws']) / len(d['ws']) * 2.237),
            "wd":    "N",
            "p_mb":  1013,
            "v_km":  10,
            "aqi":   -1,
            "up":    "",
        })
    
    # ── Почасовой прогноз (hourly_forecast) ───────────────────────
    hourly_list = []
    for item in forecast_data.get('list', [])[:24]:
        hourly_list.append({
            "wc":   owm_to_wc(item['weather'][0]['id']),
            "tm":   round(item['main']['temp']),
            "tm_f": c_to_f(item['main']['temp']) if units == 'metric' else round(item['main']['temp']),
            "uvi":  0,
            "wd":   wind_deg_to_dir(item.get('wind', {}).get('deg', 0)),
            "ws":   round(item.get('wind', {}).get('speed', 0) * 3.6),
            "pop":  round(item.get('pop', 0) * 100),
        })
    
    # ── Восход/закат (sun_phase) ───────────────────────────────────
    sunrise = datetime.fromtimestamp(curr_sys.get('sunrise', 0), timezone.utc)
    sunset  = datetime.fromtimestamp(curr_sys.get('sunset', 0), timezone.utc)
    sun_phase = {
        "sr": sunrise.strftime('%H:%M'),
        "ss": sunset.strftime('%H:%M'),
    }
    
    response = {
        "errno": 0,
        "data": {
            "rc": 0,
            "td": td,
            "forecast": forecast_list,
            "hourly_forecast": hourly_list,
            "sun_phase": sun_phase,
            "alert_list": [],
            "uvi": 0
        }
    }
    
    return jsonify(response)



# ── Короткие алиасы (для бинарного патча DEX — экономия символов) ─
@app.route('/f')       # /f?  → /api/forecasts?
def forecasts_alias():
    return forecasts()

@app.route('/cs')      # /cs? → /api/city/search?
def city_search_alias():
    return city_search()

@app.route('/w/il')    # /w/il?locale= → /api/city/iplocate?locale=
def iplocate_alias():
    return ip_locate()


# ── Числовые алиасы /w/1-5 (DEX-совместимые, алфавитный порядок) ─
@app.route('/w/1')
def w1(): return city_search()   # city/search .com

@app.route('/w/2')
def w2(): return forecasts()     # forecasts .com

@app.route('/w/3')
def w3(): return city_search()   # city/search .net

@app.route('/w/4')
def w4(): return forecasts()     # forecasts .net

@app.route('/w/5')
def w5(): return ip_locate()     # iplocate

# ── Health check ────────────────────────────────────────────────────

# ── TWC /v3/location/point (reverse geocode by lat,lon) ──────────
@app.route('/v3/location/point')
def location_point():
    if not OWM_API_KEY or OWM_API_KEY == "YOUR_OPENWEATHERMAP_API_KEY":
        return jsonify({"errno": 99, "msg": "OWM_API_KEY not set"}), 500

    geocode = request.args.get('geocode', '')
    lang = request.args.get('language', 'en-US')[:2]

    try:
        lat, lon = map(float, geocode.split(','))
    except Exception:
        return jsonify({"errors": [{"error": {"code": "NDF-0001"}}]}), 404

    r = requests.get(f"{OWM_BASE}/geo/1.0/reverse",
                     params={'lat': lat, 'lon': lon,
                             'limit': 1, 'appid': OWM_API_KEY},
                     timeout=10)

    if r.status_code != 200 or not r.json():
        return jsonify({"errors": [{"error": {"code": "NDF-0001"}}]}), 404

    c = r.json()[0]
    name = c.get('local_names', {}).get(lang, c.get('name', ''))

    return jsonify({"location": {
        "address":      name or c['name'],
        "adminDistrict": c.get('state', ''),
        "city":          name or c['name'],
        "country":       c.get('country', ''),
        "countryCode":   c.get('country', ''),
        "latitude":      c['lat'],
        "longitude":     c['lon'],
        "locale":        {"locale1": lang}
    }})


@app.route('/')
def index():
    return jsonify({
        "status": "ok",
        "info": "KSmobile Weather Proxy — powered by OpenWeatherMap",
        "endpoints": [
            "/api/city/search?f=Москва&lang=ru",
            "/api/forecasts?f=55.75,37.61&lang=ru&u=m",
            "/api/city/iplocate?lat=55.75&lng=37.61&locale=ru-ru"
        ]
    })


if __name__ == '__main__':
    print(f"""
╔══════════════════════════════════════════════════════════╗
║     Weather Proxy for KSmobile/CM Launcher               ║
╠══════════════════════════════════════════════════════════╣
║  Сервер запущен: http://{HOST}:{PORT}
║                                                          ║
║  Убедитесь что OWM_API_KEY задан!                        ║
║  Получить ключ: https://openweathermap.org/api           ║
║                                                          ║
║  Для патча APK замените в smali:                         ║
║    weather.ksmobile.com  →  <IP_вашего_сервера>:{PORT}   ║
║    weather.ksmobile.net  →  <IP_вашего_сервера>:{PORT}   ║
╚══════════════════════════════════════════════════════════╝
""")
    if OWM_API_KEY == "8ab40a42ab7e2af856bb54cc3d9da233":
        print("⚠️  ВНИМАНИЕ: Задайте OWM_API_KEY в файле перед запуском!")
    
    app.run(host=HOST, port=PORT, debug=False)
