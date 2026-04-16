#!/usr/bin/env python3
import re
import sys
from pathlib import Path


def remove_comments(content):
    lines = content.split("\n")
    result = []
    in_multiline_string = False
    in_triple_quote = False
    quote_char = None

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("#") and not in_multiline_string:
            continue

        if '"""' in stripped or "'''" in stripped:
            if '"""' in stripped:
                count = stripped.count('"""')
                if count == 1:
                    in_triple_quote = not in_triple_quote
                    quote_char = '"""'
                elif count == 2:
                    pass
            if "'''" in stripped:
                count = stripped.count("'''")
                if count == 1:
                    in_triple_quote = not in_triple_quote
                    quote_char = "'''"

            if not in_triple_quote:
                line = re.sub(r'""".*?"""', "", line)
                line = re.sub(r"'''.*?'''", "", line)

        if not in_triple_quote:
            if "#" in line:
                code_part = line.split("#")[0]
                if code_part.strip():
                    line = code_part

        result.append(line)

    return "\n".join(result)


def process_file(path):
    content = path.read_text()
    cleaned = remove_comments(content)
    path.write_text(cleaned + "\n")
    print(f"Cleaned: {path}")


def main():
    root = Path(__file__).parent.parent

    for pattern in ["**/*.py"]:
        for path in root.glob(pattern):
            if ".venv" in str(path) or "__pycache__" in str(path):
                continue
            if path.name == "strip_comments.py":
                continue
            try:
                process_file(path)
            except Exception as e:
                print(f"Error processing {path}: {e}")


if __name__ == "__main__":
    main()
