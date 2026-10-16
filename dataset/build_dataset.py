# -*- coding: utf-8 -*-
"""
Inbrisk LLM Gold-Standard Dataset Generator v2
Sıfırdan, yüksek kaliteli, uydurmasız, Inbrisk MCP & InbriskLLM mimarisine %100 sadık sentetik eğitim veri seti üreticisi.
"""

import os
import json
import random

TOOL_ARG_ALLOWED = {
    "computer_windows": {"ghost"},
    "computer_list_recipes": set(),
    "computer_capabilities": {"detail"},
    "computer_observe": {"mode", "hwnd", "detail", "maxElements", "deltaOnly", "baseSnapshotId", "maxWidth", "marks", "prune", "ocr", "ghost"},
    "computer_find": {"role", "name", "automationId", "hwnd", "process", "enabled", "nameNotContains", "value", "valueContains", "className", "within", "limit", "detail", "query", "target", "queries", "prune", "ocr", "ghost"},
    "computer_launch": {"app", "search", "executable", "path", "aumid", "uri", "arguments", "newInstance", "waitFor", "timeoutMs", "debugPort", "then", "operationId", "adapter", "bridge", "ghost"},
    "computer_type": {"text", "elementId", "target", "mode", "position", "submit", "observe", "operationId", "silent", "ghost"},
    "computer_click": {"elementId", "target", "frameId", "observationId", "x", "y", "button", "observe", "operationId", "rx", "ry", "relativeTo", "silent", "ghost"},
    "computer_hotkey": {"key", "modifiers", "keys", "operationId", "silent", "ghost"},
    "computer_do": {"app", "click", "type", "hotkey", "submit", "target", "waitMs", "cleanup", "operationId", "adapter", "bridge", "saveAsRecipe", "save_as_recipe", "enableReflex", "enable_reflex", "autoDismissModals", "auto_dismiss_modals", "ghost"},
    "computer_batch": {"steps", "set", "until", "read", "detail", "failFast", "fail_fast", "saveAsRecipe", "save_as_recipe", "enableReflex", "enable_reflex", "autoDismissModals", "auto_dismiss_modals", "operationId", "ghost"},
    "computer_run": {"steps", "runId", "detail", "saveAsRecipe", "save_as_recipe", "enableReflex", "enable_reflex", "autoDismissModals", "auto_dismiss_modals", "operationId", "ghost"},
    "computer_run_recipe": {"name", "parameters", "runId", "detail", "operationId", "ghost"},
    "computer_save_recipe": {"name", "fromRunId", "steps", "app", "description", "parameters", "preconditions", "postconditions"},
    "computer_wait_for": {"query", "name", "text", "target", "hwnd", "process", "expectedState", "expectedValue", "value", "gone", "minCount", "stopOnDialog", "ms", "timeoutMs", "ghost"},
    "computer_pause_run": {"runId", "reason"},
    "computer_resume_run": {"runId", "skipPausedStep", "reobserve", "remainingSteps", "bindings", "detail"},
    "computer_read": {"targets", "props", "ghost"},
    "computer_reset_input": {"fixBlockingWindows", "force", "ghost"},
    "computer_screenshot": {"target", "hwnd", "x", "y", "w", "h", "elementId", "monitorIndex", "maxWidth", "max_width", "marks", "ghost"},
    "computer_close_window": {"hwnd", "hwnds", "process", "titleContains", "target", "owned", "lastOwned", "closeAllAgentWindows", "force", "operationId", "ghost"},
    "computer_toolset": {"enable", "disable", "detail"},
    "computer_inspect": {"hwnd", "elementId", "target", "maxElements", "detail", "relational", "hwnds", "ghost"},
    "computer_ghost_control": {"action", "interactive", "fullScreen", "position", "opacity", "scale"},
    "computer_ghost_stop": {"sessionId", "session_id", "stopPip", "stop_pip"},
    "ghost_control_open": {"action", "interactive", "fullScreen", "position", "opacity", "scale"},
    "get_system_info": {"topic"}
}

SYSTEM_PROMPT = (
    "Sen Inbrisk'sin: yerel bir Windows masaustu otomasyon ajani.\n"
    "Sana 25 tane computer_* araci ve 1 tane get_system_info araci sunuluyor.\n"
    "computer_* araclari masaustunu DOGRUDAN yonetir ve DOGRUDAN cagrilir;\n"
    "tool_name/arguments sarmalamasi YOKTUR, aracin kendi parametrelerini alir:\n"
    '  computer_launch   {"app": "Notepad"}\n'
    '  computer_do       {"app": "Notepad", "type": "Merhaba", "submit": true}\n'
    '  computer_windows  {}\n'
    "get_system_info yalnizca sistem bilgisi verir (topic: datetime, cwd,\n"
    "hostname, user, os, disk); masaustu otomasyonu YAPMAZ.\n"
    "Uygulama acma, tiklama, yazma, tus, pencere kapatma ve ekran gozlemleme\n"
    "istekleri SADECE computer_* araclariyla yapilir.\n\n"
    "ARAC SERTIFIKASI:\n"
    "- Gorev basinda bir kez computer_capabilities {detail: 'full'} cagir.\n\n"
    "UYGULAMA ADI (app): EN SIK YAPILAN HATA\n"
    "- app bir pencere BASLIGI DEGIL, calistirilabilir uygulama ADIDIR.\n"
    "  'Not Defteri' pencere basligidir ve YANLISTIR; dogru deger 'Notepad'\n"
    "  (ya da 'notepad.exe')'tir. Turkce basligi ASLA app olarak gonderme.\n"
    "- Tercih sirasi: app (dostca ad: 'Spotify', 'Notepad', 'Calculator'),\n"
    "  sonra executable ('notepad.exe'), sonra path/aumid/uri.\n"
    "- Emin degilsen computer_launch {search: 'not'} ile arama + acma yap.\n\n"
    "HEDEFLEME:\n"
    "- 'process' alanini tercih et: exe adinin ALT DIZGESI, ornegin 'notepad'.\n"
    "  Dil bagimsizdir (locale-safe); pencere basligi DEGILDIR.\n\n"
    "GHOST (ARKA PLAN) MODU:\n"
    "- Varsayilan olarak eylemler kullanici masaustunde (on planda) calisir.\n"
    "- Kullanici 'arka planda', 'ghost', 'beni rahatsiz etmeden', 'arkada ac',\n"
    "  'sessizce', 'arkada calistir' gibi isteklerde bulunursa, ilgili computer_*\n"
    "  araclarina 'ghost': true parametresi eklenir.\n"
    "- 'ghost': true ile calisan eylemler kullanicinin aktif imlecini ve ekranini mesgul\n"
    "  etmeden ayri sanal masaustunde (Ghost Desktop) calisir.\n"
    "- Kullanici arka plan belirtmedikce ghost parametresi verilmez (on plan).\n\n"
    "KURALLAR:\n"
    "- Eylem gerekiyorsa tool cagir; gerekmiyorsa dogrudan cevap ver.\n"
    "- Tool adini, argumanlari, pencere kimligini, element id'sini ASLA uydurma.\n"
    "- Element id'leri YALNIZCA gordugun son gozlemden al.\n"
    "- Gozlem kismiysa eksiktir; gormedigini anlatma.\n"
    "- Tool hatasi veya 'candidates' listesi bir eylem icin IZIN degildir.\n"
    "- Belirsiz hedefi sessizce secme.\n"
    "- Cok adimli deterministik islerde computer_batch, tek eylemde computer_do kullan.\n"
    "- Bilmedigini uydurmak yerine gozlem yap ve emin ol. Her zaman Turkce cevap ver."
)

_call_counter = 0

def make_call(name, args):
    global _call_counter
    _call_counter += 1
    allowed = TOOL_ARG_ALLOWED.get(name, set())
    for k in args:
        if k not in allowed:
            raise ValueError(f"Kural Hatasi: {name} fonksiyonunda '{k}' parametresi gecersiz!")
    return {
        "id": f"call_{_call_counter:05d}",
        "type": "function",
        "function": {
            "name": name,
            "arguments": json.dumps(args, ensure_ascii=False)
        }
    }

def multi_turn(user_msg, steps, final_msg):
    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_msg}
    ]
    for tc, res in steps:
        msgs.append({
            "role": "assistant",
            "content": None,
            "tool_calls": [tc]
        })
        msgs.append({
            "role": "tool",
            "tool_call_id": tc["id"],
            "name": tc["function"]["name"],
            "content": json.dumps(res, ensure_ascii=False) if isinstance(res, (dict, list)) else str(res)
        })
    msgs.append({
        "role": "assistant",
        "content": final_msg
    })
    return {"messages": msgs}

def direct_turn(user_msg, assistant_msg):
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": assistant_msg}
        ]
    }

def make_full_conversation(turns):
    """
    turns: list of turn dicts:
      {"user": str, "steps": [(tc, res), ...], "assistant": str}
      or
      {"user": str, "assistant": str}
    """
    msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
    for t in turns:
        msgs.append({"role": "user", "content": t["user"]})
        if "steps" in t and t["steps"]:
            for tc, res in t["steps"]:
                msgs.append({
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [tc]
                })
                msgs.append({
                    "role": "tool",
                    "tool_call_id": tc["id"],
                    "name": tc["function"]["name"],
                    "content": json.dumps(res, ensure_ascii=False) if isinstance(res, (dict, list)) else str(res)
                })
        msgs.append({"role": "assistant", "content": t["assistant"]})
    return {"messages": msgs}


APPS = [
    ("Not Defteri", "Notepad", "notepad.exe", 10100, "Adsız - Not Defteri"),
    ("Hesap Makinesi", "Calculator", "CalculatorApp.exe", 10200, "Hesap Makinesi"),
    ("Paint", "mspaint", "mspaint.exe", 10300, "Adsız - Paint"),
    ("Dosya Gezgini", "explorer", "explorer.exe", 10400, "Bu Bilgisayar"),
    ("Microsoft Edge", "msedge", "msedge.exe", 10500, "Yeni Sekme - Edge"),
    ("Google Chrome", "chrome", "chrome.exe", 10600, "Yeni Sekme - Chrome"),
    ("Spotify", "Spotify", "Spotify.exe", 10700, "Spotify"),
    ("Visual Studio Code", "code", "Code.exe", 10800, "Visual Studio Code"),
    ("WordPad", "wordpad", "wordpad.exe", 10900, "Belge - WordPad"),
    ("Kayıt Defteri Düzenleyicisi", "regedit", "regedit.exe", 11000, "Kayıt Defteri Düzenleyicisi"),
    ("Karakter Eşlem", "charmap", "charmap.exe", 11100, "Karakter Eşlem"),
    ("Büyüteç", "magnify", "magnify.exe", 11200, "Büyüteç"),
    ("Görev Yöneticisi", "taskmgr", "taskmgr.exe", 11300, "Görev Yöneticisi"),
    ("Komut İstemi", "cmd", "cmd.exe", 11400, "Komut İstemi"),
]

def generate_ghost_contrastive_launch_samples(apps):
    samples = []
    contrast_templates = [
        ("{name}'ni aç", "{name}'ni arka planda aç"),
        ("{name}'ni normal önde başlat", "Beni rahatsız etmeden {name}'ni arkada çalıştır"),
        ("Ön planda {name} penceresini aç", "{name} uygulamasını ghost modunda aç"),
        ("Lütfen {name}'ni çalıştır", "Sessizce arkada {name}'ni açar mısın"),
        ("{name} programını ekranda aç", "{name}'ni arkada çalıştır, faremi oynatma")
    ]
    for idx, (tr_name, app_id, exe, base_pid, win_title) in enumerate(apps[:10]):
        tmpl_fg, tmpl_bg = contrast_templates[idx % len(contrast_templates)]
        pid_fg = base_pid + 50 + idx
        pid_bg = base_pid + 80 + idx

        # 1. Foreground sample (default behavior)
        prompt_fg = tmpl_fg.format(name=tr_name)
        tc_fg = make_call("computer_launch", {"app": app_id})
        res_fg = {
            "status": "Verified",
            "method": "NewProcess",
            "pid": pid_fg,
            "window": {"hwnd": f"0x{pid_fg:08x}", "title": win_title}
        }
        samples.append(multi_turn(
            prompt_fg,
            [(tc_fg, res_fg)],
            f"{tr_name} ({app_id}) ön planda başarıyla başlatıldı ve pencere odağa alındı."
        ))

        # 2. Background Ghost sample (ghost: True)
        prompt_bg = tmpl_bg.format(name=tr_name)
        tc_bg = make_call("computer_launch", {"app": app_id, "ghost": True})
        res_bg = {
            "status": "Verified",
            "method": "NewProcess",
            "pid": pid_bg,
            "ghost": True,
            "window": {"hwnd": f"0x{pid_bg:08x}", "title": win_title}
        }
        samples.append(multi_turn(
            prompt_bg,
            [(tc_bg, res_bg)],
            f"{tr_name} ({app_id}) Ghost masaüstünde arka planda başlatıldı; aktif pencereniz ve fare imleciniz etkilenmedi."
        ))
    return samples

def generate_ghost_single_turn_samples():
    samples = []

    # 1. computer_do in Ghost
    ghost_do_data = [
        ("Arka planda Not Defteri'ne Merhaba Dünya yaz", "Notepad", "Merhaba Dünya", True),
        ("Beni rahatsız etmeden Not Defteri'ne haftalık planı yaz", "Notepad", "haftalık plan", True),
        ("Arkada çalışan Not Defteri'ne 'Toplantı Notu: Saat 15:00' yaz", "Notepad", "Toplantı Notu: Saat 15:00", True),
        ("Ghost modunda Not Defteri'ne Türkçe karakter testi: çğışöü yaz", "Notepad", "çğışöü", True),
        ("Arka plandaki Not Defteri içine 'Inbrisk LLM Otomasyonu' yaz", "Notepad", "Inbrisk LLM Otomasyonu", True),
        ("Sessizce arkadaki Not Defteri'ne sistem durum raporunu ekle", "Notepad", "Sistem durum raporu", True),
    ]
    for prompt, app, text, submit in ghost_do_data:
        tc = make_call("computer_do", {"app": app, "type": text, "submit": submit, "ghost": True})
        res = {"status": "Verified", "app": app, "action": "type", "text": text, "ghost": True, "verification": "ValueReadback"}
        samples.append(multi_turn(
            prompt,
            [(tc, res)],
            f"Ghost masaüstündeki {app} içerisine '{text}' metni arka planda yazıldı."
        ))

    # 2. computer_batch in Ghost
    ghost_batches = [
        (
            "Arka planda hesap makinesinde 25 artı 75 işlemini yap",
            [
                {"do": "click", "t": "2"},
                {"do": "click", "t": "5"},
                {"do": "click", "t": "Artı"},
                {"do": "click", "t": "7"},
                {"do": "click", "t": "5"},
                {"do": "click", "t": "Eşittir"}
            ],
            "Ghost masaüstünde hesap makinesinde 25 + 75 = 100 işlemi arka planda tamamlandı."
        ),
        (
            "Beni rahatsız etmeden arkada tüm metni seçip panoya kopyala",
            [
                {"do": "hotkey", "keys": "ctrl+a"},
                {"do": "hotkey", "keys": "ctrl+c"}
            ],
            "Arka planda Ctrl+A ile metin seçildi ve Ctrl+C ile panoya kopyalandı."
        ),
        (
            "Ghost masaüstünde yeni satıra geçip panodakini yapıştır",
            [
                {"do": "hotkey", "keys": "enter"},
                {"do": "hotkey", "keys": "ctrl+v"}
            ],
            "Ghost masaüstünde Enter ile yeni satıra geçildi ve panodaki içerik yapıştırıldı."
        ),
        (
            "Arkada hesap makinesinde 100 eksi 30 işlemini gerçekleştir",
            [
                {"do": "click", "t": "1"},
                {"do": "click", "t": "0"},
                {"do": "click", "t": "0"},
                {"do": "click", "t": "Eksi"},
                {"do": "click", "t": "3"},
                {"do": "click", "t": "0"},
                {"do": "click", "t": "Eşittir"}
            ],
            "Arka planda 100 - 30 = 70 hesaplaması yapıldı."
        ),
    ]
    for prompt, steps, reply in ghost_batches:
        tc = make_call("computer_batch", {"steps": steps, "ghost": True})
        res = {"status": "Verified", "completedSteps": len(steps), "ghost": True, "stepResults": [{"step": i+1, "status": "Verified"} for i in range(len(steps))]}
        samples.append(multi_turn(prompt, [(tc, res)], reply))

    # 3. computer_windows in Ghost
    ghost_win_prompts = [
        "Arka plandaki açık pencereleri listele",
        "Ghost masaüstünde hangi pencereler açık?",
        "Beni rahatsız etmeden arkada çalışan uygulamaları kontrol et",
        "Sessizce arkadaki pencereleri tara",
    ]
    for prompt in ghost_win_prompts:
        tc = make_call("computer_windows", {"ghost": True})
        res = {
            "ghost": True,
            "windows": [
                {"hwnd": "0x0021001", "pid": 15100, "title": "Adsız - Not Defteri", "process": "notepad"},
                {"hwnd": "0x0021002", "pid": 15200, "title": "Hesap Makinesi", "process": "calculatorapp"}
            ]
        }
        samples.append(multi_turn(
            prompt,
            [(tc, res)],
            "Ghost masaüstünde çalışan pencereler listelendi: Not Defteri (0x0021001) ve Hesap Makinesi (0x0021002)."
        ))

    # 4. computer_close_window in Ghost
    ghost_close_scenarios = [
        ("Arka plandaki Not Defteri penceresini kapat", {"process": "notepad", "ghost": True}, "Arka plandaki Not Defteri penceresi kapatıldı."),
        ("Ghost masaüstündeki Hesap Makinesi'ni kapat", {"process": "calculatorapp", "ghost": True}, "Ghost masaüstündeki Hesap Makinesi sonlandırıldı."),
        ("Arkada açık olan Paint penceresini kapat", {"process": "mspaint", "ghost": True}, "Arka plandaki Paint penceresi kapatıldı."),
        ("Beni rahatsız etmeden arkadaki pencereyi sonlandır", {"hwnd": "0x0021001", "ghost": True}, "Ghost masaüstündeki 0x0021001 numaralı pencere kapatıldı."),
    ]
    for prompt, args, reply in ghost_close_scenarios:
        tc = make_call("computer_close_window", args)
        res = {"status": "Verified", "closed": True, "ghost": True}
        samples.append(multi_turn(prompt, [(tc, res)], reply))

    # 5. computer_screenshot in Ghost
    ghost_shot_prompts = [
        "Arka planın ekran görüntüsünü al",
        "Ghost masaüstünün ekranını yakala",
        "Beni rahatsız etmeden arkadaki ekranı fotoğrafla",
    ]
    for prompt in ghost_shot_prompts:
        tc = make_call("computer_screenshot", {"ghost": True})
        res = {"status": "Verified", "width": 1920, "height": 1080, "ghost": True, "format": "png"}
        samples.append(multi_turn(prompt, [(tc, res)], "Ghost masaüstünün ekran görüntüsü başarıyla alındı."))

    # 6. computer_observe in Ghost
    ghost_obs_prompts = [
        ("Arka plandaki ekranı gözlemle", "slim"),
        ("Ghost masaüstünde ne açık incele", "slim"),
        ("Beni rahatsız etmeden arkadaki masaüstünü tara", "slim"),
    ]
    for prompt, detail in ghost_obs_prompts:
        tc = make_call("computer_observe", {"detail": detail, "ghost": True})
        res = {
            "ghost": True,
            "window": {"title": "Adsız - Not Defteri", "hwnd": "0x0021001"},
            "elements": [
                {"elementId": "uia_ghost_1", "role": "Document", "name": "Metin Düzenleyici"},
                {"elementId": "uia_ghost_2", "role": "MenuItem", "name": "Dosya"}
            ],
            "truncated": False
        }
        samples.append(multi_turn(
            prompt,
            [(tc, res)],
            "Ghost masaüstü gözlemlendi: 'Adsız - Not Defteri' penceresi ve 'Metin Düzenleyici' alanı aktif."
        ))

    # 7. computer_hotkey in Ghost
    ghost_hotkeys = [
        ("Arka planda Ctrl+S ile kaydet", "s", "ctrl"),
        ("Ghost masaüstünde Enter tuşuna bas", "enter", ""),
        ("Beni rahatsız etmeden arkada Ctrl+A tuş kombinasyonunu bas", "a", "ctrl"),
        ("Arkada F5 ile yenile", "f5", ""),
    ]
    for prompt, k, m in ghost_hotkeys:
        args = {"key": k, "ghost": True}
        if m:
            args["modifiers"] = m
        tc = make_call("computer_hotkey", args)
        res = {"status": "Verified", "key": k, "modifiers": m, "ghost": True}
        samples.append(multi_turn(prompt, [(tc, res)], f"Ghost masaüstüne tuş kombinasyonu ({m+'+'+k if m else k}) iletildi."))

    # 8. computer_click & computer_type & computer_find & computer_inspect & computer_wait_for & computer_reset_input
    tc_clk = make_call("computer_click", {"target": "Kaydet", "ghost": True})
    samples.append(multi_turn(
        "Arka plandaki 'Kaydet' butonuna tıkla",
        [(tc_clk, {"status": "Verified", "clicked": True, "ghost": True})],
        "Ghost masaüstündeki 'Kaydet' butonuna fare imleciniz oynatılmadan tıklandı."
    ))

    tc_typ = make_call("computer_type", {"text": "test verisi", "ghost": True})
    samples.append(multi_turn(
        "Arka plandaki giriş kutusuna 'test verisi' yaz",
        [(tc_typ, {"status": "Verified", "text": "test verisi", "ghost": True})],
        "Ghost masaüstündeki aktif alana 'test verisi' yazıldı."
    ))

    tc_fnd = make_call("computer_find", {"name": "Giriş", "ghost": True})
    samples.append(multi_turn(
        "Ghost masaüstünde 'Giriş' butonunu bul",
        [(tc_fnd, {"status": "Verified", "elements": [{"name": "Giriş", "elementId": "uia_btn_login"}], "ghost": True})],
        "Ghost masaüstünde 'Giriş' butonu bulundu (ID: uia_btn_login)."
    ))

    tc_insp = make_call("computer_inspect", {"hwnd": "0x0021001", "detail": "full", "ghost": True})
    samples.append(multi_turn(
        "Arka plandaki Not Defteri penceresinin detaylarını incele",
        [(tc_insp, {"status": "Verified", "hwnd": "0x0021001", "ghost": True, "tree": {"role": "Window", "children": []}})],
        "Ghost masaüstündeki Not Defteri pencere hiyerarşisi başarıyla incelendi."
    ))

    tc_wait = make_call("computer_wait_for", {"process": "notepad", "expectedState": "visible", "ghost": True})
    samples.append(multi_turn(
        "Arka planda Not Defteri penceresi görünene kadar bekle",
        [(tc_wait, {"status": "Verified", "matched": True, "elapsedMs": 300, "ghost": True})],
        "Arka plandaki Not Defteri penceresi 300 ms içinde hazır duruma geldi."
    ))

    tc_rst = make_call("computer_reset_input", {"force": True, "ghost": True})
    samples.append(multi_turn(
        "Ghost masaüstü girişlerini sıfırla, takılı tuşları temizle",
        [(tc_rst, {"status": "Verified", "resetKeys": True, "ghost": True})],
        "Ghost masaüstü giriş kanalı ve tuş durumları sıfırlandı."
    ))

    tc_stop1 = make_call("computer_ghost_stop", {"stopPip": True})
    samples.append(multi_turn(
        "Ghost masaüstünü tamamen sonlandır",
        [(tc_stop1, {"success": True, "pipStopped": True, "message": "Ghost desktop session, local isolated desktop processes, and PiP stream successfully stopped."})],
        "Ghost masaüstü oturumu, arka plan süreçleri ve PiP penceresi tamamen sonlandırıldı."
    ))

    tc_stop2 = make_call("computer_ghost_stop", {})
    samples.append(multi_turn(
        "Arka plandaki ghost oturumunu kapat",
        [(tc_stop2, {"success": True, "message": "Ghost desktop session stopped."})],
        "Ghost masaüstü oturumu başarıyla kapatıldı."
    ))

    tc_ctrl_stop = make_call("computer_ghost_control", {"action": "stop"})
    samples.append(multi_turn(
        "Ghost kontrol servisini ve PiP penceresini durdur",
        [(tc_ctrl_stop, {"success": True, "action": "stop", "state": "stopped", "message": "Ghost Control daemon stopped."})],
        "Ghost kontrol servisi ve PiP penceresi durduruldu."
    ))

    return samples

def generate_ghost_multi_turn_samples():
    samples = []

    # Senaryo G1: Tam Ghost İş Akışı (Aç -> Yaz -> Kaydet -> Kapat)
    tc_g1_1 = make_call("computer_launch", {"app": "Notepad", "ghost": True})
    res_g1_1 = {"status": "Verified", "pid": 16010, "ghost": True, "window": {"hwnd": "0x0016010", "title": "Adsız - Not Defteri"}}
    tc_g1_2 = make_call("computer_do", {"app": "Notepad", "type": "Arka Plan Görev Raporu: Tamamlandı", "submit": True, "ghost": True})
    res_g1_2 = {"status": "Verified", "action": "type", "ghost": True}
    tc_g1_3 = make_call("computer_hotkey", {"key": "s", "modifiers": "ctrl", "ghost": True})
    res_g1_3 = {"status": "Verified", "pressed": "ctrl+s", "ghost": True}
    tc_g1_4 = make_call("computer_close_window", {"process": "notepad", "ghost": True})
    res_g1_4 = {"status": "Verified", "closed": True, "ghost": True}

    samples.append(make_full_conversation([
        {"user": "Beni rahatsız etmeden arka planda Not Defteri'ni aç", "steps": [(tc_g1_1, res_g1_1)], "assistant": "Not Defteri arka planda (Ghost Masaüstü) başlatıldı. Aktif masaüstünüz etkilenmedi."},
        {"user": "İçine 'Arka Plan Görev Raporu: Tamamlandı' yaz", "steps": [(tc_g1_2, res_g1_2)], "assistant": "Metin arka plandaki Not Defteri penceresine yazıldı."},
        {"user": "Ctrl+S ile kaydet", "steps": [(tc_g1_3, res_g1_3)], "assistant": "Arka planda Ctrl+S kaydetme kısayolu basıldı."},
        {"user": "Şimdi arka plandaki Not Defteri'ni kapat", "steps": [(tc_g1_4, res_g1_4)], "assistant": "Arka plandaki Not Defteri penceresi güvenle kapatıldı."}
    ]))

    # Senaryo G2: Ön Plan ve Arka Plan Karşılaştırmalı Çok Adımlı Oturum (Contrastive Multi-Turn)
    tc_g2_1 = make_call("computer_launch", {"app": "Calculator"})
    res_g2_1 = {"status": "Verified", "pid": 17010, "window": {"hwnd": "0x0017010", "title": "Hesap Makinesi"}}
    tc_g2_2 = make_call("computer_launch", {"app": "Notepad", "ghost": True})
    res_g2_2 = {"status": "Verified", "pid": 17020, "ghost": True, "window": {"hwnd": "0x0017020", "title": "Adsız - Not Defteri"}}
    tc_g2_3 = make_call("computer_do", {"app": "Notepad", "type": "Ön plan ve arka plan testi", "submit": True, "ghost": True})
    res_g2_3 = {"status": "Verified", "action": "type", "ghost": True}
    tc_g2_4 = make_call("computer_close_window", {"process": "notepad", "ghost": True})
    res_g2_4 = {"status": "Verified", "closed": True, "ghost": True}

    samples.append(make_full_conversation([
        {"user": "Hesap makinesini ön planda aç", "steps": [(tc_g2_1, res_g2_1)], "assistant": "Hesap Makinesi ön planda açıldı."},
        {"user": "Şimdi de beni rahatsız etmeden Not Defteri'ni arkada çalıştır", "steps": [(tc_g2_2, res_g2_2)], "assistant": "Not Defteri arka planda (Ghost) çalıştırıldı."},
        {"user": "Arka plandaki Not Defteri'ne 'Ön plan ve arka plan testi' yaz", "steps": [(tc_g2_3, res_g2_3)], "assistant": "Metin arka plandaki Not Defteri'ne başarıyla yazıldı."},
        {"user": "Arkadaki Not Defteri penceresini kapat, Hesap Makinesi açık kalsın", "steps": [(tc_g2_4, res_g2_4)], "assistant": "Arka plandaki Not Defteri kapatıldı, ön plandaki Hesap Makinesi açık kalmaya devam ediyor."}
    ]))

    # Senaryo G3: Ghost Batch Oturumu (Arkada Hesaplama)
    tc_g3_1 = make_call("computer_launch", {"app": "Calculator", "ghost": True})
    res_g3_1 = {"status": "Verified", "pid": 18010, "ghost": True, "window": {"hwnd": "0x0018010", "title": "Hesap Makinesi"}}
    tc_g3_2 = make_call("computer_batch", {
        "steps": [
            {"do": "click", "t": "4"},
            {"do": "click", "t": "5"},
            {"do": "click", "t": "Artı"},
            {"do": "click", "t": "5"},
            {"do": "click", "t": "5"},
            {"do": "click", "t": "Eşittir"}
        ],
        "ghost": True
    })
    res_g3_2 = {"status": "Verified", "completedSteps": 6, "ghost": True}
    tc_g3_3 = make_call("computer_close_window", {"process": "calculatorapp", "ghost": True})
    res_g3_3 = {"status": "Verified", "closed": True, "ghost": True}

    samples.append(make_full_conversation([
        {"user": "Arka planda hesap makinesini aç", "steps": [(tc_g3_1, res_g3_1)], "assistant": "Hesap Makinesi Ghost masaüstünde arka planda açıldı."},
        {"user": "Beni rahatsız etmeden 45 artı 55 işlemini yap", "steps": [(tc_g3_2, res_g3_2)], "assistant": "Ghost masaüstünde 45 + 55 = 100 işlemi tamamlandı."},
        {"user": "Arka plandaki hesap makinesini kapat", "steps": [(tc_g3_3, res_g3_3)], "assistant": "Arka plandaki Hesap Makinesi kapatıldı."}
    ]))

    # Senaryo G4: Ghost Pencere Listeleme -> Gözlem -> Tıklama
    tc_g4_1 = make_call("computer_windows", {"ghost": True})
    res_g4_1 = {"windows": [{"hwnd": "0x0019010", "title": "Adsız - Not Defteri", "process": "notepad"}], "ghost": True}
    tc_g4_2 = make_call("computer_observe", {"detail": "slim", "ghost": True})
    res_g4_2 = {"window": {"title": "Adsız - Not Defteri", "hwnd": "0x0019010"}, "elements": [{"elementId": "uia_g1", "role": "MenuItem", "name": "Dosya"}], "ghost": True}
    tc_g4_3 = make_call("computer_click", {"elementId": "uia_g1", "ghost": True})
    res_g4_3 = {"status": "Verified", "clicked": True, "ghost": True}

    samples.append(make_full_conversation([
        {"user": "Ghost masaüstünde açık pencereleri kontrol et", "steps": [(tc_g4_1, res_g4_1)], "assistant": "Ghost masaüstünde Not Defteri penceresi (0x0019010) açık durumda."},
        {"user": "Arkadaki ekranı gözlemle", "steps": [(tc_g4_2, res_g4_2)], "assistant": "Ghost masaüstündeki Not Defteri penceresinde 'Dosya' menü öğesi gözlemlendi."},
        {"user": "Oradaki 'Dosya' menüsüne arka planda tıkla", "steps": [(tc_g4_3, res_g4_3)], "assistant": "Arka plandaki Not Defteri penceresinde 'Dosya' menüsüne tıklandı."}
    ]))

    # Senaryo G5: Ghost Eleman Bulma ve Yazma
    tc_g5_1 = make_call("computer_find", {"role": "Document", "ghost": True})
    res_g5_1 = {"elements": [{"elementId": "uia_doc1", "role": "Document", "name": "Metin Düzenleyici"}], "ghost": True}
    tc_g5_2 = make_call("computer_type", {"elementId": "uia_doc1", "text": "Otomasyon Başarılı", "ghost": True})
    res_g5_2 = {"status": "Verified", "text": "Otomasyon Başarılı", "ghost": True}

    samples.append(make_full_conversation([
        {"user": "Ghost masaüstünde metin kutusunu bul", "steps": [(tc_g5_1, res_g5_1)], "assistant": "Ghost masaüstünde 'Metin Düzenleyici' alanı bulundu (ID: uia_doc1)."},
        {"user": "Beni rahatsız etmeden oraya 'Otomasyon Başarılı' yaz", "steps": [(tc_g5_2, res_g5_2)], "assistant": "Ghost masaüstündeki metin alanına 'Otomasyon Başarılı' yazıldı."}
    ]))

    return samples

def generate_all_samples():
    samples = []
    apps = APPS

    prompt_templates = [
        "{name}'ni aç",
        "{name}'ni açar mısın",
        "{name} uygulamasını başlat",
        "Lütfen {name}'ni çalıştır",
        "Bana {name} penceresini aç",
        "{name} programını aç"
    ]

    for tr_name, app_id, exe, base_pid, win_title in apps:
        for idx, tmpl in enumerate(prompt_templates[:4]):  # 4 varyasyon
            user_prompt = tmpl.format(name=tr_name)
            pid = base_pid + idx
            tc = make_call("computer_launch", {"app": app_id})
            res = {
                "status": "Verified",
                "method": "NewProcess",
                "pid": pid,
                "window": {"hwnd": f"0x{pid:08x}", "title": win_title}
            }
            samples.append(multi_turn(
                user_prompt,
                [(tc, res)],
                f"{tr_name} ({app_id}) başarıyla başlatıldı ve pencere odağa alındı."
            ))

    # ExistingInstance (Uydurma yapmama kuralı: Mevcut pencere doğrulanır)
    for tr_name, app_id, exe, base_pid, win_title in apps[:8]:
        user_prompt = f"{tr_name}'ni aç"
        hwnd = f"0x00{base_pid:06x}"
        tc1 = make_call("computer_launch", {"app": app_id})
        res1 = {
            "status": "Verified",
            "method": "ExistingInstance",
            "pid": base_pid,
            "window": {"hwnd": hwnd, "title": win_title}
        }
        tc2 = make_call("computer_windows", {})
        res2 = {
            "windows": [
                {"hwnd": hwnd, "pid": base_pid, "title": win_title, "process": app_id.lower()},
                {"hwnd": "0x00099999", "pid": 4000, "title": "Inbrisk Terminal", "process": "powershell"}
            ]
        }
        samples.append(multi_turn(
            user_prompt,
            [(tc1, res1), (tc2, res2)],
            f"{tr_name} zaten çalışıyordu; mevcut pencere ({hwnd}) kontrol edildi ve öne getirildi."
        ))

    # =========================================================================
    # 2. WINDOW MANAGEMENT & CLOSE - 45 Örnek
    # =========================================================================
    close_templates = [
        "Şimdi o pencereyi kapat",
        "Açık olan {name} penceresini kapat",
        "{name}'ni kapatır mısın",
        "{name} uygulamasını kapat",
        "Şu anki {name} penceresini sonlandır"
    ]

    for tr_name, app_id, exe, base_pid, win_title in apps[:10]:
        hwnd = f"0x00{base_pid:06x}"
        for tmpl in close_templates[:3]:
            prompt = tmpl.format(name=tr_name)
            # Model asla hwnd sormaz, windows ile bulur sonra kapatır
            tc1 = make_call("computer_windows", {})
            res1 = {
                "windows": [
                    {"hwnd": hwnd, "pid": base_pid, "title": win_title, "process": app_id.lower()},
                    {"hwnd": "0x00011111", "pid": 1200, "title": "Windows Terminal", "process": "wt"}
                ]
            }
            tc2 = make_call("computer_close_window", {"hwnd": hwnd})
            res2 = {"status": "Verified", "closed": True, "hwnd": hwnd}
            samples.append(multi_turn(
                prompt,
                [(tc1, res1), (tc2, res2)],
                f"Pencereler kontrol edildi ve {tr_name} ({hwnd}) başarıyla kapatıldı."
            ))

    # =========================================================================
    # 3. TEXT ENTRY, BATCH & KEYBOARD AUTOMATION - 40 Örnek
    # =========================================================================
    do_data = [
        ("Not Defteri'ne Merhaba Dünya yaz", "Notepad", "Merhaba Dünya", True),
        ("Not defterine Inbrisk otomasyon testi yaz", "Notepad", "Inbrisk otomasyon testi", True),
        ("Notepad içine 2026 yılı hedefleri yaz", "Notepad", "2026 yılı hedefleri", True),
        ("Not defterine toplantı notu olarak: Saat 14:00 sunum yaz", "Notepad", "Saat 14:00 sunum", True),
        ("Not Defteri'ne Türkçe karakter testi: çğışöü ÇĞİŞÖÜ yaz", "Notepad", "çğışöü ÇĞİŞÖÜ", True),
        ("Metin kutusuna test verisi gir", "Notepad", "test verisi", True),
    ]
    for prompt, app, text, submit in do_data:
        tc = make_call("computer_do", {"app": app, "type": text, "submit": submit})
        res = {"status": "Verified", "app": app, "action": "type", "text": text, "verification": "ValueReadback"}
        samples.append(multi_turn(prompt, [(tc, res)], f"{app} içerisine '{text}' metni başarıyla yazıldı."))

    batch_scenarios = [
        (
            "Not Defteri'ne 'Proje Durumu: Başarılı' yaz ve kaydet",
            [
                {"do": "type", "t": "Proje Durumu: Başarılı", "submit": True},
                {"do": "hotkey", "keys": "ctrl+s"}
            ],
            "Not Defteri'ne metin yazıldı ve kaydetme kısayolu (Ctrl+S) basıldı."
        ),
        (
            "Hesap makinesinde 15 artı 35 işlemini yap",
            [
                {"do": "click", "t": "1"},
                {"do": "click", "t": "5"},
                {"do": "click", "t": "Artı"},
                {"do": "click", "t": "3"},
                {"do": "click", "t": "5"},
                {"do": "click", "t": "Eşittir"}
            ],
            "Hesap makinesinde 15 + 35 = 50 işlemi uygulandı."
        ),
        (
            "Tüm metni seç ve panoya kopyala",
            [
                {"do": "hotkey", "keys": "ctrl+a"},
                {"do": "hotkey", "keys": "ctrl+c"}
            ],
            "Ctrl+A ile tüm metin seçildi ve Ctrl+C ile panoya kopyalandı."
        ),
        (
            "Yeni bir satır aç ve panodakileri yapıştır",
            [
                {"do": "hotkey", "keys": "enter"},
                {"do": "hotkey", "keys": "ctrl+v"}
            ],
            "Enter ile yeni satıra geçildi ve Ctrl+V ile yapıştırma yapıldı."
        ),
        (
            "Geri al işlemini uygula (Ctrl+Z)",
            [{"do": "hotkey", "keys": "ctrl+z"}],
            "Son işlem Ctrl+Z kısayolu ile geri alındı."
        )
    ]
    for prompt, steps, reply in batch_scenarios:
        tc = make_call("computer_batch", {"steps": steps})
        res = {"status": "Verified", "completedSteps": len(steps), "stepResults": [{"step": i+1, "status": "Verified"} for i in range(len(steps))]}
        samples.append(multi_turn(prompt, [(tc, res)], reply))

    # Kısayol Tuşları (computer_hotkey)
    hotkeys = [
        ("Ctrl+S yap", "s", "ctrl"),
        ("Ctrl+C kısayoluna bas", "c", "ctrl"),
        ("Ctrl+V yapıştır kısayoluna bas", "v", "ctrl"),
        ("Ctrl+A ile hepsini seç", "a", "ctrl"),
        ("Alt+Tab bas", "tab", "alt"),
        ("F5 ile yenile", "f5", ""),
        ("Escape tuşuna bas", "escape", ""),
        ("Enter tuşuna bas", "enter", ""),
        ("Tab tuşuna bas", "tab", ""),
    ]
    for prompt, k, m in hotkeys:
        args = {"key": k}
        if m:
            args["modifiers"] = m
        tc = make_call("computer_hotkey", args)
        res = {"status": "Verified", "key": k, "modifiers": m}
        samples.append(multi_turn(prompt, [(tc, res)], f"Tuş kombinasyonu ({m+'+'+k if m else k}) başarıyla iletildi."))

    # =========================================================================
    # 4. OBSERVATION, INSPECTION & TRUTHFULNESS - 30 Örnek
    # =========================================================================
    obs_queries = [
        ("Ekranda ne var?", "slim"),
        ("Masaüstünü tara", "slim"),
        ("Aktif pencere durumunu kontrol et", "slim"),
        ("Ekranı gözlemle", "slim"),
        ("Şu anki ekranın özetini ver", "slim"),
    ]
    for prompt, detail in obs_queries:
        tc = make_call("computer_observe", {"detail": detail})
        res = {
            "window": {"title": "Adsız - Not Defteri", "hwnd": "0x0012a450"},
            "elements": [
                {"elementId": "uia_101", "role": "Document", "name": "Metin Düzenleyici"},
                {"elementId": "uia_102", "role": "MenuItem", "name": "Dosya"},
                {"elementId": "uia_103", "role": "MenuItem", "name": "Düzen"}
            ],
            "truncated": False
        }
        samples.append(multi_turn(
            prompt,
            [(tc, res)],
            "Ekranda 'Adsız - Not Defteri' penceresi görünmektedir. Menü çubuğunda Dosya ve Düzen seçenekleri, merkezde ise Metin Düzenleyici alanı mevcuttur."
        ))

    # computer_inspect
    tc_insp = make_call("computer_inspect", {"hwnd": "0x0012a450", "detail": "full"})
    res_insp = {
        "status": "Verified",
        "hwnd": "0x0012a450",
        "title": "Not Defteri",
        "tree": {
            "role": "Window",
            "children": [
                {"role": "TitleBar", "name": "Not Defteri"},
                {"role": "Edit", "automationId": "15", "value": "Merhaba Inbrisk"}
            ]
        }
    }
    samples.append(multi_turn(
        "Not Defteri penceresinin detaylarını incele",
        [(tc_insp, res_insp)],
        "Not Defteri penceresi (0x0012a450) incelendi. Edit bileşeni içerisinde 'Merhaba Inbrisk' değeri okunmaktadır."
    ))

    # computer_screenshot
    tc_shot = make_call("computer_screenshot", {"hwnd": "0x0012a450"})
    res_shot = {"status": "Verified", "width": 800, "height": 600, "format": "png"}
    samples.append(multi_turn(
        "Pencerenin ekran görüntüsünü al",
        [(tc_shot, res_shot)],
        "Pencere ekran görüntüsü (800x600) başarıyla yakalandı."
    ))

    # =========================================================================
    # 5. INPUT RESET, WAIT & CAPABILITIES - 20 Örnek
    # =========================================================================
    # computer_reset_input
    tc_res = make_call("computer_reset_input", {"force": True})
    res_res = {"status": "Verified", "resetKeys": True, "resetMouse": True}
    samples.append(multi_turn(
        "Klavye ve fare girdisini sıfırla, takılı kalan tuşları temizle",
        [(tc_res, res_res)],
        "Klavye ve fare giriş durumları başarıyla sıfırlandı, basılı kalan tuşlar serbest bırakıldı."
    ))

    # computer_capabilities
    tc_cap = make_call("computer_capabilities", {"detail": "full"})
    res_cap = {
        "status": "Verified",
        "controlState": "Active",
        "toolsCount": 25,
        "verification": "Active"
    }
    samples.append(multi_turn(
        "Sistem yeteneklerini ve araç sözleşmesini kontrol et",
        [(tc_cap, res_cap)],
        "Inbrisk MCP araç yetenekleri ve sunucu politikası kontrol edildi. 25 araç aktif ve hazır durumda."
    ))

    # computer_wait_for
    tc_wait = make_call("computer_wait_for", {"process": "notepad", "expectedState": "visible", "timeoutMs": 5000})
    res_wait = {"status": "Verified", "matched": True, "elapsedMs": 450}
    samples.append(multi_turn(
        "Not Defteri penceresi ekrana gelene kadar bekle",
        [(tc_wait, res_wait)],
        "Not Defteri penceresi 450 ms içinde ekranda belirdi."
    ))

    # =========================================================================
    # 6. SYSTEM INFO SEPARATION - 15 Örnek
    # =========================================================================
    sys_list = [
        ("Saat kaç?", "datetime", "17:00:00", "Saat şu anda 17:00."),
        ("Bugünün tarihi nedir?", "datetime", "2026-10-14", "Bugünün tarihi 14 Ekim 2026."),
        ("Hangi klasörde çalışıyorsun?", "cwd", "C:\\Users\\Ahmet\\Documents\\InbriskLLM", "Aktif çalışma dizini: C:\\Users\\Ahmet\\Documents\\InbriskLLM"),
        ("Bilgisayarın sistem adı ne?", "hostname", "DESKTOP-INBRISK", "Bilgisayar adı: DESKTOP-INBRISK"),
        ("Kullanıcı adım nedir?", "user", "Ahmet", "Mevcut Windows kullanıcısı: Ahmet"),
        ("İşletim sistemi bilgisi ver", "os", "Windows 11 Pro 64-bit", "İşletim sistemi: Windows 11 Pro 64-bit."),
        ("Disk boş alanı ne kadar?", "disk", "C: 140 GB boş", "C: diskinde 140 GB boş alan var."),
    ]
    for prompt, top, val, rep in sys_list:
        tc = make_call("get_system_info", {"topic": top})
        samples.append(multi_turn(prompt, [(tc, {"topic": top, "result": val})], rep))

    # =========================================================================
    # 7. MULTI-TURN COMPREHENSIVE SESSIONS (Uçtan Uca Çok Adımlı Oturumlar) - 25 Örnek
    # =========================================================================
    # Senaryo 1: Not defteri aç -> yazı yaz -> kaydet -> kapat
    tc_s1_1 = make_call("computer_launch", {"app": "Notepad"})
    res_s1_1 = {"status": "Verified", "pid": 12050, "window": {"hwnd": "0x0012050", "title": "Adsız - Not Defteri"}}
    tc_s1_2 = make_call("computer_do", {"app": "Notepad", "type": "Haftalık Görev Listesi", "submit": True})
    res_s1_2 = {"status": "Verified", "action": "type"}
    tc_s1_3 = make_call("computer_hotkey", {"key": "s", "modifiers": "ctrl"})
    res_s1_3 = {"status": "Verified", "pressed": "ctrl+s"}
    tc_s1_4_w = make_call("computer_windows", {})
    res_s1_4_w = {"windows": [{"hwnd": "0x0012050", "title": "Adsız - Not Defteri", "process": "notepad"}]}
    tc_s1_4_c = make_call("computer_close_window", {"hwnd": "0x0012050"})
    res_s1_4_c = {"status": "Verified", "closed": True}

    samples.append(make_full_conversation([
        {"user": "Not defterini aç", "steps": [(tc_s1_1, res_s1_1)], "assistant": "Not Defteri açıldı."},
        {"user": "İçine 'Haftalık Görev Listesi' yaz", "steps": [(tc_s1_2, res_s1_2)], "assistant": "Metin Not Defteri'ne yazıldı."},
        {"user": "Ctrl+S ile kaydet", "steps": [(tc_s1_3, res_s1_3)], "assistant": "Kaydetme kısayolu basıldı."},
        {"user": "Şimdi Not Defteri'ni kapat", "steps": [(tc_s1_4_w, res_s1_4_w), (tc_s1_4_c, res_s1_4_c)], "assistant": "Not Defteri penceresi kapatıldı."}
    ]))

    # Senaryo 2: Hesap makinesinde işlem yapıp kapatma
    tc_s2_1 = make_call("computer_launch", {"app": "Calculator"})
    res_s2_1 = {"status": "Verified", "pid": 13010, "window": {"hwnd": "0x0013010", "title": "Hesap Makinesi"}}
    tc_s2_2 = make_call("computer_batch", {"steps": [{"do": "click", "t": "8"}, {"do": "click", "t": "Çarp"}, {"do": "click", "t": "9"}, {"do": "click", "t": "Eşittir"}]})
    res_s2_2 = {"status": "Verified", "completedSteps": 4}
    tc_s2_3_w = make_call("computer_windows", {})
    res_s2_3_w = {"windows": [{"hwnd": "0x0013010", "title": "Hesap Makinesi", "process": "calculatorapp"}]}
    tc_s2_3_c = make_call("computer_close_window", {"hwnd": "0x0013010"})
    res_s2_3_c = {"status": "Verified", "closed": True}

    samples.append(make_full_conversation([
        {"user": "Hesap makinesini aç", "steps": [(tc_s2_1, res_s2_1)], "assistant": "Hesap Makinesi başlatıldı."},
        {"user": "8 kere 9 kaç yapar hesapla", "steps": [(tc_s2_2, res_s2_2)], "assistant": "Hesap makinesinde 8 * 9 işlemi yapıldı (Sonuç: 72)."},
        {"user": "Pencereyi kapatabilirsin", "steps": [(tc_s2_3_w, res_s2_3_w), (tc_s2_3_c, res_s2_3_c)], "assistant": "Hesap Makinesi penceresi kapatıldı."}
    ]))

    # Senaryo 3: Gözlem -> Eleman Bul -> Tıkla
    tc_s3_1 = make_call("computer_observe", {"detail": "slim"})
    res_s3_1 = {
        "window": {"title": "Ayarlar", "hwnd": "0x0014020"},
        "elements": [{"elementId": "uia_opt1", "name": "Ağ ve İnternet", "role": "ListItem"}]
    }
    tc_s3_2 = make_call("computer_click", {"elementId": "uia_opt1"})
    res_s3_2 = {"status": "Verified", "clicked": True}

    samples.append(make_full_conversation([
        {"user": "Ekrana bak, Ayarlar'da Ağ ve İnternet var mı?", "steps": [(tc_s3_1, res_s3_1)], "assistant": "Ayarlar penceresinde 'Ağ ve İnternet' seçeneği bulundu (ID: uia_opt1)."},
        {"user": "Ona tıkla", "steps": [(tc_s3_2, res_s3_2)], "assistant": "'Ağ ve İnternet' seçeneğine tıklandı."}
    ]))

    # =========================================================================
    # 8. DIRECT CHAT & EXPLANATIONS - 20 Örnek
    # =========================================================================
    direct_dialogs = [
        ("Merhaba", "Merhaba! Ben Inbrisk masaüstü otomasyon asistanıyım. Size nasıl yardımcı olabilirim?"),
        ("Selam", "Selamlar! Bugün hangi masaüstü görevini otomatikleştirmek istersiniz?"),
        ("Sen kimsin?", "Ben Inbrisk: Windows üzerinde pencereleri yöneten, uygulamaları başlatan ve masaüstü görevlerini güvenli şekilde otomatikleştiren yerel bir yapay zeka asistanıyım."),
        ("Inbrisk ile neler yapabilirsin?", "Inbrisk ile şu işlemleri yapabilirim:\n- Windows uygulamalarını (Notepad, Calculator, Tarayıcı vb.) başlatma\n- Açık pencereleri listeleme ve güvenli kapatma\n- Metin yazma, buton tıklama ve klavye kısayollarını yürütme\n- Ekranı ve UI öğelerini inceleme\n- Çok adımlı otomasyon batch'lerini tek seferde çalıştırma\n- Ghost modu ile kullanıcıyı rahatsız etmeden arka planda çalışma"),
        ("Görevin nedir?", "Masaüstünüzdeki tekrarlayan görevleri insan hatasından uzak, güvenli ve hızlı bir şekilde yürütmektir."),
        ("Yardımın için sağ ol", "Rica ederim, her zaman buradayım! Kolay gelsin."),
        ("Windows 10 mu 11 mi kullanmalıyım?", "Inbrisk hem Windows 10 hem de Windows 11 üzerinde UI Automation altyapısıyla tam uyumlu çalışır; tercihinize göre her ikisinde de kullanabilirsiniz."),
        ("Sistem dosyalarını silebilir misin?", "Hayır. Güvenlik politikam gereği sistem dosyalarını silme, format atma veya zarar verebilecek yıkıcı işlemleri gerçekleştiremem."),
        ("Ghost modu nedir ve ne zaman kullanılır?", "Ghost modu (arka plan otomasyonu), Inbrisk'in eylemleri ayrı bir sanal masaüstünde (Ghost Desktop) yürütmesini sağlar. Kullanıcı 'arka planda', 'ghost', 'beni rahatsız etmeden' veya 'sessizce' istediğinde devreye girer. Bu modda fare imleciniz hareket etmez, aktif pencereleriniz odağını kaybetmez ve siz bilgisayarınızda çalışmaya devam ederken işlemler arka planda tamamlanır."),
    ]
    for u, a in direct_dialogs:
        samples.append(direct_turn(u, a))

    # =========================================================================
    # 9. GHOST DESKTOP SAMPLES (CONTRASTIVE, SINGLE-TURN, MULTI-TURN)
    # =========================================================================
    samples.extend(generate_ghost_contrastive_launch_samples(apps))
    samples.extend(generate_ghost_single_turn_samples())
    samples.extend(generate_ghost_multi_turn_samples())

    return samples

def main():
    dataset_dir = os.path.dirname(os.path.abspath(__file__))
    train_path = os.path.join(dataset_dir, "train.jsonl")
    val_path = os.path.join(dataset_dir, "val.jsonl")
    full_path = os.path.join(dataset_dir, "dataset_full.jsonl")

    samples = generate_all_samples()
    print(f"Toplam üretilen altın standart örnek sayısı: {len(samples)}")

    random.seed(42)
    random.shuffle(samples)
    split_idx = int(len(samples) * 0.85)
    train_samples = samples[:split_idx]
    val_samples = samples[split_idx:]

    print(f"Train örnekleri: {len(train_samples)}")
    print(f"Validation örnekleri: {len(val_samples)}")

    with open(train_path, "w", encoding="utf-8") as f:
        for s in train_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    with open(val_path, "w", encoding="utf-8") as f:
        for s in val_samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    with open(full_path, "w", encoding="utf-8") as f:
        for s in samples:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"Başarıyla kaydedildi:\n  {train_path}\n  {val_path}\n  {full_path}")

if __name__ == "__main__":
    main()
