[app]
title = 峻爸副駕
package.name = junbacopilot
package.domain = org.b52tw
source.dir = .
source.include_exts = py,png,jpg,kv,atlas,json,dat
version = 4.1.0

# 核心 Python 依賴庫
requirements = python3,kivy,pyjnius,plyer,requests,urllib3,google-genai,certifi

# 宣告全域懸浮窗、定位與錄音權限
android.permissions = INTERNET,ACCESS_FINE_LOCATION,ACCESS_COARSE_LOCATION,RECORD_AUDIO,SYSTEM_ALERT_WINDOW

# 鎖定穩定編譯版本
android.api = 34
android.minapi = 26
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a
android.allow_backup = True
orientation = portrait

[buildozer]
log_level = 2
warn_on_root = 1
