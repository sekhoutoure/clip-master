# CLIP MASTER

**Studio local de Shorts 9:16** : colle une URL ou charge une vidéo, CLIP MASTER trouve le meilleur moment, suit le sujet, ajoute des sous-titres et exporte un Short prêt à publier.
*English version below.*

## Fonctionnalités

- **Import** : URL (yt-dlp), enregistrement de live (Streamlink), VOD Twitch + chat (TwitchDownloader, optionnel) ou fichier MP4 local.
- **Détection** : coupes de scène (PySceneDetect), silences (auto-editor), moments forts (énergie audio + LLM).
- **Suivi** : visage de la facecam (MediaPipe) et sujet de jeu (YOLO) pour recadrer en 9:16, avec facecam en incrustation.
- **Sous-titres** : transcription locale mot à mot (faster-whisper), styles TikTok, rendu ASS ou Remotion (optionnel).
- **Export** : H.264 1080×1920 30 fps, loudness −14 LUFS, ralenti fluide, barre de rétention, contrôle qualité (QC).
- **Équipe IA** : un « directeur » LLM choisit le moment et écrit titre / accroche / hashtags (Groq, ou Ollama en local).

## Prérequis

- **Python 3.10 – 3.12** (MediaPipe et PyTorch n'ont pas encore de roues pour toutes les versions plus récentes)
- **FFmpeg** dans le PATH (`winget install Gyan.FFmpeg`) ou `FFMPEG_PATH` dans `.env`
- **Node.js 20+** *(optionnel, seulement pour le rendu Remotion)*
- Clé **Groq** *(optionnelle)* ou serveur **Ollama** local pour l'équipe IA

## Installation

### Windows

1. Installe Python, FFmpeg et (optionnel) Node.js.
2. Télécharge le dépôt, puis double-clique sur **`start.bat`** : il crée le venv, installe les dépendances, vérifie FFmpeg et Node, fait `npm install` dans `remotion/`, lance le serveur et ouvre le navigateur sur http://127.0.0.1:7866.

### Linux / macOS

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # puis édite .env si besoin
(cd remotion && npm install)    # optionnel
python server.py                # http://127.0.0.1:7866
```

Les poids YOLO et le modèle de visage MediaPipe sont téléchargés automatiquement au premier usage dans `models/`.

## Utilisation

1. **Importer** : colle une URL ou charge un MP4.
2. **Style** : *Standard* (facecam + jeu), *Suivi* (crop YOLO + facecam), *9:16*.
3. **Créer le Short** : le pipeline enchaîne détection, suivi, sous-titres et export. Le résultat est dans `outputs/`.
4. Le mode **Avancé** donne accès aux étapes manuelles, à Whisper, Demucs, Remotion, etc.

Tous les réglages se font dans `.env` (voir `.env.example`). Rien n'est stocké hors de ton disque, sauf les appels au LLM si tu actives Groq.

## Avertissement

**Tu dois avoir les droits sur les contenus que tu découpes** (accord du créateur, licence, usage autorisé par la plateforme). CLIP MASTER est un outil : tu es responsable de ce que tu importes, transformes et publies, et du respect des conditions des plateformes. Le projet n'est affilié à aucune plateforme, éditeur de jeu ou créateur.

## Licence

**AGPL-3.0** (voir `LICENSE`), car YOLO (Ultralytics, AGPL-3.0) est une dépendance directe. Les crédits et licences des outils tiers sont dans `NOTICE.md`.
**Remotion** (optionnel) n'est pas sous licence OSI : gratuit pour les particuliers et petites structures, licence payante pour les grandes sociétés. Voir https://github.com/remotion-dev/remotion/blob/main/LICENSE.md.

---

# English

**Local 9:16 Shorts studio**: paste a URL or load a video; CLIP MASTER finds the best moment, tracks the subject, burns captions and exports a ready-to-post Short.

**Features** — import (yt-dlp, Streamlink, Twitch VOD, local MP4) · scene/silence/highlight detection · MediaPipe + YOLO tracking with facecam inset · local word-level transcription (faster-whisper) · 1080×1920 30 fps export with −14 LUFS loudness and QC · optional LLM "AI crew" (Groq or local Ollama).

**Prerequisites** — Python 3.10–3.12, FFmpeg in PATH (or `FFMPEG_PATH`), Node.js 20+ (optional, Remotion only).

**Install** — Windows: double-click `start.bat`. Linux/macOS: `python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && cp .env.example .env && python server.py`, then open http://127.0.0.1:7866. YOLO weights and the face model download on first use.

**Usage** — Import → pick a style (Standard / Tracked / 9:16) → *Créer le Short*. Outputs land in `outputs/`. Settings live in `.env`.

**Disclaimer** — You must have the rights to any content you clip. You are responsible for what you import, transform and publish and for complying with platform terms. This project is not affiliated with any platform, game publisher or creator.

**License** — AGPL-3.0 (YOLO/Ultralytics is an AGPL dependency). Third-party credits in `NOTICE.md`. **Remotion** is source-available (free for individuals and small teams, paid license for larger companies): https://github.com/remotion-dev/remotion/blob/main/LICENSE.md.
