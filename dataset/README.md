# Inbrisk LLM Gold-Standard Fine-Tuning Dataset (v2.0)

Bu veri seti, Inbrisk Windows masaüstü otomasyon ajanını (`ai_shell.py`) yönetebilecek herhangi bir LLM'i (Qwen, Llama, Mistral vb.) sıfırdan ve en yüksek kalite standartlarında eğitmek için oluşturulmuştur.

---

## 1. Neden Eski Veriler Silindi ve Sıfırdan Oluşturuldu?
Eski denemelerdeki oturum loglarında 2B modelinin akıl yürütme (reasoning) yetersizliğinden kaynaklanan ciddi kirlenmeler vardı:
- **Halüsinasyon (Uydurma):** Model `ExistingInstance` yanıtını görünce ekranda olmayan sahte element ID'leri (`uia_24036_97`), sahte bellek uyarıları veya hayali pencereler uyduruyordu.
- **Eylemsizlik (Anlatıp Yapmama):** "Önce pencereyi bulacağım, sonra tıklayacağım" deyip hiçbir tool çağırmadan turu bitiriyordu.
- **Sorumluluktan Kaçma:** "Şimdi o pencereyi kapat" dendiğinde `computer_windows` çağırmak yerine kullanıcıya "Bana pencere hwnd'sini söyleyin" diyordu.
- **get_system_info Spam'i:** Görevi unutup sürekli alakasız sistem bilgisi araçlarını döngüye sokuyordu.

Bu veri seti, yukarıdaki tüm patolojileri tedavi eden **"Altın Standart" (Ground Truth)** sentetik konuşmalardan oluşur.

---

## 2. Veri Formatı
Veri seti, hem HuggingFace `SFTTrainer`, hem `Unsloth`, hem `Llama-Factory`, hem de `OpenAI / Axolotl` standartlarıyla %100 uyumlu standart **ChatML / OpenAI Tool Calling JSONL** formatındadır:

```json
{
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "Not Defteri'ni aç"},
    {
      "role": "assistant",
      "content": null,
      "tool_calls": [
        {
          "id": "call_00001",
          "type": "function",
          "function": {
            "name": "computer_launch",
            "arguments": "{\"app\": \"Notepad\"}"
          }
        }
      ]
    },
    {
      "role": "tool",
      "tool_call_id": "call_00001",
      "name": "computer_launch",
      "content": "{\"status\": \"Verified\", \"method\": \"NewProcess\", \"pid\": 10120}"
    },
    {
      "role": "assistant",
      "content": "Not Defteri (Notepad) başarıyla başlatıldı ve pencere odağa alındı."
    }
  ]
}
```

---

## 3. Kapsanan Yetenek Kategorileri

1. **Uygulama Başlatma ve Keşif (Launch & Discovery):**
   - Türkçe isim -> executable/app id eşlemesi (`"Not Defteri"` -> `"Notepad"`, `"Hesap Makinesi"` -> `"Calculator"`).
   - Zaten açık olan pencerelerde (`ExistingInstance`) uydurma yapmadan `computer_windows` ile teyit etme.
2. **Pencere Yönetimi ve Kapatma (Window Lifecycle):**
   - Kullanıcıdan asla HWND istememe disiplini. Önce `computer_windows {}` ile listeleme, ardından `computer_close_window {"hwnd": "..."}` ile kapatma.
3. **Metin Girişi, Klavye ve Batch Otomasyonu:**
   - Hızlı tek adım için `computer_do`.
   - Çok adımlı sıralı işlemler için `computer_batch` (`do`, `t`, `v`, `ms`, `submit`, `keys` alanları).
   - Kısayol tuşları (`computer_hotkey`).
4. **Halüsinasyon Engelleme & Gözlem Dürüstlüğü:**
   - `computer_observe` ve `computer_find` çıktılarında yalnızca var olan elementleri aktarma. Eksik bilgiyi uydurmama.
5. **Girdi Sıfırlama ve Bekleme:**
   - `computer_reset_input` ve `computer_wait_for`.
6. **Sistem Bilgisi Ayrımı:**
   - Tarih/saat/disk için `get_system_info`, masaüstü eylemleri için `computer_*`.
7. **Çok Turlu (Multi-Turn) Uçtan Uca Oturumlar:**
   - Uygulama açma -> Metin yazma -> Kaydetme -> Kapatma döngüsü.

---

## 4. Dosya Yapısı
- `train.jsonl`: Eğitim kümesi (%85)
- `val.jsonl`: Doğrulama / test kümesi (%15)
- `dataset_full.jsonl`: Tam birleşik küme
- `build_dataset.py`: Veri setini baştan üreten, doğrulayan ve yeni senaryolarla genişletebilen motor.
