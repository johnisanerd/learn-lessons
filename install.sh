#!/usr/bin/env bash
# Install the video-lessons, slide-lessons and human-touch skills for one or more coding agents.
#
#   ./install.sh                 install for every agent found on this machine
#   ./install.sh --claude        Claude Code   -> ~/.claude/skills
#   ./install.sh --codex         Codex CLI     -> ~/.agents/skills
#   ./install.sh --gemini        Gemini CLI    -> ~/.gemini/skills
#   ./install.sh --dir PATH      any other agent that reads SKILL.md folders
#   ./install.sh --link          symlink instead of copy (for editing this repo)
#   ./install.sh --force         replace a same-named skill this script didn't install
#   ./install.sh --set-token     store your Apify token in ~/.config/video-lessons/.env (videos only)
#   ./install.sh --uninstall     remove the skills from every target above
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS=(video-lessons slide-lessons human-touch)
ENV_DIR="$HOME/.config/video-lessons"
targets=() mode=copy force=0 set_token=0 uninstall=0

while (($#)); do
  case "$1" in
    --claude) targets+=("$HOME/.claude/skills") ;;
    --codex) targets+=("$HOME/.agents/skills") ;;
    --gemini) targets+=("$HOME/.gemini/skills") ;;
    --dir)
      [[ -n "${2:-}" && "$2" != -* ]] || { echo "--dir needs a folder path" >&2; exit 1; }
      targets+=("$(cd "$2" 2>/dev/null && pwd || echo "$2")"); shift ;;
    --link) mode=link ;;
    --force) force=1 ;;
    --set-token) set_token=1 ;;
    --uninstall) uninstall=1 ;;
    -h|--help) sed -n '2,13p' "$0"; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

if ((set_token)); then
  mkdir -p "$ENV_DIR" && chmod 700 "$ENV_DIR"
  read -rsp "Paste your Apify API token (input hidden): " token; echo
  [[ -n "$token" ]] || { echo "No token entered; nothing saved." >&2; exit 1; }
  umask 077
  printf 'APIFY_TOKEN=%s\n' "$token" > "$ENV_DIR/.env"
  chmod 600 "$ENV_DIR/.env"
  echo "Saved to $ENV_DIR/.env (readable only by you)."
  ((${#targets[@]})) || exit 0
fi

if ((${#targets[@]} == 0)); then
  [[ -d "$HOME/.claude" ]] && targets+=("$HOME/.claude/skills")
  { [[ -d "$HOME/.codex" ]] || command -v codex >/dev/null; } && targets+=("$HOME/.agents/skills")
  { [[ -d "$HOME/.gemini" ]] || command -v gemini >/dev/null; } && targets+=("$HOME/.gemini/skills")
  ((${#targets[@]})) || { echo "No agent found. Pass --claude, --codex, --gemini or --dir PATH." >&2; exit 1; }
fi

for target in "${targets[@]}"; do
  for skill in "${SKILLS[@]}"; do
    dest="$target/$skill"
    if ((uninstall)); then
      if [[ -L "$dest" || -f "$dest/.installed-by-video-lessons" ]]; then
        rm -rf "$dest" && echo "removed $dest"
      elif [[ -e "$dest" ]]; then
        echo "skipped $dest (not installed by this script)"
      fi
      continue
    fi
    if [[ -e "$dest" || -L "$dest" ]]; then
      ours=0; [[ -L "$dest" || -f "$dest/.installed-by-video-lessons" ]] && ours=1
      if [[ $ours == 0 && $force == 0 ]]; then
        echo "kept your existing $dest (use --force to replace it)"
        continue
      fi
      rm -rf "$dest"
    fi
    mkdir -p "$target"
    if [[ $mode == link ]]; then
      ln -s "$REPO/skills/$skill" "$dest"
    else
      cp -R "$REPO/skills/$skill" "$dest"
      touch "$dest/.installed-by-video-lessons"
    fi
    echo "installed $skill -> $dest ($mode)"
  done
done

((uninstall)) && exit 0

if [[ -z "${APIFY_TOKEN:-}" && ! -s "$ENV_DIR/.env" ]]; then
  echo
  echo "Next: add your Apify token so video-lessons can fetch transcripts (slide-lessons needs no key):"
  echo "  $0 --set-token"
fi
command -v yt-dlp >/dev/null || echo "Optional: install yt-dlp to use playlists (pip install yt-dlp)."
{ command -v soffice >/dev/null || command -v libreoffice >/dev/null; } ||
  echo "Optional: install LibreOffice for slide images and .ppt/.odp/.key decks (sudo apt install libreoffice-impress)."
command -v pdftoppm >/dev/null || echo "Optional: install poppler for slide PDFs and slide images (sudo apt install poppler-utils)."
echo "Restart your agent so it picks up the new skills."
