"""Arac profili analizi: 2B modelin her turda tasidiyi sema yukunu olcer
ve budanmis profiller uretir.

BASELINE.md'nin uyarisi: "tools/list ~71-115 KB - every session pays it".
Native tool calling'e gecis bu maliyeti devreye soktugu icin (eski grammar
yolu Ollama'ya sema gondermezdi) model her turda ~26K token sema ile
kosullaniyor. Bu script neyin ne kadar yer tuttugunu gosterir.
"""
import json
import sys

sys.path.insert(0, r"C:\Users\Ahmet\Documents\InbriskLLM")
import ai_shell as A  # noqa: E402

OUT = r"C:\Users\Ahmet\Documents\InbriskLLM\dataset"

# Olcumde gercekten kullanilan / ihtiyac duyulan cekirdek
CORE = ["computer_windows", "computer_observe", "computer_find", "computer_launch",
        "computer_do", "computer_batch", "computer_type", "computer_click",
        "computer_close_window", "get_system_info"]


def kb(x):
    return len(json.dumps(x, ensure_ascii=False)) / 1024


def trim_schema(schema, keep_max_props=8, depth=0):
    """Sema yuzeyini kucultur: sadece yazin, kirp, aciklamalari kisalt."""
    if not isinstance(schema, dict) or depth > 3:
        return {"type": "object", "properties": {}}
    if schema.get("type") != "object" and "properties" not in schema:
        out = {k: schema[k] for k in ("type", "enum", "const") if k in schema}
        return out or {"type": "object"}
    props = schema.get("properties") or {}
    items = list(props.items())[:keep_max_props]
    return {
        "type": "object",
        "properties": {k: trim_schema(v, keep_max_props, depth + 1) for k, v in items},
        **({"required": schema["required"]} if schema.get("required") else {}),
    }


def main():
    m = A.PersistentInbriskMCP(timeout=35)
    m._refresh_tools()
    full = A.build_ollama_tools(m)

    print("=" * 64)
    print("TAM PROFIL (simdiki varsayilan)")
    print("=" * 64)
    print(f"  arac sayisi : {len(full)}")
    print(f"  toplam     : {kb(full):.1f} KB  (~{kb(full)*1024/4:.0f} token/tur)")
    print()
    print("  en buyuk 12 arac:")
    for t in sorted(full, key=lambda t: -kb(t))[:12]:
        d = len(t["function"].get("description") or "")
        s = kb(t["function"]["parameters"])
        print(f"    {t['function']['name']:<26} sema {s:6.1f} KB  aciklama {d:5d} kar")

    # --- cekirdek profil ---
    by_name = {t["function"]["name"]: t for t in full}
    core = [by_name[n] for n in CORE if n in by_name]

    print()
    print("=" * 64)
    print("CEKIRDEK PROFIL (10 arac, budanmis sema)")
    print("=" * 64)
    trimmed = []
    for t in core:
        f = t["function"]
        trimmed.append({
            "type": "function",
            "function": {
                "name": f["name"],
                "description": (f.get("description") or "")[:220],
                "parameters": trim_schema(f["parameters"]),
            },
        })
    print(f"  arac sayisi : {len(trimmed)}")
    print(f"  toplam     : {kb(trimmed):.1f} KB  (~{kb(trimmed)*1024/4:.0f} token/tur)")
    print()
    print(f"  tasarruf    : {kb(full):.1f} KB -> {kb(trimmed):.1f} KB "
          f"({100 - 100*kb(trimmed)/kb(full):.0f}% kuculme)")

    with open(f"{OUT}/tools_core_trimmed.json", "w", encoding="utf-8") as fh:
        json.dump(trimmed, fh, ensure_ascii=False, indent=1)

    # budanma olmadan cekirdek
    core_notrim = [by_name[n] for n in CORE if n in by_name]
    with open(f"{OUT}/tools_core_full_schema.json", "w", encoding="utf-8") as fh:
        json.dump(core_notrim, fh, ensure_ascii=False, indent=1)
    print(f"  (budanmamis cekirdek: {kb(core_notrim):.1f} KB)")

    print()
    print("=" * 64)
    print("ONERI")
    print("=" * 64)
    print(f"  2B model icin: tools_core_trimmed.json  ({kb(trimmed):.1f} KB)")
    print(f"  Tam yetenek  : tools.json                ({kb(full):.1f} KB)")
    print("  Inbrisk tarafinda: build_ollama_tools() profili secilebilir")
    print("  yapmalı -- bkz. config.json'a 'tool_profile' alanı.")
    m.close()


if __name__ == "__main__":
    main()