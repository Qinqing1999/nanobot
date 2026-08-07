"""Print all channel dependencies as a space-separated list for shell scripts.

With --file PATH, write one requirement per line to a requirements file
(useful on Windows where <, >, [, ] break batch parsing).
"""
import argparse
import sys

from nanobot.channels.registry import discover_plugins
from nanobot.optional_features import install_args_for_extra


def collect_deps() -> list[str]:
    plugins = discover_plugins()
    args: list[str] = []
    seen: set[str] = set()
    for name in sorted(plugins):
        plugin = plugins[name]
        deps = list(plugin.dependencies)
        if not deps:
            continue
        channel_args, _ = install_args_for_extra(name, deps)
        for req in channel_args:
            if req not in seen:
                seen.add(req)
                args.append(req)
    return args


def main() -> None:
    parser = argparse.ArgumentParser(description="List channel dependencies")
    parser.add_argument("--file", help="Write requirements to this file (one per line)")
    args = parser.parse_args()

    deps = collect_deps()

    if args.file:
        from pathlib import Path

        Path(args.file).write_text("\n".join(deps) + "\n", encoding="utf-8")
        print(f"Wrote {len(deps)} requirements to {args.file}", file=sys.stderr)
    else:
        print(" ".join(deps))


if __name__ == "__main__":
    main()
