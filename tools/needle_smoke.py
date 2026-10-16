"""Needle 3 smoke test: engine + weights dogrulamasi."""
import json
import sys
import traceback

import needle


@needle.tool
def get_weather(city: str):
    "Get the current weather for a city."
    return {"city": city, "temp_c": 27, "sky": "clear"}


def main() -> int:
    try:
        agent = needle.Needle(tools=[get_weather])
        result = agent.run("what's it like in Ankara right now?")
        print(json.dumps(result, indent=2, default=str)[:1200])
        return 0
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
