# -*- coding: utf-8 -*-
"""
SenluoFlow 集成驱动：本地图片素材 → MoneyPrinterTurbo 视频（无外部模型能力）。

用法: python run_senluo_task.py <job.json>
job.json:
{
  "name": "产品名",
  "script": "英语脚本文案（由调用方生成，无需外部 LLM）",
  "images": ["本地图片绝对路径", ...],
  "voice": "en-US-AriaNeural",      # edge-tts 音色（女生英语）
  "clip_duration": 5,               # 每张图片片段时长(秒)
  "output": "成片输出路径(绝对)"
}
stdout 输出:
  PROGRESS <pct>   进度行
  RESULT <json>    结束结果 {"ok": bool, "output": "...", "task_id": "...", "error": "..."}
"""
import json
import os
import shutil
import sys
import threading
import time
import uuid

from app.models.schema import MaterialInfo, VideoParams
from app.services import state as sm
from app.services import task
from app.utils import utils


def main():
    job = json.load(open(sys.argv[1], encoding="utf-8"))
    name = str(job.get("name") or "video").strip() or "video"
    script = str(job.get("script") or "").strip()
    clip_duration = max(2, int(job.get("clip_duration", 5)))
    voice = str(job.get("voice") or "en-US-AriaNeural").strip()

    # 1) 复制素材到 MPT 本地素材目录（preprocess_video 只允许读取该目录内文件）
    local_dir = utils.storage_dir("local_videos", create=True)
    materials = []
    for img in job.get("images") or []:
        if not os.path.isfile(img):
            continue
        dst = os.path.join(local_dir, os.path.basename(img))
        if os.path.abspath(dst) != os.path.abspath(img):
            try:
                shutil.copy2(img, dst)
            except OSError as e:
                print(f"RESULT {json.dumps({'ok': False, 'error': f'copy material failed: {e}'}, ensure_ascii=False)}", flush=True)
                return
        materials.append(
            MaterialInfo(provider="local", url=os.path.basename(dst), duration=clip_duration)
        )
    if not materials:
        print("RESULT {\"ok\": false, \"error\": \"no local materials\"}", flush=True)
        return

    task_id = "senluo_" + uuid.uuid4().hex[:10]
    utils.task_dir(task_id)  # 创建任务目录

    params = VideoParams(
        video_subject=name,
        video_script=script,
        video_aspect="9:16",
        video_concat_mode="sequential",
        video_clip_duration=clip_duration,
        video_source="local",
        video_materials=materials,
        voice_name=voice,
        voice_rate=1.0,
        subtitle_enabled=True,
        bgm_type="none",
        bgm_volume=0,
        n_threads=2,
        video_count=1,
    )
    sm.state.update_task(task_id, state=4, progress=0)

    result = {}

    def run():
        try:
            result.update(task.start(task_id, params, stop_at="video") or {})
        except Exception as e:
            result["error"] = f"{type(e).__name__}: {e}"

    t = threading.Thread(target=run, daemon=True)
    t.start()
    while t.is_alive():
        try:
            st = sm.state.get_task(task_id) or {}
            pct = int(st.get("progress", 0) or 0)
            print(f"PROGRESS {pct}", flush=True)
        except Exception:
            pass
        time.sleep(2)
    t.join()

    videos = result.get("videos") or []
    if not videos:
        err = result.get("error") or "MPT task failed"
        print(f"RESULT {json.dumps({'ok': False, 'error': err}, ensure_ascii=False)}", flush=True)
        return

    final = videos[0]
    out = str(job.get("output") or "")
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        try:
            shutil.copy2(final, out)
        except OSError as e:
            print(f"RESULT {json.dumps({'ok': False, 'error': f'copy output failed: {e}'}, ensure_ascii=False)}", flush=True)
            return
    print(f"RESULT {json.dumps({'ok': True, 'output': out or final, 'task_id': task_id}, ensure_ascii=False)}", flush=True)


if __name__ == "__main__":
    main()
