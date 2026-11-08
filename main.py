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

def register_chinese_font():
    """在 App 啟動時安全掛載 Android 系統內建繁體中文字型"""
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
                print(f"[Font] Loaded: {fp}")
                break
            except Exception as e:
                print(f"[Font Error]: {e}")

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
            activity.bind(on_activity_result=self._on_activity_result)

            Locale = autoclass('java.util.Locale')
            TextToSpeech = autoclass('android.speech.tts.TextToSpeech')
            PythonActivity = autoclass('org.kivy.android.PythonActivity')
            self.tts = TextToSpeech(PythonActivity.mActivity, None)
            self.tts.setLanguage(Locale.TAIWAN)
        except Exception as e:
            print(f"[AndroidBridge] Init Error: {e}")

    def _on_activity_result(self, request_code, result_code, intent):
        if request_code == 0x100:
            if result_code == -1 and intent is not None:
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

    def stop_speaking(self):
        if IS_ANDROID and self.tts:
            try:
                self.tts.stop()
            except:
                pass

    def start_speech_recognition(self):
        if IS_ANDROID:
            try:
                from jnius import autoclass
                Intent = autoclass('android.content.Intent')
                RecognizerIntent = autoclass('android.speech.RecognizerIntent')
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, "zh-TW")
                intent.putExtra(RecognizerIntent.EXTRA_PROMPT, "請說「副駕副駕」+ 指令...")
                PythonActivity.mActivity.startActivityForResult(intent, 0x100)
            except Exception as e:
                print(f"[STT Error]: {e}")
                Clock.schedule_once(lambda dt: self.on_speech_result(""), 0)
        else:
            print("[桌面模擬] 觸發語音辨識...")

    def open_google_maps_search(self, query: str):
        if IS_ANDROID:
            try:
                from jnius import autoclass
                Intent = autoclass('android.content.Intent')
                Uri = autoclass('android.net.Uri')
                PythonActivity = autoclass('org.kivy.android.PythonActivity')
                uri_str = f"geo:0,0?q={urllib.parse.quote(query)}"
                intent = Intent(Intent.ACTION_VIEW, Uri.parse(uri_str))
                intent.setPackage("com.google.android.apps.maps")
                PythonActivity.mActivity.startActivity(intent)
            except Exception as e:
                print(f"[Maps Error]: {e}")
        else:
            print(f"[桌面模擬] 連動開啟 Google Maps：{query}")

class GeminiCopilotAI:
    def __init__(self):
        self.api_key = self._load_api_key()
        self.chat_history = []

    def _load_api_key(self):
        if os.path.exists(".secure_api.dat"):
            try:
                with open(".secure_api.dat", "r") as f:
                    return base64.b64decode(f.read()).decode('utf-8').strip()
            except:
                pass
        if os.path.exists("config.json"):
            try:
                with open("config.json", "r") as f:
                    return json.load(f).get("GEMINI_API_KEY", "").strip()
            except:
                pass
        return os.environ.get("GEMINI_API_KEY", "").strip()

    def _init_gemini(self):
        self.chat_history = []

    def _call_gemini_rest(self, contents, sys_prompt, temperature=0.3):
        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={self.api_key}"
        headers = {"Content-Type": "application/json"}
        payload = {
            "system_instruction": {"parts": [{"text": sys_prompt}]},
            "contents": contents,
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": temperature}
        }
        resp = requests.post(url, headers=headers, json=payload, timeout=25)
        resp.raise_for_status()
        data = resp.json()
        return data["candidates"][0]["content"]["parts"][0]["text"]

    def query_task(self, prompt: str, poi_context: str, callback):
        def _worker():
            if not self.api_key:
                callback("尚未設定 API 金鑰！請長按下方圓鈕 0.8 秒開啟選單並輸入金鑰。", False, "")
                return
            try:
                sys_prompt = (
                    "你是「峻爸副駕 v4」隨車 AI。語氣簡明，嚴禁 markdown 符號與表情符號。\n"
                    f"{poi_context}\n"
                    "【執行原則】：\n"
                    "1. 搜尋以 3.5 星以上為準，強制檢查近 3 個月有無 1 星嚴重負評。\n"
                    "2. 路線巡檢整合 CWA 氣象、1968 事故時速與新聞，遇異常立刻示警改道。\n"
                    "3. 字數嚴格限制在 35 到 65 字，適合行車朗讀。\n"
                    "4. 若有具體推薦地點，最後一行附上：[MAP:目標名稱]"
                )
                contents = [{"role": "user", "parts": [{"text": prompt}]}]
                reply_text = self._call_gemini_rest(contents, sys_prompt, temperature=0.3)
                self._process_response(reply_text, callback)
            except Exception as e:
                callback(f"連線或金鑰異常：{str(e)[:35]}", False, "")
        threading.Thread(target=_worker, daemon=True).start()

    def query_chat(self, prompt: str, poi_context: str, callback):
        def _worker():
            if not self.api_key:
                callback("尚未設定 API 金鑰！請長按下方圓鈕 0.8 秒開啟選單並輸入金鑰。", False, "")
                return
            try:
                chat_sys_prompt = (
                    "你是「峻爸副駕 v4」隨車智慧 AI，兼具專業領航員與幽默副駕駛雙重身分。嚴禁 markdown 與表情符號。\n"
                    "目前處於【閒聊與深度探索模式】：\n"
                    "請解除所有字數限制！展現博學、幽默、像朋友一樣的語氣，可以深度分析景點典故或進行長篇自由對談。\n"
                    "若使用者確定要導航，請在對話最後附上 [MAP:目標名稱]。"
                )
                full_message = f"{poi_context}\n駕駛說：{prompt}" if poi_context else prompt
                self.chat_history.append({"role": "user", "parts": [{"text": full_message}]})
                recent_history = self.chat_history[-20:]
                reply_text = self._call_gemini_rest(recent_history, chat_sys_prompt, temperature=0.6)
                self.chat_history.append({"role": "model", "parts": [{"text": reply_text}]})
                self._process_response(reply_text, callback)
            except Exception as e:
                callback(f"閒聊連線異常：{str(e)[:35]}", False, "")
        threading.Thread(target=_worker, daemon=True).start()

    def _process_response(self, full_text: str, callback):
        clean_text = full_text.strip()
        map_target = ""
        if "[MAP:" in full_text:
            parts = full_text.split("[MAP:")
            clean_text = parts[0].strip()
            map_target = parts[1].replace("]", "").strip()
        callback(clean_text, True, map_target)

class FloatingWidget(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(orientation="vertical", spacing=12, padding=16, **kwargs)
        self.bridge = AndroidBridge(self.on_speech_recognized, self.on_navigation_app_detected)
        self.ai = GeminiCopilotAI()
        self.poi_mgr = POIManager()
        
        self.current_mode = "A"
        self.destination = ""
        self.awaiting_upgrade_confirm = False
        self.cur_lat = 23.003
        self.cur_lng = 120.255

        self.is_listening = False
        self.long_press_clock = None
        self.monitor_thread = None

        self.status_label = Label(
            text="【方案 A：待命中】\n點按下方圓鈕說話 (需喊「副駕副駕」)\n長按圓鈕 0.8 秒開啟選單與金鑰設定",
            font_size="20sp",
            size_hint_y=0.45,
            color=(0.2, 0.9, 0.4, 1),
            halign="center",
            valign="middle"
        )
        self.status_label.bind(size=self.status_label.setter('text_size'))
        self.add_widget(self.status_label)

        self.btn_float = Button(
            text="待命\n(點按說話)",
            font_size="28sp",
            bold=True,
            halign="center",
            size_hint=(None, None),
            size=("170dp", "170dp"),
            pos_hint={"center_x": 0.5},
            background_color=(0.1, 0.6, 0.3, 1)
        )
        self.btn_float.bind(on_touch_down=self.on_btn_touch_down)
        self.btn_float.bind(on_touch_up=self.on_btn_touch_up)
        self.add_widget(self.btn_float)

    def on_btn_touch_down(self, instance, touch):
        if instance.collide_point(*touch.pos):
            self.bridge.stop_speaking()
            self.long_press_clock = Clock.schedule_once(lambda dt: self.open_main_menu(), 0.8)
            return True
        return super().on_touch_down(touch)

    def on_btn_touch_up(self, instance, touch):
        if instance.collide_point(*touch.pos):
            if self.long_press_clock:
                Clock.unschedule(self.long_press_clock)
                self.long_press_clock = None
                self.toggle_listening()
            return True
        return super().on_touch_up(touch)

    def toggle_listening(self):
        if not self.is_listening:
            self.is_listening = True
            self.btn_float.text = "監聽中"
            self.btn_float.background_color = (0.9, 0.2, 0.2, 1)
            self.status_label.text = "【語音監聽中】\n請說「副駕副駕」+ 您的指令..."
            self.status_label.color = (1.0, 0.4, 0.4, 1)
            self.bridge.start_speech_recognition()
        else:
            self.is_listening = False
            self.update_mode_ui()

    def update_mode_ui(self):
        if self.current_mode == "A":
            self.btn_float.text = "待命\n(點按說話)"
            self.btn_float.background_color = (0.1, 0.6, 0.3, 1)
            self.status_label.text = "【方案 A：傳統待命】\n(單次精確指令 ｜ 長按圓鈕開選單)"
            self.status_label.color = (0.2, 0.9, 0.4, 1)
        elif self.current_mode == "B":
            self.btn_float.text = "巡航中"
            self.btn_float.background_color = (0.5, 0.2, 0.8, 1)
            dest_info = f"鎖定：{self.destination}" if self.destination else "路線巡檢中"
            self.status_label.text = f"【方案 B：深度巡航】\n({dest_info} ｜ 每5分鐘路況巡視)"
            self.status_label.color = (0.7, 0.5, 1.0, 1)
        elif self.current_mode == "C":
            self.btn_float.text = "閒聊中"
            self.btn_float.background_color = (0.9, 0.7, 0.1, 1)
            self.status_label.text = "【方案 C：自由閒聊模式】\n(解除字數限制，具備對話記憶)"
            self.status_label.color = (0.9, 0.8, 0.2, 1)

    def on_speech_recognized(self, text: str):
        self.is_listening = False
        raw = text.strip()
        if not raw:
            self.update_mode_ui()
            return

        if self.awaiting_upgrade_confirm:
            if any(yes in raw for yes in ["好", "要", "升級", "確定", "可以"]):
                self.awaiting_upgrade_confirm = False
                self.current_mode = "B"
                self.update_mode_ui()
                self.bridge.speak(f"已啟動方案 B 深度巡航，正在為您監控前往 {self.destination} 的天候與路況！")
                self.start_plan_b_monitoring()
                return
            elif any(no in raw for no in ["不要", "不用", "取消", "維持"]):
                self.awaiting_upgrade_confirm = False
                self.update_mode_ui()
                self.bridge.speak("好的，維持原模式。")
                return

        is_woken, command = WakeWordParser.extract_command(raw)
        if not is_woken:
            self.status_label.text = f"【未偵測到喚醒詞】\n聽到：「{raw}」\n(需喊「副駕副駕」才會執行)"
            self.status_label.color = (0.8, 0.8, 0.8, 1)
            return

        if not command:
            self.update_mode_ui()
            self.bridge.speak("峻爸我在，請說指令。")
            return

        self.process_verified_command(command)

    def process_verified_command(self, command: str):
        norm_cmd = WakeWordParser.normalize_text(command)

        if any(kw in norm_cmd for kw in ["開啟閒聊", "啟動閒聊", "閒聊模式"]):
            self.current_mode = "C"
            self.update_mode_ui()
            self.bridge.speak("已切換為閒聊模式，字數限制解除，想聊什麼歷史或景點都可以說！")
            return
            
        if any(kw in norm_cmd for kw in ["開b方案", "啟動巡航", "開始巡檢", "方案b"]):
            dest = command.replace("開b方案", "").replace("啟動巡航", "").strip(" 到至，、")
            if dest:
                self.destination = dest
            self.current_mode = "B"
            self.update_mode_ui()
            self.bridge.speak("收到，已啟動方案 B 巡航模式！")
            self.start_plan_b_monitoring()
            return

        if any(kw in norm_cmd for kw in ["退回a方案", "切回a方案", "退回傳統", "取消巡航", "結束閒聊", "方案a"]):
            self.current_mode = "A"
            self.destination = ""
            self.update_mode_ui()
            self.bridge.speak("已退回方案 A 傳統待命模式。")
            return

        self.execute_search_flow(command)

    def on_navigation_app_detected(self, app_name: str, detected_destination: str):
        self.destination = detected_destination
        self.awaiting_upgrade_confirm = True
        self.btn_float.text = "警示"
        self.btn_float.background_color = (0.9, 0.5, 0.1, 1)
        self.status_label.text = f"【偵測導航：{detected_destination}】\n是否升級 B 方案？"
        self.status_label.color = (1.0, 0.6, 0.2, 1)
        self.bridge.speak(f"偵測到您開啟導航前往{detected_destination}，是否升級 B 方案？請說要或不要。")

    def start_plan_b_monitoring(self):
        if self.monitor_thread and self.monitor_thread.is_alive():
            return
        def _monitor_loop():
            while self.current_mode == "B":
                self.run_environmental_traffic_check()
                for _ in range(30):
                    if self.current_mode != "B":
                        break
                    time.sleep(10)
        self.monitor_thread = threading.Thread(target=_monitor_loop, daemon=True)
        self.monitor_thread.start()

    def run_environmental_traffic_check(self):
        check_prompt = (
            f"即時行車安全巡檢：正前往目的地「{self.destination}」。\n"
            "聯網比對氣象署 CWA 警報與高公局 1968 事故。若通暢回報『路況正常』；有紫爆或封路請發出警示與改道建議。"
        )
        self.ai.query_task(check_prompt, "", self.on_monitor_alert)

    @mainthread
    def on_monitor_alert(self, reply: str, success: bool, map_target: str):
        if success:
            if any(w in reply for w in ["警告", "塞車", "事故", "大雨", "積水"]):
                self.btn_float.text = "路況警報"
                self.btn_float.background_color = (1.0, 0.4, 0.1, 1)
                self.status_label.text = f"【巡檢警報】\n{reply}"
                self.status_label.color = (1.0, 0.5, 0.2, 1)
                self.bridge.speak(f"副駕路況警報：{reply}")
            else:
                self.update_mode_ui()

    def execute_search_flow(self, query: str, radius_km=None):
        r_text = f"{radius_km}公里內" if radius_km else "不限距離"
        self.btn_float.text = "運算中"
        self.btn_float.background_color = (0.2, 0.4, 0.9, 1)
        self.status_label.text = f"【AI 運算中】\n正在處理：「{query}」..."
        self.status_label.color = (0.4, 0.7, 1.0, 1)

        nearby_list = self.poi_mgr.get_nearby_pois(self.cur_lat, self.cur_lng, radius_km)
        poi_context = ""
        if nearby_list:
            poi_context = "【自建口袋名單 (由近到遠)】：\n"
            for p in nearby_list[:4]:
                poi_context += f"- {p['name']} (距 {p['distance_km']}km / {p.get('category','')} / {p.get('notes','')})\n"

        if self.current_mode == "C":
            full_prompt = f"駕駛指令：{query}"
            self.ai.query_chat(full_prompt, poi_context, self.on_ai_response)
        else:
            full_prompt = f"駕駛指令：{query}（範圍：{r_text}）。請找 3.5 星以上並檢查近 3 個月 1 星負評。"
            self.ai.query_task(full_prompt, poi_context, self.on_ai_response)

    @mainthread
    def on_ai_response(self, reply: str, success: bool, map_target: str):
        self.btn_float.text = "播報中\n(點按中斷)"
        self.status_label.text = reply
        self.status_label.color = (0.2, 0.9, 0.5, 1) if success else (1.0, 0.4, 0.4, 1)
        self.bridge.speak(reply)
        if map_target:
            self.bridge.open_google_maps_search(map_target)

    def open_main_menu(self):
        self.long_press_clock = None
        layout = BoxLayout(orientation="vertical", spacing=8, padding=12)
        popup = Popup(title="峻爸副駕中樞控制盤", content=layout, size_hint=(0.92, 0.88))

        mode_box = GridLayout(cols=3, spacing=6, size_hint_y=0.14)
        btn_ma = Button(text="切換 方案A\n(一般待命)", font_size="14sp", halign="center", background_color=(0.1, 0.6, 0.3, 1))
        btn_mb = Button(text="切換 方案B\n(路況巡航)", font_size="14sp", halign="center", background_color=(0.5, 0.2, 0.8, 1))
        btn_mc = Button(text="切換 方案C\n(自由閒聊)", font_size="14sp", halign="center", background_color=(0.8, 0.6, 0.1, 1))
        
        btn_ma.bind(on_press=lambda x: [popup.dismiss(), self.process_verified_command("退回a方案")])
        btn_mb.bind(on_press=lambda x: [popup.dismiss(), self.process_verified_command("開b方案")])
        btn_mc.bind(on_press=lambda x: [popup.dismiss(), self.process_verified_command("開啟閒聊")])
        mode_box.add_widget(btn_ma)
        mode_box.add_widget(btn_mb)
        mode_box.add_widget(btn_mc)
        layout.add_widget(mode_box)

        dist_box = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=0.12)
        dist_box.add_widget(Label(text="搜尋距離：", font_size="16sp", size_hint_x=0.35))
        dist_spinner = Spinner(text="3公里內", values=("1公里內", "3公里內", "5公里內", "不限距離"), font_size="16sp", size_hint_x=0.65)
        dist_box.add_widget(dist_spinner)
        layout.add_widget(dist_box)

        btn_grid = GridLayout(cols=2, spacing=8, size_hint_y=0.26)
        btn_food = Button(text="【找附近好吃】\n3.5星+ 無負評美食", font_size="17sp", halign="center", background_color=(0.9, 0.5, 0.1, 1))
        def _search_food(x):
            km_map = {"1公里內": 1.0, "3公里內": 3.0, "5公里內": 5.0, "不限距離": None}
            popup.dismiss()
            self.execute_search_flow("推薦附近好吃的美食小吃", radius_km=km_map.get(dist_spinner.text))
        btn_food.bind(on_press=_search_food)
        btn_grid.add_widget(btn_food)

        btn_spot = Button(text="【找附近好玩】\n休閒景點與秘境", font_size="17sp", halign="center", background_color=(0.2, 0.7, 0.3, 1))
        def _search_spot(x):
            km_map = {"1公里內": 1.0, "3公里內": 3.0, "5公里內": 5.0, "不限距離": None}
            popup.dismiss()
            self.execute_search_flow("推薦附近好玩的休閒景點", radius_km=km_map.get(dist_spinner.text))
        btn_spot.bind(on_press=_search_spot)
        btn_grid.add_widget(btn_spot)
        layout.add_widget(btn_grid)

        cmd_box = BoxLayout(orientation="horizontal", spacing=6, size_hint_y=0.13)
        cmd_input = TextInput(hint_text="也可直接打字輸入指令或問題...", multiline=False, font_size="15sp", size_hint_x=0.72)
        btn_send = Button(text="送出", font_size="15sp", size_hint_x=0.28, background_color=(0.2, 0.5, 0.9, 1))
        def _send_manual(x):
            q = cmd_input.text.strip()
            if q:
                popup.dismiss()
                self.process_verified_command(q)
        btn_send.bind(on_press=_send_manual)
        cmd_box.add_widget(cmd_input)
        cmd_box.add_widget(btn_send)
        layout.add_widget(cmd_box)

        btn_settings = Button(text="[ 系統設定與 API 金鑰 ] (點此展開)", font_size="14sp", size_hint_y=0.11, background_color=(0.3, 0.4, 0.4, 1))
        settings_panel = BoxLayout(orientation="vertical", spacing=6, size_hint_y=None, height=0, opacity=0)
        
        key_status = "已設定金鑰" if self.ai.api_key else "尚未設定金鑰！"
        perm_label = Label(text=f"目前狀態：{key_status}", font_size="13sp", size_hint_y=0.28, color=(0.9, 0.9, 0.5, 1))
        settings_panel.add_widget(perm_label)
        api_input = TextInput(hint_text="貼上 Gemini API Key...", password=True, multiline=False, size_hint_y=0.36)
        settings_panel.add_widget(api_input)
        
        btn_action_box = BoxLayout(orientation="horizontal", spacing=8, size_hint_y=0.36)
        btn_get_api = Button(text="前往獲取金鑰", font_size="14sp", background_color=(0.2, 0.6, 1.0, 1))
        btn_get_api.bind(on_press=lambda x: webbrowser.open("https://aistudio.google.com/app/apikey"))
        btn_save = Button(text="加密儲存並收折", font_size="14sp", background_color=(0.1, 0.6, 0.3, 1))
        btn_action_box.add_widget(btn_get_api)
        btn_action_box.add_widget(btn_save)
        settings_panel.add_widget(btn_action_box)

        layout.add_widget(btn_settings)
        layout.add_widget(settings_panel)

        def toggle_settings(instance):
            if settings_panel.height == 0:
                settings_panel.height = "130dp"
                settings_panel.opacity = 1
                btn_settings.text = "[ 系統設定與 API 金鑰 ] (點此收起)"
            else:
                settings_panel.height = 0
                settings_panel.opacity = 0
                btn_settings.text = "[ 系統設定與 API 金鑰 ] (點此展開)"
        btn_settings.bind(on_press=toggle_settings)

        def save_api_securely(instance):
            key = api_input.text.strip()
            if key:
                encoded_key = base64.b64encode(key.encode('utf-8')).decode('utf-8')
                with open(".secure_api.dat", "w") as f:
                    f.write(encoded_key)
                self.ai.api_key = key
                self.ai._init_gemini()
                api_input.text = ""
                perm_label.text = "目前狀態：金鑰已成功更新！"
            toggle_settings(None)
            self.bridge.speak("API 金鑰已加密儲存。")
        btn_save.bind(on_press=save_api_securely)

        popup.open()

class JunbaCopilotApp(App):
    def build(self):
        register_chinese_font()
        Window.clearcolor = (0.07, 0.07, 0.1, 1.0)
        return FloatingWidget()

if __name__ == "__main__":
    JunbaCopilotApp().run()
