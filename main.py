#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Floating tips app with carousel rotation.

Changes:
- Extract tips into a list and rotate the displayed tip once per minute.
- Show a live countdown in seconds until the next rotation.
- Selenium fetching runs in a background thread and updates the tips list periodically.
- UI updates the displayed tip and countdown every second via tkinter's `after`.
"""

import ctypes
import os
import re
import struct
import threading
import time
from datetime import datetime
import tkinter as tk
from ctypes import wintypes
from tkinter import font
from typing import List

import toml
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.edge.options import Options as EdgeOptions
from selenium.webdriver.edge.service import Service as EdgeService


# Windows API 字体加载函数
def load_custom_font(font_path):
    """使用Windows API加载自定义字体文件"""
    if not os.path.exists(font_path):
        return None

    try:
        # 加载字体文件
        gdi32 = ctypes.WinDLL("gdi32.dll")
        kernel32 = ctypes.WinDLL("kernel32.dll")

        # 定义AddFontResourceEx函数
        FR_PRIVATE = 0x10

        # 将路径转换为宽字符
        font_path_w = ctypes.c_wchar_p(font_path)

        # 添加字体资源
        result = gdi32.AddFontResourceExW(font_path_w, FR_PRIVATE, 0)

        if result > 0:
            return True
        return None
    except Exception:
        return None


def parse_dict_to_list(d) -> list:
    if isinstance(d, str):
        return [d]
    if isinstance(d, list):
        return d
    if isinstance(d, dict):
        res = []
        for i in d.values():
            res += parse_dict_to_list(i)
        return res
    return []


def extract_font_name_from_ttf(ttf_path):
    """
    从TTF文件中提取字体名称
    返回字体名称或None（如果提取失败）
    """
    if not os.path.exists(ttf_path):
        return None

    try:
        with open(ttf_path, "rb") as f:
            # 读取字体文件头部
            data = f.read(12)
            if len(data) < 12:
                return None

            # 解析头部
            sfnt_version, num_tables = struct.unpack(">IH", data[:6])

            # 跳过头部剩余部分
            f.seek(12)

            # 查找名称表
            name_table_offset = None
            name_table_length = None

            for i in range(num_tables):
                table_data = f.read(16)
                if len(table_data) < 16:
                    break

                tag, checksum, offset, length = struct.unpack(">4sIII", table_data)

                if tag == b"name":
                    name_table_offset = offset
                    name_table_length = length
                    break

            if name_table_offset is None:
                return None

            # 读取名称表
            f.seek(name_table_offset)
            name_data = f.read(name_table_length)

            if len(name_data) < 6:
                return None

            # 解析名称表头部
            format_selector, name_record_count, string_storage_offset = struct.unpack(
                ">HHH", name_data[:6]
            )

            # 查找字体族名称（名称ID = 1，平台ID = 3，编码ID = 1，语言ID = 0x0804中文或0x0409英文）
            font_name = None

            for i in range(name_record_count):
                record_offset = 6 + i * 12
                if record_offset + 12 > len(name_data):
                    break

                platform_id, encoding_id, language_id, name_id, length, offset = (
                    struct.unpack(
                        ">HHHHHH", name_data[record_offset : record_offset + 12]
                    )
                )

                # 查找字体族名称（name_id = 1）
                if name_id == 1:
                    # 优先中文（0x0804）或英文（0x0409）名称
                    if language_id in [0x0804, 0x0409] or font_name is None:
                        string_offset = string_storage_offset + offset
                        if string_offset + length <= len(name_data):
                            try:
                                # 优先尝试UTF-16BE解码（平台3常用）
                                if platform_id == 3:
                                    name_bytes = name_data[
                                        string_offset : string_offset + length
                                    ]
                                    font_name = name_bytes.decode("utf-16be").strip()
                                else:
                                    # UTF-8或Latin-1作为后备
                                    name_bytes = name_data[
                                        string_offset : string_offset + length
                                    ]
                                    try:
                                        font_name = name_bytes.decode("utf-8").strip()
                                    except:
                                        font_name = name_bytes.decode("latin-1").strip()
                            except:
                                continue

            return font_name if font_name else None

    except Exception as e:
        print(f"提取字体名称时出错: {e}")
        return None


# 读取配置文件
def load_config():
    config_file = "config.toml"
    default_config = {
        "config": {
            "cloud_file_scr": "https://www.kdocs.cn/l/colfFw2Piprw",
            "refresh_interval": 60,
            "fetch_interval": 300,
            "error_display_duration": 30,
            "always_on_top": True,
        },
        "font": {
            "custom_font_file": "FLyouzichati-Regular-2.ttf",
            "fallback_font": "Microsoft YaHei",
        },
        "colors": {
            "background_color": "white",
            "tip_text_color": "#333333",
            "countdown_text_color": "#10aec2",
            "border_color": "#10aec2",
        },
    }

    if not os.path.exists(config_file):
        # 创建默认配置文件
        with open(config_file, "w", encoding="utf-8") as f:
            toml.dump(default_config, f)
        return default_config["config"]

    try:
        with open(config_file, "r", encoding="utf-8") as f:
            config = toml.load(f)

        # 确保配置项存在 - 处理config部分
        if "config" not in config:
            config["config"] = {}
        for key, value in default_config["config"].items():
            if key not in config["config"]:
                config["config"][key] = value

        # 确保配置项存在 - 处理font部分
        if "font" not in config:
            config["font"] = {}
        for key, value in default_config["font"].items():
            if key not in config["font"]:
                config["font"][key] = value

        # 确保配置项存在 - 处理colors部分
        if "colors" not in config:
            config["colors"] = {}
        for key, value in default_config["colors"].items():
            if key not in config["colors"]:
                config["colors"][key] = value

        # 保存更新后的配置
        with open(config_file, "w", encoding="utf-8") as f:
            toml.dump(config, f)

        # 返回合并后的配置字典
        merged_config = {}
        merged_config.update(config["config"])
        merged_config.update(config["font"])
        merged_config.update(config["colors"])
        return merged_config

    except Exception:
        # 配置文件读取失败，使用默认值并重新创建
        with open(config_file, "w", encoding="utf-8") as f:
            toml.dump(default_config, f)

        # 返回合并后的默认配置
        merged_config = {}
        merged_config.update(default_config["config"])
        merged_config.update(default_config["font"])
        merged_config.update(default_config["colors"])
        return merged_config


# 加载配置
config = load_config()
CLOUD_FILE_SCR = config["cloud_file_scr"]
REFRESH_INTERVAL = config["refresh_interval"]
FETCH_INTERVAL = config["fetch_interval"]
ERROR_DISPLAY_DURATION = config["error_display_duration"]
ALWAYS_ON_TOP = config["always_on_top"]

# 字体配置
CUSTOM_FONT_FILE = config["custom_font_file"]
FALLBACK_FONT = config["fallback_font"]

# 颜色配置
BACKGROUND_COLOR = config["background_color"]
TIP_TEXT_COLOR = config["tip_text_color"]
COUNTDOWN_TEXT_COLOR = config["countdown_text_color"]
BORDER_COLOR = config["border_color"]


def slow_scroll(driver, step=500):
    """
    Slowly scroll a container to ensure content loads into the DOM (KDocs specific).
    """
    try:
        container = driver.find_element(By.ID, "workspace")
    except Exception:
        return
    cnt = 0
    while cnt <= 10:
        try:
            driver.execute_script(f"arguments[0].scrollBy(0, {step});", container)
        except Exception:
            break
        time.sleep(0.5)
        cnt += 1


class FloatingTipsApp:
    def __init__(self, root):
        self.root = root
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", ALWAYS_ON_TOP)
        self.root.configure(bg=BACKGROUND_COLOR)  # 使用配置的背景色
        self.root.attributes("-alpha", 1.0)  # 不透明
        # 使用Windows API实现透明效果（延迟执行，确保窗口已创建）
        self.root.after(100, self._set_window_transparent)

        # 初始位置和大小
        self.root.geometry("500x60+500+100")

        # Data for carousel and error management (initialize first)
        self.tips_lock = threading.Lock()
        self.tips: List[str] = []  # list of tip strings
        self.current_index = 0
        self.tip_shown_at = time.time()  # timestamp when current tip started showing
        self.rotation_seconds = REFRESH_INTERVAL  # 每条显示时长（秒）

        # Error state management
        self.error_message = None  # current error message to display
        self.error_display_until = 0  # timestamp until which error should be displayed
        self.error_display_duration = (
            ERROR_DISPLAY_DURATION  # error message display duration from config
        )

        # Running flag
        self.running = True
        self.fetch_interval_seconds = (
            FETCH_INTERVAL  # selenium fetch interval (默认300秒)
        )

        # 加载自定义字体
        custom_font_path = os.path.join(os.path.dirname(__file__), CUSTOM_FONT_FILE)
        font_family = FALLBACK_FONT
        font_load_success = False

        if os.path.exists(custom_font_path):
            try:
                # 使用Windows API加载字体
                if load_custom_font(custom_font_path):
                    # 字体加载成功，动态提取字体名称
                    extracted_font_name = extract_font_name_from_ttf(custom_font_path)
                    if extracted_font_name:
                        font_family = extracted_font_name
                        font_load_success = True
                        # 显示字体加载成功提示
                        with self.tips_lock:
                            self.error_message = (
                                f"✅ 字体加载成功: {extracted_font_name}"
                            )
                            self.error_display_until = (
                                time.time() + 5
                            )  # 成功提示显示5秒
                    else:
                        # 字体加载成功但无法提取名称，使用文件名作为后备
                        font_family = FALLBACK_FONT
                        font_load_success = True
                        with self.tips_lock:
                            self.error_message = (
                                f"✅ 字体加载成功，但无法提取字体名称，使用备用字体"
                            )
                            self.error_display_until = time.time() + 5
                else:
                    # 字体文件存在但加载失败
                    with self.tips_lock:
                        self.error_message = f"字体文件加载失败: {CUSTOM_FONT_FILE}"
                        self.error_display_until = time.time() + ERROR_DISPLAY_DURATION
            except Exception as e:
                # 字体加载异常
                with self.tips_lock:
                    self.error_message = f"字体加载异常: {str(e)[:60]}"
                    self.error_display_until = time.time() + ERROR_DISPLAY_DURATION
        else:
            # 字体文件不存在
            with self.tips_lock:
                self.error_message = f"字体文件不存在: {CUSTOM_FONT_FILE}，使用备用字体"
                self.error_display_until = time.time() + ERROR_DISPLAY_DURATION

        # Error state management
        self.error_message = None  # current error message to display
        self.error_display_until = 0  # timestamp until which error should be displayed
        self.error_display_duration = (
            ERROR_DISPLAY_DURATION  # error message display duration from config
        )

        # Running flag
        self.running = True
        self.fetch_interval_seconds = (
            FETCH_INTERVAL  # selenium fetch interval (默认300秒)
        )

        # 创建圆角矩形容器（外层背景）
        self.container = tk.Frame(root, bg=BACKGROUND_COLOR, bd=0, relief="flat")
        self.container.pack(fill="both", expand=True, padx=8, pady=8)

        # 创建Canvas用于绘制细线圆角边框
        self.border_canvas = tk.Canvas(
            self.container, bg=BACKGROUND_COLOR, bd=0, highlightthickness=0
        )
        self.border_canvas.pack(fill="both", expand=True)

        # 在Canvas上创建内容框架（透明背景，不遮住边框）
        self.content_frame = tk.Frame(self.border_canvas, bg=BACKGROUND_COLOR, bd=0)

        # 创建水平布局的标签容器（移除左右padding，让文字贴边）
        self.labels_frame = tk.Frame(self.content_frame, bg=BACKGROUND_COLOR, bd=0)
        self.labels_frame.pack(fill="both", expand=True, padx=0, pady=0)

        # 左侧Notice显示区（移除背景色，文字靠最左）
        self.tip_label = tk.Label(
            self.labels_frame,
            text="系统启动中，正在初始化浏览器...",
            fg=TIP_TEXT_COLOR,
            bg=BACKGROUND_COLOR,
            font=(font_family, 10, "bold"),
            wraplength=380,
            justify="left",
            anchor="w",
        )
        self.tip_label.pack(side="left", fill="both", expand=True, padx=8, pady=6)

        # 右侧倒计时（移除背景色，文字靠最右）
        self.countdown_label = tk.Label(
            self.labels_frame,
            text="--",
            fg=COUNTDOWN_TEXT_COLOR,
            bg=BACKGROUND_COLOR,
            font=(font_family, 9),
            anchor="e",
            justify="right",
        )
        self.countdown_label.pack(side="right", fill="y", padx=8, pady=6)

        # 延迟绘制圆角边框
        self.root.after(150, self._draw_rounded_border)

        # 鼠标拖动绑定
        self.tip_label.bind("<Button-1>", self.start_move)
        self.tip_label.bind("<B1-Motion>", self.do_move)
        self.tip_label.bind("<ButtonRelease-1>", self.stop_move)
        self.countdown_label.bind("<Button-1>", self.start_move)
        self.countdown_label.bind("<B1-Motion>", self.do_move)
        self.countdown_label.bind("<ButtonRelease-1>", self.stop_move)
        self.container.bind("<Button-1>", self.start_move)
        self.container.bind("<B1-Motion>", self.do_move)
        self.container.bind("<ButtonRelease-1>", self.stop_move)
        self.border_canvas.bind("<Button-1>", self.start_move)
        self.border_canvas.bind("<B1-Motion>", self.do_move)
        self.border_canvas.bind("<ButtonRelease-1>", self.stop_move)
        self.content_frame.bind("<Button-1>", self.start_move)
        self.content_frame.bind("<B1-Motion>", self.do_move)
        self.content_frame.bind("<ButtonRelease-1>", self.stop_move)
        self.labels_frame.bind("<Button-1>", self.start_move)
        self.labels_frame.bind("<B1-Motion>", self.do_move)
        self.labels_frame.bind("<ButtonRelease-1>", self.stop_move)

        # 右键退出
        self.root.bind("<Button-3>", self.on_closing)

        # 启动 Selenium 线程（守护线程）
        self.update_thread = threading.Thread(
            target=self.selenium_loader_task, daemon=True
        )
        self.update_thread.start()

        # 启动 UI 每秒更新（倒计时 + 轮播）
        self._schedule_ui_update()

    # ---------------------
    # 窗口拖动相关
    # ---------------------
    def start_move(self, event):
        self.x = event.x
        self.y = event.y
        self.root.attributes("-alpha", 1.0)  # 拖动时不透明

    def do_move(self, event):
        deltax = event.x - self.x
        deltay = event.y - self.y
        x = self.root.winfo_x() + deltax
        y = self.root.winfo_y() + deltay
        self.root.geometry(f"+{x}+{y}")

    def stop_move(self, event):
        self.root.attributes("-alpha", 1.0)  # 拖动结束后保持不透明

    def _draw_rounded_border(self):
        """在Canvas上绘制细线圆角边框"""
        try:
            # 获取Canvas尺寸
            self.border_canvas.update_idletasks()
            width = self.border_canvas.winfo_width()
            height = self.border_canvas.winfo_height()

            if width <= 1 or height <= 1:
                width = 480
                height = 44

            # 清除之前的绘制
            self.border_canvas.delete("all")

            # 将内容框架放置在Canvas中央
            self.content_frame.place(relx=0.5, rely=0.5, anchor="center")

            # 绘制细线圆角矩形边框
            radius = 12  # 圆角半径
            line_width = 1  # 线宽（细线）
            padding = line_width + 1

            # 绘制圆角矩形路径
            # 左上角圆弧
            self.border_canvas.create_arc(
                padding,
                padding,
                padding + radius * 2,
                padding + radius * 2,
                start=90,
                extent=90,
                style="arc",
                outline=BORDER_COLOR,
                width=line_width,
            )
            # 右上角圆弧
            self.border_canvas.create_arc(
                width - padding - radius * 2,
                padding,
                width - padding,
                padding + radius * 2,
                start=0,
                extent=90,
                style="arc",
                outline=BORDER_COLOR,
                width=line_width,
            )
            # 右下角圆弧
            self.border_canvas.create_arc(
                width - padding - radius * 2,
                height - padding - radius * 2,
                width - padding,
                height - padding,
                start=270,
                extent=90,
                style="arc",
                outline=BORDER_COLOR,
                width=line_width,
            )
            # 左下角圆弧
            self.border_canvas.create_arc(
                padding,
                height - padding - radius * 2,
                padding + radius * 2,
                height - padding,
                start=180,
                extent=90,
                style="arc",
                outline=BORDER_COLOR,
                width=line_width,
            )

            # 绘制四条直线
            # 上边
            self.border_canvas.create_line(
                padding + radius,
                padding,
                width - padding - radius,
                padding,
                fill=BORDER_COLOR,
                width=line_width,
            )
            # 下边
            self.border_canvas.create_line(
                padding + radius,
                height - padding,
                width - padding - radius,
                height - padding,
                fill=BORDER_COLOR,
                width=line_width,
            )
            # 左边
            self.border_canvas.create_line(
                padding,
                padding + radius,
                padding,
                height - padding - radius,
                fill=BORDER_COLOR,
                width=line_width,
            )
            # 右边
            self.border_canvas.create_line(
                width - padding,
                padding + radius,
                width - padding,
                height - padding - radius,
                fill=BORDER_COLOR,
                width=line_width,
            )

        except Exception as e:
            print(f"绘制圆角边框时出错: {e}")

    def _set_window_transparent(self):
        """使用Windows API设置窗口圆角效果"""
        try:
            from ctypes import wintypes

            # 获取窗口句柄 - 使用多种方式尝试
            hwnd = None

            # 方式1: 使用winfo_id()
            try:
                window_id = self.root.winfo_id()
                if window_id:
                    hwnd = ctypes.windll.user32.GetParent(window_id)
                    if not hwnd:
                        hwnd = window_id
            except:
                pass

            # 方式2: 使用FindWindow查找
            if not hwnd:
                try:
                    class_name = (
                        self.root.wm_class()[0] if self.root.wm_class() else None
                    )
                    window_title = self.root.wm_title()
                    if class_name:
                        hwnd = ctypes.windll.user32.FindWindowW(
                            class_name, window_title
                        )
                except:
                    pass

            if not hwnd:
                print("无法获取窗口句柄")
                return

            # 获取窗口实际大小
            self.root.update_idletasks()
            width = self.root.winfo_width()
            height = self.root.winfo_height()

            if width <= 1 or height <= 1:
                # 如果窗口还未渲染，使用默认尺寸
                width = 500
                height = 60

            # 确保窗口有有效的大小
            if width < 100:
                width = 500
            if height < 30:
                height = 60

            radius = 20  # 圆角半径

            # 使用CreateRoundRectRgn创建圆角矩形区域
            gdi32 = ctypes.windll.gdi32
            CreateRoundRectRgn = gdi32.CreateRoundRectRgn
            CreateRoundRectRgn.argtypes = [
                wintypes.INT,
                wintypes.INT,
                wintypes.INT,
                wintypes.INT,
                wintypes.INT,
                wintypes.INT,
            ]
            CreateRoundRectRgn.restype = wintypes.HRGN

            hRgn = CreateRoundRectRgn(0, 0, width, height, radius * 2, radius * 2)

            if hRgn:
                # 设置窗口区域
                SetWindowRgn = ctypes.windll.user32.SetWindowRgn
                SetWindowRgn.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
                SetWindowRgn.restype = wintypes.INT

                result = SetWindowRgn(hwnd, hRgn, True)
                if result:
                    print(f"圆角设置成功: {width}x{height}, radius={radius}")
                else:
                    print("SetWindowRgn调用失败")
            else:
                print("CreateRoundRectRgn创建区域失败")

        except Exception as e:
            print(f"设置窗口圆角效果时出错: {e}")
            import traceback

            traceback.print_exc()

    # ---------------------
    # Selenium fetch thread
    # ---------------------
    def _parse_tips_from_raw(self, raw_content: str) -> List[str]:
        """
        Parse raw content between [StartNotice] and [EndNotice] into a list of tips.

        Strategy:
        - Normalize lines.
        - Insert newlines before enumerations like '1.' '2.' etc (if not already).
        - Split by blank lines or enumeration markers to produce individual tips.
        """
        if not raw_content:
            return []

        # Ensure enumerations start on new lines (e.g., "1. text")
        text = re.sub(r"\s*(\d+\.)\s*", r"\n\1 ", raw_content)

        # Normalize different newline styles and strip
        text = text.replace("\r\n", "\n").replace("\r", "\n").strip()

        # Split into candidate lines by newline; group lines that belong together
        lines = [ln.strip() for ln in text.split("\n") if ln.strip()]

        tips: List[str] = []
        current_tip_lines: List[str] = []

        # Heuristic: if line starts with enumerator like "1." treat as new tip
        for ln in lines:
            if re.match(r"^\d+\.\s+", ln):
                # finish previous
                if current_tip_lines:
                    tips.append(" ".join(current_tip_lines).strip())
                # start new
                current_tip_lines = [re.sub(r"^\d+\.\s*", "", ln).strip()]
            else:
                # Continue current tip
                current_tip_lines.append(ln)

        if current_tip_lines:
            tips.append(" ".join(current_tip_lines).strip())

        # Final cleanup: remove empty and very short fragments
        clean = [t for t in [t.strip() for t in tips] if t and len(t) > 1]
        return clean

    def selenium_loader_task(self):
        """
        Background thread that periodically fetches the page and updates self.tips.
        """
        edge_options = EdgeOptions()
        if os.path.exists("edge/msedgedriver.exe"):
            edge_service = EdgeService(executable_path="edge/msedgedriver.exe")
        else:
            edge_service = EdgeService()
        edge_options.add_argument("--headless")
        edge_options.add_argument("--disable-gpu")

        driver = None
        try:
            driver = webdriver.Edge(options=edge_options, service=edge_service)
            driver.implicitly_wait(10)

            while self.running:
                try:
                    # Update temporary UI state
                    self.root.after(
                        0, lambda: self.tip_label.config(text="正在抓取公告...")
                    )
                    driver.get(CLOUD_FILE_SCR)
                    # 等待页面加载
                    time.sleep(8)
                    # 关闭可能的弹窗（选择性）
                    try:
                        driver.find_element(
                            By.CSS_SELECTOR, "button.is-icon .kd-icon-symbol_cross_two"
                        ).click()
                        time.sleep(0.5)
                    except Exception:
                        pass

                    self.root.after(
                        0, lambda: self.tip_label.config(text="抓取并解析中...")
                    )
                    slow_scroll(driver)

                    # 1. 抓取页面上所有的 SVG text 标签（此前实现）
                    text_elements = driver.find_elements(By.TAG_NAME, "text")
                    all_text = "".join([el.text for el in text_elements])

                    # 2. 使用正则提取 [StartNotice] 和 [EndNotice] 之间的内容
                    pattern = r"\[StartNotice\](.*?)\[EndNotice\]"
                    match = re.search(pattern, all_text, re.S)

                    if match:
                        raw_content = match.group(1)
                        parsed = self._parse_tips_from_raw(raw_content)

                        if parsed:
                            # Replace tips atomically and clear any error state
                            with self.tips_lock:
                                self.tips = parsed
                                self.current_index = 0
                                self.tip_shown_at = time.time()
                                self.error_message = (
                                    None  # Clear error state on success
                                )
                                self.error_display_until = 0
                            # Optional: immediately update UI to first tip
                            self._refresh_ui_now()
                        else:
                            # No parsed tips
                            with self.tips_lock:
                                self.tips = []
                                self.error_message = "未检测到有效公告内容"
                                self.error_display_until = (
                                    time.time() + self.error_display_duration
                                )
                    else:
                        with self.tips_lock:
                            self.tips = []
                            self.error_message = (
                                "未检测到公告标记 [StartNotice]/[EndNotice]"
                            )
                            self.error_display_until = (
                                time.time() + self.error_display_duration
                            )

                except Exception as e:
                    # Keep UI informed - set error state instead of directly updating label
                    with self.tips_lock:
                        self.error_message = f"抓取失败: {str(e)[:80]}"
                        self.error_display_until = (
                            time.time() + self.error_display_duration
                        )

                # 等待下次抓取（线程睡眠）
                # 使用循环睡眠检测运行状态以便快速退出
                sleep_left = self.fetch_interval_seconds
                while sleep_left > 0 and self.running:
                    time.sleep(1)
                    sleep_left -= 1

        except Exception as e:
            error_msg = f"浏览器启动失败: {e}"
            with self.tips_lock:
                self.error_message = error_msg
                self.error_display_until = time.time() + self.error_display_duration
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass

    # ---------------------
    # UI 更新逻辑（每秒）
    # ---------------------
    def _schedule_ui_update(self):
        """
        Schedule the next UI update in 1 second.
        """
        if not self.running:
            return
        self._update_display_and_countdown()
        # schedule again in 1 second
        self.root.after(1000, self._schedule_ui_update)

    def _refresh_ui_now(self):
        """
        Force immediate UI refresh from the main thread.
        """
        self.root.after(0, self._update_display_and_countdown)

    def _update_display_and_countdown(self):
        """
        Update the displayed tip and countdown label. This runs in the main thread (tkinter).
        """
        now = time.time()
        with self.tips_lock:
            tips_copy = list(self.tips)
            error_message = self.error_message
            error_display_until = self.error_display_until

        # Check if we should display an error message
        if error_message and now < error_display_until:
            # Display error message
            display_text = error_message
            remaining_error_time = max(0, int(error_display_until - now))
            countdown_text = f"{remaining_error_time}s"
        elif tips_copy:
            # Normal tip display logic
            # Ensure current_index is within range
            if self.current_index >= len(tips_copy):
                self.current_index = 0
                self.tip_shown_at = now

            elapsed = now - self.tip_shown_at
            # If elapsed exceeds rotation_seconds, advance index
            if elapsed >= self.rotation_seconds:
                advance_by = int(elapsed // self.rotation_seconds)
                self.current_index = (self.current_index + advance_by) % len(tips_copy)
                # Recompute shown_at to align with the rotation period
                self.tip_shown_at += advance_by * self.rotation_seconds
                elapsed = now - self.tip_shown_at

            remaining = max(0, int(self.rotation_seconds - elapsed))

            # Build display text: just the tip content (single line)
            display_text = tips_copy[self.current_index]
            countdown_text = f"{remaining}s"
        else:
            # No tips available: show placeholder and keep a short refresh cadence
            display_text = "未检测到公告内容或标记，正在等待抓取..."
            countdown_text = "--"

        # Update labels (only if text changed to reduce flicker)
        # Use .after(0, ...) is not needed because we are already on main thread, but safe to ensure thread-safety.
        self.tip_label.config(text=display_text)
        self.countdown_label.config(text=countdown_text)

    # ---------------------
    # 关闭与退出
    # ---------------------
    def on_closing(self, event=None):
        self.running = False
        # Give background threads a moment to stop gracefully
        # (They are daemon threads, but we attempt a polite stop)
        try:
            # destroy root in the main thread
            self.root.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    root = tk.Tk()
    app = FloatingTipsApp(root)
    root.mainloop()
