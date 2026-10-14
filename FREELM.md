# freelm — OPSİYONEL EK SEÇENEK (ana sistem DEĞİL)

Bu proje Inbrisk ajanını Ollama + `inbrisk.exe mcp` üzerinden çalıştırır.
**freelm bu akışın varsayılanı değildir.** Amaç: aynı ajanı (25 `computer_*`
aracıyla, chat ile PC yönetimi) ücretsiz bulut API'leri üzerinden de çalıştırabilmek.

Kaynak: https://github.com/shihabshahrier/freelm

--------------------------------------------------------------------------------
DURUM
--------------------------------------------------------------------------------
- Kurulum : freelm 0.5.2 (pip, --user) — Python 3.12.10
- Anahtar : GROQ_API_KEY (User ortam değişkeni) → `qwen/qwen3.8-27b`
           Groq: qwen/qwen3.8-27b · openai/gpt-oss-120b · gpt-oss-20b · allam-2-7b
           + keyless son care: Kilo Gateway, OVHcloud
- Doğrulama: `run_freelm.bat` ile uçtan uca denendi → MCP aracı çağrıldı,
             Türkçe tarih/saat döndü, oturum temiz kapandı (çıkış kodu 0).

--------------------------------------------------------------------------------
KULLANIM — İKİ AYRI BAŞLATICI
--------------------------------------------------------------------------------
| Dosya | Model kaynağı | Ne yapar |
| --- | --- | --- |
| `run_inbrisk.bat` | Ollama (yerel `qwen3.5:2b`) | **Varsayılan akış** — hiç değişmedi |
| `run_freelm.bat`  | freelm (Groq + keyless) | Aynı ajan, ücretsiz bulut API'si |

`run_freelm.bat` otomatik olarak:
1. `http://127.0.0.1:4000/v1` ucunu arka planda başlatır (yoksa),
2. Inbrisk MCP'yi bağlar (25 araç) ve ajanı `--backend openai` ile açar,
3. Ajan çıkınca (`cikis`) başladığı sunucuyu kapatır.

Yani davranış `run_inbrisk.bat` ile aynı: **yaz, sor, ajan PC'yi yönetsin.**
Tek fark modelin bulutta ve ücretsiz olması.

--------------------------------------------------------------------------------
ENV AYARLARI (isteğe bağlı)
--------------------------------------------------------------------------------
- `FREELM_PORT`   → port değiştir (varsayılan 4000)
- `FREELM_KEYLESS`→ `.bat` içinde varsayılan `1` (keyless son care hep açık).
                    Kapalıyken tek anahtarda sağlayıcı 503/429 verirse
                    failover'in gidecek yeri kalmaz ve oturum kırılır.

--------------------------------------------------------------------------------
FAİAL OLMADAN, DOĞRUDAN FREELM
--------------------------------------------------------------------------------
    freelm chat "Explain failover in one line"
    freelm chat "..." --model chat:fast --stream
    freelm models --provider groq      # canli model listesi
    freelm health                      # bu sürecin anahtar durumu (ağ yok)
    freelm doctor                      # anahtarları canlı test eder, düzeltme söyler

Model seçimi: `auto` her zaman en iyisini seçer. `chat:tools` alias'ı şu an boş
dönebilir (freelm'in hazır listesinde "tools" etiketli Groq modeli yok) —
fazla araç kullanan isteklerde somut id ver: `qwen/qwen3.8-27b`.

--------------------------------------------------------------------------------
config.json
--------------------------------------------------------------------------------
    "openai_url": "http://127.0.0.1:4000/v1",
    "openai_key": "freelm"

Bu sayede `python ai_shell.py --backend openai` da doğrudan çalışır ve
Ollama açıkken ajan içinde `/backend openai` ile geçiş yapılabilir.
`default_backend` **"ollama" olarak bırakıldı** — yani ana sistem değişmedi.

--------------------------------------------------------------------------------
YAPILAN KOD DEĞİŞİKLİĞİ (tek, minimal)
--------------------------------------------------------------------------------
`ai_shell.py` → `_openai_stream()`:

- imzaya `tools=None` eklendi, gövdede `"tools": tools or TOOLS` yapıldı
- çağrı yerine `getattr(cfg, "ollama_tools", None)` geçirildi

Önceden openai backend modele SADECE `get_system_info` yayınlıyordu; 25 adet
`computer_*` MCP aracı yalnızca Ollama backend'ine gidiyordu. Artık openai
(freelm) backend'i de aynı araç setini alıyor, ToolGuard/MCP yönlendirmesi zaten
ortak kodda olduğu için başka değişiklik gerekmedi. Ollama akışı etkilenmedi.

--------------------------------------------------------------------------------
DİĞER SAĞLAYICI EKLEME (failover'ı güçlendirir)
--------------------------------------------------------------------------------
Groq tek başına bazen 503 döner; ikinci bir anahtar gerçek failover sağlar.
Google AI Studio en cömert ücretsiz katmandır (kredi kartı istemez):
https://aistudio.google.com/apikey

    [Environment]::SetEnvironmentVariable("GEMINI_API_KEY","ANAHTAR","User")

Diğerleri: GROQ_API_KEY, OPENROUTER_API_KEY, CLOUDFLARE_API_TOKEN +
CLOUDFLARE_ACCOUNT_ID, MISTRAL_API_KEY, NVIDIA_API_KEY, ZAI_API_KEY,
COHERE_API_KEY, KILO_API_KEY
Aynı sağlayıcı için birden çok anahtar: virgülle ayır (`GROQ_API_KEY=k1,k2`).
Ekleme sonrası doğrula: `freelm doctor`

--------------------------------------------------------------------------------
NOTLAR / SINIRLAR
--------------------------------------------------------------------------------
- Ücretsiz katmanlar sık değişir; `freelm doctor` o an neyin çalıştığını söyler.
- Groq ücretsiz katmanı 30 req/min sınırındadır; yoğun oturumlarda ara ara 503
  görülebilir — bu sağlayıcı tarafı bir sınırdır, anahtar eklemek çözer.
- Hiçbir telemetri yok; istekler doğrudan ilgili sağlayıcıya gider.
- Kilo/OpenRouter/Z.ai ücretli modeller varsayılan olarak bloklu (free-only guard).
- İki anahtar notu: Groq anahtarı bu sohbete yazıldı. Şüphelenirsen
  console.groq.com/keys'ten iptal edip yenisini üret.
- Kalkan lisans: MIT.