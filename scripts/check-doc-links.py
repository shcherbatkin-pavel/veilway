#!/usr/bin/env python3
"""Check public Markdown relative links and GitHub-style heading anchors locally."""
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]


def prose(text):
    return re.sub(r"^(`{3,}|~{3,}).*?^\1[^\n]*$", "", text, flags=re.M | re.S)


def anchors(text):
    result, counts = set(), {}
    for title in re.findall(r"^#{1,6}\s+(.+?)\s*#*\s*$", prose(text), re.M):
        title = re.sub(r"\[([^]]+)\]\([^)]*\)", r"\1", title)
        slug = re.sub(r"[^\w\- ]", "", title.lower()).replace(" ", "-")
        duplicate = counts.get(slug, 0)
        counts[slug] = duplicate + 1
        result.add(slug + (f"-{duplicate}" if duplicate else ""))
    result.update(re.findall(r'(?:id|name)=[\'\"]([^\'\"]+)', text))
    return result


def main():
    names = subprocess.check_output(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=ROOT,
    ).decode().split("\0")
    public = {ROOT / name for name in names if name}
    documents = sorted(path for path in public if path.suffix == ".md" and path.is_file() and not path.is_symlink())
    errors = []
    for document in documents:
        text = prose(document.read_text())
        links = re.findall(r"!?\[[^]\n]*\]\((<[^>]+>|[^\s)]+)(?:\s+[^)]*)?\)", text)
        links += re.findall(r"^\[[^]]+\]:\s*(<[^>]+>|\S+)", text, re.M)
        for link in links:
            url = urlsplit(link.strip("<>"))
            if url.scheme or url.netloc:
                continue
            target = (document.parent / unquote(url.path)).resolve() if url.path else document
            if target != ROOT and ROOT not in target.parents:
                errors.append(f"{document.relative_to(ROOT)}: link leaves repository")
            elif not target.exists() or target.is_symlink():
                errors.append(f"{document.relative_to(ROOT)}: missing target {link}")
            elif target.is_file() and target not in public:
                errors.append(f"{document.relative_to(ROOT)}: target is not public {link}")
            elif url.fragment and target.suffix == ".md" and unquote(url.fragment) not in anchors(target.read_text()):
                errors.append(f"{document.relative_to(ROOT)}: missing anchor {link}")
    for error in errors:
        print(error)
    if not errors:
        print(f"Public Markdown links passed ({len(documents)} documents); external URLs were not fetched")
    return bool(errors)


if __name__ == "__main__":
    raise SystemExit(main())
