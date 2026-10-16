"""Needle backend schema + tam dongu debug betigi."""
import json
import sys

sys.path.insert(0, r"C:\Users\Ahmet\Documents\InbriskLLM")
import ai_shell  # noqa: E402

tools = [
    {"type": "function", "function": {
        "name": "computer_launch",
        "description": "Bir Windows uygulamasını ekranda (ghost: false) veya izole arka planda (ghost: true) açar/başlatır.",
        "parameters": {"type": "object", "properties": {
            "app": {"type": "string", "description": "Uygulama adı (örn: Notepad, Calculator, Paint)"},
            "ghost": {"type": "boolean", "description": "Arka planda gizlice açmak için true, normal kullanıcı ekranı için false."}
        }, "required": ["app"]}}
    },
]
backend = ai_shell.Backend("needle", "", needle_tokens=288)
schemas = backend._needle_schemas(tools)
print(json.dumps(schemas, ensure_ascii=False, indent=1))
