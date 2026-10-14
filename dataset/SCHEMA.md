# Inbrisk Fine-Tuning Veri Seti — Şema ve Kullanım

## Dosyalar

| Dosya | İçerik |
|---|---|
| `build_dataset.py` | Üretici. Örnekleri kurar **ve ToolGuard'dan geçirerek** yazar. |
| `profile_tools.py` | Araç profili analizi (yük ölçümü + budama). |
| `tools.json` | Tam araç listesi, 26 araç, 103.6 KB. **Tek kez yüklenir.** |
| `tools_core_full_schema.json` | 10 araç, budanmamış şema, 59.9 KB. |
| `tools_core_trimmed.json` | 10 araç, budanmış şema, **7.6 KB**. 2B için önerilen. |
| `train.jsonl` / `val.jsonl` / `test.jsonl` | Bölünmüş örnekler. |

## Örnek formatı

```json
{
  "id": "close_probe_before_refuse",
  "messages": [
    {"role": "system",    "content": "<SYSTEM_PROMPT>"},
    {"role": "user",      "content": "Şimdi o pencereyi kapat"},
    {"role": "assistant", "tool_calls": [
        {"type": "function",
         "function": {"name": "computer_windows",
                      "arguments": "{}"}}]},
    {"role": "tool", "name": "computer_windows", "content": "<gerçek çıktı>"},
    {"role": "assistant", "content": "..."}
  ],
  "meta": {"tags": ["close", "probe_first"], "note": "..."}
}
```

### Neden `tools` örnek içinde değil

26 aracın şeması **103.6 KB (~26.5K token)**. Örnek başına gömülürse 100
örnek = 10 MB'ın ~%96'sı tekrardır ve eğitim kaybı şema token'larına boğulur.

Bu maliyet **native tool calling'e geçişle devreye girdi**: eski grammar yolu
(`format:` ile Structured Outputs) Ollama'ya şema göndermiyordu. Yani 2B model
artık her turda ~26.5K token'lık şema ile koşullanıyor.

`tools.json` tek dosyada durur; Colab yükleyici derleme anında mesajlara ekler.

## Doğrulama garantisi

`build_dataset.py` her tool çağrısını Inbrisk'in kendi ToolGuard'ından geçirir:

```python
clean = A.normalize_tool_args(tool, args)
A.validate_tool_call(tool, clean)     # canlı MCP inputSchema'ya karşı jsonschema
```

Geçersiz çağrı veri setine **giremez**. Bu yapısal bir koruma — üretim sırasında
bir örnek reddedilirse `rejected` listesine düşer ve dosyaya yazılmaz.

Pratikte bu koruma işe yaradı: yazım sırasında
`computer_close_window {"app": "notepad"}` örneğini reddetti (geçersiz alan),
çünkü hedefleme `hwnd`/`titleContains`/`process` ile yapılır.

## Serileştirme — template'i elle yazma

```python
tokenizer.apply_chat_template(
    messages, tools=tools, add_generation_prompt=False, tokenize=False)
```

Template sözdizimi modele özeldir ve sürümle değişir. Elle yazmak sessizce
yanlış format üretir.

## Loss masking

Yalnızca `assistant` mesajlarında kayıp. `system`, `user` ve `tool` mesajları
maskelenmeli. Aksi halde model tool çıktılarını da üretmeyi öğrenir — ki bu
tam olarak uydurma davranışıdır.

## Öğretilen davranışlar (ölçülen hatalara birebir)

| Etiket | Kaynak |
|---|---|
| `guard_recovery` | Toparlanma 0/5 ölçüldü — birincil hedef |
| `no_fabrication` | `launchState`, `uia_24036_97`, "916 MB" uydurmaları |
| `probe_first`, `no_lazy_refusal` | "hwnd'yi söyleyin" diye hiçbir şey yapmama |
| `honest_partial` | 60/120 gözlemde görülmeyeni anlatma |
| `act`, `no_narration` | "tıklayacağım" deyip yapmama |
| `candidates_not_permission` | `Malformed` dönen `candidates` → kapatma izni değil |
| `no_repeat` | Aynı aracı tekrar etmeme |
| `untrusted_content` | Ekran metninin talimat sayılmaması |
| `trust_runtime` | `KONTROL UYARISI` notunu ciddiye alma |
| `no_shell_escape` | PowerShell kaçışı (araç kaldırıldı) |

## Colab'da yükleme

```python
import json, datasets
tools = json.load(open("dataset/tools.json"))
rows  = [json.loads(l) for l in open("dataset/train.jsonl", encoding="utf-8")]
ds = datasets.Dataset.from_list(rows)

def render(ex):
    return tokenizer.apply_chat_template(
        ex["messages"], tools=tools, add_generation_prompt=False, tokenize=False)
ds = ds.map(lambda ex: {"text": render(ex)}, remove_columns=ds.column_names)
```

## Bilinen eksikler (bunları eğitimden ÖNCE kapat)

1. **22 örnek çok az.** 500–2000 hedeflenmeli. Elle yazım sürdürülemez —
   üretici genişletilmeli ya da gerçek oturum trafiği toplanmalı.
2. **Uygulama adları Türkçe.** Örneklerde Not Defteri/Hesap Makinesi kullanıldı;
   İngilizce ortam için varyantlar gerekir.
3. **`safety.md` kararlarının hepsi kapsanmıyor.** `EmergencyStopped`,
   `InterruptedByDialog`, `SharedProcessKillRefused`, `ConfirmationRequired`
   örnekleri eksik — `C:\Users\Ahmet\Documents\inbrisk\.agents\skills\inbrisk\references\safety.md`
   yetkili kaynak.
4. **Çok adımlı görevler yok.** En uzun örnek 3 adım. 5+ adımlı zincirler
   ölçülebilir değil.
5. **Gerçek trace yok.** Metinler kurgusal, *biçimler* gerçek. Gerçek
   oturum kaydı toplanırsa `build_dataset.py`'ye bir içe aktarma yolu eklenmeli.
6. **Sadece başarı yolu.** Her aracın "mutlak sürülmesi" örneği yok —
   sadece düzeltilmiş hâli var.