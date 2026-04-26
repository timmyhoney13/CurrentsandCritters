#!/usr/bin/env python3
"""
Card image updater for Currents & Critters.

Drop new PNG files into the staging folders, then run:
    python3 update_cards.py

Staging folders (create if they don't exist):
    new_cards/horizontal/   →  horizontal_cards/page_01.png … page_48.png
    new_cards/vertical/     →  vertical_cards/page_01.png  … page_44.png
    new_cards/oceans/       →  oceans_cards/page_01.png    … page_66.png

Any PNG file names are accepted — files are sorted naturally and
renamed to page_01.png, page_02.png, … in that order.

You can update just one deck by only populating that staging folder.
After copying the script bumps CARD_IMAGE_VERSION in preview.html,
then commits and pushes (triggering a Render redeploy automatically).
"""

import os, re, shutil, subprocess, sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).parent.resolve()

STAGING = HERE / "new_cards"

DECKS = {
    "horizontal": {
        "src":       STAGING / "horizontal",
        "dst":       HERE / "horizontal_cards",
        "expected":  48,
        "label":     "horizontal (Up / Down animal cards)",
    },
    "vertical": {
        "src":       STAGING / "vertical",
        "dst":       HERE / "vertical_cards",
        "expected":  44,
        "label":     "vertical (Left / Right animal cards)",
    },
    "oceans": {
        "src":       STAGING / "oceans",
        "dst":       HERE / "oceans_cards",
        "expected":  66,
        "label":     "oceans cards",
    },
}

PREVIEW_HTML = HERE / "multiplayer" / "client" / "preview.html"
VERSION_RE   = re.compile(r'(const CARD_IMAGE_VERSION\s*=\s*")[^"]*(")')


def natural_key(p: Path):
    """Sort files: extract all digit runs for numeric ordering."""
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r"(\d+)", p.name)]


def find_pngs(folder: Path) -> list[Path]:
    return sorted(folder.glob("*.png"), key=natural_key)


def copy_deck(deck: dict) -> int:
    src: Path = deck["src"]
    dst: Path = deck["dst"]
    expected: int = deck["expected"]
    label: str = deck["label"]

    if not src.exists():
        print(f"  [skip] {label} — staging folder not found: {src}")
        return 0

    pngs = find_pngs(src)
    if not pngs:
        print(f"  [skip] {label} — no PNG files in {src}")
        return 0

    if len(pngs) != expected:
        ans = input(
            f"\n  ⚠  {label}: found {len(pngs)} PNG(s) but expected {expected}.\n"
            f"     Continue anyway? [y/N] "
        ).strip().lower()
        if ans != "y":
            print(f"  [abort] {label}")
            return 0

    dst.mkdir(parents=True, exist_ok=True)
    for i, src_file in enumerate(pngs, start=1):
        dst_file = dst / f"page_{i:02d}.png"
        shutil.copy2(src_file, dst_file)

    print(f"  [ok]   {label}: copied {len(pngs)} images → {dst.name}/")
    return len(pngs)


def bump_version(preview: Path) -> str:
    text = preview.read_text(encoding="utf-8")
    new_ver = datetime.now().strftime("%Y%m%d-%H%M%S")
    new_text, n = VERSION_RE.subn(lambda m: m.group(1) + new_ver + m.group(2), text)
    if n == 0:
        print("  [warn] CARD_IMAGE_VERSION not found in preview.html — cache NOT busted")
        return new_ver
    preview.write_text(new_text, encoding="utf-8")
    print(f"  [ok]   CARD_IMAGE_VERSION → {new_ver}")
    return new_ver


def git_push(version: str, decks_updated: list[str]):
    os.chdir(HERE)
    subprocess.run(["git", "add",
                    "horizontal_cards",
                    "vertical_cards",
                    "oceans_cards",
                    "multiplayer/client/preview.html"], check=True)
    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True)
    if not status.stdout.strip():
        print("  [skip] Nothing changed — git working tree is clean.")
        return
    msg = f"Update card images ({', '.join(decks_updated)}) — version {version}"
    subprocess.run(["git", "commit", "-m", msg], check=True)
    subprocess.run(["git", "push", "origin", "main"], check=True)
    print("  [ok]   Pushed to GitHub — Render will redeploy automatically.")


def main():
    print("\n=== Currents & Critters card image updater ===\n")

    # Ensure staging root exists with a helpful message
    if not STAGING.exists():
        STAGING.mkdir(parents=True)
        for key in DECKS:
            (STAGING / key).mkdir(exist_ok=True)
        print(f"Created staging folders under:\n  {STAGING}\n")
        print("Drop your PNG files into the right subfolder, then run this script again.\n")
        print("  new_cards/horizontal/  — Up / Down animal card images  (48 pages)")
        print("  new_cards/vertical/    — Left / Right animal cards      (44 pages)")
        print("  new_cards/oceans/      — Ocean / environment cards      (66 pages)\n")
        sys.exit(0)

    updated = []
    for key, deck in DECKS.items():
        n = copy_deck(deck)
        if n:
            updated.append(key)

    if not updated:
        print("\nNothing to update. Add PNG files to the new_cards/ subfolders and re-run.\n")
        sys.exit(0)

    print()
    version = bump_version(PREVIEW_HTML)

    print()
    push = input("Push to GitHub now? [Y/n] ").strip().lower()
    if push in ("", "y"):
        git_push(version, updated)
    else:
        print("  [skip] Push skipped. Run 'git push origin main' manually when ready.")

    print("\nDone.\n")


if __name__ == "__main__":
    main()
