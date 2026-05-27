#!/usr/bin/env python3
"""Extract paragraph-level citation gaps from LaTeX or plain text."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

PATTERNS = {
    "literal-cite-marker": re.compile(r"\[(?:cite|citation needed)\]", re.IGNORECASE),
    "todo-cite-command": re.compile(r"\\(?:todo|TODO)\{[^}]*cite[^}]*\}"),
    "plain-cite-request": re.compile(r"\b(?:need|add|find)\s+(?:a\s+)?citation\b", re.IGNORECASE),
    "zh-support-paper": re.compile(
        r"(?:我想找(?:一篇|一些)?论文(?:来)?(?:支撑|支持|佐证)?(?:论点|观点|结论)?|"
        r"找(?:一篇|一些)?论文(?:来)?(?:支撑|支持|佐证)?(?:论点|观点|结论)?|"
        r"需要(?:文献|引用|论文)(?:支撑|支持|佐证)?|"
        r"找文献支持)"
    ),
}


def read_text(path_str: str) -> str:
    if path_str == "-":
        return sys.stdin.read()
    return Path(path_str).read_text(encoding="utf-8")


def build_paragraphs(text: str) -> list[dict[str, object]]:
    lines = text.splitlines()
    paragraphs: list[dict[str, object]] = []
    start = None
    buffer: list[str] = []

    for index, raw_line in enumerate(lines, start=1):
        if raw_line.strip():
            if start is None:
                start = index
            buffer.append(raw_line)
            continue
        if buffer:
            paragraphs.append(
                {
                    "start_line": start,
                    "end_line": index - 1,
                    "text": "\n".join(buffer),
                }
            )
            start = None
            buffer = []

    if buffer:
        paragraphs.append(
            {
                "start_line": start,
                "end_line": len(lines),
                "text": "\n".join(buffer),
            }
        )

    return paragraphs


def clean_claim_text(text: str) -> str:
    cleaned = text
    for pattern in PATTERNS.values():
        cleaned = pattern.sub(" ", cleaned)
    cleaned = re.sub(r"\\[a-zA-Z]+\{", " ", cleaned)
    cleaned = cleaned.replace("{", " ").replace("}", " ")
    cleaned = re.sub(r"^[，。,；：:\-\s]+", "", cleaned)
    cleaned = re.sub(r"^(?:这里|此处|这里需要)\s*", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"^[，。,；：:\-\s]+", "", cleaned)
    return cleaned


def extract_matches(text: str) -> list[dict[str, object]]:
    matches: list[dict[str, object]] = []
    for paragraph in build_paragraphs(text):
        paragraph_text = str(paragraph["text"])
        triggers = [
            label
            for label, pattern in PATTERNS.items()
            if pattern.search(paragraph_text)
        ]
        if not triggers:
            continue
        matches.append(
            {
                "id": len(matches) + 1,
                "start_line": paragraph["start_line"],
                "end_line": paragraph["end_line"],
                "triggers": triggers,
                "text": re.sub(r"\s+", " ", paragraph_text).strip(),
                "clean_claim": clean_claim_text(paragraph_text),
            }
        )
    return matches


def render_text(matches: list[dict[str, object]]) -> str:
    if not matches:
        return "No citation gaps found."

    output: list[str] = []
    for item in matches:
        output.append(
            (
                f"[{item['id']}] lines {item['start_line']}-{item['end_line']} "
                f"triggers={','.join(item['triggers'])}\n"
                f"claim: {item['clean_claim']}\n"
                f"text: {item['text']}"
            )
        )
    return "\n\n".join(output)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "input",
        help="Path to a .tex or text file, or '-' to read from stdin.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON instead of plain text.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    text = read_text(args.input)
    matches = extract_matches(text)
    if args.json:
        json.dump(matches, sys.stdout, ensure_ascii=False, indent=2)
        sys.stdout.write("\n")
    else:
        sys.stdout.write(render_text(matches) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
