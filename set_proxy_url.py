#!/usr/bin/env python3
"""
set_proxy_url.py — Вшивает URL вашего прокси-сервера в пропатченный APK

Использование:
  python set_proxy_url.py launcher_weather_patched.apk https://wx.onrender.com

Получите файл:
  launcher_weather_patched_READY.apk  ← устанавливайте на телефон
"""

import sys, os, shutil, struct, zlib, hashlib, zipfile, subprocess, tempfile

# Текущий плейсхолдер в APK (вшит при сборке)
PLACEHOLDER_BASE = b"http://10.0.0.1:5000"

# Максимально допустимые длины URL для каждого эндпоинта (из оригинального DEX)
MAX_LENGTHS = {
    b"/api/forecasts?":          43,
    b"/api/city/search?":        45,
    b"/api/city/iplocate?locale=": 56,
}

def fix_dex_checksums(data: bytearray):
    adler = zlib.adler32(bytes(data[12:])) & 0xFFFFFFFF
    struct.pack_into('<I', data, 8, adler)
    data[12:32] = hashlib.sha1(bytes(data[32:])).digest()

def patch_dex(dex_data: bytes, new_base: str) -> bytes:
    data = bytearray(dex_data)
    new_base_b = new_base.rstrip('/').encode()
    replaced = 0

    for path_suffix, max_len in MAX_LENGTHS.items():
        old_full = PLACEHOLDER_BASE + path_suffix
        new_full = new_base_b + path_suffix

        if len(new_full) > max_len:
            print(f"\n  ✗ ОШИБКА: URL слишком длинный!")
            print(f"    {new_full.decode()}")
            print(f"    Длина: {len(new_full)}, максимум: {max_len}")
            print(f"    Используйте более короткий домен (например: wx.onrender.com)")
            sys.exit(1)

        # Find and patch all occurrences
        pos = 0
        while True:
            idx = data.find(old_full, pos)
            if idx == -1:
                break

            old_len = len(old_full)
            new_len = len(new_full)

            # Update ULEB128 length byte before the string
            data[idx - 1] = new_len

            # Write new URL
            data[idx:idx + new_len] = new_full

            # Null-terminate and zero-pad remaining space
            data[idx + new_len] = 0
            for i in range(idx + new_len + 1, idx + old_len + 1):
                data[i] = 0

            print(f"  ✓ {old_full.decode()}")
            print(f"    → {new_full.decode()}")
            replaced += 1
            pos = idx + new_len

    if replaced == 0:
        print("\n  ⚠ Плейсхолдер не найден в DEX.")
        print("  Возможно APK уже пропатчен другим URL.")
        return bytes(dex_data)

    fix_dex_checksums(data)
    print(f"\n  Всего замен: {replaced} | Контрольные суммы обновлены")
    return bytes(data)


def repack_and_sign(apk_in: str, apk_out: str, dex_patched: bytes):
    # Write temp APK
    tmp = apk_out + ".tmp"
    shutil.copy2(apk_in, tmp)

    # Replace classes.dex
    with zipfile.ZipFile(tmp, 'a') as z:
        z.writestr('classes.dex', dex_patched)

    # Remove old signature
    subprocess.run(['zip', '-q', '-d', tmp, 'META-INF/*'],
                   capture_output=True)

    # Zipalign
    aligned = apk_out + ".aligned"
    r = subprocess.run(['zipalign', '-f', '4', tmp, aligned],
                       capture_output=True)
    src = aligned if r.returncode == 0 else tmp

    # Create keystore if missing
    ks = os.path.join(os.path.dirname(apk_in), 'patch.jks')
    if not os.path.exists(ks):
        print("\n▶ Создаём ключ подписи...")
        subprocess.run([
            'keytool', '-genkey', '-v',
            '-keystore', ks, '-alias', 'key0',
            '-keyalg', 'RSA', '-keysize', '2048',
            '-validity', '10000',
            '-dname', 'CN=WeatherPatch,O=Patch,C=RU',
            '-storepass', 'patch123', '-keypass', 'patch123'
        ], capture_output=True)

    # Sign
    print("▶ Подписываем APK...")
    r = subprocess.run([
        'apksigner', 'sign',
        '--ks', ks,
        '--ks-pass', 'pass:patch123',
        '--key-pass', 'pass:patch123',
        '--ks-key-alias', 'key0',
        '--out', apk_out,
        src
    ], capture_output=True, text=True)

    if r.returncode != 0:
        # Fallback: jarsigner
        subprocess.run([
            'jarsigner', '-keystore', ks,
            '-storepass', 'patch123', '-keypass', 'patch123',
            src, 'key0'
        ], capture_output=True)
        shutil.move(src, apk_out)

    # Cleanup
    for f in [tmp, aligned]:
        if os.path.exists(f):
            os.remove(f)


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)

    apk_in   = sys.argv[1]
    new_url  = sys.argv[2].rstrip('/')
    apk_out  = apk_in.replace('.apk', '_READY.apk')

    if not os.path.exists(apk_in):
        print(f"Файл не найден: {apk_in}")
        sys.exit(1)

    print(f"╔══════════════════════════════════════════════╗")
    print(f"║  APK Weather URL Patcher                     ║")
    print(f"╚══════════════════════════════════════════════╝")
    print(f"\n▶ APK: {apk_in}")
    print(f"▶ Новый URL прокси: {new_url}")
    print(f"\n▶ Патчим DEX...")

    with zipfile.ZipFile(apk_in, 'r') as z:
        dex_original = z.read('classes.dex')

    dex_patched = patch_dex(dex_original, new_url)

    print(f"\n▶ Пересобираем и подписываем APK...")
    repack_and_sign(apk_in, apk_out, dex_patched)

    print(f"\n╔══════════════════════════════════════════════╗")
    print(f"║  ✅ Готово!                                  ║")
    print(f"║  Файл: {apk_out:<37}║")
    print(f"╚══════════════════════════════════════════════╝")
    print(f"\nУстановите APK на телефон:")
    print(f"  adb install -r \"{apk_out}\"")

if __name__ == '__main__':
    main()
