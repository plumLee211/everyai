"""시스템이 다크 모드여도 글자가 보인다. 앱의 색은 밝은 화면 한 벌뿐이다."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator

import pytest
from PyQt6.QtGui import QColor, QImage, QPalette
from PyQt6.QtWidgets import QApplication, QTabWidget, QWidget
from pytestqt.qtbot import QtBot

from barrierfree.ui import theme

Role = QPalette.ColorRole
READABLE = 4.5  # WCAG 2.x의 본문 글자 대비


def _luminance(color: QColor) -> float:
    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * channel(color.redF())
        + 0.7152 * channel(color.greenF())
        + 0.0722 * channel(color.blueF())
    )


def _contrast(a: QColor, b: QColor) -> float:
    high, low = sorted((_luminance(a), _luminance(b)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _dark_palette() -> QPalette:
    palette = QPalette()
    for role in (Role.Window, Role.Base, Role.AlternateBase, Role.Button, Role.ToolTipBase):
        palette.setColor(role, QColor("#1e1e1e"))
    for role in (Role.WindowText, Role.Text, Role.ButtonText, Role.ToolTipText):
        palette.setColor(role, QColor("#ffffff"))
    return palette


@pytest.fixture
def dark_app(qapp: QApplication) -> Iterator[QApplication]:
    """다크 모드인 컴퓨터처럼 어두운 기본 색에서 시작해 앱의 모양을 입힌다."""
    saved_palette, saved_sheet = QPalette(qapp.palette()), qapp.styleSheet()
    saved_style = qapp.style().objectName()
    qapp.setPalette(_dark_palette())
    theme.apply(qapp)
    yield qapp
    qapp.setStyleSheet(saved_sheet)
    qapp.setStyle(saved_style)
    qapp.setPalette(saved_palette)


@pytest.mark.parametrize(
    ("text", "background"),
    [
        (Role.WindowText, Role.Window),
        (Role.Text, Role.Base),
        (Role.Text, Role.AlternateBase),
        (Role.ButtonText, Role.Button),
        (Role.ToolTipText, Role.ToolTipBase),
        (Role.HighlightedText, Role.Highlight),
    ],
    ids=["창", "입력", "교차-행", "버튼", "도움말", "선택"],
)
def test_default_colors_stay_light_and_readable(
    dark_app: QApplication, text: QPalette.ColorRole, background: QPalette.ColorRole
) -> None:
    palette = dark_app.palette()
    assert _contrast(palette.color(text), palette.color(background)) >= READABLE
    if background is Role.Window or background is Role.Base:
        assert _luminance(palette.color(background)) > 0.8


@pytest.mark.parametrize("current", [0, 1], ids=["첫-탭-선택", "둘째-탭-선택"])
def test_both_tab_labels_are_readable(dark_app: QApplication, qtbot: QtBot, current: int) -> None:
    # 작은 글자는 획이 가늘어, 글꼴에 따라 설정한 글자색 그대로인 픽셀이 하나도 없다(CI에서 확인).
    # 색은 그대로 두고 크기만 키워서 잰다.
    dark_app.setStyleSheet(dark_app.styleSheet() + "QTabBar::tab { font-size: 48px; }")
    tabs = QTabWidget()
    for name in ("문제 목록", "대조 보기"):
        tabs.addTab(QWidget(), name)
    qtbot.addWidget(tabs)
    tabs.setCurrentIndex(current)
    tabs.resize(900, 300)
    tabs.show()
    bar = tabs.tabBar()
    assert bar is not None
    for index in range(2):
        image: QImage = bar.grab(bar.tabRect(index).adjusted(4, 4, -4, -4)).toImage()
        pixels = Counter(
            image.pixelColor(x, y).name()
            for x in range(image.width())
            for y in range(image.height())
        )
        background = QColor(pixels.most_common(1)[0][0])
        assert _luminance(background) > 0.7, (index, background.name())
        assert max(_contrast(QColor(c), background) for c in pixels) >= READABLE, index
