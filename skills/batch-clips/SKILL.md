---
name: batch-clips
description: Queue several videos (links or MP4 files) in CLIP MASTER and process them one after another, with cancel, retry and resume after a restart. Use when the user wants to clip many VODs at once or run an overnight batch.
---

# Batch clips

The queue runs the full pipeline (pull, find, track, caption, optional translation, ship) for each item, one at a time. State is saved in `projects/_queue.json`: items that were running when the server stopped go back to *queued* and resume on the next start. A manual job and the queue never render at the same time.

## UI

**Batch queue** card: paste one link per line, click *Add to queue*, or *Add an MP4* (several files allowed). The platform preset, translation language and Whisper model currently selected in the page apply to everything you add. Each row shows status, progress and buttons Cancel / Retry / Remove / Open.

## API (Origin header required for POST/DELETE)

```bash
O='Origin: http://127.0.0.1:7866'; H='Content-Type: application/json'
curl -s -H "$O" -H "$H" -d '{"urls":["https://example.com/a","https://example.com/b"],
  "options":{"export_preset":"youtube_shorts","translate_to":"en","model_size":"tiny"}}' localhost:7866/api/queue
curl -s localhost:7866/api/queue                                  # items + status
curl -s -X POST -H "$O" localhost:7866/api/queue/<id>/cancel
curl -s -X POST -H "$O" localhost:7866/api/queue/<id>/retry       # only error / cancelled items
curl -s -X DELETE -H "$O" localhost:7866/api/queue/<id>           # not while running
curl -s -X POST -H "$O" localhost:7866/api/queue-clear            # drop done + cancelled
curl -s -H "$O" -F file=@clip.mp4 -F 'options={"export_preset":"tiktok"}' localhost:7866/api/queue/upload
```

Statuses: `queued`, `running`, `done` (has `output` = file in `outputs/`), `error` (has `error`), `cancelled`. Up to 50 URLs per request, 200 items in total. Cancelling a running item stops it at the next progress tick (the current FFmpeg step finishes first).

## Tips

- Add `clip_start`/`clip_end` is not available per queue item over the UI; for long VODs download the section first (`/api/pull`) or give direct clip URLs.
- Uploaded files are deleted from the queue folder once the item is done or cancelled; failed items keep them so Retry works.
