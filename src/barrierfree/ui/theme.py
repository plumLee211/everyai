"""UI 시안(Figma "배리어프리 영상 AI 점검 도구 - UI 초안")의 색·글꼴·공통 위젯 스타일.

색은 시안 PNG에서 읽은 근사값이다(Figma 변수는 MCP 호출 한도로 읽지 못했다). 시안의 토큰을 받으면
`COLORS`만 바꾸면 된다. 위젯은 객체 이름(`objectName`)과 동적 속성(`variant`, `tone`)으로 스타일을
고르므로, 화면 코드에는 색이 직접 나오지 않는다.
"""

from __future__ import annotations

from PyQt6.QtWidgets import QFrame, QLabel, QVBoxLayout, QWidget

COLORS = {
    "primary": "#2F5BD0",
    "primary_hover": "#274CB0",
    "primary_weak": "#E8EEFC",
    "text": "#1D2433",
    "muted": "#5F6677",
    "faint": "#8A91A3",
    "border": "#E4E7EE",
    "surface": "#FFFFFF",
    "app_bg": "#F5F6FA",
    "sidebar_bg": "#F8F9FC",
    "tile_bg": "#F8F9FC",
    "success_bg": "#E4F4EA",
    "success_fg": "#2C7A4B",
    "warn_bg": "#FFF3D1",
    "warn_fg": "#8A5A00",
    "danger_bg": "#FDE8E8",
    "danger_fg": "#B83A3A",
    "neutral_bg": "#EEF0F5",
    "neutral_fg": "#4A5164",
    "player_bg": "#1B1E26",
    "player_fg": "#F2F4F8",
}

FONT_FAMILY = '"Pretendard", "Malgun Gothic", "Segoe UI", sans-serif'

TONES = ("success", "warn", "danger", "info", "neutral")


def stylesheet() -> str:
    c = COLORS
    return f"""
* {{ font-family: {FONT_FAMILY}; font-size: 13px; color: {c["text"]}; }}
QMainWindow, QDialog {{ background: {c["app_bg"]}; }}
QToolTip {{ background: {c["text"]}; color: {c["surface"]}; border: 0; padding: 4px 6px; }}

/* ---- 헤더·사이드바 ---- */
#AppHeader {{ background: {c["surface"]}; border-bottom: 1px solid {c["border"]}; }}
#Brand {{ color: {c["primary"]}; font-size: 18px; font-weight: 700; }}
#WorkTitle {{ font-size: 14px; font-weight: 600; }}
#Sidebar {{ background: {c["sidebar_bg"]}; border-right: 1px solid {c["border"]}; }}
#Sidebar QLabel#SidebarSection {{ color: {c["muted"]}; font-size: 11px; }}
#Sidebar QFrame#SidebarDivider {{ background: {c["border"]}; max-height: 1px; border: 0; }}
#Sidebar QPushButton {{
    text-align: left; border: 0; border-radius: 8px; padding: 9px 12px;
    background: transparent; color: {c["text"]}; font-weight: 500;
}}
#Sidebar QPushButton:hover {{ background: {c["neutral_bg"]}; }}
#Sidebar QPushButton[selected="true"] {{ background: {c["primary_weak"]}; color: {c["primary"]}; }}
#Sidebar QPushButton[recent="true"] {{ padding: 5px 12px; font-size: 12px; color: {c["muted"]}; }}
#Sidebar QPushButton:disabled {{ color: {c["faint"]}; }}
#Breadcrumb {{ color: {c["muted"]}; font-size: 12px; }}

/* ---- 카드·텍스트 ---- */
QFrame[card="true"] {{
    background: {c["surface"]}; border: 1px solid {c["border"]}; border-radius: 12px;
}}
QFrame[tile="true"] {{ background: {c["tile_bg"]}; border: 0; border-radius: 8px; }}
QFrame[tile="true"] QLabel {{ background: transparent; }}
QFrame[card="true"] QLabel {{ background: transparent; border: 0; }}
QLabel[role="title"] {{ font-size: 26px; font-weight: 700; }}
QLabel[role="section"] {{ font-size: 14px; font-weight: 700; }}
QLabel[role="muted"] {{ color: {c["muted"]}; }}
QLabel[role="caption"] {{ color: {c["muted"]}; font-size: 12px; }}
QLabel[role="error"] {{ color: {c["danger_fg"]}; }}

QFrame[banner="info"] {{ background: {c["primary_weak"]}; border: 0; border-radius: 8px; }}
QFrame[banner="info"] QLabel {{ background: transparent; }}
QFrame[banner="warn"] {{ background: {c["warn_bg"]}; border: 0; border-radius: 8px; }}
QFrame[banner="warn"] QLabel {{ background: transparent; color: {c["warn_fg"]}; }}

/* ---- 칩(상태 표시) ---- */
QLabel[chip="true"] {{ border-radius: 6px; padding: 4px 8px; font-size: 11px; font-weight: 600; }}
QLabel[chip="true"][tone="success"] {{ background: {c["success_bg"]}; color: {c["success_fg"]}; }}
QLabel[chip="true"][tone="warn"] {{ background: {c["warn_bg"]}; color: {c["warn_fg"]}; }}
QLabel[chip="true"][tone="danger"] {{ background: {c["danger_bg"]}; color: {c["danger_fg"]}; }}
QLabel[chip="true"][tone="info"] {{ background: {c["primary_weak"]}; color: {c["primary"]}; }}
QLabel[chip="true"][tone="neutral"] {{ background: {c["neutral_bg"]}; color: {c["neutral_fg"]}; }}

/* ---- 버튼 ---- */
QPushButton {{
    background: {c["surface"]}; border: 1px solid {c["border"]}; border-radius: 8px;
    padding: 9px 18px; font-weight: 600;
}}
QPushButton:hover {{ background: {c["app_bg"]}; }}
QPushButton:disabled {{ color: {c["faint"]}; background: {c["neutral_bg"]}; }}
QPushButton[variant="primary"] {{ background: {c["primary"]}; border-color: {c["primary"]};
    color: {c["surface"]}; }}
QPushButton[variant="primary"]:hover {{ background: {c["primary_hover"]}; }}
QPushButton[variant="primary"]:disabled {{ background: #A9BBE8; border-color: #A9BBE8;
    color: {c["surface"]}; }}
QPushButton[variant="link"] {{ border: 0; background: transparent; color: {c["muted"]};
    padding: 4px 8px; font-weight: 500; }}
QPushButton[variant="link"]:hover {{ color: {c["primary"]}; background: transparent; }}
QPushButton:focus {{ border: 1px solid {c["primary"]}; }}

/* ---- 입력 ---- */
QLineEdit, QComboBox {{
    background: {c["surface"]}; border: 1px solid {c["border"]}; border-radius: 8px;
    padding: 7px 10px; min-height: 18px;
}}
QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox QAbstractItemView {{ background: {c["surface"]}; border: 1px solid {c["border"]};
    selection-background-color: {c["primary_weak"]}; selection-color: {c["text"]}; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid {c["faint"]}; background: {c["surface"]}; }}
QCheckBox::indicator:checked {{ background: {c["primary"]}; border-color: {c["primary"]}; }}

QProgressBar {{ background: {c["neutral_bg"]}; border: 0; border-radius: 6px; max-height: 12px;
    min-height: 12px; text-align: center; color: transparent; }}
QProgressBar::chunk {{ background: {c["primary"]}; border-radius: 6px; }}
QSlider::groove:horizontal {{ background: {c["neutral_bg"]}; height: 5px; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c["primary"]}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {c["surface"]}; border: 2px solid {c["primary"]};
    width: 12px; margin: -5px 0; border-radius: 7px; }}

/* ---- 문제 목록 ---- */
QTableView {{ background: transparent; border: 0; gridline-color: transparent;
    selection-background-color: {c["primary_weak"]}; selection-color: {c["text"]};
    alternate-background-color: transparent; outline: 0; }}
QTableView::item {{ padding: 8px 6px; border-bottom: 1px solid {c["border"]}; }}
QHeaderView::section {{ background: transparent; border: 0; color: {c["muted"]};
    font-size: 11px; padding: 4px 6px; }}

/* ---- 플레이어 ---- */
#PlayerPanel {{ background: {c["player_bg"]}; border-radius: 12px; }}
#PlayerPanel QLabel {{ color: {c["player_fg"]}; background: transparent; }}
#PlayerPanel QPushButton {{ background: transparent; color: {c["player_fg"]};
    border: 1px solid #4A5064; padding: 5px 12px; }}
#PlayerPanel QPushButton:hover {{ background: #2A2F3C; }}
#PlayerPanel QSlider::groove:horizontal {{ background: #F2F4F8; }}

/* ---- 비교 상자(검수 상세) ---- */
QFrame[compare="speech"] {{ background: {c["tile_bg"]}; border: 0; border-radius: 8px; }}
QFrame[compare="subtitle"] {{ background: {c["primary_weak"]}; border: 0; border-radius: 8px; }}
QFrame[compare] QLabel {{ background: transparent; }}

/* ---- OCR 영역 미리보기(분석 설정) ---- */
#OcrPreview {{ background: {c["player_bg"]}; border-radius: 8px; }}
"""


def set_tone(label: QLabel, tone: str) -> None:
    """칩의 색을 바꾼다(속성이 바뀌면 스타일을 다시 적용해야 한다)."""
    if tone not in TONES:
        raise ValueError(tone)
    label.setProperty("tone", tone)
    repolish(label)


def repolish(widget: QWidget) -> None:
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


def chip(text: str, tone: str = "neutral") -> QLabel:
    label = QLabel(text)
    label.setProperty("chip", True)
    label.setProperty("tone", tone)
    return label


def role(label: QLabel, name: str) -> QLabel:
    """텍스트 역할(title/section/muted/caption/error)을 지정한다."""
    label.setProperty("role", name)
    return label


def primary(button: QWidget) -> None:
    button.setProperty("variant", "primary")


def link(button: QWidget) -> None:
    button.setProperty("variant", "link")


def card(margins: tuple[int, int, int, int] = (20, 20, 20, 20), spacing: int = 12) -> QFrame:
    """흰 배경의 둥근 카드. 안쪽은 `card.layout()`(세로)에 채운다."""
    frame = QFrame()
    frame.setProperty("card", True)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(*margins)
    layout.setSpacing(spacing)
    return frame


def tile() -> QFrame:
    frame = QFrame()
    frame.setProperty("tile", True)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(12, 12, 12, 12)
    layout.setSpacing(6)
    return frame


def banner(kind: str = "info") -> QFrame:
    frame = QFrame()
    frame.setProperty("banner", kind)
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(16, 12, 16, 12)
    layout.setSpacing(4)
    return frame
