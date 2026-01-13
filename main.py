import json
import os
import urllib.parse
from datetime import datetime, timedelta
import requests
from loguru import logger
from openai import OpenAI

from utils import (
    init, 
    handle_note_info, 
    download_note, 
    save_to_xlsx, 
    get_saved_note_ids, 
    ocr_process_note_images, 
    generate_request_params,
    splice_str,
    norm_str
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
        logger.info(f'爬取笔记信息 {note_url}: {success}, msg: {msg}')
        return success, msg, note_info

    def process_and_save_notes(self, note_list: list, base_path: dict, save_choice: str, excel_name: str = '', mode='w', enable_ocr: bool = False):
        if (save_choice == 'all' or save_choice == 'excel') and excel_name == '':
            raise ValueError('excel_name 不能为空')
            
        for note_info in note_list:
            if save_choice == 'all' or 'media' in save_choice:
                save_path = download_note(note_info, base_path['media'], save_choice)
                if enable_ocr:
                    ocr_process_note_images(save_path)

        if save_choice == 'all' or save_choice == 'excel':
            file_path = os.path.abspath(os.path.join(base_path['excel'], f'{excel_name}.xlsx'))
            save_to_xlsx(note_list, file_path, mode=mode)

    def generate_ai_summary(self, content):
        api_key = os.getenv('ARK_API_KEY')
        if not api_key:
            logger.error("ARK_API_KEY not found in environment variables. Skipping AI summary.")
            return "Error: ARK_API_KEY not found."

        try:
            client = OpenAI(
                base_url="https://ark.cn-beijing.volces.com/api/v3",
                api_key=api_key,
            )
            
            # Using standard chat completion
            response = client.chat.completions.create(
                model="doubao-seed-1-8-251228",
                messages=[
                    {
                        "role": "system",
                        "content": "你是一个专业的投资分析助手。请根据提供的用户笔记内容（包含OCR识别的文字），总结整理出一份当日投资分析。"
                    },
                    {
                        "role": "user",
                        "content": content
                    }
                ]
            )
            return response.choices[0].message.content
        except Exception as e:
            logger.error(f"AI Summary generation failed: {e}")
            return f"Error generating summary: {e}"

    def generate_day_summary_files(self, date_str, note_list, base_path):
        summary_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'datas', 'day_summary_datas')
        if not os.path.exists(summary_dir):
            os.makedirs(summary_dir)
        
        content_file_path = os.path.join(summary_dir, f'{date_str}_content.txt')
        summary_file_path = os.path.join(summary_dir, f'{date_str}_summary.txt')
        
        # 1. Generate Content File
        full_content = ""
        for note in note_list:
            # Reconstruct save path to find OCR files
            title = norm_str(note['title'])[:40]
            nickname = norm_str(note['nickname'])[:20]
            if not title.strip(): title = '无标题'
            
            user_id = note['user_id']
            note_id = note['note_id']
            upload_time = note.get('upload_time')
            date_prefix = ""
            if upload_time:
                try:
                    date_prefix = upload_time.split(' ')[0].replace('-', '') + "_"
                except:
                    pass
            
            # Path format from download_note: f'{path}/{nickname}_{user_id}/{date_prefix}{title}_{note_id}'
            # We need to construct the absolute path to check for txt files
            note_dir_name = f"{date_prefix}{title}_{note_id}"
            user_dir_name = f"{nickname}_{user_id}"
            note_ab_path = os.path.join(base_path['media'], user_dir_name, note_dir_name)
            
            ocr_text = ""
            if os.path.exists(note_ab_path):
                for file in os.listdir(note_ab_path):
                    if file.endswith('.txt') and file != 'detail.txt':
                        with open(os.path.join(note_ab_path, file), 'r', encoding='utf-8') as f:
                            ocr_text += f"\n[图片文字 - {file}]:\n" + f.read()
            
            note_block = f"""
==================================================
笔记ID: {note['note_id']}
类型: {note['note_type']}
用户昵称: {note['nickname']}
标题: {note['title']}
描述: {note['desc']}
标签: {', '.join(note.get('tags', []))}
上传时间: {note.get('upload_time')}
OCR识别结果:
{ocr_text}
==================================================
"""
            full_content += note_block

        try:
            with open(content_file_path, 'w', encoding='utf-8') as f:
                f.write(full_content)
            logger.info(f"Generated day content file: {content_file_path}")
        except Exception as e:
            logger.error(f"Failed to write content file: {e}")
            return

        # 2. Generate AI Summary
        if full_content.strip():
            logger.info("Generating AI summary...")
            summary = self.generate_ai_summary(full_content)
            try:
                with open(summary_file_path, 'w', encoding='utf-8') as f:
                    f.write(summary)
                logger.info(f"Generated day summary file: {summary_file_path}")
            except Exception as e:
                logger.error(f"Failed to write summary file: {e}")
        else:
            logger.warning("No content to summarize.")

    def spider_user_all_note(self, user_url: str, cookies_str: str, base_path: dict, save_choice: str, excel_name: str = '', proxies=None, crawl_interval: str = 'all', is_update: bool = False, enable_ocr: bool = False):
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
                
                for simple_note_info in simple_note_infos:
                    note_id = simple_note_info['note_id']
                    
                    if is_update and note_id in existing_ids:
                        continue
                        
                    note_url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={simple_note_info.get('xsec_token', '')}"
                    
                    success, msg, note_info = self.spider_note(note_url, cookies_str, proxies)
                    
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
                    elif is_old:
                        old_note_count += 1
                        if old_note_count >= MAX_OLD_TOLERANCE:
                            logger.info(f"Reached time limit ({crawl_interval}). Stopping.")
                            break
                            
                if old_note_count >= MAX_OLD_TOLERANCE:
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
            
        logger.info(f'爬取用户 {user_url} 结束, 共 {len(note_info_list)} 篇符合条件的笔记')
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
    
    # crawling configuration
    crawl_date = '1month' # Target date
    all_daily_notes = []
    
    for i, user_url in enumerate(user_urls):
        logger.info(f"Processing user {i+1}/{len(user_urls)}: {user_url}")
        note_list, success, msg = data_spider.spider_user_all_note(
            user_url, 
            cookies_str, 
            base_path, 
            'all', 
            crawl_interval=crawl_date, 
            is_update=True, 
            enable_ocr=True
        )
        if success and note_list:
            all_daily_notes.extend(note_list)
            
    # Generate summary for the day across all users
    enable_summary = False
    if enable_summary and all_daily_notes:
        logger.info(f"Generating summary for {len(all_daily_notes)} notes from {len(user_urls)} users...")
        data_spider.generate_day_summary_files(crawl_date, all_daily_notes, base_path)
    elif not all_daily_notes:
        logger.info("No notes found for the specified date.")