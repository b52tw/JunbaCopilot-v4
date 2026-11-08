import os
import re
import json
import math
import time
import base64
import platform
import threading
import urllib.parse
import webbrowser
import requests

from kivy.app import App
from kivy.core.text import LabelBase
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.gridlayout import GridLayout
from kivy.uix.button import Button
from kivy.uix.label import Label
from kivy.uix.popup import Popup
from kivy.uix.spinner import Spinner
from kivy.uix.textinput import TextInput
from kivy.clock import mainthread, Clock
from kivy.core.window import Window

IS_ANDROID = platform.system() == "Linux" and "ANDROID_STORAGE" in os.environ

# 【關鍵修正 1】自動偵測並註冊 Android 系統內建中文字型，徹底解決方框亂碼
def register_chinese_font():
    font_candidates = [
        "/system/fonts/NotoSansCJK-Regular.ttc",
        "/system/fonts/NotoSansTC-Regular.otf",
        "/system/fonts/DroidSansFallback.ttf",
        "C:/Windows/Fonts/msjh.ttc"
    ]
    for fp in font_candidates:
        if os.path.exists(fp):
            try:
                LabelBase.register(name="Roboto", fn_regular=fp, fn_bold=fp)
                print(f"[Font] 成功載入中文字型: {fp}")
                break
            except Exception as e:
                print(f"[Font Error]: {e}")

register_chinese_font()

def haversine_km(lat1, lon1, lat2, lon2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    return round(R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 2)

class POIManager:
    def __init__(self, filepath="custom_poi.json"):
        self.filepath = filepath
        self.poi_list = self.load_pois()

    def load_pois(self):
        if os.path.exists(self.filepath):
            try:
                with open(self.filepath, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as e:
                print(f"[POI Load Error]: {e}")
        return []

    def get_nearby_pois(self, current_lat, current_lng, radius_km=None):
        results = []
        for p in self.poi_list:
            lat, lng = p.get("lat"), p.get("lng")
            if lat and lng:
                dist = haversine_km(current_lat, current_lng, lat, lng)
                if radius_km is None or dist <= radius_km:
                    item = p.copy()
                    item["distance_km"] = dist
                    results.append(item)
        results.sort(key=lambda x: x["distance_km"])
        return results

class WakeWordParser:
    HOMOPHONE_MAP = {
        "富": "副", "復": "副", "付": "副", "負": "副", "附": "副", "覆": "副",
        "架": "駕", "價": "駕", "家": "駕", "假": "駕", "嫁": "駕", "稼": "駕"
    }
    WAKE_PATTERNS = [
        r"副駕[，、\s]*副駕",
        r"峻爸[，、\s]*副駕",
        r"(?:嘿|哈囉|小)[，、\s]*副駕",
        r"副駕[，、\s]*(?:請回答|在嗎|幫忙)",
    ]

    @classmethod
    def normalize_text(cls, text: str) -> str:
        t = text
        for wrong, right in cls.HOMOPHONE_MAP.items():
            t = t.replace(wrong, right)
        return t

    @classmethod
    def extract_command(cls, raw_text: str):
        normalized = cls.normalize_text(raw_text)
        for pattern in cls.WAKE_PATTERNS:
            match = re.search(pattern, normalized)
            if match:
                end_pos = match.end()
                command = raw_text[end_pos:].strip(" ，、。！？\t\n")
                return True, command

        if (normalized.count("副") + normalized.count("駕")) >= 2:
            last_idx = max(raw_text.rfind("駕"), raw_text.rfind("架"), raw_text.rfind("價"))
            if last_idx != -1 and last_idx + 1 < len(raw_text):
                return True, raw_text[last_idx + 1:].strip(" ，、。！？\t\n")
            return True, ""

        return False, ""

class AndroidBridge:
    def __init__(self, on_speech_result_callback, on_nav_detected_callback):
        self.tts = None
        self.on_speech_result = on_speech_result_callback
        self.on_nav_detected = on_nav_detected_callback
        if IS_ANDROID:
            self._init_android()

    def _init_android(self):
        try:
            from jnius import autoclass
            from android import activity
            from android.permissions import request_permissions, Permission
            request_permissions([
                Permission.ACCESS_FINE_LOCATION,
                Permission.ACCESS_COARSE_LOCATION,
                Permission.RECORD_AUDIO
            ])
            # 【關鍵修正 2】綁定語音辨識回傳結果的監聽器
            activity.bind(on_activity_result=self._on_activity_result)

            Locale = autoclass('java.util.Locale')
            TextToSpeech = autoclass('android.speech.tts.TextToSpeech')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            self.tts = TextToSpeech(PythonActivity.mActivity, None)
            self.tts.setLanguage(Locale.TAIWAN)
        except Exception as e:
            print(f"[AndroidBridge] Init Error: {e}")

    def _on_activity_result(self, request_code, result_code, intent):
        """接收 Google 語音辨識視窗回傳的文字"""
        if request_code == 0x100:
            if result_code == -1 and intent is not None: # RESULT_OK = -1
                try:
                    from jnius import autoclass
                    RecognizerIntent = autoclass('android.speech.RecognizerIntent')
                    results = intent.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)
                    if results and results.size() > 0:
                        recognized_text = results.get(0)
                        Clock.schedule_once(lambda dt: self.on_speech_result(recognized_text), 0)
                        return
                except Exception as e:
                    print(f"[STT Result Error]: {e}")
            Clock.schedule_once(lambda dt: self.on_speech_result(""), 0)

    def speak(self, text: str):
        if IS_ANDROID and self.tts:
            try:
                from jnius import autoclass
                TextToSpeech = autoclass('android.speech.tts.TextToSpeech')
                self.tts.speak(text, TextToSpeech.QUEUE_FLUSH, None, "copilot_tts")
            except Exception as e:
                print(f"[TTS Error]: {e}")
        else:
            print(f"[語音播報]: {text}")

    def stop_speaking(self
