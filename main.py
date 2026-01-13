import json
import os
import urllib.parse
from datetime import datetime, timedelta
import requests
import time
from loguru import logger


from utils import (
    init, 
    handle_note_info, 
    download_note, 
    save_to_xlsx, 
    get_saved_note_ids, 
    ocr_process_note_images, 
    generate_request_params,
    splice_str,
    generate_request_params,
    splice_str,
    norm_str,
    generate_ai_summary,
    send_wxpusher_message
)

class XHS_Apis():
    def __init__(self):
        self.base_url = "https://edith.xiaohongshu.com"

    def get_user_note_info(self, user_id: str, cursor: str, cookies_str: str, xsec_token='', xsec_source='', proxies: dict = None):
        res_json = None
        try:
            api = f"/api/sns/web/v1/user_posted"
            params = {
                "num": "30",
                "cursor": cursor,
                "user_id": user_id,
                "image_formats": "jpg,webp,avif",
                "xsec_token": xsec_token,
                "xsec_source": xsec_source,
            }
            splice_api = splice_str(api, params)
            headers, cookies, data = generate_request_params(cookies_str, splice_api, '', 'GET')
            response = requests.get(self.base_url + splice_api, headers=headers, cookies=cookies, proxies=proxies)
            logger.info(response.text)
            res_json = response.json()
            success, msg = res_json["success"], res_json["msg"]
        except Exception as e:
            success = False
            msg = str(e)
        return success, msg, res_json

    def get_user_notes_iter(self, user_url: str, cookies_str: str, proxies: dict = None):
        cursor = ''
        try:
            urlParse = urllib.parse.urlparse(user_url)
            user_id = urlParse.path.split("/")[-1]
            kvs = urlParse.query.split('&')
            kvDist = {}
            for kv in kvs:
                if '=' in kv:
                    kvDist[kv.split('=')[0]] = kv.split('=')[1]
            
            xsec_token = kvDist.get('xsec_token', "")
            xsec_source = kvDist.get('xsec_source', "pc_search")
            
            while True:
                success, msg, res_json = self.get_user_note_info(user_id, cursor, cookies_str, xsec_token, xsec_source, proxies)
                if not success:
                    yield False, msg, []
                    return

                notes = res_json.get("data", {}).get("notes", [])
                yield True, "success", notes

                if res_json.get("data", {}).get('cursor'):
                    cursor = str(res_json["data"]["cursor"])
                else:
                    break
                
                if len(notes) == 0 or not res_json.get("data", {}).get("has_more"):
                    break
        except Exception as e:
            yield False, str(e), []

    def get_note_info(self, url: str, cookies_str: str, proxies: dict = None):
        res_json = None
        try:
            urlParse = urllib.parse.urlparse(url)
            note_id = urlParse.path.split("/")[-1]
            kvs = urlParse.query.split('&')
            kvDist = {}
            for kv in kvs:
                if '=' in kv:
                    kvDist[kv.split('=')[0]] = kv.split('=')[1]

            api = f"/api/sns/web/v1/feed"
            data = {
                "source_note_id": note_id,
                "image_formats": ["jpg", "webp", "avif"],
                "extra": {"need_body_topic": "1"},
                "xsec_source": kvDist.get('xsec_source', "pc_search"),
                "xsec_token": kvDist.get('xsec_token', "")
            }
            headers, cookies, data_encoded = generate_request_params(cookies_str, api, data, 'POST')
            response = requests.post(self.base_url + api, headers=headers, data=data_encoded, cookies=cookies, proxies=proxies)
            res_json = response.json()
            success, msg = res_json["success"], res_json["msg"]
        except Exception as e:
            success = False
            msg = str(e)
        return success, msg, res_json

class Data_Spider():
    def __init__(self):
        self.xhs_apis = XHS_Apis()

    def spider_note(self, note_url: str, cookies_str: str, proxies=None):
        note_info = None
        try:
            success, msg, note_info = self.xhs_apis.get_note_info(note_url, cookies_str, proxies)
            if success:
                note_info = note_info['data']['items'][0]
                note_info['url'] = note_url
                note_info = handle_note_info(note_info)
        except Exception as e:
            success = False
            msg = e
        note_id_log = note_url.split('/')[-1].split('?')[0] if note_url else 'Unknown'
        logger.info(f'爬取笔记信息 {note_id_log}: {success}, msg: {msg}')
        return success, msg, note_info

    def process_and_save_notes(self, note_list: list, base_path: dict, save_choice: str, excel_name: str = '', mode='w', enable_ocr: bool = False):
        if (save_choice == 'all' or save_choice == 'excel') and excel_name == '':
            raise ValueError('excel_name 不能为空')
            
        for note_info in note_list:
            if save_choice == 'all' or 'media' in save_choice:
                save_path = download_note(note_info, base_path['media'], save_choice)
                note_info['local_save_path'] = save_path
                if enable_ocr:
                    ocr_process_note_images(save_path)

        if save_choice == 'all' or save_choice == 'excel':
            file_path = os.path.abspath(os.path.join(base_path['excel'], f'{excel_name}.xlsx'))
            save_to_xlsx(note_list, file_path, mode=mode)





    def spider_user_all_note(self, user_url: str, cookies_str: str, base_path: dict, save_choice: str, excel_name: str = '', proxies=None, crawl_interval: str = 'all', is_update: bool = False, enable_ocr: bool = False, progress_info: str = ''):
        note_info_list = []
        note_url_list = []
        
        start_time = None
        target_date = None
        
        if crawl_interval == '1day':
            start_time = datetime.now() - timedelta(days=1)
        elif crawl_interval == '3day':
            start_time = datetime.now() - timedelta(days=3)
        elif crawl_interval == '1week':
            start_time = datetime.now() - timedelta(weeks=1)
        elif crawl_interval == '1month':
            start_time = datetime.now() - timedelta(days=30)
        elif '-' in crawl_interval and len(crawl_interval) == 10: 
            try:
                target_date = datetime.strptime(crawl_interval, "%Y-%m-%d").date()
            except ValueError:
                logger.error(f"Invalid date format: {crawl_interval}")
                return [], False, "Invalid date format"
        
        old_note_count = 0
        MAX_OLD_TOLERANCE = 3 
        
        if save_choice == 'all' or save_choice == 'excel':
            # normalize filename from user url or use nickname if available (but we loop users, so handled per user)
            excel_name = user_url.split('/')[-1].split('?')[0] if not excel_name else excel_name
            excel_path = os.path.abspath(os.path.join(base_path['excel'], f'{excel_name}.xlsx'))
        
        existing_ids = set()
        if is_update and (save_choice == 'all' or save_choice == 'excel'):
            existing_ids = get_saved_note_ids(excel_path)
            logger.info(f"增量更新模式：发现 {len(existing_ids)} 个已保存的笔记")

        try:
            for success, msg, simple_note_infos in self.xhs_apis.get_user_notes_iter(user_url, cookies_str, proxies):
                if not success:
                    logger.error(f"Failed to fetch user notes: {msg}")
                    break
                
                logger.info(f'Fetching batch of {len(simple_note_infos)} notes...')

                if simple_note_infos and len(simple_note_infos) > 0:
                    try:
                         # Log user info on first batch success if progress_info is provided or just once
                         # We can check if we handled this log already? 
                         # Simpler: just log it here. The loop goes batch by batch. 
                         # But we only want to log "Processing user..." once.
                         pass
                    except:
                        pass
                
                # Check for nickname for logging
                if progress_info:
                    nickname = "Unknown"
                    if simple_note_infos and len(simple_note_infos) > 0:
                        nickname = simple_note_infos[0].get('user', {}).get('nickname', 'Unknown')
                    
                    user_id_log = user_url.split('/')[-1].split('?')[0] if user_url else 'Unknown'
                    logger.info(f"Processing user {progress_info}: {nickname} ({user_id_log})")
                    progress_info = '' # Clear so we don't log again for next batch
                
                for simple_note_info in simple_note_infos:
                    note_id = simple_note_info['note_id']
                    
                    if is_update and note_id in existing_ids:
                        continue
                        
                    note_url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={simple_note_info.get('xsec_token', '')}"
                    
                    success, msg, note_info = self.spider_note(note_url, cookies_str, proxies)
                    time.sleep(10)
                    
                    if not success or not note_info:
                        continue
                        
                    upload_time_str = note_info.get('upload_time') 
                    if not upload_time_str:
                        if crawl_interval == 'all':
                             note_info_list.append(note_info)
                             note_url_list.append(note_url)
                        continue

                    upload_dt = datetime.strptime(upload_time_str, "%Y-%m-%d %H:%M:%S")
                    is_match = False
                    is_old = False

                    if crawl_interval == 'all':
                        is_match = True
                    elif target_date:
                        if upload_dt.date() == target_date:
                            is_match = True
                        elif upload_dt.date() < target_date:
                            is_old = True
                    elif start_time:
                        if upload_dt >= start_time:
                            is_match = True
                        else:
                            is_old = True
                    
                    if is_match:
                        note_info_list.append(note_info)
                        note_url_list.append(note_url)
                        old_note_count = 0 
                        
                        if target_date and len(note_info_list) >= 10:
                            logger.info(f"已达到特定日期最大爬取数量限制 (10篇). Stopping.")
                            # Break out of both loops by returning or breaking with a flag. 
                            # Since we are inside a method that returns note_info_list, we can break the outer loop or return.
                            # But we also need to save data. The saving happens after the loop.
                            # So we should break both loops.
                            break 
                    elif is_old:
                        old_note_count += 1
                        if old_note_count >= MAX_OLD_TOLERANCE:
                            logger.info(f"Reached time limit ({crawl_interval}). Stopping.")
                            break
                            
                if old_note_count >= MAX_OLD_TOLERANCE:
                    break
                
                if target_date and len(note_info_list) >= 10:
                    break

            mode = 'a' if is_update else 'w'
            if note_info_list:
                self.process_and_save_notes(note_info_list, base_path, save_choice, excel_name, mode=mode, enable_ocr=enable_ocr)
            
            success = True
            msg = "Success"
            
        except Exception as e:
            success = False
            msg = e
            logger.error(f'爬取用户笔记异常: {e}')
            
        user_id_log = user_url.split('/')[-1].split('?')[0] if user_url else 'Unknown'
        logger.info(f'爬取用户 {user_id_log} 结束, 共 {len(note_info_list)} 篇符合条件的笔记')
        return note_info_list, success, msg

if __name__ == '__main__':
    cookies_str, base_path = init()
    data_spider = Data_Spider()
    
    # Read user profiles
    user_file = 'user_profile.txt'
    user_urls = []
    if os.path.exists(user_file):
        with open(user_file, 'r', encoding='utf-8') as f:
            user_urls = [line.strip() for line in f if line.strip()]
    
    if not user_urls:
         logger.warning("No user URLs found in user_profile.txt")
    
    all_daily_notes = []
    
    for i, user_url in enumerate(user_urls):
        progress_info = f"{i+1}/{len(user_urls)}"
        note_list, success, msg = data_spider.spider_user_all_note(
            user_url, 
            cookies_str, 
            base_path, 
            'all', 
            crawl_interval=datetime.now().strftime('%Y-%m-%d'), 
            is_update=True, 
            enable_ocr=True,
            progress_info=progress_info
        )
        if success and note_list:
            all_daily_notes.extend(note_list)
            

    # Generate summary and push notification for updated notes
    if all_daily_notes:
        logger.info(f"Generating summary for {len(all_daily_notes)} new/updated notes...")
        
        full_content = ""
        for note in all_daily_notes:
            save_path = note.get('local_save_path')
            if not save_path or not os.path.exists(save_path):
                continue
                
            ocr_text = ""
            for file in os.listdir(save_path):
                if file.endswith('.txt') and file != 'detail.txt':
                    with open(os.path.join(save_path, file), 'r', encoding='utf-8') as f:
                        ocr_text += f"\n[图片文字 - {file}]:\n" + f.read()
            
            note_block = f"""
==================================================
笔记ID: {note.get('note_id')}
类型: {note.get('note_type')}
用户昵称: {note.get('nickname')}
标题: {note.get('title')}
描述: {note.get('desc')}
标签: {', '.join(note.get('tags', []))}
上传时间: {note.get('upload_time')}
OCR识别结果:
{ocr_text}
==================================================
"""
            full_content += note_block

        if full_content.strip():
            summary = generate_ai_summary(full_content)
            
            # Send Notification
            push_list_file = 'user_id_push_list.txt' # Relative to cwd or absolute
            if not os.path.exists(push_list_file):
                 push_list_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'user_id_push_list.txt')
            
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
                
                # Construct summary title based on updated users
                unique_nicknames = list(set([note.get('nickname') for note in all_daily_notes if note.get('nickname')]))
                if unique_nicknames:
                    first_user = unique_nicknames[0]
                    if len(unique_nicknames) == 1:
                        summary_title = f"{first_user}更新总结"
                    else:
                        summary_title = f"{first_user}等用户更新总结"
                else:
                    summary_title = "" # Fallback to default in utils
                
                send_wxpusher_message(summary, uids, summary_prefix=summary_title)
            else:
                logger.info("No UIDs found, skipping notification.")
        else:
             logger.warning("No valid content found for summary.")
    else:
        logger.info("No new notes found to summarize.")
