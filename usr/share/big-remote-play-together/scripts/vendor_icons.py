
import os
import re
import shutil
from pathlib import Path

# --- Configuration ---
SOURCE_DIR = Path("/home/ruscher/Documentos/Git/big-remote-play-together/src")
DEST_DIR = SOURCE_DIR / "icons"
SEARCH_PATHS = [
    Path("/usr/share/icons"),
    Path("/usr/share/pixmaps")
]
THEME_PRIORITY = ["Adwaita", "hicolor", "breeze", "breeze-dark", "papirus", "Papirus", "gnome"]
EXTENSIONS = [".svg", ".png"]

# --- Known icon names from code ---
# I'll augment this by scanning files too
HARDCODED_ICONS = {
    "big-remote-play-together",
    "steam", "lutris", "heroic",
    "view-conceal-symbolic",
    "network-transmit-receive-symbolic"
}

def find_icon_usage(directory):
    found_icons = set()
    # Patterns for icon usage
    # new_from_icon_name("icon-name")
    # set_icon_name('icon-name')
    # icon_name="icon-name"
    # 'icon': 'icon-name'
    patterns = [
        re.compile(r"new_from_icon_name\(['\"]([\w-]+)['\"]\)", re.MULTILINE),
        re.compile(r"set_icon_name\(['\"]([\w-]+)['\"]\)", re.MULTILINE),
        re.compile(r"icon_name=['\"]([\w-]+)['\"]", re.MULTILINE),
        re.compile(r"'icon':\s*['\"]([\w-]+)['\"]", re.MULTILINE),
        re.compile(r'"icon":\s*["\']([\w-]+)["\']', re.MULTILINE),
    ]

    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".py") or file.endswith(".ui"):
                path = Path(root) / file
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        content = f.read()
                        for p in patterns:
                            matches = p.findall(content)
                            for match in matches:
                                found_icons.add(match)
                except Exception as e:
                    print(f"Error reading {path}: {e}")
    return found_icons

def find_icon_file(icon_name):
    # Check if already present in DEST_DIR
    for ext in EXTENSIONS:
        if (DEST_DIR / (icon_name + ext)).exists():
            return None # Already exists

    # Search in system paths
    # Heuristic: prioritize themes
    candidates = []

    for base_path in SEARCH_PATHS:
        if not base_path.exists(): continue
        
        # 1. Search in priority themes first
        for theme in THEME_PRIORITY:
            theme_path = base_path / theme
            if theme_path.exists():
                # glob recursively for icon
                for ext in EXTENSIONS:
                    # Try explicit match
                    matches = list(theme_path.rglob(f"{icon_name}{ext}"))
                    if matches:
                        # Prefer scalable/symbolic for SVGs?
                        # Let's just take the first match for now, maybe sort by file size or path
                        # Prefer 'symbolic' if the name has symbolic
                        candidates.extend(matches)
        
        # 2. Search anywhere in base path if not found in priority themes
        if not candidates:
             for ext in EXTENSIONS:
                matches = list(base_path.rglob(f"{icon_name}{ext}"))
                candidates.extend(matches)

    if not candidates:
        return None

    # Filter/Sort candidates
    # Prefer SVG over PNG
    # Prefer exact name match
    # Prefer 'symbolic' folder if icon name has 'symbolic'
    
    def sort_key(p):
        score = 0
        s_path = str(p)
        if p.suffix == '.svg': score += 100
        if 'symbolic' in icon_name and 'symbolic' in s_path: score += 50
        if '48x48' in s_path: score += 10 # Good size for PNG
        if 'scalable' in s_path: score += 20
        # Theme priority check
        for i, t in enumerate(reversed(THEME_PRIORITY)):
            if f"/{t}/" in s_path:
                score += (i + 1) * 5
        return score

    candidates.sort(key=sort_key, reverse=True)
    return candidates[0]

def main():
    if not DEST_DIR.exists():
        DEST_DIR.mkdir(parents=True)

    print(f"Scanning for icons in {SOURCE_DIR}...")
    icons = find_icon_usage(SOURCE_DIR)
    icons.update(HARDCODED_ICONS)
    
    print(f"Found {len(icons)} distinct icon names.")
    
    for icon in sorted(icons):
        if not icon or len(icon) < 2: continue # skip empty or 1-char trash
        
        print(f"Processing: {icon}")
        src_file = find_icon_file(icon)
        
        if src_file:
            dest_file = DEST_DIR / src_file.name
            try:
                shutil.copy2(src_file, dest_file)
                print(f"  -> Copied {src_file} to {dest_file}")
            except Exception as e:
                print(f"  -> Failed to copy {src_file}: {e}")
        else:
            # Check if it already exists in destination (find_icon_file returns None if so)
            existing = list(DEST_DIR.glob(f"{icon}.*"))
            if existing:
                print(f"  -> Already exists in {DEST_DIR}")
            else:
                print(f"  -> NOT FOUND in system paths")

if __name__ == "__main__":
    main()
