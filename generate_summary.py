import argparse
import os
import json
from datetime import datetime
import requests
from loguru import logger
from utils import init, norm_str, generate_ai_summary, send_wxpusher_message, get_spider_file

def parse_args():
    parser = argparse.ArgumentParser(description="Generate AI Summary for Notes")
    parser.add_argument("--start_time", type=str, required=True, help="Start time (YYYYMMDDHHMM)")
    parser.add_argument("--end_time", type=str, required=True, help="End time (YYYYMMDDHHMM)")
    return parser.parse_args()



def get_notes_in_range(media_path, start_time, end_time):
    notes_content = ""
    count = 0
    
    start_dt = datetime.strptime(start_time, "%Y%m%d%H%M")
    end_dt = datetime.strptime(end_time, "%Y%m%d%H%M")
    
    if not os.path.exists(media_path):
        logger.warning(f"Media path does not exist: {media_path}")
        return "", 0

    # Walk through user directories
    for user_dir in os.listdir(media_path):
        user_path = os.path.join(media_path, user_dir)
        if not os.path.isdir(user_path):
            continue
            
        # Walk through note directories
        for note_dir in os.listdir(user_path):
            note_path = os.path.join(user_path, note_dir)
            if not os.path.isdir(note_path):
                continue
                
            info_path = os.path.join(note_path, 'info.json')
            if not os.path.exists(info_path):
                continue
                
            try:
                with open(info_path, 'r', encoding='utf-8') as f:
                    note_info = json.load(f)
                
                upload_time_str = note_info.get('upload_time')
                if not upload_time_str:
                    continue
                    
                note_dt = datetime.strptime(upload_time_str, "%Y-%m-%d %H:%M:%S")
                
                # Truncate seconds for comparison if input is only up to minute, or just compare directly.
                # Since input is HH:MM, start_dt has 00 seconds. end_dt has 00 seconds.
                # We should check if note_dt is within [start_dt, end_dt].
                # note_dt seconds precision is fine. A note at 10:00:30 is >= 10:00:00.
                
                if start_dt <= note_dt <= end_dt:
                    # Collect content
                    ocr_text = ""
                    for file in os.listdir(note_path):
                        if file.endswith('.txt') and file != 'detail.txt':
                            with open(os.path.join(note_path, file), 'r', encoding='utf-8') as f:
                                ocr_text += f"\n[图片文字 - {file}]:\n" + f.read()
                    
                    note_block = f"""
==================================================
笔记ID: {note_info.get('note_id')}
类型: {note_info.get('note_type')}
用户昵称: {note_info.get('nickname')}
标题: {note_info.get('title')}
描述: {note_info.get('desc')}
标签: {', '.join(note_info.get('tags', []))}
上传时间: {note_info.get('upload_time')}
OCR识别结果:
{ocr_text}
==================================================
"""
                    notes_content += note_block
                    count += 1
                    
            except Exception as e:
                logger.error(f"Error processing note {note_path}: {e}")
                continue
                
    return notes_content, count

def main():
    args = parse_args()
    cookies_str, base_path = init()
    
    logger.info(f"Collecting notes from {args.start_time} to {args.end_time}...")
    
    full_content, count = get_notes_in_range(base_path['media'], args.start_time, args.end_time)
    
    if count == 0:
        logger.info("No notes found in the specified date range.")
        return

    logger.info(f"Found {count} notes. Generating summary...")
    
    summary_dir = base_path.get("summary") or os.path.join(os.path.dirname(os.path.abspath(__file__)), 'datas', 'day_summary_datas')
    if not os.path.exists(summary_dir):
        os.makedirs(summary_dir, exist_ok=True)
    
    date_range_str = f"{args.start_time}_to_{args.end_time}"
    content_file_path = os.path.join(summary_dir, f'{date_range_str}_content.txt')
    summary_file_path = os.path.join(summary_dir, f'{date_range_str}_summary.txt')
    
    # Save content file
    try:
        with open(content_file_path, 'w', encoding='utf-8') as f:
            f.write(full_content)
        logger.info(f"Generated content file: {content_file_path}")
    except Exception as e:
        logger.error(f"Failed to write content file: {e}")
        return

    # Generate and save summary
    summary = generate_ai_summary(full_content)
    try:
        with open(summary_file_path, 'w', encoding='utf-8') as f:
            f.write(summary)
        logger.info(f"Generated summary file: {summary_file_path}")
    except Exception as e:
        logger.error(f"Failed to write summary file: {e}")
        return

    # Send Notification
    push_list_file = get_spider_file('user_id_push_list.txt', migrate_from_project=True)
    uids = []
    if os.path.exists(push_list_file):
        try:
            with open(push_list_file, 'r', encoding='utf-8') as f:
                uids = [line.strip() for line in f if line.strip()]
        except Exception as e:
            logger.error(f"Failed to read user_id_push_list.txt: {e}")
    else:
        logger.warning("user_id_push_list.txt not found.")

    if uids:
        logger.info("Sending notification...")
        send_wxpusher_message(summary, uids)
    else:
        logger.info("No UIDs found, skipping notification.")

if __name__ == '__main__':
    main()
