"""ui/player.py 테스트. 진짜 QMediaPlayer를 여러 번 만들고 지우면 같은 프로세스의 다른 Qt
테스트와 충돌해 스위트 전체가 멈춘다(이 환경에서 재현). 그래서 미디어 백엔드를 주입하는 구조로
바꾸고, 여기서는 가짜 백엔드로 우리 로직(이동 후 일시정지, 시간 표시, 오류 전달)만 검증한다.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QObject, QUrl, pyqtSignal
from PyQt6.QtWidgets import QWidget
from pytestqt.qtbot import QtBot

from barrierfree.ui.player import MediaBackend, VideoPlayerPanel


class FakeBackend(QObject):
    position_changed = pyqtSignal(int)
    duration_changed = pyqtSignal(int)
    state_changed = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self.source = QUrl()
        self.position_ms = 0
        self.playing = False
        self.calls: list[str] = []

    def set_source(self, url: QUrl) -> None:
        self.source = url
        self.calls.append("set_source")

    def set_position(self, ms: int) -> None:
        self.position_ms = ms
        self.calls.append(f"set_position:{ms}")

    def play(self) -> None:
        self.playing = True
        self.calls.append("play")
        self.state_changed.emit()

    def pause(self) -> None:
        self.playing = False
        self.calls.append("pause")
        self.state_changed.emit()

    def video_widget(self) -> QWidget:
        return QWidget()


def make_panel(qtbot: QtBot) -> tuple[VideoPlayerPanel, FakeBackend]:
    backend = FakeBackend()
    panel = VideoPlayerPanel(backend_factory=lambda: backend)
    qtbot.addWidget(panel)
    return panel, backend


def test_seek_moves_then_pauses_and_never_plays(qtbot: QtBot) -> None:
    panel, backend = make_panel(qtbot)
    backend.play()  # 재생 중이던 상태에서 문제를 선택했다고 가정
    backend.calls.clear()
    panel.seek(12.5)
    assert backend.calls == ["set_position:12500", "pause"]
    assert not panel.is_playing()


def test_negative_seek_is_clamped_to_zero(qtbot: QtBot) -> None:
    panel, backend = make_panel(qtbot)
    panel.seek(-3)
    assert backend.position_ms == 0


def test_load_uses_a_local_file_url_that_keeps_korean_names(qtbot: QtBot, tmp_path: Path) -> None:
    panel, backend = make_panel(qtbot)
    panel.load(tmp_path / "한글 영상.mp4")
    assert backend.source.isLocalFile()
    # Qt는 Windows에서도 '/' 구분자를 돌려주므로 문자열이 아니라 경로로 비교한다.
    assert Path(backend.source.toLocalFile()) == tmp_path / "한글 영상.mp4"
    assert panel.source_url().endswith(".mp4")


def test_play_button_toggles_and_its_label_follows_the_state(qtbot: QtBot) -> None:
    panel, backend = make_panel(qtbot)
    assert panel.play_button.text() == "재생"
    panel.play_button.click()
    assert panel.is_playing() and panel.play_button.text() == "일시정지"
    panel.play_button.click()
    assert not panel.is_playing() and panel.play_button.text() == "재생"


def test_play_button_and_slider_are_reported_as_user_activity(qtbot: QtBot) -> None:
    panel, backend = make_panel(qtbot)
    with qtbot.waitSignal(panel.activity):
        panel.play_button.click()
    with qtbot.waitSignal(panel.activity):
        panel.slider.sliderMoved.emit(5000)
    assert backend.position_ms == 5000  # 탐색 자체도 그대로 전달된다


def test_backend_errors_become_a_playback_failed_signal(qtbot: QtBot) -> None:
    panel, backend = make_panel(qtbot)
    with qtbot.waitSignal(panel.playback_failed) as blocker:
        backend.error_occurred.emit("cannot decode")
    assert "cannot decode" in blocker.args[0]


def test_time_labels_follow_the_backend(qtbot: QtBot) -> None:
    panel, backend = make_panel(qtbot)
    backend.duration_changed.emit(125_000)
    backend.position_changed.emit(65_000)
    assert panel.position_label.text() == "01:05"
    assert panel.duration_label.text() == "02:05"
    assert panel.slider.maximum() == 125_000 and panel.slider.value() == 65_000


def test_current_seconds_reads_the_backend_position(qtbot: QtBot) -> None:
    panel, backend = make_panel(qtbot)
    backend.position_ms = 4200
    assert panel.current_seconds() == 4.2


def test_fake_backend_satisfies_the_protocol() -> None:
    backend: MediaBackend = FakeBackend()
    assert backend is not None
