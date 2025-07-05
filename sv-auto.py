import sys
import os
import logging
import time
import json
import cv2
import numpy as np
import threading
import queue
import datetime
import random
import io
import ctypes
from ctypes import wintypes

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QTextEdit, QFrame, QSizePolicy, QGridLayout
)
from PyQt5.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt5.QtGui import QFont, QPixmap, QPalette, QBrush, QColor, QFontDatabase, QIcon

# 背景图片路径 - 请修改为您的实际路径
BACKGROUND_IMAGE = os.path.join("Image", "ui背景.jpg")  # 背景图片路径

# 随从的位置坐标（720P分辨率）
follower_positions = [
    (310, 398),
    (389, 398),
    (468, 398),
    (547, 398),
    (626, 398),
    (705, 398),
    (784, 398),
    (863, 398),
    (942, 398),
]
reversed_follower_positions = follower_positions[::-1]

DEFAULT_CONFIG = {
    "emulator_port": 16384,
    "scan_interval": 2,
    "evolution_threshold": 0.85,
    "extra_templates_dir": "extra_templates"
}

def load_config():
    """加载配置文件"""
    config_file = "config.json"

    # 如果配置文件不存在，创建默认配置
    if not os.path.exists(config_file):
        logger.info(f"创建默认配置文件: {config_file}")
        with open(config_file, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_CONFIG, f, indent=2)
        return DEFAULT_CONFIG.copy()

    try:
        with open(config_file, 'r', encoding='utf-8') as f:
            config = json.load(f)
            # 合并默认配置和用户配置
            for key in DEFAULT_CONFIG:
                if key not in config:
                    config[key] = DEFAULT_CONFIG[key]
            return config
    except Exception as e:
        logger.error(f"加载配置文件失败: {str(e)}，使用默认配置")
        return DEFAULT_CONFIG.copy()

# 配置日志
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# 创建文件日志处理器
file_handler = logging.FileHandler("script_log.log")
file_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)

# 创建控制台日志处理器
if sys.stdout is not None:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
if sys.stderr is not None:
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')
console_handler = logging.StreamHandler(sys.stdout)
console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)

# 添加处理器
logger.addHandler(file_handler)
logger.addHandler(console_handler)

# 全局状态变量
script_running = True
script_paused = False

# 回合统计相关变量
current_round_count = 1  # 当前对战的回合数
match_start_time = None  # 当前对战开始时间
match_history = []  # 存储所有对战记录
round_stats_file = "round_statistics.json"  # 统计数据保存文件
current_run_matches = 0  # 本次运行的对战次数
current_run_start_time = None  # 本次脚本启动时间

# 创建Tkinter消息队列
notification_queue = queue.Queue()

# 模板目录
TEMPLATES_DIR = "templates"

# 进化按钮模板（全局）
evolution_template = None
super_evolution_template = None

# 命令队列
command_queue = queue.Queue()

# 全局设备对象
device = None

# 随从基准背景色
base_colors = None

# 是否在对战中
in_match = False

# UI日志信号
ui_log_signal = pyqtSignal(str)

# ================== 核心功能函数 ==================
def take_screenshot():
    """获取设备截图 - 使用全局设备对象"""
    global device  # 使用全局设备对象

    if device is None:
        logger.error("设备未初始化")
        return None

    try:
        return device.screenshot()
    except Exception as e:
        logger.error(f"截图失败: {str(e)}")
        return None

def load_template(templates_dir, filename):
    """加载模板图像并返回灰度图"""
    path = os.path.join(templates_dir, filename)
    if not os.path.exists(path):
        logger.error(f"模板文件不存在: {path}")
        return None

    template = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if template is None:
        logger.error(f"无法加载模板: {path}")
    return template

def create_template_info(template, name, threshold=0.85):
    """创建模板信息字典"""
    if template is None:
        return None

    h, w = template.shape
    return {
        'name': name,
        'template': template,
        'w': w,
        'h': h,
        'threshold': threshold
    }

def match_template(gray_image, template_info):
    """执行模板匹配并返回结果"""
    if not template_info:
        return None, 0

    result = cv2.matchTemplate(gray_image, template_info['template'], cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    return max_loc, max_val

def scan_shield_targets():
    """扫描指定矩形区域(247,151)-(1028,312)内的护盾随从（彩色匹配），最多返回一个置信度最高的目标"""
    shield_dir = "shield"
    shield_targets = []  # 存储所有检测到的护盾目标及其置信度

    # 确保shield目录存在
    if not os.path.exists(shield_dir) or not os.path.isdir(shield_dir):
        return []

    screenshot_shield = take_screenshot()
    if screenshot_shield is None:
        return []

    # 将PIL截图转为OpenCV格式
    screenshot_np = np.array(screenshot_shield)
    screenshot_cv = cv2.cvtColor(screenshot_np, cv2.COLOR_RGB2BGR)  # RGB转BGR格式

    # 定义扫描区域 (247,151) 到 (1028,312)
    x1, y1 = 247, 151
    x2, y2 = 1028, 312
    width = x2 - x1
    height = y2 - y1

    # 只扫描指定矩形区域
    roi = screenshot_cv[y1:y2, x1:x2]

    # 加载所有护盾模板（彩色）
    shield_templates = []
    for filename in os.listdir(shield_dir):
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp')):
            path = os.path.join(shield_dir, filename)
            template = cv2.imread(path)  # 以彩色模式读取模板
            if template is not None:
                shield_templates.append(template)

    # 扫描匹配护盾模板并记录置信度
    for template in shield_templates:
        h, w = template.shape[:2]  # 获取彩色模板的高度和宽度

        # 执行彩色模板匹配
        result = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
        threshold = 0.75  # 匹配阈值

        # 获取所有匹配位置及其置信度
        loc = np.where(result >= threshold)
        for pt in zip(*loc[::-1]):  # 遍历所有匹配位置
            confidence = result[pt[1], pt[0]]  # 获取当前匹配位置的置信度

            # 将相对坐标转换为绝对坐标
            abs_x = int(pt[0] + x1 + w // 2)
            abs_y = int(pt[1] + y1 + h // 2)

            # 添加到目标列表（包含坐标和置信度）
            shield_targets.append({
                'x': abs_x,
                'y': abs_y,
                'confidence': confidence
            })

    # 按置信度从高到低排序
    shield_targets.sort(key=lambda t: t['confidence'], reverse=True)

    # 只取置信度最高的一个目标
    if len(shield_targets) > 1:
        shield_targets = shield_targets[:1]
    # 提取坐标列表
    coordinates = [(target['x'], target['y']) for target in shield_targets]

    return coordinates

def save_round_statistics():
    """保存回合统计数据到文件"""
    try:
        with open(round_stats_file, 'w', encoding='utf-8') as f:
            json.dump(match_history, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"保存统计数据失败: {str(e)}")

def load_round_statistics():
    """从文件加载回合统计数据"""
    global match_history

    if not os.path.exists(round_stats_file):
        return

    try:
        with open(round_stats_file, 'r', encoding='utf-8') as f:
            match_history = json.load(f)
    except Exception as e:
        logger.error(f"加载统计数据失败: {str(e)}")

def curved_drag(u2_device, start_x, start_y, end_x, end_y, duration, steps=8):
    """
    模拟曲线拖拽操作
    :param u2_device: 设备对象
    :param start_x: 起始点x坐标
    :param start_y: 起始点y坐标
    :param end_x: 结束点x坐标
    :param end_y: 结束点y坐标
    :param duration: 拖拽持续时间（秒）
    :param steps: 拖拽路径中的步骤数
    """
    u2_device.touch.down(start_x, start_y)

    for i in range(1, steps + 1):
        t = i / steps
        # 模拟抛物线
        xi = start_x + (end_x - start_x) * t
        yi = start_y + (end_y - start_y) * (t ** 0.85)

        u2_device.touch.move(int(xi), int(yi))
        time.sleep(duration / steps)

    u2_device.touch.up(end_x, end_y)

def load_evolution_template():
    """加载进化按钮模板"""
    global evolution_template
    if evolution_template is None:
        # 使用全局定义的 TEMPLATES_DIR
        template_img = load_template(TEMPLATES_DIR, 'evolution.png')
        if template_img is None:
            logger.error("无法加载进化按钮模板")
            return None

        evolution_template = create_template_info(
            template_img,
            "进化按钮",
            threshold=0.85
        )
    return evolution_template

def load_super_evolution_template():
    """加载超进化按钮模板"""
    global super_evolution_template
    if super_evolution_template is None:
        # 使用全局定义的 TEMPLATES_DIR
        template_img = load_template(TEMPLATES_DIR, 'super_evolution.png')
        if template_img is None:
            logger.error("无法加载超进化按钮模板")
            return None

        super_evolution_template = create_template_info(
            template_img,
            "超进化按钮",
            threshold=0.85
        )
    return super_evolution_template

def detect_evolution_button(gray_screenshot):
    """检测进化按钮是否出现"""
    evolution_info = load_evolution_template()
    if not evolution_info:
        return None, 0

    max_loc, max_val = match_template(gray_screenshot, evolution_info)
    return max_loc, max_val

def detect_super_evolution_button(gray_screenshot):
    """检测超进化按钮是否出现"""
    evolution_info = load_super_evolution_template()
    if not evolution_info:
        return None, 0

    max_loc, max_val = match_template(gray_screenshot, evolution_info)
    return max_loc, max_val

def perform_follower_attacks(u2_device, screenshot, base_colors):
    """检测并执行随从攻击（优先攻击护盾目标）（从右往左尝试攻击）"""
    # 对面主人位置（默认攻击目标）
    default_target = (646, 64)
    need_scan_shield = True

    # 颜色检测不够准确，先固定使用旧逻辑
    if False:
        for i, pos in enumerate(reversed_follower_positions):
            x, y = pos
            attackDelay = 0.02
            # 获取当前位置的色彩
            current_color1 = screenshot.getpixel((x, y))
            # 获取Y轴向下20个像素点的色彩
            current_color2 = screenshot.getpixel((x, y + 20))

            # 检查基准背景色是否存在
            if i >= len(base_colors):
                continue
            # 获取基准背景色（两个点）
            base_color1, base_color2 = base_colors[i]

            # 计算当前位置的色彩差异（RGB欧氏距离）
            r_diff1 = abs(current_color1[0] - base_color1[0])
            g_diff1 = abs(current_color1[1] - base_color1[1])
            b_diff1 = abs(current_color1[2] - base_color1[2])
            color_diff1 = (r_diff1 + g_diff1 + b_diff1) / 3

            # 计算下方20像素点的色彩差异
            r_diff2 = abs(current_color2[0] - base_color2[0])
            g_diff2 = abs(current_color2[1] - base_color2[1])
            b_diff2 = abs(current_color2[2] - base_color2[2])
            color_diff2 = (r_diff2 + g_diff2 + b_diff2) / 3

            # 如果两个位置中有一个色彩差异超过阈值，则认为有随从
            if color_diff1 > 25 or color_diff2 > 25:
                # 扫描护盾目标
                if need_scan_shield:
                    shield_targets = scan_shield_targets()

                if shield_targets:
                    logger.info(f"检测到护盾目标，优先攻击")
                    target_x, target_y = shield_targets[0]
                    attackDelay = 1
                else:
                    need_scan_shield = False
                    logger.info(f"未检测到护盾，直接攻击主战者")
                    target_x, target_y = default_target

                # 确保坐标是整数
                target_x = int(target_x)
                target_y = int(target_y)
                curved_drag(u2_device, x, y, target_x, target_y, 0.04, 4)
                time.sleep(attackDelay)
    else:
        # 后备方案：执行简易坐标
        # 固定攻击
        # logger.warning("未找到基准背景色，执行默认攻击")
        for i, pos in enumerate(reversed_follower_positions):
            x, y = pos
            attackDelay = 0.02

            if need_scan_shield:
                logger.info(f"开始检测护盾")
                shield_targets = scan_shield_targets()
            else:
                logger.info(f"跳过护盾检测")

            if shield_targets:
                logger.info(f"检测到护盾目标，优先攻击")
                target_x, target_y = shield_targets[0]
                attackDelay = 2.1
            else:
                need_scan_shield = False
                logger.info(f"未检测到护盾，直接攻击主战者")
                target_x, target_y = default_target
            # 确保坐标是整数
            target_x = int(target_x)
            target_y = int(target_y)
            curved_drag(u2_device, x, y, target_x, target_y, 0.04, 4)
            time.sleep(attackDelay)

    # 避免攻击被卡掉
    time.sleep(0.4)

def perform_evolution_actions(u2_device, screenshot, base_colors):
    """
    执行进化/超进化操作（带检测）- 复用颜色检测逻辑
    :param u2_device: u2设备对象
    :param screenshot: 当前截图
    :param base_colors: 基准背景色列表
    :param is_super: 是否为超进化操作
    :return: 是否检测到进化按钮
    """
    global device

    evolution_detected = False

    # 基准背景色检测并不准确，先固定使用旧逻辑
    return perform_evolution_actions_fallback(u2_device)

    # 如果无法获取截图，回退到旧逻辑
    if screenshot is None:
        logger.warning("无法获取截图，使用旧逻辑进行进化操作")
        return perform_evolution_actions_fallback(u2_device)

    # 如果没有基准背景色，使用旧逻辑
    if not base_colors:
        logger.warning("没有基准背景色，使用旧逻辑进行进化操作")
        return perform_evolution_actions_fallback(u2_device)

    # 有护盾时从右侧开始进化，没有护盾时从左侧开始进化
    exist_shield = scan_shield_targets()
    if exist_shield:
        evolve_positions = reversed_follower_positions
    else:
        evolve_positions = follower_positions

    # 遍历每个随从位置
    for i, pos in enumerate(evolve_positions):
        x, y = pos

        # 检查基准背景色是否存在
        if i >= len(base_colors):
            continue

        # 获取当前位置的色彩
        current_color1 = screenshot.getpixel((x, y))
        # 获取Y轴向下20个像素点的色彩
        current_color2 = screenshot.getpixel((x, y + 20))
        base_color1, base_color2 = base_colors[i]

        # 计算当前位置的色彩差异
        r_diff1 = abs(current_color1[0] - base_color1[0])
        g_diff1 = abs(current_color1[1] - base_color1[1])
        b_diff1 = abs(current_color1[2] - base_color1[2])
        color_diff1 = (r_diff1 + g_diff1 + b_diff1) / 3

        # 计算下方20像素点的色彩差异
        r_diff2 = abs(current_color2[0] - base_color2[0])
        g_diff2 = abs(current_color2[1] - base_color2[1])
        b_diff2 = abs(current_color2[2] - base_color2[2])
        color_diff2 = (r_diff2 + g_diff2 + b_diff2) / 3

        # 如果两个位置中有一个色彩差异超过阈值，则认为有随从
        if color_diff1 > 25 or color_diff2 > 25:
            # 点击该位置
            u2_device.click(x, y)
            time.sleep(0.1)  # 等待进化按钮出现

            # 获取新截图检测进化按钮
            new_screenshot = take_screenshot()
            if new_screenshot is None:
                logger.warning(f"位置 {i} 无法获取截图，跳过检测")
                continue

            # 转换为OpenCV格式
            new_screenshot_np = np.array(new_screenshot)
            new_screenshot_cv = cv2.cvtColor(new_screenshot_np, cv2.COLOR_RGB2BGR)
            gray_screenshot = cv2.cvtColor(new_screenshot_cv, cv2.COLOR_BGR2GRAY)

            # 同时检查两个检测函数
            max_loc, max_val = detect_super_evolution_button(gray_screenshot)
            if max_val >= 0.825:
                template_info = load_super_evolution_template()
                if template_info:
                    center_x = max_loc[0] + template_info['w'] // 2
                    center_y = max_loc[1] + template_info['h'] // 2
                    u2_device.click(center_x, center_y)
                    logger.info(f"检测到超进化按钮并点击 ")
                    evolution_detected = True
                    break

            max_loc1, max_val1 = detect_evolution_button(gray_screenshot)
            if max_val1 >= 0.90:
                template_info = load_evolution_template()
                if template_info:
                    center_x = max_loc1[0] + template_info['w'] // 2
                    center_y = max_loc1[1] + template_info['h'] // 2
                    u2_device.click(center_x, center_y)
                    logger.info(f"检测到进化按钮并点击 ")
                    evolution_detected = True
                    break

    return evolution_detected

def perform_evolution_actions_fallback(u2_device, is_super=False):
    """
    执行进化/超进化操作的旧逻辑（遍历所有位置）
    :param device: 设备对象
    """
    evolution_detected = False
    logger_word = False

    # 有护盾时从右侧开始进化，没有护盾时从左侧开始进化
    exist_shield = scan_shield_targets()
    if exist_shield:
        evolve_positions = reversed_follower_positions
    else:
        evolve_positions = follower_positions

    # 遍历所有位置
    for i, pos in enumerate(evolve_positions):
        follower_x, follower_y = pos
        u2_device.click(follower_x, follower_y)
        time.sleep(0.5)

        # 尝试检测进化按钮
        screenshot = take_screenshot()
        if screenshot is None:
            logger.warning(f"位置 {i} 无法获取截图，跳过检测")
            time.sleep(0.1)
            continue

        # 转换为OpenCV格式
        screenshot_np = np.array(screenshot)
        screenshot_cv = cv2.cvtColor(screenshot_np, cv2.COLOR_RGB2BGR)
        gray_screenshot = cv2.cvtColor(screenshot_cv, cv2.COLOR_BGR2GRAY)

        max_loc, max_val = detect_super_evolution_button(gray_screenshot)
        if max_val >= 0.825:
            template_info = load_super_evolution_template()
            center_x = max_loc[0] + template_info['w'] // 2
            center_y = max_loc[1] + template_info['h'] // 2
            u2_device.click(center_x, center_y)
            logger.info(f"检测到超进化按钮并点击")
            evolution_detected = True
            break

        max_loc1, max_val1 = detect_evolution_button(gray_screenshot)
        if max_val1 >= 0.90:  # 检测阈值
            template_info = load_evolution_template()
            center_x = max_loc1[0] + template_info['w'] // 2
            center_y = max_loc1[1] + template_info['h'] // 2
            u2_device.click(center_x, center_y)
            if not logger_word:
                logger.info(f"检测到进化按钮并点击")
                logger_word = True
            evolution_detected = True
            break

        time.sleep(0.1)

    return evolution_detected

def perform_full_actions(u2_device, round_count, base_colors):
    """720P分辨率下的出牌攻击操作"""
    # 不管是不是后手先点能量点的位置再说
    u2_device.click(1173, 500)
    time.sleep(0.1)

    # 展牌
    u2_device.click(1049, 646)
    time.sleep(0.25)

    # 出牌拖拽（大于6回合时从中心向两侧）
    start_y = 672 + random.randint(-2, 2)
    end_y = 400 + random.randint(-2, 2)
    duration = 0.05
    if round_count >= 6:
        drag_points_x = [684, 600, 700, 551, 830, 501, 900, 405, 959]
    else:
        drag_points_x = [405, 501, 551, 600, 684, 700, 830, 900, 959]
    for x in drag_points_x:
        curved_drag(u2_device, x + random.randint(-2, 2), start_y, x + random.randint(-2, 2), end_y, duration, 6)
        time.sleep(0.05)
    time.sleep(0.1)

    # 执行随从攻击（使用统一函数
    screenshot = take_screenshot()
    if screenshot:
        perform_follower_attacks(
            u2_device,
            screenshot,
            base_colors,
        )
    else:
        logger.error("无法获取截图，跳过攻击操作")
    time.sleep(0.1)

def perform_fullPlus_actions(u2_device, round_count, base_colors):
    """720P分辨率下执行进化/超进化与攻击操作"""
    # 不管是不是后手先点能力点的位置再说
    u2_device.click(1173, 500)
    time.sleep(0.1)

    # 展牌
    u2_device.click(1049, 646)
    time.sleep(0.25)

    # 出牌拖拽（大于6回合时从中心向两侧）
    start_y = 672 + random.randint(-2, 2)
    end_y = 400 + random.randint(-2, 2)
    duration = 0.05
    if round_count >= 6:
        drag_points_x = [684, 600, 700, 551, 830, 501, 900, 405, 959]
    else:
        drag_points_x = [405, 501, 551, 600, 684, 700, 830, 900, 959]

    for x in drag_points_x:
        curved_drag(u2_device, x + random.randint(-2, 2), start_y, x + random.randint(-2, 2), end_y, duration, 6)
        time.sleep(0.05)
    time.sleep(0.1)

    # 获取当前截图
    screenshot = take_screenshot()
    # 执行进化操作
    if screenshot is not None:
        evolved = perform_evolution_actions(
            u2_device,
            screenshot,
            base_colors,
        )
        if evolved:
            # 等待最终进化/超进化动画完成
            time.sleep(6.5)

    # 点击空白处关闭面板
    u2_device.click(1026 + random.randint(-2, 2), 178 + random.randint(-2, 2))
    time.sleep(0.1)

    # 执行随从攻击
    screenshot = take_screenshot()
    if screenshot:
        perform_follower_attacks(
            u2_device,
            screenshot,
            base_colors,
        )
    else:
        logger.error("无法获取截图，遍历攻击操作")
    time.sleep(0.1)

def scan_self_shield_targets():
    """扫描己方随从区域的护盾目标（彩色匹配），返回置信度最高的目标"""
    shield_dir = "shield"
    shield_targets = []  # 存储所有检测到的护盾目标及其置信度

    # 确保shield目录存在
    if not os.path.exists(shield_dir) or not os.path.isdir(shield_dir):
        return []

    screenshot_shield = take_screenshot()
    if screenshot_shield is None:
        return []

    # 将PIL截图转为OpenCV格式
    screenshot_np = np.array(screenshot_shield)
    screenshot_cv = cv2.cvtColor(screenshot_np, cv2.COLOR_RGB2BGR)  # RGB转BGR格式

    # 定义扫描区域 (254, 320) 到 (1063, 484) - 己方随从区域
    x1, y1 = 254, 320
    x2, y2 = 1063, 484
    width = x2 - x1
    height = y2 - y1

    # 只扫描指定矩形区域
    roi = screenshot_cv[y1:y2, x1:x2]

    # 加载所有护盾模板（彩色）
    shield_templates = []
    for filename in os.listdir(shield_dir):
        if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.bmp')):
            path = os.path.join(shield_dir, filename)
            template = cv2.imread(path)  # 以彩色模式读取模板
            if template is not None:
                shield_templates.append(template)

    # 扫描匹配护盾模板并记录置信度
    for template in shield_templates:
        h, w = template.shape[:2]  # 获取彩色模板的高度和宽度

        # 执行彩色模板匹配
        result = cv2.matchTemplate(roi, template, cv2.TM_CCOEFF_NORMED)
        threshold = 0.75  # 匹配阈值

        # 获取所有匹配位置及其置信度
        loc = np.where(result >= threshold)
        for pt in zip(*loc[::-1]):  # 遍历所有匹配位置
            confidence = result[pt[1], pt[0]]  # 获取当前匹配位置的置信度

            # 将相对坐标转换为绝对坐标
            abs_x = int(pt[0] + x1 + w // 2)
            abs_y = int(pt[1] + y1 + h // 2)

            # 添加到目标列表（包含坐标和置信度）
            shield_targets.append({
                'x': abs_x,
                'y': abs_y,
                'confidence': confidence
            })

    # 按置信度从高到低排序
    shield_targets.sort(key=lambda t: t['confidence'], reverse=True)

    # 返回所有检测到的目标
    return shield_targets

def enable_ansi_support():
    if sys.platform != "win32":
        return  # 非Windows系统无需处理

    # 调用Windows API启用虚拟终端处理
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    handle = kernel32.GetStdHandle(wintypes.DWORD(-11))  # STD_OUTPUT_HANDLE (-11)
    mode = wintypes.DWORD()
    if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        return
    # 启用 ENABLE_VIRTUAL_TERMINAL_PROCESSING (0x0004)
    if not kernel32.SetConsoleMode(handle, mode.value | 0x0004):
        return

# ================== UI 相关类 ==================
class UILogHandler(logging.Handler):
    def __init__(self, log_signal):
        super().__init__()
        self.log_signal = log_signal

    def emit(self, record):
        log_entry = self.format(record)
        self.log_signal.emit(log_entry)

class ScriptThread(QThread):
    log_signal = pyqtSignal(str)
    status_signal = pyqtSignal(str)
    connected_signal = pyqtSignal(bool)
    stats_signal = pyqtSignal(dict)

    def __init__(self, config, parent=None):
        super().__init__(parent)
        self.config = config
        self.running = True
        self.paused = False
        self.start_time = 0
        self.battle_count = 0
        self.turn_count = 0
        self.current_turn = 0
        self.adb_port = config["emulator_port"]
        self.scan_interval = config["scan_interval"]

        # 初始化设备对象
        self.u2_device = None
        self.adb_device = None

        # 模板字典
        self.templates = {}

        # 设置日志处理器
        ui_handler = UILogHandler(self.log_signal)
        ui_handler.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
        logger.addHandler(ui_handler)

    def run(self):
        global script_running, script_paused, device, current_round_count
        global match_start_time, current_run_matches, current_run_start_time
        global in_match, evolution_template, super_evolution_template, base_colors

        self.start_time = time.time()
        self.status_signal.emit("运行中")
        self.log_signal.emit("===== 脚本开始运行 =====")

        # 加载配置
        EMULATOR_PORT = self.config["emulator_port"]
        SCAN_INTERVAL = self.config["scan_interval"]

        # 初始化设备对象
        device = None

        # 初始化进化模板
        evolution_template = None
        super_evolution_template = None

        # 记录脚本启动时间
        current_run_start_time = datetime.datetime.now()
        current_run_matches = 0
        in_match = False  # 是否在对战中

        # 加载历史统计数据
        load_round_statistics()

        # 1. 加载所有模板
        self.log_signal.emit("正在加载模板...")

        self.templates = {
            'dailyCard': create_template_info(load_template(TEMPLATES_DIR, 'dailyCard.png'), "每日卡包"),
            'missionCompleted': create_template_info(load_template(TEMPLATES_DIR, 'missionCompleted.png'), "任务完成"),
            'backTitle': create_template_info(load_template(TEMPLATES_DIR, 'backTitle.png'), "返回标题"),
            'error_retry': create_template_info(load_template(TEMPLATES_DIR, 'error_retry.png'), "重试"),
            'Ok': create_template_info(load_template(TEMPLATES_DIR, 'Ok.png'), "好的"),
            'decision': create_template_info(load_template(TEMPLATES_DIR, 'decision.png'), "决定"),
            'end_round': create_template_info(load_template(TEMPLATES_DIR, 'end_round.png'), "结束回合"),
            'enemy_round': create_template_info(load_template(TEMPLATES_DIR, 'enemy_round.png'), "敌方回合"),
            'end': create_template_info(load_template(TEMPLATES_DIR, 'end.png'), "结束"),
            'war': create_template_info(load_template(TEMPLATES_DIR, 'war.png'), "决斗"),
            'mainPage': create_template_info(load_template(TEMPLATES_DIR, 'mainPage.png'), "游戏主页面"),
            'MuMuPage': create_template_info(load_template(TEMPLATES_DIR, 'MuMuPage.png'), "MuMu主页面"),
            'LoginPage': create_template_info(load_template(TEMPLATES_DIR, 'LoginPage.png'), "排队主界面"),
            'enterGame': create_template_info(load_template(TEMPLATES_DIR, 'enterGame.png'), "排队进入"),
            'yes': create_template_info(load_template(TEMPLATES_DIR, 'Yes.png'), "继续中断的对战"),
            'close1': create_template_info(load_template(TEMPLATES_DIR, 'close1.png'), "关闭卡组预览/编辑"),
            'close2': create_template_info(load_template(TEMPLATES_DIR, 'close2.png'), "关闭卡组预览/编辑"),
            'backMain': create_template_info(load_template(TEMPLATES_DIR, 'backMain.png'), "返回主页面"),
            'rankUp': create_template_info(load_template(TEMPLATES_DIR, 'rankUp.png'), "阶位提升"),
            'groupUp': create_template_info(load_template(TEMPLATES_DIR, 'groupUp.png'), "分组升级"),
            'rank': create_template_info(load_template(TEMPLATES_DIR, 'rank.png'), "阶级积分"),
        }

        extra_dir = self.config.get("extra_templates_dir", "")
        if extra_dir and os.path.isdir(extra_dir):
            self.log_signal.emit(f"开始加载额外模板目录: {extra_dir}")

            # 支持的图片扩展名
            valid_extensions = ['.png', '.jpg', '.jpeg', '.bmp']

            for filename in os.listdir(extra_dir):
                filepath = os.path.join(extra_dir, filename)

                # 检查是否是图片文件
                if os.path.isfile(filepath) and os.path.splitext(filename)[1].lower() in valid_extensions:
                    template_name = os.path.splitext(filename)[0]  # 使用文件名作为模板名称

                    # 加载模板
                    template_img = load_template(extra_dir, filename)
                    if template_img is None:
                        self.log_signal.emit(f"无法加载额外模板: {filename}")
                        continue

                    # 创建模板信息（使用全局阈值）
                    template_info = create_template_info(
                        template_img,
                        f"额外模板-{template_name}",
                        threshold=self.config["evolution_threshold"]
                    )

                    # 添加到模板字典（如果已存在则跳过）
                    if template_name not in self.templates:
                        self.templates[template_name] = template_info
                        self.log_signal.emit(f"已添加额外模板: {template_name} (来自: {filename})")

        self.log_signal.emit("模板加载完成")

        # 2. 连接设备
        self.log_signal.emit("正在连接设备...")
        try:
            import adbutils
            from adbutils import adb, AdbClient
            import uiautomator2 as u2

            # 创建 adb 客户端
            client = AdbClient(host="127.0.0.1")

            # 目标设备序列号
            target_serial = f"127.0.0.1:{EMULATOR_PORT}"

            # 检查是否已连接目标模拟器
            devices_list = client.device_list()
            emulator_connected = any(d.serial == target_serial for d in devices_list)

            if not emulator_connected:
                self.log_signal.emit(f"尝试自动连接模拟器({target_serial})...")
                try:
                    adb.connect(target_serial)
                    time.sleep(2)  # 等待连接稳定
                    devices_list = client.device_list()
                    emulator_connected = any(d.serial == target_serial for d in devices_list)
                    if emulator_connected:
                        self.log_signal.emit(f"模拟器连接成功: {target_serial}")
                    else:
                        self.log_signal.emit(f"连接模拟器失败: 未出现在 device_list 中")
                except Exception as conn_err:
                    self.log_signal.emit(f"adbutils 连接失败: {conn_err}")

            # 获取设备列表（再次获取以确保最新）
            devices_list = client.device_list()
            if not devices_list:
                raise RuntimeError("未找到连接的设备，请确保模拟器已启动")

            # 查找目标设备
            target_device = None
            for d in devices_list:
                if d.serial == target_serial:
                    target_device = d
                    break

            if not target_device:
                target_device = devices_list[0]
                self.log_signal.emit(f"未找到配置的模拟器({target_serial})，使用第一个设备: {target_device.serial}")

            # 获取 adbutils 设备对象
            device = adb.device(serial=target_device.serial)
            self.log_signal.emit(f"已连接设备: {target_device.serial}")

            # 获取 uiautomator2 设备对象
            u2_device = u2.connect(target_device.serial)
            self.u2_device = u2_device
            self.adb_device = device
            self.log_signal.emit("设备连接成功")
        except Exception as e:
            self.log_signal.emit(f"设备连接失败: {str(e)}")
            self.status_signal.emit("连接失败")
            return

        # 3. 检测脚本启动时是否已经在对战中
        self.log_signal.emit("检测当前游戏状态...")
        init_screenshot = take_screenshot()
        if init_screenshot is not None:
            # 转换为OpenCV格式
            init_screenshot_np = np.array(init_screenshot)
            init_screenshot_cv = cv2.cvtColor(init_screenshot_np, cv2.COLOR_RGB2BGR)
            gray_init_screenshot = cv2.cvtColor(init_screenshot_cv, cv2.COLOR_BGR2GRAY)

            # 检测是否已经在游戏中
            end_round_info = self.templates['end_round']
            enemy_round_info = self.templates['enemy_round']
            decision_info = self.templates['decision']

            # 检测我方回合
            if end_round_info:
                max_loc, max_val = match_template(gray_init_screenshot, end_round_info)
                if max_val >= end_round_info['threshold']:
                    in_match = True
                    match_start_time = time.time()
                    current_round_count = 2
                    self.log_signal.emit("脚本启动时检测到已处于我方回合，自动设置回合数为2")

            # 检测敌方回合
            if enemy_round_info:
                max_loc, max_val = match_template(gray_init_screenshot, enemy_round_info)
                if max_val >= enemy_round_info['threshold']:
                    in_match = True
                    match_start_time = time.time()
                    current_round_count = 2
                    self.log_signal.emit("脚本启动时检测到已处于敌方回合，自动设置回合数为2")

            # 检测换牌开场
            if decision_info:
                max_loc, max_val = match_template(gray_init_screenshot, decision_info)
                if max_val >= decision_info['threshold']:
                    in_match = True
                    match_start_time = time.time()
                    current_round_count = 1
                    self.log_signal.emit("脚本启动时检测到已处于换牌阶段，自动设置回合数为1")
        else:
            self.log_signal.emit("无法获取初始截图，跳过状态检测")

        last_detected_button = None
        base_colors = None

        # 5. 主循环
        self.log_signal.emit("脚本初始化完成，开始运行...")
        self.log_signal.emit("模拟器请调成1280x720分辨率")

        # 防倒卖声明
        red_start = "\033[91m"  # ANSI红色开始
        red_end = "\033[0m"     # ANSI颜色重置
        message = f"""
{red_start}
【提示】本脚本为免费开源项目，您无需付费即可获取。
若您通过付费渠道购买，可能已遭遇误导。
免费版本请加群: 967632615
警惕倒卖行为！
{red_end}
"""
        self.log_signal.emit(message.strip())

        needLogPause = True
        while self.running:
            start_time = time.time()

            # 检查脚本暂停状态
            if self.paused:
                if needLogPause:
                    # 记录暂停信息
                    self.log_signal.emit("脚本暂停中...")
                    needLogPause = False
                # 在暂停状态下每1秒检查一次
                time.sleep(1)
                continue

            # 获取截图
            needLogPause = True
            screenshot = take_screenshot()
            if screenshot is None:
                time.sleep(SCAN_INTERVAL)
                continue

            # 转换为OpenCV格式
            screenshot_np = np.array(screenshot)
            screenshot_cv = cv2.cvtColor(screenshot_np, cv2.COLOR_RGB2BGR)
            gray_screenshot = cv2.cvtColor(screenshot_cv, cv2.COLOR_BGR2GRAY)

            # 检查其他按钮
            button_detected = False
            for key, template_info in self.templates.items():  # 遍历所有模板（包括动态加载的）
                if not template_info:
                    continue

                max_loc, max_val = match_template(gray_screenshot, template_info)
                if max_val >= template_info['threshold']:
                    if key in ['enemy_round']:
                        continue

                    if key != last_detected_button:
                        if key == 'end_round' and in_match:
                            self.log_signal.emit(f"已发现'结束回合'按钮 (当前回合: {current_round_count})")

                    # 处理每日卡包
                    if key == 'dailyCard':
                        #点击固定位置跳过
                        self.log_signal.emit("检测到每日卡包，尝试跳过")
                        self.u2_device.click(717, 80)

                    # 处理对战开始/结束逻辑
                    if key == 'war':
                        # 检测到"决斗"按钮，表示新对战开始
                        if in_match:
                            # 如果已经在战斗中，先结束当前对战
                            self.end_current_match()
                            self.log_signal.emit("检测到新对战开始，结束上一场对战")
                        # 开始新的对战
                        base_colors = None  # 重置开局基准背景色
                        self.start_new_match()
                        in_match = True
                        self.log_signal.emit("检测到新对战开始")

                    if key == 'end_round' and in_match:
                        # 新增：在第一回合且未出牌时记录基准背景色
                        if current_round_count == 1 and base_colors is None:
                            base_colors = []
                            for pos in follower_positions:
                                x, y = pos
                                # 记录当前位置的色彩
                                color1 = screenshot.getpixel((x, y))
                                # 记录Y轴向下20个像素点的色彩
                                color2 = screenshot.getpixel((x, y + 20))
                                base_colors.append((color1, color2))
                            self.log_signal.emit("第1回合，记录基准背景色完成")

                        self_shield_targets = scan_self_shield_targets()
                        if self_shield_targets:
                            # 暂停脚本并通知用户
                            self.paused = True
                            self.log_signal.emit(f"检测到己方护盾目标！脚本已暂停")

                            # 获取最高置信度的目标
                            best_target = self_shield_targets[0]
                            self.log_signal.emit(
                                f"检测到己方护盾随从！位置: ({best_target['x']}, {best_target['y']}), "
                                f"置信度: {best_target['confidence']:.2f}\n"
                                "脚本已暂停，请手动处理。"
                            )

                            # 跳过后续操作
                            last_detected_button = key
                            button_detected = True
                            break

                        if current_round_count in (4, 5, 6, 7, 8):  # 第4 ，5，6 ,7,8回合
                            self.log_signal.emit(f"第{current_round_count}回合，执行进化/超进化")
                            perform_fullPlus_actions(self.u2_device, current_round_count, base_colors)
                        elif current_round_count > 12:   #12回合以上弃权防止烧绳
                            self.log_signal.emit(f"12回合以上，直接弃权")
                            time.sleep(0.5)
                            self.u2_device.click(57, 63)
                            time.sleep(0.5)
                            self.u2_device.click(642, 148)
                            time.sleep(0.5)
                            self.u2_device.click(773, 560)
                            time.sleep(1)
                        else:
                            self.log_signal.emit(f"第{current_round_count}回合，执行正常操作")
                            perform_full_actions(self.u2_device, current_round_count, base_colors)

                        current_round_count += 1
                        has_clicked_plus_this_round = False

                    # 计算中心点并点击
                    center_x = max_loc[0] + template_info['w'] // 2
                    center_y = max_loc[1] + template_info['h'] // 2
                    self.u2_device.click(center_x + random.randint(-2, 2), center_y + random.randint(-2, 2))
                    button_detected = True

                    if key != last_detected_button:
                        self.log_signal.emit(f"检测到按钮并点击: {template_info['name']} ")
                    # 更新状态跟踪
                    last_detected_button = key
                    time.sleep(0.5)
                    break

            # 更新统计信息
            stats = {
                'current_turn': current_round_count,
                'run_time': int(time.time() - self.start_time),
                'battle_count': current_run_matches,
                'turn_count': current_round_count * current_run_matches
            }
            self.stats_signal.emit(stats)

            # 计算处理时间并调整等待
            process_time = time.time() - start_time
            sleep_time = max(0, SCAN_INTERVAL - process_time)
            time.sleep(sleep_time)

        # 结束当前对战（如果正在进行）
        if in_match:
            self.end_current_match()

        # 保存统计数据
        save_round_statistics()

        # 结束信息
        run_duration = datetime.datetime.now() - current_run_start_time
        hours, remainder = divmod(run_duration.total_seconds(), 3600)
        minutes, seconds = divmod(remainder, 60)

        self.log_signal.emit("\n===== 本次运行总结 =====")
        self.log_signal.emit(f"脚本启动时间: {current_run_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        self.log_signal.emit(f"运行时长: {int(hours)}小时{int(minutes)}分钟{int(seconds)}秒")
        self.log_signal.emit(f"完成对战次数: {current_run_matches}")
        self.log_signal.emit("===== 脚本结束运行 =====")
        self.status_signal.emit("已停止")

    def start_new_match(self):
        """开始新的对战"""
        global current_round_count, match_start_time, current_run_matches

        # 重置回合计数器
        current_round_count = 1
        match_start_time = time.time()
        current_run_matches += 1
        self.log_signal.emit(f"===== 开始新的对战 =====")
        self.log_signal.emit(f"本次运行对战次数: {current_run_matches}")

    def end_current_match(self):
        """结束当前对战并记录统计数据"""
        global current_round_count, match_start_time, match_history

        if match_start_time is None:
            return

        match_duration = time.time() - match_start_time
        minutes, seconds = divmod(match_duration, 60)

        match_record = {
            "date": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "rounds": current_round_count,
            "duration": f"{int(minutes)}分{int(seconds)}秒",
            "run_id": current_run_start_time.strftime("%Y%m%d%H%M%S")
        }

        match_history.append(match_record)

        # 保存统计数据到文件
        save_round_statistics()

        self.log_signal.emit(f"===== 对战结束 =====")
        self.log_signal.emit(f"回合数: {current_round_count}, 持续时间: {int(minutes)}分{int(seconds)}秒")

        # 重置对战状态
        match_start_time = None
        current_round_count = 1

    def stop(self):
        self.running = False

    def pause(self):
        self.paused = True
        self.status_signal.emit("已暂停")

    def resume(self):
        self.paused = False
        self.status_signal.emit("运行中")

class ShadowverseAutomationUI(QMainWindow):
    def __init__(self):
        super().__init__()
        # 移除窗口边框和标题栏
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowMinimizeButtonHint)
        self.setWindowTitle("Shadowverse 自动化脚本")
        self.setGeometry(100, 100, 900, 700)

        # 设置窗口背景
        self.set_background()

        # 初始化UI
        self.init_ui()

        # 工作线程
        self.script_thread = None
        self.run_time = 0
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.update_run_time)

        # 窗口控制按钮状态
        self.is_maximized = False

    def set_background(self):
        # 创建调色板
        palette = self.palette()

        # 检查背景图片是否存在
        if os.path.exists(BACKGROUND_IMAGE):
            # 加载背景图片并缩放以适应窗口
            background = QPixmap(BACKGROUND_IMAGE).scaled(
                self.size(),
                Qt.IgnoreAspectRatio,
                Qt.SmoothTransformation
            )
            palette.setBrush(QPalette.Window, QBrush(background))
        else:
            # 如果图片不存在，使用半透明黑色背景
            palette.setColor(QPalette.Window, QColor(30, 30, 40, 180))

        self.setPalette(palette)

    def resizeEvent(self, event):
        # 当窗口大小改变时，重新设置背景图片
        self.set_background()
        super().resizeEvent(event)

    def init_ui(self):
        # 主控件
        central_widget = QWidget()
        central_widget.setObjectName("CentralWidget")
        central_widget.setStyleSheet("""
            #CentralWidget {
                background-color: rgba(30, 30, 40, 180);
                border-radius: 15px;
                padding: 15px;
            }
            QLabel {
                color: #E0E0FF;
                font-weight: bold;
            }
            QLineEdit {
                background-color: rgba(50, 50, 70, 200);
                color: #FFFFFF;
                border: 1px solid #5A5A8F;
                border-radius: 5px;
                padding: 5px;
            }
            QPushButton {
                background-color: #4A4A7F;
                color: #FFFFFF;
                border: none;
                border-radius: 5px;
                padding: 8px 15px;
                font-weight: bold;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #5A5A9F;
            }
            QPushButton:pressed {
                background-color: #3A3A6F;
            }
            QTextEdit {
                background-color: rgba(25, 25, 35, 220);
                color: #66AAFF;
                border: 1px solid #444477;
                border-radius: 5px;
            }
            #StatsFrame {
                background-color: rgba(40, 40, 60, 200);
                border: 1px solid #555588;
                border-radius: 8px;
                padding: 10px;
            }
            .StatLabel {
                color: #AACCFF;
                font-size: 14px;
            }
            .StatValue {
                color: #FFFF88;
                font-size: 16px;
                font-weight: bold;
            }
            #TitleLabel {
                font-size: 24px;
                color: #88AAFF;
                font-weight: bold;
                padding: 10px 0;
            }
            #WindowControlButton {
                background: transparent;
                border: none;
                min-width: 30px;
                max-width: 30px;
                min-height: 30px;
                max-height: 30px;
                padding: 0;
                margin: 0;
            }
            #WindowControlButton:hover {
                background-color: rgba(255, 255, 255, 30);
            }
            #CloseButton:hover {
                background-color: rgba(255, 0, 0, 100);
            }
        """)

        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # 顶部栏布局
        top_bar_layout = QHBoxLayout()
        top_bar_layout.setContentsMargins(0, 0, 0, 0)
        top_bar_layout.setSpacing(15)

        # 添加程序标题
        title_label = QLabel("Shadowverse自动化脚本[免费]  Q群892100160")
        title_label.setObjectName("TitleLabel")
        top_bar_layout.addWidget(title_label)

        # 添加空白区域使按钮靠右
        top_bar_layout.addStretch()

        # 添加窗口控制按钮
        self.minimize_btn = QPushButton("－")
        self.minimize_btn.setObjectName("WindowControlButton")
        self.minimize_btn.clicked.connect(self.showMinimized)

        self.maximize_btn = QPushButton("□")
        self.maximize_btn.setObjectName("WindowControlButton")
        self.maximize_btn.clicked.connect(self.toggle_maximize)

        self.close_btn = QPushButton("×")
        self.close_btn.setObjectName("WindowControlButton")
        self.close_btn.setObjectName("CloseButton")
        self.close_btn.clicked.connect(self.close)

        top_bar_layout.addWidget(self.minimize_btn)
        top_bar_layout.addWidget(self.maximize_btn)
        top_bar_layout.addWidget(self.close_btn)

        main_layout.addLayout(top_bar_layout)

        # ADB 连接部分
        adb_layout = QHBoxLayout()
        adb_label = QLabel("ADB 端口:")
        self.adb_input = QLineEdit("127.0.0.1:16384")
        self.connect_btn = QPushButton("连接设备")
        self.connect_btn.clicked.connect(self.connect_device)

        adb_layout.addWidget(adb_label)
        adb_layout.addWidget(self.adb_input)
        adb_layout.addWidget(self.connect_btn)
        adb_layout.addStretch()

        status_label = QLabel("状态:")
        self.status_label = QLabel("未连接")
        self.status_label.setStyleSheet("color: #FF5555;")
        adb_layout.addWidget(status_label)
        adb_layout.addWidget(self.status_label)

        main_layout.addLayout(adb_layout)

        # 控制按钮
        btn_layout = QHBoxLayout()
        self.start_btn = QPushButton("开始")
        self.stop_btn = QPushButton("停止")
        self.pause_btn = QPushButton("暂停")
        self.stats_btn = QPushButton("显示统计")

        self.start_btn.clicked.connect(self.start_script)
        self.stop_btn.clicked.connect(self.stop_script)
        self.pause_btn.clicked.connect(self.pause_script)
        self.stats_btn.clicked.connect(self.show_stats)

        btn_layout.addWidget(self.start_btn)
        btn_layout.addWidget(self.stop_btn)
        btn_layout.addWidget(self.pause_btn)
        btn_layout.addWidget(self.stats_btn)
        btn_layout.addStretch()

        main_layout.addLayout(btn_layout)

        # 统计信息面板
        stats_frame = QFrame()
        stats_frame.setObjectName("StatsFrame")
        stats_layout = QGridLayout(stats_frame)
        stats_layout.setHorizontalSpacing(30)
        stats_layout.setVerticalSpacing(10)

        # 第一行统计信息
        stats_layout.addWidget(QLabel("当前回合:"), 0, 0)
        self.current_turn_label = QLabel("0")
        self.current_turn_label.setObjectName("StatValue")
        stats_layout.addWidget(self.current_turn_label, 0, 1)

        stats_layout.addWidget(QLabel("运行时间:"), 0, 2)
        self.run_time_label = QLabel("00:00:00")
        self.run_time_label.setObjectName("StatValue")
        stats_layout.addWidget(self.run_time_label, 0, 3)

        # 第二行统计信息
        stats_layout.addWidget(QLabel("对战次数:"), 1, 0)
        self.battle_count_label = QLabel("0")
        self.battle_count_label.setObjectName("StatValue")
        stats_layout.addWidget(self.battle_count_label, 1, 1)

        stats_layout.addWidget(QLabel("回合总数:"), 1, 2)
        self.turn_count_label = QLabel("0")
        self.turn_count_label.setObjectName("StatValue")
        stats_layout.addWidget(self.turn_count_label, 1, 3)

        main_layout.addWidget(stats_frame)

        # 运行日志标题
        log_title = QLabel("运行日志:")
        log_title.setStyleSheet("font-size: 16px; color: #88AAFF;")
        main_layout.addWidget(log_title)

        # 日志区域
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        main_layout.addWidget(self.log_output)

        self.setCentralWidget(central_widget)

        # 初始化按钮状态
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(False)
        self.pause_btn.setEnabled(False)
        self.stats_btn.setEnabled(False)

    def toggle_maximize(self):
        if self.is_maximized:
            self.showNormal()
            self.maximize_btn.setText("□")
            self.is_maximized = False
        else:
            self.showMaximized()
            self.maximize_btn.setText("❐")
            self.is_maximized = True

    def connect_device(self):
        self.log_output.append("正在连接设备...")
        self.connect_btn.setEnabled(False)

        # 加载配置
        config = DEFAULT_CONFIG.copy()
        try:
            config["emulator_port"] = int(self.adb_input.text().split(":")[-1])
        except:
            config["emulator_port"] = 16384

        # 创建工作线程
        self.script_thread = ScriptThread(config)
        self.script_thread.log_signal.connect(self.log_output.append)
        self.script_thread.status_signal.connect(self.update_status)
        self.script_thread.stats_signal.connect(self.update_stats)
        self.script_thread.start()

        # 更新按钮状态
        self.start_btn.setEnabled(True)

    def start_script(self):
        if self.script_thread:
            self.script_thread.resume()
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(True)
            self.pause_btn.setEnabled(True)
            self.stats_btn.setEnabled(True)
            self.timer.start(1000)  # 每秒更新一次运行时间

    def stop_script(self):
        if self.script_thread:
            self.script_thread.stop()
            self.start_btn.setEnabled(False)
            self.stop_btn.setEnabled(False)
            self.pause_btn.setEnabled(False)
            self.timer.stop()

    def pause_script(self):
        if self.script_thread:
            self.script_thread.pause()

    def show_stats(self):
        self.log_output.append("===== 对战统计 =====")
        self.log_output.append(f"总对战次数: {self.battle_count_label.text()}")
        self.log_output.append(f"总回合数: {self.turn_count_label.text()}")
        self.log_output.append(f"平均回合数: {self.calculate_avg_turns()}")

    def calculate_avg_turns(self):
        battle_count = int(self.battle_count_label.text())
        turn_count = int(self.turn_count_label.text())
        return round(turn_count / battle_count, 2) if battle_count > 0 else 0

    def update_status(self, status):
        self.status_label.setText(status)
        if status == "运行中":
            self.status_label.setStyleSheet("color: #55FF55;")
        elif status == "已暂停":
            self.status_label.setStyleSheet("color: #FFFF55;")
            self.timer.stop()
        else:
            self.status_label.setStyleSheet("color: #FF5555;")

    def update_stats(self, stats):
        self.current_turn_label.setText(str(stats['current_turn']))
        self.run_time = stats['run_time']
        self.update_run_time()
        self.battle_count_label.setText(str(stats['battle_count']))
        self.turn_count_label.setText(str(stats['turn_count']))

    def update_run_time(self):
        hours = self.run_time // 3600
        minutes = (self.run_time % 3600) // 60
        seconds = self.run_time % 60
        self.run_time_label.setText(f"{hours:02d}:{minutes:02d}:{seconds:02d}")
        self.run_time += 1

    # 添加鼠标事件处理以实现窗口拖动
    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.drag_position = event.globalPos() - self.frameGeometry().topLeft()
            event.accept()

    def mouseMoveEvent(self, event):
        if hasattr(self, 'drag_position') and event.buttons() == Qt.LeftButton:
            self.move(event.globalPos() - self.drag_position)
            event.accept()

if __name__ == "__main__":
    try:
        app = QApplication(sys.argv)
        window = ShadowverseAutomationUI()
        window.show()
        sys.exit(app.exec_())
    except Exception as e:
        print(f"程序崩溃: {e}")
        input("按 Enter 退出...")