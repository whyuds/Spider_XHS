import json
import os
import urllib.parse
from datetime import datetime, timedelta
import requests
import time
from loguru import logger

# 假设 utils 依然可用，保持原样导入
from utils import (
    init, 
    handle_note_info, 
    download_note, 
    save_to_xlsx, 
    get_saved_note_ids, 
    ocr_process_note_images, 
    generate_request_params, 
    splice_str, 
    norm_str, 
    generate_ai_summary, 
    send_wxpusher_message,
    get_spider_file
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
            # logger.info(response.text) # 减少日志输出，仅在出错或调试时开启
            res_json = response.json()
            success, msg = res_json.get("success"), res_json.get("msg")
        except Exception as e:
            success = False
            msg = str(e)
        return success, msg, res_json

    def get_user_notes_iter(self, user_url: str, cookies_str: str, proxies: dict = None):
        cursor = ''
        try:
            urlParse = urllib.parse.urlparse(user_url)
            # 兼容有些url path末尾可能有/的情况
            path_parts = [p for p in urlParse.path.split("/") if p]
            user_id = path_parts[-1]
            
            kvs = urlParse.query.split('&')
            kvDist = {}
            for kv in kvs:
                if '=' in kv:
                    kvDist[kv.split('=')[0]] = kv.split('=')[1]
            
            xsec_token = kvDist.get('xsec_token', "")
            xsec_source = kvDist.get('xsec_source', "pc_search")
            
            while True:
                # 带重试机制获取笔记列表
                max_retries = 3
                retry_wait_seconds = 180  # 3分钟
                notes = []
                
                for retry_count in range(max_retries):
                    success, msg, res_json = self.get_user_note_info(user_id, cursor, cookies_str, xsec_token, xsec_source, proxies)
                    if not success:
                        yield False, msg, []
                        return

                    notes = res_json.get("data", {}).get("notes", [])
                    
                    # 如果是首次请求且笔记列表为空，可能是被风控，尝试重试
                    if cursor == '' and len(notes) == 0 and retry_count < max_retries - 1:
                        logger.warning(f"用户 {user_id} 笔记列表为空 (疑似风控)，等待 {retry_wait_seconds // 60} 分钟后重试... (已重试 {retry_count + 1}/{max_retries - 1})")
                        time.sleep(retry_wait_seconds)
                    else:
                        break
                
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
            path_parts = [p for p in urlParse.path.split("/") if p]
            note_id = path_parts[-1]

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
            success, msg = res_json.get("success"), res_json.get("msg")
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
            success, msg, res_data = self.xhs_apis.get_note_info(note_url, cookies_str, proxies)
            if success and res_data.get('data', {}).get('items'):
                note_info = res_data['data']['items'][0]
                note_info['url'] = note_url
                note_info = handle_note_info(note_info)
            else:
                success = False
                if not msg: msg = "No items in response"
        except Exception as e:
            success = False
            msg = str(e)
        
        note_id_log = note_url.split('/')[-1].split('?')[0] if note_url else 'Unknown'
        logger.info(f'爬取笔记详情 {note_id_log}: {success}, msg: {msg}')
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

    def spider_user_all_note(self, user_url: str, cookies_str: str, base_path: dict, save_choice: str, excel_name: str = '', proxies=None, start_time: str = None, crawl_mode: str = 'FIRST_PAGE', is_update: bool = False, enable_ocr: bool = False, progress_info: str = ''):
        """
        :param start_time: 格式 "YYYY-MM-DD" 或 "YYYY-MM-DD HH:MM:SS"。如果为此时间之前的笔记，则停止爬取。
        :param crawl_mode: "FIRST_PAGE" (只爬取第一页) 或 "ALL" (遍历所有)。默认 "FIRST_PAGE"。
        """
        note_info_list = []
        
        # 统一处理时间对象
        target_dt = None
        if start_time:
            try:
                if len(start_time) == 10:
                    target_dt = datetime.strptime(start_time, "%Y-%m-%d")
                else:
                    target_dt = datetime.strptime(start_time, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                logger.error(f"Invalid start_time format: {start_time}. Continuing without time limit.")

        # 获取已存在的ID，用于跳过
        if save_choice == 'all' or save_choice == 'excel':
            excel_name = user_url.split('/')[-1].split('?')[0] if not excel_name else excel_name
            excel_path = os.path.abspath(os.path.join(base_path['excel'], f'{excel_name}.xlsx'))
        
        existing_ids = set()
        if is_update and (save_choice == 'all' or save_choice == 'excel'):
            existing_ids = get_saved_note_ids(excel_path)
            logger.info(f"增量更新模式：发现 {len(existing_ids)} 个已保存的笔记")

        old_note_count = 0
        MAX_OLD_TOLERANCE = 3 # 容错计数，防止因为置顶笔记(较旧)导致直接结束
        
        should_stop_outer = False
        current_user_tag = ""  # 用户标识，用于日志输出
        user_id_log = user_url.split('/')[-1].split('?')[0] if user_url else 'Unknown'

        try:
            for success, msg, simple_note_infos in self.xhs_apis.get_user_notes_iter(user_url, cookies_str, proxies):
                if not success:
                    logger.error(f"Failed to fetch user notes list: {msg}")
                    break
                
                # 日志记录用户信息（仅首次迭代打印）
                if progress_info:
                    if simple_note_infos:
                        nickname = simple_note_infos[0].get('user', {}).get('nickname', 'Unknown')
                    else:
                        nickname = user_id_log  # 如果没有笔记，用 user_id 作为标识
                    logger.info(f"")
                    logger.info(f"{'='*60}")
                    logger.info(f"[{progress_info}] 开始处理用户: {nickname} ({user_id_log})")
                    logger.info(f"{'='*60}")
                    current_user_tag = f"[{nickname}]"
                    progress_info = ''

                for simple_note_info in simple_note_infos:
                    note_id = simple_note_info['note_id']
                    
                    # 优化：如果笔记已存在，直接跳过，不做详情页请求
                    if is_update and note_id in existing_ids:
                        # 跳过日志太多时可以注释掉这行
                        # logger.info(f"{current_user_tag} 笔记 {note_id} 已存在，跳过。")
                        continue
                        
                    note_url = f"https://www.xiaohongshu.com/explore/{note_id}?xsec_token={simple_note_info.get('xsec_token', '')}"
                    
                    # 抓取详情，带重试机制
                    max_retries = 3
                    retry_wait_seconds = 180  # 3分钟
                    
                    for retry_count in range(max_retries):
                        success, msg, note_info = self.spider_note(note_url, cookies_str, proxies)
                        
                        if success and note_info:
                            break
                        
                        # 如果是 "No items in response" 错误，等待后重试
                        if msg == "No items in response" and retry_count < max_retries - 1:
                            logger.warning(f"{current_user_tag} 笔记 {note_id} 获取失败 (No items in response)，等待 {retry_wait_seconds // 60} 分钟后重试... (已重试 {retry_count + 1}/{max_retries - 1})")
                            time.sleep(retry_wait_seconds)
                        else:
                            break
                    
                    time.sleep(10) # 保持间隔，避免风控
                    
                    if not success or not note_info:
                        continue
                        
                    # 时间过滤逻辑
                    upload_time_str = note_info.get('upload_time') 
                    if upload_time_str:
                        current_note_dt = datetime.strptime(upload_time_str, "%Y-%m-%d %H:%M:%S")
                        
                        if target_dt:
                            # 如果有指定开始时间
                            # 注意：如果 start_time 是 "2026-01-01"，target_dt 是该日 00:00:00
                            # 简单的比较：如果笔记时间早于 target_dt，说明是旧笔记
                            if current_note_dt < target_dt:
                                old_note_count += 1
                                logger.info(f"{current_user_tag} 发现旧笔记 ({upload_time_str})，累计旧笔记数: {old_note_count}")
                                
                                # 旧笔记也保存，这样下次增量更新会被识别为已存在并跳过
                                note_info_list.append(note_info)
                                
                                if old_note_count >= MAX_OLD_TOLERANCE:
                                    logger.info(f"{current_user_tag} 已达到旧笔记容忍上限，停止抓取该用户。")
                                    should_stop_outer = True
                                    break
                                continue # 继续下一个笔记
                            else:
                                old_note_count = 0 # 重置计数器，因为发现了新笔记（可能是置顶笔记后的新笔记）

                    note_info_list.append(note_info)
                
                if should_stop_outer:
                    break

                # 优化：如果是 FIRST_PAGE 模式，处理完第一批后直接退出
                if crawl_mode == 'FIRST_PAGE':
                    logger.info("FIRST_PAGE mode: Finished first batch, stopping.")
                    break
            
            # 保存
            mode = 'a' if is_update else 'w'
            if note_info_list:
                self.process_and_save_notes(note_info_list, base_path, save_choice, excel_name, mode=mode, enable_ocr=enable_ocr)
            
            success = True
            msg = "Success"
            
        except Exception as e:
            success = False
            msg = str(e)
            logger.error(f'爬取用户笔记异常: {e}')
            
        logger.info(f"{current_user_tag} 用户爬取结束, 本次新增/更新 {len(note_info_list)} 篇笔记")
        return note_info_list, success, msg

if __name__ == '__main__':
    cookies_str, base_path = init()
    data_spider = Data_Spider()
    
    user_file = get_spider_file('user_profile.txt', migrate_from_project=True)
    user_urls = []
    if os.path.exists(user_file):
        with open(user_file, 'r', encoding='utf-8') as f:
            user_urls = [line.strip() for line in f if line.strip()]
    
    if not user_urls:
         logger.warning("No user URLs found in user_profile.txt")
    else:
        logger.info(f"本次将处理 {len(user_urls)} 个用户：")
        for idx, user_url in enumerate(user_urls, start=1):
            user_id = "Unknown"
            try:
                url_parse = urllib.parse.urlparse(user_url)
                path_parts = [p for p in url_parse.path.split("/") if p]
                user_id = path_parts[-1] if path_parts else "Unknown"
            except Exception:
                user_id = "Unknown"
            logger.info(f"[{idx}/{len(user_urls)}] user_id={user_id} url={user_url}")
    
    all_daily_notes = []
    
    # 设定追更的起始日期，例如：抓取今天和昨天的
    # 如果想抓取 "2026-01-13" 之后发布的所有笔记
    start_date = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
    # start_date = "2026-01-01" 
    
    logger.info(f"开始任务: 模式=FIRST_PAGE, 追更起始日期={start_date}")

    for i, user_url in enumerate(user_urls):
        progress_info = f"{i+1}/{len(user_urls)}"
        
        note_list, success, msg = data_spider.spider_user_all_note(
            user_url, 
            cookies_str, 
            base_path, 
            'all', 
            crawl_mode='FIRST_PAGE', # 默认只看首页，极大提高速度
            start_time=start_date,   # 传入起始时间，早于此时间的笔记将停止抓取
            is_update=True, 
            enable_ocr=True,
            progress_info=progress_info
        )
        
        if success and note_list:
            all_daily_notes.extend(note_list)
            
    # 后续总结与推送逻辑保持不变
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
            
            push_list_file = get_spider_file('user_id_push_list.txt', migrate_from_project=True)
            
            uids = []
            if os.path.exists(push_list_file):
                try:
                    with open(push_list_file, 'r', encoding='utf-8') as f:
                        uids = [line.strip() for line in f if line.strip()]
                except Exception as e:
                    logger.error(f"Failed to read user_id_push_list.txt: {e}")

            if uids:
                logger.info("Sending notification...")
                unique_nicknames = list(set([note.get('nickname') for note in all_daily_notes if note.get('nickname')]))
                if unique_nicknames:
                    first_user = unique_nicknames[0]
                    summary_title = f"{first_user}更新总结" if len(unique_nicknames) == 1 else f"{first_user}等用户更新总结"
                else:
                    summary_title = "小红书订阅更新"
                
                send_wxpusher_message(summary, uids, summary_prefix=summary_title)
            else:
                logger.info("No UIDs found, skipping notification.")
    else:
        logger.info("No new notes found to summarize.")
