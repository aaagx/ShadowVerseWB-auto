from adbutils import adb, AdbClient
import uiautomator2 as u2
import cv2
import numpy as np
import time
import ctypes
import sys
import os
import logging
import threading
import datetime
import json
from collections import defaultdict
import tkinter as tk
from tkinter import messagebox
import queue
import random
import io
import ctypes
from ctypes import wintypes

# 随从的位置坐标（720P分辨率）
follower_positions =[
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
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
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



def show_tkinter_notification(title, message):
    """使用Tkinter显示通知"""
    try:
        # 创建临时窗口
        root = tk.Tk()
        root.withdraw()  # 隐藏主窗口

        # 显示消息框
        messagebox.showinfo(title, message)

        # 关闭窗口
        root.destroy()
    except Exception as e:
        logger.error(f"显示Tkinter通知失败: {str(e)}")
        # 回退到传统弹窗
        ctypes.windll.user32.MessageBoxW(0, message, title, 0x40)


def notification_handler():
    """处理通知队列中的消息"""
    while True:
        try:
            # 从队列获取通知
            notification = notification_queue.get()
            if notification is None:  # 退出信号
                break

            title, message = notification
            show_tkinter_notification(title, message)
        except Exception as e:
            logger.error(f"通知处理出错: {str(e)}")
        finally:
            notification_queue.task_done()


def connect_with_adbutils(config):
    """连接设备并返回 (uiautomator2_device, adbutils_device)"""
    global device
    try:
        # 从配置获取端口
        EMULATOR_PORT = config["emulator_port"]
        target_serial = f"127.0.0.1:{EMULATOR_PORT}"

        # 创建 adb 客户端
        client = AdbClient(host="127.0.0.1")

        # 检查是否已连接目标模拟器
        devices_list = client.device_list()
        emulator_connected = any(d.serial == target_serial for d in devices_list)

        if not emulator_connected:
            logger.info(f"尝试自动连接模拟器({target_serial})...")
            try:
                adb.connect(target_serial)
                time.sleep(2)  # 等待连接稳定
                devices_list = client.device_list()
                emulator_connected = any(d.serial == target_serial for d in devices_list)
                if emulator_connected:
                    logger.info(f"模拟器连接成功: {target_serial}")
                else:
                    logger.warning(f"连接模拟器失败: 未出现在 device_list 中")
            except Exception as conn_err:
                logger.warning(f"adbutils 连接失败: {conn_err}")

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
            logger.warning(f"未找到配置的模拟器({target_serial})，使用第一个设备: {target_device.serial}")

        # 获取 adbutils 设备对象
        device = adb.device(serial=target_device.serial)
        logger.info(f"已连接设备: {target_device.serial}")

        # 获取 uiautomator2 设备对象
        u2_device = u2.connect(target_device.serial)
        return u2_device, device

    except Exception as e:
        logger.error(f"设备连接失败: {str(e)}")
        error_msg = (
            "设备连接失败！\n\n"
            f"当前配置: 模拟器ADB端口={EMULATOR_PORT}\n"
            "请按以下步骤操作：\n"
            "1. 确保模拟器已启动\n"
            "2. 检查config.json中的端口配置是否正确\n"
            f"3. 可手动运行: adb connect {target_serial}\n"
            "4. 再次运行本脚本\n"
            f"错误详情: {str(e)}"
        )
        notification_queue.put(("设备连接错误", error_msg))
        raise

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


def ask_user_question(question, default=True):
    """询问用户是/否问题"""
    print(f"{question} [{'Y/n' if default else 'y/N'}] ", end='')
    while True:
        response = input().strip().lower()

        if response == '':
            return default
        if response in ['y', 'yes']:
            return True
        if response in ['n', 'no']:
            return False

        print("请输入 y 或 n")


def command_listener():
    """监听控制台命令的线程函数"""
    global script_running, script_paused

    logger.info("控制台命令监听已启动 (输入 'p'暂停, 'r'恢复, 'e'退出 或 's'统计)")

    # 添加命令提示
    print("\n>>> 命令提示: 'p'暂停, 'r'恢复, 'e'退出, 's'显示统计 <<<")
    print(">>> 输入命令后按回车 <<<\n")

    while script_running:
        try:
            # 直接使用input获取命令
            cmd = input("> ").strip().lower()

            # 将命令放入队列
            command_queue.put(cmd)

            # 添加处理反馈
            if cmd in ['p', 'r', 'e', 's']:
                print(f"命令 '{cmd}' 已接收")
            else:
                print(f"未知命令: '{cmd}'. 可用命令: p, r, e, s")

        except Exception as e:
            logger.error(f"命令监听出错: {str(e)}")
            time.sleep(1)  # 避免频繁出错

    logger.info("命令监听线程已退出")


def handle_command(cmd):
    """处理用户命令"""
    global script_running, script_paused

    if not cmd:
        return

    if cmd == "p":
        script_paused = True
        logger.warning("用户请求暂停脚本")
        print(">>> 脚本已暂停 <<<")
    elif cmd == "r":
        script_paused = False
        logger.info("用户请求恢复脚本")
        print(">>> 脚本已恢复 <<<")
    elif cmd == "e":
        script_running = False
        logger.info("正在退出脚本...")
        print(">>> 正在退出脚本... <<<")
    elif cmd == "s":
        show_round_statistics()
        print(">>> 已显示统计信息 <<<")
    else:
        logger.warning(f"未知命令: '{cmd}'. 可用命令:'p'暂停, 'r'恢复, 'e'退出 或 's'统计")
        print(f">>> 未知命令: '{cmd}' <<<")


def start_new_match():
    """开始新的对战"""
    global current_round_count, match_start_time, current_run_matches

    # 重置回合计数器
    current_round_count = 1
    match_start_time = time.time()
    current_run_matches += 1
    logger.info(f"===== 开始新的对战 =====")
    logger.info(f"本次运行对战次数: {current_run_matches}")


def detect_existing_match(gray_screenshot, templates):
    """检测脚本启动时是否已经处于对战状态"""
    global current_round_count, match_start_time, in_match

    # 检查是否已经在对战中（检测"结束回合"按钮或"敌方回合"或者 决定 按钮）
    end_round_info = templates['end_round']
    enemy_round_info = templates['enemy_round']
    decision_info = templates['decision']

    # 检测我方回合
    if end_round_info:
        max_loc, max_val = match_template(gray_screenshot, end_round_info)
        if max_val >= end_round_info['threshold']:
            in_match = True
            match_start_time = time.time()
            current_round_count = 2
            logger.info("脚本启动时检测到已处于我方回合，自动设置回合数为2")
            return True

    # 检测敌方回合
    if enemy_round_info:
        max_loc, max_val = match_template(gray_screenshot, enemy_round_info)
        if max_val >= enemy_round_info['threshold']:
            in_match = True
            match_start_time = time.time()
            current_round_count = 2
            logger.info("脚本启动时检测到已处于敌方回合，自动设置回合数为2")
            return True

    # 检测换牌开场
    if decision_info:
        max_loc, max_val = match_template(gray_screenshot, decision_info)
        if max_val >= decision_info['threshold']:
            in_match = True
            match_start_time = time.time()
            current_round_count = 1
            logger.info("脚本启动时检测到已处于换牌阶段，自动设置回合数为1")
            return True

    return False


def end_current_match():
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

    logger.info(f"===== 对战结束 =====")
    logger.info(f"回合数: {current_round_count}, 持续时间: {int(minutes)}分{int(seconds)}秒")

    # 重置对战状态
    match_start_time = None
    current_round_count = 1


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
        threshold = 0.75# 匹配阈值

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


def show_round_statistics():
    """显示回合统计数据"""
    if not match_history:
        logger.info("暂无对战统计数据")
        return

    # 计算总数据
    total_matches = len(match_history)
    total_rounds = sum(match['rounds'] for match in match_history)
    avg_rounds = total_rounds / total_matches if total_matches > 0 else 0

    # 计算本次运行数据
    current_run_matches = 0
    current_run_rounds = 0
    for match in match_history:
        if match.get('run_id') == current_run_start_time.strftime("%Y%m%d%H%M%S"):
            current_run_matches += 1
            current_run_rounds += match['rounds']

    current_run_avg = current_run_rounds / current_run_matches if current_run_matches > 0 else 0

    # 按回合数分组统计
    round_distribution = defaultdict(int)
    for match in match_history:
        round_distribution[match['rounds']] += 1

    # 显示统计数据
    logger.info(f"\n===== 对战回合统计 =====")
    logger.info(f"总对战次数: {total_matches}")
    logger.info(f"总回合数: {total_rounds}")
    logger.info(f"平均每局回合数: {avg_rounds:.1f}")

    # 显示本次运行统计
    logger.info(f"\n===== 本次运行统计 =====")
    logger.info(f"对战次数: {current_run_matches}")
    logger.info(f"总回合数: {current_run_rounds}")
    logger.info(f"平均每局回合数: {current_run_avg:.1f}")

    logger.info("\n回合数分布:")
    for rounds in sorted(round_distribution.keys()):
        count = round_distribution[rounds]
        percentage = (count / total_matches) * 100
        logger.info(f"{rounds}回合: {count}次 ({percentage:.1f}%)")

    # 显示最近5场对战
    logger.info("\n最近5场对战:")
    for match in match_history[-5:]:
        run_marker = "(本次运行)" if match.get('run_id') == current_run_start_time.strftime("%Y%m%d%H%M%S") else ""
        logger.info(f"{match['date']} - {match['rounds']}回合 ({match['duration']}) {run_marker}")


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

    # 颜色检测不够准确，先使用旧逻辑
    if False:
    # if base_colors:
        # 使用颜色检测

        for i, pos in enumerate(reversed_follower_positions):
            x, y = pos
            attackDelay = 0.03
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
                curved_drag(u2_device, x, y, target_x, target_y, 0.05, 3)
                time.sleep(attackDelay)
    else:
        # 后备方案：执行简易坐标
        # 固定攻击
        # logger.warning("未找到基准背景色，执行默认攻击")
        for i, pos in enumerate(reversed_follower_positions):
            x, y = pos
            attackDelay = 0.03

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
            curved_drag(u2_device, x, y, target_x, target_y, 0.05, 3)
            time.sleep(attackDelay)

    # 避免攻击被卡掉
    time.sleep(0.25)


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
    start_y = 672+random.randint(-2,2)
    end_y = 400+random.randint(-2,2)
    duration = 0.05
    if round_count >= 6:
        drag_points_x = [600, 700, 684, 551, 830, 501, 900, 405, 959]
    else:
        drag_points_x = [405, 501, 551, 600, 684, 700, 830, 900, 959]
    for x in drag_points_x:
        curved_drag(u2_device, x+random.randint(-2,2), start_y, x+random.randint(-2,2), end_y, duration, 6)
        time.sleep(0.05)
    time.sleep(0.5)

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
    start_y = 672+random.randint(-2,2)
    end_y = 400+random.randint(-2,2)
    duration = 0.05
    if round_count >= 6:
        drag_points_x = [600, 700, 684, 551, 830, 501, 900, 405, 959]
    else:
        drag_points_x = [405, 501, 551, 600, 684, 700, 830, 900, 959]

    for x in drag_points_x:
        curved_drag(u2_device, x+random.randint(-2,2), start_y, x+random.randint(-2,2), end_y, duration, 6)
        time.sleep(0.05)
    time.sleep(0.5)

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
    u2_device.click(1026+random.randint(-2,2),178+random.randint(-2,2))
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

def main():
    global script_running, script_paused
    global current_round_count, match_start_time, current_run_matches, current_run_start_time
    global in_match, evolution_template, super_evolution_template
    global device, base_colors, follower_positions

    enable_ansi_support()

    # 加载配置
    config = load_config()
    extra_dir = config.get("extra_templates_dir", "")
    EMULATOR_PORT = config["emulator_port"]
    SCAN_INTERVAL = config["scan_interval"]

    skip_buttons = []



    # 在日志中显示当前配置
    logger.info(f"当前配置: 模拟器ADB端口={EMULATOR_PORT}, 扫描间隔={SCAN_INTERVAL}秒"f"")

    # 初始化设备对象
    device = None

    # 初始化进化模板
    evolution_template = None
    super_evolution_template = None

    # 启动通知处理线程
    notification_thread = threading.Thread(target=notification_handler)
    notification_thread.daemon = False
    notification_thread.start()

    # 记录脚本启动时间
    current_run_start_time = datetime.datetime.now()
    current_run_matches = 0
    in_match = False  # 是否在对战中

    # 配置参数
    SCAN_INTERVAL = 2  # 主循环间隔(
    # 秒)

    logger.info("===== 脚本开始运行 =====")
    logger.info(f"脚本启动时间: {current_run_start_time.strftime('%Y-%m-%d %H:%M:%S')}")

    # 加载历史统计数据
    load_round_statistics()

    # 1. 加载所有模板
    logger.info("正在加载模板...")

    templates = {
        'dailyCard': create_template_info(load_template(TEMPLATES_DIR, 'dailyCard.png'), "每日卡包"),
        'missionCompleted': create_template_info(load_template(TEMPLATES_DIR, 'missionCompleted.png'), "任务完成"),
        'backTitle': create_template_info(load_template(TEMPLATES_DIR, 'backTitle.png'), "返回标题"),
        'errorBackMain': create_template_info(load_template(TEMPLATES_DIR, 'errorBackMain.png'), "遇到错误，返回主页面"),
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

    if extra_dir and os.path.isdir(extra_dir):
        logger.info(f"开始加载额外模板目录: {extra_dir}")

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
                    logger.warning(f"无法加载额外模板: {filename}")
                    continue

                # 创建模板信息（使用全局阈值）
                template_info = create_template_info(
                    template_img,
                    f"额外模板-{template_name}",
                    threshold=config["evolution_threshold"]
                )

                # 添加到模板字典（如果已存在则跳过）
                if template_name not in templates:
                    templates[template_name] = template_info
                    logger.info(f"已添加额外模板: {template_name} (来自: {filename})")

    logger.info("模板加载完成")

    # 2. 连接设备
    logger.info("正在连接设备...")
    try:
        u2_device, adb_device = connect_with_adbutils(config)
        device = adb_device
        logger.info("设备连接成功")
    except Exception as e:
        logger.error(f"设备连接失败: {str(e)}")
        return

    # 3. 启动命令监听线程
    cmd_thread = threading.Thread(target=command_listener, daemon=True)
    cmd_thread.start()

    # 4. 检测脚本启动时是否已经在对战中
    logger.info("检测当前游戏状态...")
    init_screenshot = take_screenshot()
    if init_screenshot is not None:
        # 转换为OpenCV格式
        init_screenshot_np = np.array(init_screenshot)
        init_screenshot_cv = cv2.cvtColor(init_screenshot_np, cv2.COLOR_RGB2BGR)
        gray_init_screenshot = cv2.cvtColor(init_screenshot_cv, cv2.COLOR_BGR2GRAY)

        # 检测是否已经在游戏中
        if detect_existing_match(gray_init_screenshot, templates):
            # 设置本次运行的对战次数
            current_run_matches = 1
            logger.info(f"本次运行对战次数: {current_run_matches} (包含已开始的对战)")
        else:
            logger.info("未检测到进行中的对战")
    else:
        logger.warning("无法获取初始截图，跳过状态检测")

    last_detected_button = None

    # 存储基准背景色
    base_colors = None

    # 5. 主循环
    logger.info("脚本初始化完成，开始运行（模拟器请调成在1280x720分辨率）..."
                "\n\n===================================================="
                "\n1、请使用1280x720分辨率，然后将对战场地设置成简易场地"
                "\n2、额外需要自动点击的图片可放在 extra_templates 文件夹里"
                "\n3、敌方护盾没识别到的请替换或添加 shield 文件夹里的截图"
                "\n4、模拟器端口可在 config.json 中修改"
                "\n5、默认国服，根据服务器自行选择附带的资源替换 templates 和 extra_templates 文件夹，如有按钮无法识别请自行替换素材"
                "\n6、阶位对战图片在不同分段均不相同，第一次使用时请自行截图替换 templates 中的 mainPage.png"
                "\n7、自动跳过每日免费卡包，请记得自行领取"
                "\n8、国际服使用mumu模拟器如果发现游戏亮度过低，请在模拟器配置中将亮度拉到最高"
                "\n9、如果模拟器第一次打开游戏卡在设备优化界面，在模拟器设置中配置使用DirectX而非Vulkan"
                "\n10、使用前需要在游戏设置中关闭回合结束提示"
                "\n11、自己不要带盾，检测到己方带盾会自动暂停脚本"
                "\n====================================================\n\n")
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
    logger.info(message.strip())

    try:
        needLogPause = True
        needAddRoundCount = True
        while script_running:
            start_time = time.time()

            # 检查命令队列
            while not command_queue.empty():
                cmd = command_queue.get()
                handle_command(cmd)

            # 检查脚本暂停状态
            if script_paused:
                if needLogPause:
                    # 记录暂停信息
                    logger.info("脚本暂停中...输入 'r' 继续")
                    needLogPause = False
                # 在暂停状态下每1秒检查一次
                time.sleep(1)
                continue

            # 获取截图
            needLogPause = True
            screenshot = take_screenshot()
            # debug
            # from PIL import Image
            # screenshot = Image.open('./test_resource/1.png')

            if screenshot is None:
                time.sleep(SCAN_INTERVAL)
                continue

            # 转换为OpenCV格式
            screenshot_np = np.array(screenshot)
            screenshot_cv = cv2.cvtColor(screenshot_np, cv2.COLOR_RGB2BGR)
            gray_screenshot = cv2.cvtColor(screenshot_cv, cv2.COLOR_BGR2GRAY)

            # 检查其他按钮
            button_detected = False
            for key, template_info in templates.items():  # 遍历所有模板（包括动态加载的）
                if not template_info:
                    continue

                max_loc, max_val = match_template(gray_screenshot, template_info)
                if max_val >= template_info['threshold']:
                    if key in skip_buttons:
                        continue

                    if key != last_detected_button:
                        if key == 'end_round' and in_match:
                            logger.info(f"已发现'结束回合'按钮 (当前回合: {current_round_count})")


                    # 处理每日卡包
                    if key == 'dailyCard':
                        #点击固定位置跳过
                        logger.info("检测到每日卡包，尝试跳过")
                        u2_device.click(717, 80)

                    # 处理对战开始/结束逻辑
                    if key == 'war':
                        # 检测到"决斗"按钮，表示新对战开始
                        if in_match:
                            # 如果已经在战斗中，先结束当前对战
                            end_current_match()
                            logger.info("检测到新对战开始，结束上一场对战")
                        # 开始新的对战
                        base_colors = None  # 重置开局基准背景色
                        start_new_match()
                        in_match = True
                        logger.info("检测到新对战开始")

                    if key == 'enemy_round':
                        if key != last_detected_button:
                            # 敌方回合开始时重置needAddRoundCount
                            logger.info("检测到敌方回合")
                            needAddRoundCount = True
                            last_detected_button = key
                        time.sleep(1)
                        continue

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
                            logger.info("第1回合，记录基准背景色完成")

                        self_shield_targets = scan_self_shield_targets()
                        if self_shield_targets:
                            # 暂停脚本并通知用户
                            script_paused = True
                            logger.warning(f"检测到己方护盾目标！脚本已暂停")

                            # 获取最高置信度的目标
                            best_target = self_shield_targets[0]
                            notification_msg = (
                                f"检测到己方护盾随从！\n\n"
                                f"位置: ({best_target['x']}, {best_target['y']})\n"
                                f"置信度: {best_target['confidence']:.2f}\n\n"
                                "脚本已暂停，请手动处理。\n"
                                "处理完成后输入 'r' 继续运行脚本。"
                            )

                            # 发送通知
                            notification_queue.put(("检测到护盾随从", notification_msg))

                            # 跳过后续操作
                            last_detected_button = key
                            button_detected = True
                            break


                        if current_round_count in (4, 5, 6, 7, 8):  # 第4 ，5，6 ,7,8回合
                            logger.info(f"第{current_round_count}回合，执行进化/超进化")
                            perform_fullPlus_actions(u2_device, current_round_count, base_colors)
                        elif current_round_count > 12:   #12回合以上弃权防止烧绳
                            logger.info(f"12回合以上，直接弃权")
                            time.sleep(0.5)
                            u2_device.click(57, 63)
                            time.sleep(0.5)
                            u2_device.click(642, 148)
                            time.sleep(0.5)
                            u2_device.click(773, 560)
                            time.sleep(1)
                        else:
                            logger.info(f"第{current_round_count}回合，执行正常操作")
                            perform_full_actions(u2_device, current_round_count, base_colors)

                        if needAddRoundCount:
                            current_round_count += 1
                            needAddRoundCount = False


                    # 计算中心点并点击

                    center_x = max_loc[0] + template_info['w'] // 2
                    center_y = max_loc[1] + template_info['h'] // 2
                    u2_device.click(center_x+random.randint(-2,2), center_y+random.randint(-2,2))
                    button_detected =True

                    if key != last_detected_button:
                        logger.info(f"检测到按钮并点击: {template_info['name']} ")
                    # 更新状态跟踪
                    last_detected_button = key
                    time.sleep(0.5)
                    break

            # 计算处理时间并调整等待
            process_time = time.time() - start_time
            sleep_time = max(0, SCAN_INTERVAL - process_time)
            time.sleep(sleep_time)

    except KeyboardInterrupt:
        logger.info("用户中断脚本执行")
    except Exception as e:
        logger.exception("脚本运行异常:")
    finally:
        # 结束当前对战（如果正在进行）
        if in_match:
            end_current_match()

        # 设置运行标志为False
        script_running = False

        # 保存统计数据
        save_round_statistics()

        # 关闭命令线程
        if 'cmd_thread' in locals() and cmd_thread.is_alive():
            try:
                # 对于Windows系统，发送一个虚拟按键来中断kbhit()
                if os.name == 'nt':
                    import msvcrt
                    # 发送一个回车键来中断等待
                    ctypes.windll.user32.keybd_event(0x0D, 0, 0, 0)
                    ctypes.windll.user32.keybd_event(0x0D, 0, 0x0002, 0)
                # 关闭标准输入以中断 input() 调用
                sys.stdin.close()
                logger.debug("已关闭标准输入")
            except Exception as e:
                logger.error(f"关闭标准输入时出错: {str(e)}")

            # 等待线程退出
            cmd_thread.join(timeout=2.0)
            if cmd_thread.is_alive():
                logger.warning("命令线程未能正常退出")

        # 关闭通知线程
        notification_queue.put(None)
        if 'notification_thread' in locals() and notification_thread.is_alive():
            notification_thread.join(timeout=2.0)
            if notification_thread.is_alive():
                logger.warning("通知线程未能正常退出")

        # 显示最终统计数据
        show_round_statistics()

        # 计算本次运行时间
        run_duration = datetime.datetime.now() - current_run_start_time
        hours, remainder = divmod(run_duration.total_seconds(), 3600)
        minutes, seconds = divmod(remainder, 60)

        # 结束信息
        logger.info("\n===== 本次运行总结 =====")
        logger.info(f"脚本启动时间: {current_run_start_time.strftime('%Y-%m-%d %H:%M:%S')}")
        logger.info(f"运行时长: {int(hours)}小时{int(minutes)}分钟{int(seconds)}秒")
        logger.info(f"完成对战次数: {current_run_matches}")
        logger.info("===== 脚本结束运行 =====")



if __name__ == "__main__":
    main()
