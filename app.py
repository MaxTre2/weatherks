"""
Weather Proxy Server for KSmobile/CM Launcher
Использует OpenWeatherMap вместо мёртвых серверов ksmobile.com
"""

from flask import Flask, request, jsonify
import requests
from datetime import datetime, timezone
import os

app = Flask(__name__)

OWM_API_KEY = os.environ.get("OWM_API_KEY", "")
OWM_BASE = "https://api.openweathermap.org"

# Коды погоды OpenWeatherMap → Weather Channel (TWC)
OWM_TO_WC = {
    200:4,  201:4,  202:4,  210:4,  211:4,  212:4,  221:4,  230:4,  231:4,  232:4,
    300:9,  301:9,  302:9,  310:9,  311:9,  312:9,  313:9,  314:9,  321:9,
    500:11, 501:12, 502:12, 503:12, 504:12, 511:10, 520:40, 521:40, 522:40,
    600:14, 601:16, 602:41, 611:6,  612:6,  613:6,  615:5,  616:5,
    620:14, 621:16, 622:41,
    701:20, 711:22, 721:21, 731:19, 741:20, 751:19, 761:19, 762:19, 771:23, 781:0,
    800:32, 801:34, 802:30, 803:28, 804:26,
}

def owm_to_wc(owm_id, is_night=False):
    wc = OWM_TO_WC.get(owm_id, 32)
    if is_night and wc in (32, 34):
        wc -= 1
    return wc

def wind_dir(deg):
    dirs = ['N','NNE','NE','ENE','E','ESE','SE','SSE',
            'S','SSW','SW','WSW','W','WNW','NW','NNW']
    return dirs[round(deg / 22.5) % 16]

def check_key():
    if not OWM_API_KEY:
        return jsonify({"errno": 99, "msg": "OWM_API_KEY not set"}), 500
    return None


# ── Поиск города ─────────────────────────────────────────────────
@app.route('/api/city/search')
def city_search():
    err = check_key()
    if err: return err

    query = request.args.get('f', '')
    lang  = request.args.get('lang', 'en')[:2]

    r = requests.get(f"{OWM_BASE}/geo/1.0/direct",
                     params={'q': query, 'limit': 10, 'appid': OWM_API_KEY},
                     timeout=10)

    if r.status_code != 200 or not r.json():
        return jsonify({"errno": 1, "data": []})

    results = []
    for c in r.json():
        name = c.get('local_names', {}).get(lang, c.get('name', ''))
        results.append({
            "g":  str(c['lat']),
            "s":  str(c['lon']),
            "c":  f"{c['lat']},{c['lon']}",
            "n":  name or c['name'],
            "co": c.get('country', ''),
            "st": c.get('state', ''),
            "tz": "UTC"
        })

    return jsonify({"errno": 0, "data": results})


# ── IP-геолокация ─────────────────────────────────────────────────
@app.route('/api/city/iplocate')
def ip_locate():
    err = check_key()
    if err: return err

    lat  = request.args.get('lat', '')
    lng  = request.args.get('lng', '')
    lang = request.args.get('locale', 'en')[:2]

    if lat and lng:
        r = requests.get(f"{OWM_BASE}/geo/1.0/reverse",
                         params={'lat': lat, 'lon': lng,
                                 'limit': 1, 'appid': OWM_API_KEY},
                         timeout=10)
        if r.status_code == 200 and r.json():
            c = r.json()[0]
            name = c.get('local_names', {}).get(lang, c.get('name', ''))
            return jsonify({"errno": 0, "data": {
                "g":  str(c['lat']),
                "s":  str(c['lon']),
                "c":  f"{c['lat']},{c['lon']}",
                "n":  name or c['name'],
                "co": c.get('country', ''),
                "tz": "UTC"
            }})

    return jsonify({"errno": 1, "data": {}})


# ── Прогноз погоды ────────────────────────────────────────────────
@app.route('/api/forecasts')
def forecasts():
    err = check_key()
    if err: return err

    city_code = request.args.get('f', '')
    lang      = request.args.get('lang', 'en')[:2]
    units     = 'metric' if request.args.get('u', 'm') == 'm' else 'imperial'

    try:
        lat, lon = map(float, city_code.split(','))
    except Exception:
        return jsonify({"errno": 2, "data": {}})

    params = {'lat': lat, 'lon': lon, 'appid': OWM_API_KEY,
              'units': units, 'lang': lang}

    r_curr = requests.get(f"{OWM_BASE}/data/2.5/weather",
                          params=params, timeout=10)
    r_fore = requests.get(f"{OWM_BASE}/data/2.5/forecast",
                          params={**params, 'cnt': 40}, timeout=10)

    if r_curr.status_code != 200 or r_fore.status_code != 200:
        return jsonify({"errno": 3, "data": {}})

    curr = r_curr.json()
    fore = r_fore.json()

    sys_  = curr.get('sys', {})
    main  = curr['main']
    wind  = curr.get('wind', {})
    now   = datetime.now(timezone.utc).timestamp()
    night = not (sys_.get('sunrise', 0) < now < sys_.get('sunset', 1e12))

    # Текущая погода
    td = {
        "wc":   owm_to_wc(curr['weather'][0]['id'], night),
        "tn":   round(main['temp']),
        "th":   round(main['temp_max']),
        "tl":   round(main['temp_min']),
        "fl":   round(main.get('feels_like', main['temp'])),
        "rh":   main['humidity'],
        "wd":   wind_dir(wind.get('deg', 0)),
        "kph":  round(wind.get('speed', 0) * 3.6),
        "mph":  round(wind.get('speed', 0) * 2.237),
        "p_mb": main.get('pressure', 1013),
        "v_km": round(curr.get('visibility', 10000) / 1000),
        "aqi":  -1,
        "up":   "",
        "date": datetime.fromtimestamp(curr['dt'], timezone.utc)
                        .strftime('%Y%m%d %H:%M'),
    }

    # Дневной прогноз (агрегация по дням)
    daily = {}
    for item in fore.get('list', []):
        day = datetime.fromtimestamp(item['dt'], timezone.utc).strftime('%Y%m%d')
        if day not in daily:
            daily[day] = {'t':[], 'mn':[], 'mx':[], 'wc':item['weather'][0]['id'],
                          'ws':[], 'rh':[], 'date': day}
        d = daily[day]
        d['t'].append(item['main']['temp'])
        d['mn'].append(item['main']['temp_min'])
        d['mx'].append(item['main']['temp_max'])
        d['ws'].append(item.get('wind', {}).get('speed', 0))
        d['rh'].append(item['main']['humidity'])

    forecast = []
    for day in sorted(daily)[:10]:
        d = daily[day]
        avg = sum(d['t']) / len(d['t'])
        forecast.append({
            "date":  d['date'] + " 12:00",
            "wc":    owm_to_wc(d['wc']),
            "wctd":  owm_to_wc(d['wc']),
            "tn":    round(avg),
            "tl":    round(min(d['mn'])),
            "th":    round(max(d['mx'])),
            "fl":    round(avg - 2),
            "rh":    round(sum(d['rh']) / len(d['rh'])),
            "kph":   round(sum(d['ws']) / len(d['ws']) * 3.6),
            "mph":   round(sum(d['ws']) / len(d['ws']) * 2.237),
            "wd":    "N", "p_mb": 1013, "v_km": 10, "aqi": -1, "up": "",
        })

    # Почасовой прогноз
    hourly = []
    for item in fore.get('list', [])[:24]:
        hourly.append({
            "wc":   owm_to_wc(item['weather'][0]['id']),
            "tm":   round(item['main']['temp']),
            "tm_f": round(item['main']['temp'] * 9/5 + 32),
            "uvi":  0,
            "wd":   wind_dir(item.get('wind', {}).get('deg', 0)),
            "ws":   round(item.get('wind', {}).get('speed', 0) * 3.6),
            "pop":  round(item.get('pop', 0) * 100),
        })

    # Восход/закат
    sunrise = datetime.fromtimestamp(sys_.get('sunrise', 0), timezone.utc)
    sunset  = datetime.fromtimestamp(sys_.get('sunset', 0), timezone.utc)

    return jsonify({"errno": 0, "data": {
        "rc": 0,
        "td": td,
        "forecast": forecast,
        "hourly_forecast": hourly,
        "sun_phase": {"sr": sunrise.strftime('%H:%M'),
                      "ss": sunset.strftime('%H:%M')},
        "alert_list": [],
        "uvi": 0
    }})


@app.route('/')
def index():
    return jsonify({
        "status": "ok",
        "key_set": bool(OWM_API_KEY),
        "endpoints": ["/api/city/search", "/api/forecasts", "/api/city/iplocate"]
    })


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
