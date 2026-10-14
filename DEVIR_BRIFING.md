# İNBRİSK — DEVİR BRİFİNGİ

Aşağıdaki metnin tamamını oku, sonra bana sorular sor. Görevim Inbrisk adlı
Windows masaüstü otomasyon agent'ını inşa etmek. Bugüne kadar birlikte çalıştık,
sonucu ve bundan sonrası için senden şunları istiyorum.

================================================================================
1. PROJE NEDİR
================================================================================

`C:\Users\Ahmet\Documents\InbriskLLM\ai_shell.py` — tek dosya, ~1900 satır.
Yerel LLM'e Windows masaüstünü yönetme yeteneği veren bir terminal ajanı.
Çalışma şekli: Ollama üzerinden yerel model + `inbrisk.exe mcp` sunucusu (25 adet
`computer_*` aracı, C#/UIA tabanlı) + kalıcı PowerShell oturumu.

Başlangıçta agent mimarisi iki ayrı yoldan oluşuyordu ve bu çalışmanın ana
konusu buydu.

================================================================================
2. BAŞLANGIÇ DURUMU VE ÖLÇÜM
================================================================================

Mevcut modeller: `inbrisk-agent` (fine-tune 1.7B), `qwen3-coder:30b`,
`qwen2.5:7b`, `qwen3:4b`, `qwen2.5:3b`.

İstenen "qwen 1.7b base model" Ollama'da **yok**. Doğrusu:
  - `qwen:1.8b-text` = gerçek base/completion model (indirildi, HİÇ kullanılmadı)
  - `qwen:1.8b` = chat-tuned, `1.8b-chat` ile aynı hash
Ardından "tool kullanabilen model" istendi → `qwen3.5:2b` (2.7 GB, 256K context,
tools + vision + thinking) indirildi ve **tüm çalışma bu model üzerinde** yürüdü.

ÖNEMLİ: Ölçtüğümüz şey `qwen3.5:2b`, yani **eğitilmiş (instruct) bir model**,
ham base değil. Bu ayrım fine-tuning kararı için kritik.

================================================================================
3. MİMARİ ANALİZ VE BİRLEŞTİRME
================================================================================

Kodda iki ayrı ajan yolu bulundu:

YOL A (`--backend inbrisk-agent`):
  - `_ollama_agent_raw()` modeli grammar-constrained JSON üretmeye zorluyordu
  - `build_tool_schema()` ile `{"name": <enum>, "arguments": {...}}` şeması
  - Modelden **her koşulda** bir tool çağrısı bekleniyordu, "iş bitti" çıkışı
    yoktu → `computer_windows` tekrar edip `MAX_STALL` devreye girene dek döngü
  - `INBRISK_AGENT_SYS_PROMPT` (5 cümle), ayrı ve izole konuşma geçmişi

YOL B (`--backend ollama`):
  - Ollama native tool calling, `tools` dizisi ile
  - Kalıcı `messages` geçmişi, `SYSTEM_PROMPT` (≈48 satır), `TOOLS` = 2 araç

Karar: **Yol B esas alındı, Yol A emekliye ayrıldı.** AST tabanlı silme ile 377
satır çıkarıldı (`run_inbrisk_agent_turn`, `_agent_summary`, `_ollama_agent_raw`,
`build_tool_schema`, `extract_json_call`, `filter_tools_for_intent`,
`INTENT_TOOL_MAP`, `INBRISK_AGENT_SYS_PROMPT`, `AGENT_FINISH*`, `_with_no_think`).
Silme öncesi "silinen bloklar dışında referans kalmıyor" kontrolü çalıştırıldı.

================================================================================
4. ÖLÇÜLEN SONUÇLAR (qwen3.5:2b, ham, fine-tuning YOK)
================================================================================

5 senaryo: (1) Merhaba (2) Not Defteri'ni aç (3) Not Defteri'ne Merhaba yaz
(4) Şimdi o pencereyi kapat (5) Ekranda ne var?

Son koşu (temiz mimari):
  İlk denemede geçerli parametre ....... 9/9   (guard reddi: 0)
  İlk denemede doğru araç seçimi ........ 5/5
  Görev tamamlama ....................... 0/5
  Gözleme sadakat ...................... iyi
  Guard reddi sonrası toparlanma ........ ölçülemedi (red kalmadı)

Kalan hata kalıpları — hepsi MODELDE, runtime'da değil:
  (a) UYDURMA. `computer_launch {"app":"notepad"}` başarıyla döndü, gerçek sonuç
      `{"status":"Verified","method":"ExistingInstance","pid":15056}`. Model
      buna bakıp `launchState:"AlreadyRunning"`, `uia_24036_97` (hayali element
      id), "sağ altta Chrome, 916 MB bellek uyarısı" gibi **sonuçta hiç
      olmayan** şeyler uydurdu ve emoji'li bir analiz raporu üretti.
  (b) ANLATMA-EYLEMEME. "Önce pencereyi bulacağım, sonra tıklayacağım, sonra
      yazacağım" diyor, hiçbirini yapmıyor.
  (c) KEŞFETMEDEN REDDETME. "Şimdi o pencereyi kapat" isteğinde
      `computer_windows` çağırmak yerine kullanıcıya "hwnd'yi söyleyin" diyor.
      (Yanlış pencere kapatmadı — iyi — ama sıfır eylem.)
  (d) get_system_info spam — model görevi bırakıp yeni eklediğim aracı 5 kez çağırdı.

================================================================================
5. GÜVENLİK VE KALİTE KATMANLARI (şu anki mimari)
================================================================================

TEK yürütme kapısı: `execute_mcp_guarded(mcp, name, args, task_state, ...)`
  1) `normalize_tool_args()` — alias normalize → bilinmeyen alanı **reddet** →
     şema tipine göre JSON çöz (çift tırnaklı ve tek tırnaklı pseudo-JSON)
  2) `validate_tool_call()` — canlı MCP inputSchema'ya karşı jsonschema
     doğrulama. Şemalar `fetch_tool_arg_schemas()` ile sunucudan canlı çekiliyor (25 adet)
  3) `check_window_close()` — pencere sahipliği (TaskState)
  4) çalıştır → sonucu kaydet → gözlem araçlarındaysa `process_observation()`

Pencere sahipliği: `TaskState.opened` = {hwnd, pid, title}. Hata sonucunda dönen
`candidates` listesinin kapanma yetkisi YOKTUR. Onaysız sessiz kapatma yolu
kapatıldı; kullanıcı onayı `input()` ile alınır (piped stdin'de EOF → güvenli
varsayılan: red).

Observation Processor: deterministik Python (LLM değil — ikinci bir LLM yeni
halüsinasyon kaynağı olurdu). Kesikliği açıkça işaretler:
  "KISMIR GORLEM: yalnizca 60/120 UI elementi gonderildi. Gorunmeyen elementler
   hakkinda HICBIR sey bilmiyorsun; onlari uydurma."

Araç yayını: 25 adet `computer_*` + 1 `get_system_info` = 26 araç, **düz**
sunuluyor. `run_shell` ve `inbrisk_mcp` kaldırıldı, `agent_finish` kaldırıldı.

================================================================================
6. ÇÖZÜLMESİ GEREKEN TUZAKLAR (tekrar etme!)
================================================================================

6.1 `observationId` güvenilir DEĞİL. Aynı değişmeyen ekranda 1→2→2 ilerliyor.
    `computer_observe` çağrıları arasında anlamsal ekran durumunu temsil etmiyor.
    → Çözüm: `observation_fingerprint()` içerik-bazlı hash. Dışarıda bırakılanlar:
      observationId/süre sayaçları, `obj=<runtime pointer>`, `stats:`,
      `delta since snapshot`, OCR metni (alt piksel gürültüsü kararsız).
      Element id'lerinin SAYISAL kısmı da nötralize ediliyor çünkü
      `uia_13580_11` her gözlemde `uia_13580_28` oluyor. Satırlar sıralanıyor.
      Not: element id'leri sunucuda CANLI kalıyor (3 gözlem sonra eski id hâlâ
      çözülüyor, `stale=False`), yeniden numaralama sadece listeleme çıktısında.
    → Hâlâ EKSİK: UI fingerprint == görev ilerlemesi DEĞİLDİR. Bir arka plan
      işlemi ekranda iz bırakmaz ama görev ilerlemiş olur. İki ayrı sinyal
      (UI fingerprint + task progress) kullanılmalı. YAPILMADI.

6.2 `run_shell` kaldırıldı çünkü güvenlik + araç seçimi. Regex beyaz liste
    `Get-Date -Format $(Remove-Item C:\test.txt)` ile atlatılabiliyordu.
    Yerine sabit komut tablosuna dayalı `get_system_info(topic)`.

6.3 MCP şemalarında `additionalProperties` KAPALI DEĞİL → jsonschema tek
    başına bilinmeyen alanları yakalamıyor, kendi kontrolümüz şart.
    Canlı şemalar `TOOL_ARG_ALLOWED` ile birebir aynı (sürüklenme yok).

6.4 AZ ARAÇ SEÇİM SORUNU DEĞİLDİ, PROMPT/ENTEGRASYON SORUNUYDU. Bir koşuda
    model "Not Defteri'ni aç" için 5/5 doğru araç (`computer_launch`) seçti ama
    hepsi reddedildi — çünkü ben `inbrisk_mcp`'yi yayınlamayı kaldırmış,
    SYSTEM_PROMPT'taki `{"tool_name": ..., "arguments": ...}` örneğini
    unutmuştum. Model eski iki kademeli alışkanlığını sürdürüyordu.

6.5 UZUN/ÇELİŞKİLİ PROMPT REGRESYON YAPTI. SYSTEM_PROMPT'a İngilizce kural
    bloğunu eski Türkçe bloğun ÖNÜNE ekleyince model tamamen `run_shell`e
    (`Get-ChildItem` döngüsü) sapmış, MCP'ye hiç yönelmemiş ve İngilizce
    yanıt vermişti. Sebep: eski bloktaki "her istek için run_shell çağır"
    cümlesi art arda geldiği için baskın çıktı.
    → DERS: uzunluk değil TUTARLILIK sorun. Çelişkili prompt 2B modeli bozar.

6.6 `computer_observe`/`computer_inspect`/`computer_windows`/`computer_find`
    dördü de "bak" diyor ve model bunları karıştırıyor. `computer_inspect`
    hwnd'siz çağrılınca ön plan penceresini hedefliyor → YANLIŞ pencere.

================================================================================
7. MODEL KARARI
================================================================================

DONANIM: NVIDIA GeForce GTX 1650 — 4 GB VRAM. 32 GB RAM.
  qwen3.5:2b  2.7 GB → ✅ VRAM'e sığar
  qwen3.5:4b  3.3–4.0 GB → ⚠️ taşma
  qwen3.5:9b  6.6–7.6 GB → ❌ RAM üzerinden
  (Kurulu 30b modeller de RAM'de çalışıyor; kullanıcı RAM-hızı inference'i
   tolere ediyor, yani "yavaş olur" itirazı geçerli değil. BAĞLAYICI OLAN
   VRAM sınırı inference değil, FINE-TUNING.)

ÖNERİ (kullanıcıyla paylaşıldı): **Önce fine-tune, model yükseltme.**
Gerekçe: arayüz katmanı çalışıyor (9/9 parametre, 5/5 araç); kalan hatalar
davranışsal ve eğitim verisiyle düzeltilir. Daha büyük modele geçersek
fine-tuning'in gerçekten gerekli olup olmadığını asla test etmemiş oluruz.

`qwen3.5:4b` upgrade ÖNERİLMEDİ — bu görev için 2B'dan anlamlı üstün değil,
sadece VRAM'i taşır.

YÜKSELTME EŞİĞİ (şimdiden sabitlendi): fine-tune sonrası benchmark'ta
  (1) uydurma azalmıyorsa
  (2) "o pencere" gibi bağlam çözümlemede kalıcı hata varsa
  (3) 5 adımlı bir görevi tek seferde bitiremiyorsa
→ o zaman 4B'ye geç.

================================================================================
8. SORU: MODELİ KİLİTLELİMİ Mİ?
================================================================================

Benim görüşüm, doğrula ve gerekiyorsa düzelt:

KİLİTLE — eğitim girdisi olan BASE model:
  `Qwen/Qwen3.5-2B-Base` HuggingFace'de var (Ollama'da `-base` tag'i YOK;
  Ollama'daki `qwen:1.8b-text` Qwen1.5 ailesinden, farklı ve eski — eğitim
  hedefi olarak kullanma). Eğitilecek ağırlıkların tekrar üretilebilir
  olması için HF'de revision SHA'sı sabitlenmeli ve `snapshot_download`
  ile `local_dir`'ye indirilip `local_files_only=True` ile okunmalı.
  Also record: base model id + revision + tokenizer revision + dataset hash
  + seed + the exact LoRA hyperparameters + library versions.

KİLİTLEME — inference modeli: `qwen3.5:2b` Ollama digest `0689d44085e0`.
  Mimari hâlâ değişiyor (get_system_info spam düzeltmesi yapılacak), benchmark
  tekrarlanacak. Kilitlersek bu iterasyonları kaybederiz. Kilit kararı
  benchmark'ın "olgunlaştı" ilanından sonra verilmeli.

KİLİTLE — mimari referansı: `ai_shell.py.unified.bak` (birleştirme öncesi),
`ai_shell.py.prefuse.bak`, `ai_shell.py.preguard2.bak`, `config.json.bak`.
  Bu dizi kalıcı arşiv olmalı; repo git değil.

================================================================================
9. BİZDEN SONRAKİ ADIM: FINE-TUNING VERİ SETİ
================================================================================

`agent_finish` gibi özel bir bitiş komutu ÖĞRETMEYECEĞİZ — native tool-calling
geçmişi öğretiyoruz, doğal sonlandırma zaten yapısal olarak var.
Gemini'nin hazırladığı düz JSON şeması eğitim için KULLANILMAYACAK.

Veri seti, modelin kendi chat template'iyle serileştirilmeli. Tokenizer'ın
`apply_chat_template(messages, tools=tools, add_generation_prompt=True)`
fonksiyonunu kullan — template sözdizimini elle yazma.

Hedef davranışlar (ölçülmüş hatalara birebir karşılık geliyor):
  1. EYLEME GEÇ — plan metni yazma, tek araç çağrısı yap
  2. UYDURMA — sonuçta olmayan alan/id/boyut/sayı üretme
  3. KEŞFETMEDEN REDDETME — `computer_close_window` öncesi `computer_windows`
  4. GÖZLEMİ TEKRARLAMA — hedefli argümanla ilerle
  5. PARÇALI GÖZLEM DÜRÜSTLÜĞÜ — görülmeyeni anlatma

Olumlu örnek (gerçek bir koşudan):
  system: <SYSTEM_PROMPT + araç şemaları>
  user: "Not Defteri'ni aç"
  assistant: tool_calls: computer_launch({"app":"notepad"})
  tool: {"status":"Verified","method":"ExistingInstance","pid":15056,...}
  assistant: tool_calls: computer_windows({})
  tool: <pencere listesi>
  assistant: "Not Defteri zaten çalışıyormuş; pencereyi buldum."

Bu, (a) uydurmayı ve (b) anlatma-eylememe'yi aynı anda öğretiyor.

================================================================================
10. İSTEDİĞİM ÇIKTI (sırayla)
================================================================================

(A) Bu brifingi okuduğunu doğrula ve **6. bölümdeki tuzaklardan birine düştüğünü
    görüyorsan SÖYLE** — özellikle 6.1 (fingerprint ≠ görev ilerlemesi) hâlâ
    açık bir borç olarak duruyor.

(B) Model kilitleme kararını netleştir: kilitlenecek tam olarak ne, nerede,
    hangi mekanizmayla. 8. bölümdeki önerimi onayla veya gerekçesiyle reddet.

(C) `Qwen/Qwen3.5-2B-Base` gerçekten HuggingFace'de var mı, tam repo id'si ve
    base/instruct ayrımı nedir? Bu tür 2B bir modeli QLoRA ile Colab T4'te
    eğitmek için pratik mi, yoksa T4 16 GB'ı 4-bit 2B için yeterli mi?
    Colab'da hangi GPU sınıfı garanti edilebilir?

(D) **Google Colab için üRETİM KALİTESİNDE EĞİTİM HÜCRELERİ YAZ.**
    Aşağıdaki taslağı ele al, düzelt ve genişlet. Gereksinimler:
      - Colab T4 (16 GB) varsayımı; 4-bit QLoRA
      - Yalnızca ASSISTANT mesajlarında kayıp (loss masking) — tool çıktıları
        ve kullanıcı mesajları maskelenmeli
      - `apply_chat_template` ile serileştirme
      - Dataset şeması: mesaj listesi + tools listesi; kaynağı JSONL
      - LoRA: r/alfa/dropout/target_modules, bf16, gradient accumulation,
        cosine schedule, warmup
      - Bölünme: train/val, seed sabitli
      - Eğitim sonrası: Ollama'a aktarılabilir GGUF üretimi veya doğrudan
        HF ile yerel çıkarım
      - Otomatik benchmark: 5 senaryo, bizim ölçtüğümüz 5 metrik
        (geçerli parametre oranı, doğru araç oranı, guard reddi, görev
        tamamlama, toparlanma oranı)
      - Hata ayıklama için tek örnekli "smoke test" hücresi

    Taslak iskelet (doldurmanızı bekliyorum):
      pip install: bitsandbytes, transformers, accelerate, peft, trl, datasets
      model: AutoModelForCausalLM + BitsAndBytesConfig(nf4, double quant, bf16)
      peft:  LoraConfig(task_type="CAUSAL_LM", target_modules=[q,k,v,o,
              gate,up,down]_proj)
      train: TRL SFTTrainer, assistant-only loss, seq len 4096, packing

    YANLİSLARLARI DÜZELTME: bu taslak henüz çalıştırılmadı ve doğrulanmadı.
    Özellikle hedef modül adlarının Qwen3.5 mimarisinde doğru olduğunu ve
    TRL sürümünde assistant-only loss'un API'sinin değişmiş olabileceğini
    kontrol et — TRL bunu sürümden sürüme değiştirdi.

(E) Son olarak: fine-tuning ile ULAŞILAMAZ olan şeyleri açıkça ayır.
    Özellikle (i) "o pencere" gibi referans çözümleme, (ii) `ExistingInstance`
    dönen bir sonucun yanlış pencereyi işaret ettiğini fark etme.
    Bunlar kapasite mi davranış mı? Eğitimle ne kadar iyileşir?