import os
import sys
import time
import json
import argparse

import ai_shell

def run_all_scenarios():
    print("=" * 70)
    print("INBRISK LLM AUTOMATED TEST RUNNER (4 SCENARIOS)")
    print("=" * 70)

    cfg_dict = ai_shell.load_config()
    model = cfg_dict.get("local_agent_model", "inbrisk-3b")
    inbrisk_exe = cfg_dict.get("inbrisk_exe", ai_shell.DEFAULT_INBRISK_EXE)
    timeout = cfg_dict.get("timeout", 60)
    iterations = cfg_dict.get("iterations", 10)

    # Prepare CLI-like cfg
    class Cfg:
        pass
    cfg = Cfg()
    cfg.model = model
    cfg.backend = "ollama"
    cfg.base_url = ""
    cfg.api_key = ""
    cfg.timeout = timeout
    cfg.iterations = iterations
    cfg.inbrisk_exe = inbrisk_exe
    cfg.mcp_arg = ["--yes"]
    cfg.mcp_yes = True
    cfg.agent_keep_alive = 600
    cfg.agent_temperature = 0.0

    print(f"Connecting to Inbrisk MCP: {inbrisk_exe} with model {model}...")
    logger = ai_shell.Logger(ai_shell.LOGS_DIR)
    mcp = ai_shell.PersistentInbriskMCP(exe_path=cfg.inbrisk_exe,
                                        timeout=max(cfg.timeout, 35),
                                        logger=logger,
                                        server_args=cfg.mcp_arg)
    ai_shell._policy_mcp(mcp)
    ai_shell.fetch_server_policy(mcp)
    cfg.ollama_tools = ai_shell.build_ollama_tools(mcp)
    shell = ai_shell.PersistentShell(timeout=cfg.timeout)

    base_system_prompt = (ai_shell.build_system_prompt(mcp)
                          + f" Calisma dizini: {os.getcwd()}.")

    scenarios = [
        {
            "name": "Senaryo 1: Not Defteri",
            "prompts": [
                "Not defterini aç",
                "Not Defterine 'Inbrisk LLM Test' yaz",
                "Pencereyi kapat"
            ]
        },
        {
            "name": "Senaryo 2: Hesap Makinesi",
            "prompts": [
                "Hesap makinesini aç",
                "hesap makinesini kapat"
            ]
        },
        {
            "name": "Senaryo 3: Chrome & ProtectedWindow Testi",
            "prompts": [
                "Chrome'u aç",
                "Chrome'u kapat"
            ]
        },
        {
            "name": "Senaryo 4: Sistem & Gezgin",
            "prompts": [
                "Sistem saatini öğren",
                "Dosya Gezginini aç"
            ]
        }
    ]

    report = []

    for sc_idx, sc in enumerate(scenarios, 1):
        print("\n" + "#" * 70)
        print(f"BASLATILIYOR: [{sc_idx}/4] {sc['name']}")
        print("#" * 70)

        # Fresh conversation & task state for each scenario
        task_state = ai_shell.TaskState()
        cfg.task_state = task_state
        messages = [{"role": "system", "content": base_system_prompt}]

        scenario_result = {
            "scenario": sc["name"],
            "turns": []
        }

        for turn_idx, prompt in enumerate(sc["prompts"], 1):
            print(f"\n--- [Turn {turn_idx}] Kullanici: '{prompt}' ---")
            messages.append({"role": "user", "content": prompt})
            logger.event("user", text=prompt)

            t0 = time.time()
            prev_msg_count = len(messages)

            try:
                ai_shell.agent_turn(model=cfg.model, messages=messages, shell=shell,
                                   logger=logger, cfg=cfg, max_iters=cfg.iterations,
                                   mcp=mcp)
            except Exception as e:
                print(f"[TUR HATASI] {e}")

            turn_duration = round(time.time() - t0, 2)

            # Analyze newly generated messages
            new_msgs = messages[prev_msg_count:]
            tool_calls_made = []
            assistant_replies = []

            for m in new_msgs:
                if m.get("role") == "assistant":
                    if m.get("content"):
                        assistant_replies.append(m["content"])
                    for tc in (m.get("tool_calls") or []):
                        f = tc.get("function", {})
                        tool_calls_made.append({
                            "name": f.get("name"),
                            "arguments": f.get("arguments")
                        })
                elif m.get("role") == "tool":
                    # Tool response
                    pass

            turn_summary = {
                "turn": turn_idx,
                "prompt": prompt,
                "duration_s": turn_duration,
                "tool_calls": tool_calls_made,
                "reply": " ".join(assistant_replies).strip(),
                "opened_hwnds": list(task_state.opened.keys())
            }
            scenario_result["turns"].append(turn_summary)
            print(f"Turn {turn_idx} Tamamlandi: Sure: {turn_duration}s | Araclar: {[tc['name'] for tc in tool_calls_made]}")
            print(f"Asistan Yaniti: {turn_summary['reply'][:200]}...")

            # Small cooldown between turns
            time.sleep(1.5)

        report.append(scenario_result)
        time.sleep(2)

    # Clean up
    shell.close()
    mcp.close()
    logger.close()

    print("\n" + "=" * 70)
    print("TUM TESTLER TAMAMLANDI - OZET RAPOR")
    print("=" * 70)

    report_path = os.path.join(ai_shell.BASE_DIR, "test_scenarios_report.json")
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(f"Rapor kaydedildi: {report_path}")
    return report

if __name__ == "__main__":
    run_all_scenarios()
