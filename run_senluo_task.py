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

from loguru import logger as _logger

from app.models.schema import MaterialInfo, VideoParams
from app.services import state as sm
from app.services import task
from app.utils import utils


def main():
    job = json.load(open(sys.argv[1], encoding="utf-8"))
    verbose = bool(job.get("verbose", True))
    # 收紧/放宽 MPT 日志（必须在导入 app.config 之后执行，覆盖其已添加的 handler）：
    # 详细模式保留 INFO 级，否则只保留 WARNING+；纯消息格式（无时间戳/ANSI）
    _logger.remove()
    _logger.add(sys.stdout, level="INFO" if verbose else "WARNING",
                format="{message}", colorize=False)

    name = str(job.get("name") or "video").strip() or "video"
    script = str(job.get("script") or "").strip()
    clip_duration = max(2, int(job.get("clip_duration", 5)))
    voice = str(job.get("voice") or "en-US-AriaNeural").strip()
    aspect = str(job.get("aspect") or "9:16").strip() or "9:16"
    subtitle_enabled = bool(job.get("subtitle_enabled", True))
    base_dir = str(job.get("base") or "")
    bgm_type = str(job.get("bgm_type") or "none").strip() or "none"
    bgm_volume = float(job.get("bgm_volume", 0.2) or 0.2)

    def short(p):
        """完整路径 → 「产品目录名\\往后」回显。"""
        try:
            if base_dir:
                rel = os.path.relpath(str(p), base_dir)
                if not rel.startswith(".."):
                    return os.path.join(os.path.basename(base_dir), rel)
            return os.path.basename(str(p))
        except Exception:
            return str(p)

    # 1) 复制素材到 MPT 本地素材目录（preprocess_video 只允许读取该目录内文件）
    local_dir = utils.storage_dir("local_videos", create=True)
    materials = []
    copied_dsts = []                      # 本次复制到 local_videos 的文件（用于任务后清理）
    for img in job.get("images") or []:
        if not os.path.isfile(img):
            if verbose:
                print(f"[跳过] 素材不存在: {short(img)}", flush=True)
            continue
        dst = os.path.join(local_dir, os.path.basename(img))
        if os.path.abspath(dst) != os.path.abspath(img):
            try:
                shutil.copy2(img, dst)
            except OSError as e:
                print(f"RESULT {json.dumps({'ok': False, 'error': f'复制素材失败: {e}'}, ensure_ascii=False)}", flush=True)
                return
            copied_dsts.append(dst)
        else:
            copied_dsts.append(img)
        materials.append(
            MaterialInfo(provider="local", url=os.path.basename(dst), duration=clip_duration)
        )
        if verbose:
            print(f"[视频] 素材 {short(img)} → 素材库/{os.path.basename(dst)} ({clip_duration}s/张)", flush=True)
    if not materials:
        print("RESULT {\"ok\": false, \"error\": \"无可用素材\"}", flush=True)
        return

    task_id = "senluo_" + uuid.uuid4().hex[:10]
    utils.task_dir(task_id)  # 创建任务目录

    params = VideoParams(
        video_subject=name,
        video_script=script,
        video_aspect=aspect,
        video_concat_mode="sequential",
        video_clip_duration=clip_duration,
        video_source="local",
        video_materials=materials,
        voice_name=voice,
        voice_rate=1.0,
        subtitle_enabled=subtitle_enabled,
        subtitle_position="bottom",
        bgm_type=bgm_type,
        bgm_volume=bgm_volume,
        n_threads=2,
        video_count=1,
    )
    sm.state.update_task(task_id, state=4, progress=0)
    if verbose:
        print(f"[视频] 任务 {task_id} 启动：{name} | 口播 {voice} | {aspect} | "
              f"字幕{'开' if subtitle_enabled else '关'} | 素材 {len(materials)} 张", flush=True)

    result = {}

    def run():
        try:
            result.update(task.start(task_id, params, stop_at="video") or {})
        except Exception as e:
            result["error"] = f"任务异常 {type(e).__name__}: {e}"

    t = threading.Thread(target=run, daemon=True)
    t_start = time.time()
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
    if verbose:
        print(f"[视频] 任务结束，耗时 {int(time.time() - t_start)}s", flush=True)

    videos = result.get("videos") or []
    if not videos:
        err = result.get("error") or "视频任务失败（无结果）"
        print(f"RESULT {json.dumps({'ok': False, 'error': err}, ensure_ascii=False)}", flush=True)
        return

    final = videos[0]
    out = str(job.get("output") or "")
    if out:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        try:
            shutil.copy2(final, out)
        except OSError as e:
            print(f"RESULT {json.dumps({'ok': False, 'error': f'复制成片失败: {e}'}, ensure_ascii=False)}", flush=True)
            return

    # 成片已复制到 out，清理本次任务的临时文件：
    # 1) 任务目录（audio/combined/final/script 等中间产物）
    # 2) 复制到 local_videos 的素材及其生成的片段（xxx.png / xxx.png.mp4）
    try:
        shutil.rmtree(utils.task_dir(task_id), ignore_errors=True)
        for f in copied_dsts:
            for cand in (f, f + ".mp4"):
                try:
                    if os.path.isfile(cand):
                        os.remove(cand)
                except OSError:
                    pass
        if verbose:
            print(f"[视频] 临时文件已清理（{len(copied_dsts)} 份素材）", flush=True)
    except Exception as e:
        if verbose:
            print(f"[视频] 临时文件清理失败: {e}", flush=True)

    print(f"RESULT {json.dumps({'ok': True, 'output': out or final, 'task_id': task_id}, ensure_ascii=False)}", flush=True)


if __name__ == "__main__":
    main()
