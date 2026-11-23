# NOTICE — third-party software and credits

CLIP MASTER is licensed under the **GNU AGPL-3.0** (see `LICENSE`). It builds on the open-source projects below.
None of their source code is vendored in this repository except the two font files listed at the end:
they are installed as dependencies (pip / npm) or used as external programs.

Licenses were read from each project's repository on 2026-11-23. Always check the upstream project for the
authoritative, current terms.

## Used at runtime

| Tool | Used for | License | Link |
|---|---|---|---|
| **Ultralytics YOLO** (YOLOv8n weights, downloaded on first use) | Subject / person tracking for the 9:16 crop | **AGPL-3.0** (commercial Enterprise license available from Ultralytics) | https://github.com/ultralytics/ultralytics |
| **MediaPipe** (BlazeFace short-range model, downloaded on first use) | Facecam face detection | Apache-2.0 | https://github.com/google-ai-edge/mediapipe |
| **faster-whisper** (+ OpenAI Whisper models) | Speech transcription with word timestamps | MIT | https://github.com/SYSTRAN/faster-whisper |
| **yt-dlp** | Downloading videos you have the right to use | Unlicense | https://github.com/yt-dlp/yt-dlp |
| **Streamlink** | Recording live streams | BSD-2-Clause | https://github.com/streamlink/streamlink |
| **PySceneDetect** | Scene / cut detection | BSD-3-Clause | https://github.com/Breakthrough/PySceneDetect |
| **auto-editor** | Silence removal (optional) | Unlicense | https://github.com/WyattBlue/auto-editor |
| **Demucs** | Voice / music separation (optional) | MIT | https://github.com/facebookresearch/demucs |
| **FFmpeg** | Encoding, filters, loudness normalisation, subtitles (libass) | LGPL-2.1+ (GPL-2.0+ for some builds) | https://ffmpeg.org / https://github.com/FFmpeg/FFmpeg |
| **imageio-ffmpeg** | Fallback FFmpeg binary | BSD-2-Clause | https://github.com/imageio/imageio-ffmpeg |
| **Remotion** (npm: `remotion`, `@remotion/cli`) | Animated React captions (optional) | **Remotion License** (source-available, not OSI) — see below | https://github.com/remotion-dev/remotion |
| **TwitchDownloader CLI** (optional external binary, not bundled) | Twitch VOD + chat download | MIT | https://github.com/lay295/TwitchDownloader |
| **Groq API** / **Ollama** (optional) | LLM "AI crew" (highlight director, caption polish) | Hosted service terms / MIT (Ollama) | https://groq.com / https://github.com/ollama/ollama |
| FastAPI, Starlette | Web API | MIT / BSD-3-Clause | https://github.com/fastapi/fastapi |
| Uvicorn | ASGI server | BSD-3-Clause | https://github.com/encode/uvicorn |
| python-multipart | File uploads | Apache-2.0 | https://github.com/Kludex/python-multipart |
| python-dotenv | `.env` loading | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| OpenCV (`opencv-python`) | Frame reading / drawing | Apache-2.0 (wheels: MIT) | https://github.com/opencv/opencv-python |
| NumPy / SciPy | Numerics, smoothing | BSD-3-Clause | https://numpy.org / https://scipy.org |
| Pillow | Images | HPND | https://github.com/python-pillow/Pillow |
| PyTorch (via Ultralytics) | Neural network runtime | BSD-3-Clause | https://github.com/pytorch/pytorch |
| React, React DOM | Remotion compositions | MIT | https://github.com/facebook/react |

### Remotion license

Remotion is **not** under an OSI-approved license. Individuals, non-profits and small companies may use it for free,
including commercially; larger for-profit organisations need a paid company license. Read the current terms at
https://github.com/remotion-dev/remotion/blob/main/LICENSE.md before using the Remotion render in a company.
The Remotion step is optional: CLIP MASTER renders captions with FFmpeg/libass without it.

### Ultralytics / AGPL note

YOLO is integrated as a normal dependency, which is why this project is released under AGPL-3.0. If you run a
modified version of CLIP MASTER as a network service, the AGPL requires you to offer the corresponding source
to its users.

## Ideas re-implemented in-house (no code copied)

These projects inspired features that CLIP MASTER re-implements with FFmpeg, OpenCV or the LLM director.
Their code is neither bundled nor required.

| Project | Idea | License | Link |
|---|---|---|---|
| FunClip | LLM-based highlight selection from transcripts | MIT | https://github.com/modelscope/FunClip |
| ClipsAI | Long video to clips, 9:16 reframing | MIT | https://github.com/ClipsAI/clipsai |
| WhisperX | Word-level caption timing | BSD-2-Clause | https://github.com/m-bain/whisperX |
| auto-subtitle | Burned-in subtitles | MIT | https://github.com/m1guelpf/auto-subtitle |
| Video2X | Upscaling (here: FFmpeg Lanczos + unsharp) | AGPL-3.0 | https://github.com/k4yt3x/video2x |
| Practical-RIFE | Smooth slow-motion (here: FFmpeg `minterpolate`) | MIT | https://github.com/hzwer/Practical-RIFE |
| MoviePy | Programmatic montage | MIT | https://github.com/Zulko/moviepy |
| LosslessCut | Lossless trimming (here: `ffmpeg -c copy`) | GPL-2.0 | https://github.com/mifi/lossless-cut |

## Vendored files

| File | License | Source |
|---|---|---|
| `assets/fonts/Anton-Regular.ttf` | SIL Open Font License 1.1 | https://fonts.google.com/specimen/Anton |
| `assets/fonts/Geist-VariableFont_wght.ttf` | SIL Open Font License 1.1 | https://github.com/vercel/geist-font |

## Trademarks

All product names are trademarks of their respective owners. CLIP MASTER is an independent project and is not
affiliated with, endorsed by or sponsored by any of the projects, platforms or companies named in this file or
by any game publisher, streamer or platform whose content you may clip.


## Tools fetched by the installer (`third_party/`)

`third_party/manifest.yml` is the machine-readable list (name, URL, pinned tag or commit, license, purpose). The installer downloads
these onto the user's machine; **none of their code is stored in this repository**.

- **TwitchDownloaderCLI** 1.56.5 (MIT): release binary, checksum-verified, used for optional Twitch VOD + chat download.
- **Reference-only sources** (`--with-reference`, not used at runtime): SAM 2 (Apache-2.0), FunClip (MIT), ClipsAI (MIT),
  WhisperX (BSD-2-Clause), OpenCut (MIT), silero-vad (MIT), Practical-RIFE (MIT, code only: model weights have their own terms),
  auto-subtitle (MIT), MoviePy (MIT).
- **Deliberately not fetched**: `remotion-dev/skills` (no license file), LosslessCut (GPL-2.0-only, idea re-implemented with
  `ffmpeg -c copy`), Video2X (AGPL-3.0, about 1 GB, not used).

Model weights downloaded on first use: `yolov8n.pt` (Ultralytics assets, AGPL-3.0) and the MediaPipe BlazeFace short-range model (Apache-2.0).
