from __future__ import annotations

import argparse
import secrets
from pathlib import Path


DEFAULT_ENV_FILE = ".env"
ENV_KEY = "TABLE_ADMIN_TOKEN"


def _set_env_value(text: str, key: str, value: str) -> str:
    lines = text.splitlines()
    updated = False
    next_lines: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            next_lines.append(f"{key}={value}")
            updated = True
        else:
            next_lines.append(line)
    if not updated:
        if next_lines and next_lines[-1].strip():
            next_lines.append("")
        next_lines.append(f"{key}={value}")
    return "\n".join(next_lines).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a random TABLE_ADMIN_TOKEN in a local env file.")
    parser.add_argument("--env-file", default=DEFAULT_ENV_FILE, help="Env file to update. Default: .env")
    parser.add_argument("--bytes", type=int, default=32, help="Random byte length before URL-safe encoding.")
    parser.add_argument("--force", action="store_true", help="Replace an existing TABLE_ADMIN_TOKEN value.")
    parser.add_argument("--print-token", action="store_true", help="Print the generated token to stdout.")
    args = parser.parse_args()

    if args.bytes < 16:
        parser.error("--bytes must be at least 16")

    env_path = Path(args.env_file)
    original_text = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
    existing = None
    for line in original_text.splitlines():
        if line.strip().startswith(f"{ENV_KEY}="):
            existing = line.split("=", 1)[1].strip()
            break

    if existing and not args.force:
        print(f"{env_path} already has {ENV_KEY}. Use --force to rotate it.")
        return 0

    token = secrets.token_urlsafe(args.bytes)
    env_path.write_text(_set_env_value(original_text, ENV_KEY, token), encoding="utf-8")
    print(f"Generated {ENV_KEY} in {env_path}")
    if args.print_token:
        print(token)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
