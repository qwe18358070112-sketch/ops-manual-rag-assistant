from __future__ import annotations

import re

from app.models.records import SourceDocument

SOURCE_TYPE_LABELS = {
    "internal_manual": "手册",
    "official_manual": "官方手册",
    "emergency_plan": "预案",
    "weekly_report": "周报",
    "monthly_report": "月报",
}

SYSTEM_TAGS = {
    "视频平台",
    "视联网",
    "综治中心",
    "平安风险智控",
    "政企驻场运维",
}

CORE_SOURCE_TAGS = {
    "案例库",
    "周报",
    "月报",
    "手册",
    "预案",
    "官方手册",
    *SYSTEM_TAGS,
}

SECTION_TAG_RULES: dict[str, tuple[str, ...]] = {
    "资源共享": ("资源共享", "共享操作", "共享点位", "共享推送", "共享目录"),
    "点位治理": ("点位治理", "点位恢复", "在线率", "离线点位", "监控点位", "点位"),
    "收藏夹": ("收藏夹", "收藏组", "点位收藏"),
    "账号管理": ("账号管理", "用户管理", "账号维护", "租户管理", "权限管理", "授权"),
    "账号问题": ("账号问题", "密码错误", "账号异常", "登录异常", "无法登录", "登录不上"),
    "客户端登录": ("客户端登录", "登录不上", "登录异常", "登录失败", "客户端登录"),
    "客户端卡顿": ("客户端卡顿", "卡顿", "客户端报错", "闪退", "死机"),
    "漏洞整改": ("漏洞整改", "漏洞", "整改", "弱口令", "端口开放", "cors", "安全扫描"),
    "网络故障": ("断网", "网络故障", "交换机", "ping", "无法上网", "网络异常", "连通"),
    "设备巡检": ("设备巡检", "巡检", "日常检查", "机房巡检", "终端巡检"),
    "轮询助手": ("轮询助手", "自动化程序", "自动监测", "自动恢复", "宫格", "全屏", "黑屏跳过"),
    "语音助手": ("语音助手", "热词", "语音指令", "指令优化"),
    "会议保障": ("会议保障", "会前", "会中", "会后", "会议流程"),
    "视联网会议": ("视联网会议", "视频会议", "会控", "会议终端"),
    "大屏处理": ("大屏", "蓝屏", "黑屏", "无画面", "无信号", "拼控", "电视墙"),
    "信号源": ("信号源", "hdmi", "切换矩阵", "切换源"),
    "应急处理": ("应急处理", "应急预案", "故障处理", "处置流程", "应急处置"),
    "数据库": ("数据库", "mysql", "sql", "库表", "备份恢复"),
    "日志分析": ("日志", "日志分析", "日志查询", "审计日志"),
    "证书更新": ("证书", "证书更新", "ssl", "https", "证书到期"),
    "值班保障": ("值班", "清明值班", "节假日值班", "应急值守"),
    "算法管理": ("算法管理", "算法应用", "算法配置"),
}


def _compact_text(*parts: str) -> str:
    joined = "\n".join(part for part in parts if part).lower()
    return re.sub(r"\s+", "", joined)


def derive_section_tags(source: SourceDocument, section_title: str, content: str) -> list[str]:
    compact = _compact_text(source.title, section_title, content)
    tags: list[str] = []

    def add(tag: str) -> None:
        normalized = str(tag).strip()
        if normalized and normalized not in tags:
            tags.append(normalized)

    add(SOURCE_TYPE_LABELS.get(source.source_type, ""))
    add(source.system_name)

    for tag in source.tags:
        if tag in CORE_SOURCE_TAGS:
            add(tag)

    for tag, keywords in SECTION_TAG_RULES.items():
        if any(keyword.lower().replace(" ", "") in compact for keyword in keywords):
            add(tag)

    if source.source_group == "case":
        add("历史案例")
    elif source.source_group == "manual":
        add("手册问答")

    if len(tags) < 4:
        for tag in source.tags:
            add(tag)
            if len(tags) >= 6:
                break

    return tags[:12]
