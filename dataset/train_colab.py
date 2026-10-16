# -*- coding: utf-8 -*-
"""
Inbrisk LLM - Colab Self-Contained Training Script (Qwen2.5-Coder-3B-Instruct)
Target GPU: RTX 6000 Blackwell Server Edition (102 GB VRAM)
"""

import os
import sys
import json
import random
import torch
from datasets import load_dataset
from transformers import (
    AutoModelForCausalLM,
    AutoTokenizer,
    TrainingArguments,
    Trainer,
    DataCollatorForSeq2Seq,
)

# 102 GB VRAM olduğu için Full Parameter Fine-Tuning aktif (Adaptörsüz, tek parça saf model)
USE_FULL_FINETUNING = True

MODEL_ID = "Qwen/Qwen2.5-Coder-3B-Instruct"
TRAIN_FILE = "train.jsonl"
VAL_FILE = "val.jsonl"
OUTPUT_DIR = "inbrisk-qwen2.5-coder-3b-ft"

# =============================================================================
# DATASET GENERATOR (Eğer train.jsonl yoksa otomatik sıfırdan üretir)
# =============================================================================
def ensure_dataset():
    if os.path.exists(TRAIN_FILE) and os.path.exists(VAL_FILE):
        print(f"[Dataset] '{TRAIN_FILE}' ve '{VAL_FILE}' mevcut, doğrudan kullanılıyor.")
        return

    print(f"[Dataset] '{TRAIN_FILE}' bulunamadı! Altın standart Inbrisk veri seti sıfırdan üretiliyor...")

    SYS_P = (
        "Sen Inbrisk'sin: yerel bir Windows masaustu otomasyon ajani.\n"
        "Sana 25 tane computer_* araci ve 1 tane get_system_info araci sunuluyor.\n"
        "computer_* araclari masaustunu DOGRUDAN yonetir ve DOGRUDAN cagrilir;\n"
        "tool_name/arguments sarmalamasi YOKTUR, aracin kendi parametrelerini alir:\n"
        '  computer_launch   {"app": "Notepad"}\n'
        '  computer_launch   {"app": "Notepad", "ghost": true}\n'
        '  computer_do       {"app": "Notepad", "type": "Merhaba", "submit": true}\n'
        '  computer_windows  {}\n'
        "get_system_info yalnizca sistem bilgisi verir (topic: datetime, cwd, hostname, user, os, disk); masaustu otomasyonu YAPMAZ.\n"
        "Uygulama acma, tiklama, yazma, tus, pencere kapatma ve ekran gozlemleme istekleri SADECE computer_* araclariyla yapilir.\n\n"
        "GHOST MODU (ARKA PLAN OTURUMU):\n"
        "- Varsayilan olarak tum islemler kullanicinin aktif ekraninda yapilir (ghost: false).\n"
        "- Kullanici 'arka planda', 'arkada', 'sessizce', 'ghost modunda', 'ekranimi mesgul etmeden' derse ilgili tum computer_* araclarina 'ghost: true' parametresi eklenir.\n\n"
        "KURALLAR:\n"
        "- Eylem gerekiyorsa tool cagir; gerekmiyorsa dogrudan cevap ver.\n"
        "- Tool adini, argumanlari, pencere kimligini, element id'sini ASLA uydurma.\n"
        "- Element id'leri YALNIZCA gordugun son gozlemden al.\n"
        "- Gozlem kismiysa eksiktir; gormedigini anlatma.\n"
        "- Bilmedigini uydurmak yerine gozlem yap ve emin ol. Her zaman Turkce cevap ver."
    )

    apps = [
        ("Not Defteri", "Notepad", 10100, "Adsız - Not Defteri"),
        ("Hesap Makinesi", "Calculator", 10200, "Hesap Makinesi"),
        ("Paint", "mspaint", 10300, "Adsız - Paint"),
        ("Dosya Gezgini", "explorer", 10400, "Bu Bilgisayar"),
        ("Microsoft Edge", "msedge", 10500, "Yeni Sekme - Edge"),
        ("Google Chrome", "chrome", 10600, "Yeni Sekme - Chrome"),
        ("Spotify", "Spotify", 10700, "Spotify"),
        ("Visual Studio Code", "code", 10800, "Visual Studio Code"),
    ]

    samples = []
    c_id = 0

    def tc_dict(name, args):
        nonlocal c_id
        c_id += 1
        return {"id": f"call_{c_id:05d}", "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}

    for tr_name, app_id, pid, title in apps:
        # Launch (4 varyasyon)
        for p in [f"{tr_name}'ni aç", f"{tr_name}'ni açar mısın", f"{tr_name} uygulamasını başlat", f"Bana {tr_name} penceresini aç"]:
            tc = tc_dict("computer_launch", {"app": app_id})
            res = {"status": "Verified", "method": "NewProcess", "pid": pid, "window": {"hwnd": f"0x{pid:06x}", "title": title}}
            samples.append({
                "messages": [
                    {"role": "system", "content": SYS_P},
                    {"role": "user", "content": p},
                    {"role": "assistant", "content": None, "tool_calls": [tc]},
                    {"role": "tool", "tool_call_id": tc["id"], "name": "computer_launch", "content": json.dumps(res)},
                    {"role": "assistant", "content": f"{tr_name} başarıyla açıldı."}
                ]
            })

        # Close
        for p in [f"Şimdi o pencereyi kapat", f"{tr_name}'ni kapatır mısın", f"Açık olan {tr_name} penceresini kapat"]:
            hwnd = f"0x00{pid:06x}"
            tc1 = tc_dict("computer_windows", {})
            res1 = {"windows": [{"hwnd": hwnd, "title": title, "process": app_id.lower()}]}
            tc2 = tc_dict("computer_close_window", {"hwnd": hwnd})
            res2 = {"status": "Verified", "closed": True, "hwnd": hwnd}
            samples.append({
                "messages": [
                    {"role": "system", "content": SYS_P},
                    {"role": "user", "content": p},
                    {"role": "assistant", "content": None, "tool_calls": [tc1]},
                    {"role": "tool", "tool_call_id": tc1["id"], "name": "computer_windows", "content": json.dumps(res1)},
                    {"role": "assistant", "content": None, "tool_calls": [tc2]},
                    {"role": "tool", "tool_call_id": tc2["id"], "name": "computer_close_window", "content": json.dumps(res2)},
                    {"role": "assistant", "content": f"{tr_name} penceresi başarıyla kapatıldı."}
                ]
            })

    # Direct chat
    directs = [
        ("Merhaba", "Merhaba! Ben Inbrisk masaüstü otomasyon asistanınızım. Size nasıl yardımcı olabilirim?"),
        ("Sen kimsin?", "Ben Inbrisk: Windows üzerinde pencereleri ve masaüstü görevlerini yöneten yerel yapay zeka asistanıyım."),
        ("Neler yapabilirsin?", "Uygulamaları açabilir, pencereleri kapatabilir, metin yazabilir, tıklayabilir ve masaüstünü gözlemleyebilirim."),
    ]
    for u, a in directs:
        samples.append({"messages": [{"role": "system", "content": SYS_P}, {"role": "user", "content": u}, {"role": "assistant", "content": a}]})

    random.seed(42)
    random.shuffle(samples)
    split = int(len(samples) * 0.85)

    with open(TRAIN_FILE, "w", encoding="utf-8") as f:
        for s in samples[:split]:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")
    with open(VAL_FILE, "w", encoding="utf-8") as f:
        for s in samples[split:]:
            f.write(json.dumps(s, ensure_ascii=False) + "\n")

    print(f"[Dataset] Tamamlandı: {len(samples[:split])} train, {len(samples[split:])} val satırı üretildi.")

# =============================================================================
# EĞİTİM MOTORU
# =============================================================================
def main():
    print(f"[1/5] GPU ve Donanım Kontrolü...")
    print(f"CUDA Mevcut: {torch.cuda.is_available()}")
    if torch.cuda.is_available():
        print(f"Aygıt: {torch.cuda.get_device_name(0)}")
        print(f"Toplam VRAM: {torch.cuda.get_device_properties(0).total_memory / 1e9:.2f} GB")

    # Veri setini doğrula / yoksa otomatik oluştur
    ensure_dataset()

    print(f"[2/5] Tokenizer ve Model Yükleniyor: {MODEL_ID} (bfloat16)...")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, trust_remote_code=True)
    if tokenizer.pad_token is None:
        tokenizer.pad_token = tokenizer.eos_token

    # 102 GB VRAM gücü: Saf bfloat16 & SDPA / FlashAttention
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_ID,
        torch_dtype=torch.bfloat16,
        attn_implementation="sdpa",
        device_map="auto",
        trust_remote_code=True,
    )

    print(f"[3/5] 102 GB VRAM İle FULL PARAMETER FINE-TUNING Aktif! (Tüm model eğitiliyor)...")
    for param in model.parameters():
        param.requires_grad = True

    print(f"[4/5] Dataset Tokenlaştırılıyor (ChatML)...")
    raw_dataset = load_dataset("json", data_files={"train": TRAIN_FILE, "validation": VAL_FILE})

    def tokenize_func(examples):
        input_ids_list = []
        labels_list = []
        for convo in examples["messages"]:
            text = tokenizer.apply_chat_template(convo, tokenize=False, add_generation_prompt=False)
            enc = tokenizer(text, truncation=True, max_length=8192)
            input_ids_list.append(enc["input_ids"])
            labels_list.append(enc["input_ids"].copy())
        return {"input_ids": input_ids_list, "labels": labels_list}

    train_data = raw_dataset["train"].map(tokenize_func, batched=True, remove_columns=["messages"])
    val_data = raw_dataset["validation"].map(tokenize_func, batched=True, remove_columns=["messages"])

    print(f"[5/5] SFT Eğitimi Başlatılıyor (HuggingFace Standart Trainer)...")
    training_args = TrainingArguments(
        output_dir=OUTPUT_DIR,
        per_device_train_batch_size=4,
        gradient_accumulation_steps=2,
        num_train_epochs=4,
        learning_rate=2e-5,
        lr_scheduler_type="cosine",
        warmup_steps=5,
        logging_steps=5,
        save_strategy="no",
        bf16=True,
        optim="adamw_torch",
        weight_decay=0.01,
        report_to="none",
    )

    data_collator = DataCollatorForSeq2Seq(
        tokenizer=tokenizer,
        model=model,
        pad_to_multiple_of=8,
    )

    trainer = Trainer(
        model=model,
        args=training_args,
        train_dataset=train_data,
        eval_dataset=val_data,
        data_collator=data_collator,
    )

    trainer.train()

    print("Eğitim Tamamlandı! Model kaydediliyor...")
    final_save_path = os.path.join(OUTPUT_DIR, "final_model")
    trainer.save_model(final_save_path)
    tokenizer.save_pretrained(final_save_path)
    print(f"\n🎉 Model başarıyla hazırlandı ve kaydedildi: {final_save_path}")

if __name__ == "__main__":
    main()
