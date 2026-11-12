"""영상 플레이어 패널 (VID-01~05, docs/project/ui-spec.md 7.4).

검수자가 문제를 선택하면 `seek`로 해당 시점에 이동하고 일시정지한다. 선택만으로 재생을 시작하지
않는다. 재생 실패는 `playback_failed`로 알리고, 분석 결과 검수는 계속할 수 있게 한다.

미디어 재생은 `MediaBackend` 뒤에 둔다. 진짜 `QMediaPlayer`를 한 프로세스에서 여러 번 만들고 지우면
다른 Qt 테스트와 충돌해 스위트가 멈추는 것이 확인되었고, 재생 자체는 우리 로직이 아니기 때문이다.
실제 앱은 `QtMediaBackend`를, 테스트는 가짜 백엔드를 쓴다.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from PyQt6.QtCore import QObject, Qt, QUrl, pyqtSignal
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget

from barrierfree.ui.issue_model import format_time


class MediaBackend(Protocol):
    position_changed: pyqtSignal
    duration_changed: pyqtSignal
    state_changed: pyqtSignal
    error_occurred: pyqtSignal

    position_ms: int
    playing: bool
    source: QUrl

    def set_source(self, url: QUrl) -> None: ...
    def set_position(self, ms: int) -> None: ...
    def play(self) -> None: ...
    def pause(self) -> None: ...
    def video_widget(self) -> QWidget: ...


class QtMediaBackend(QObject):
    """진짜 QMediaPlayer를 감싼 기본 백엔드."""

    position_changed = pyqtSignal(int)
    duration_changed = pyqtSignal(int)
    state_changed = pyqtSignal()
    error_occurred = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._player = QMediaPlayer(self)
        self._audio = QAudioOutput(self)
        self._player.setAudioOutput(self._audio)
        self._video = QVideoWidget()
        self._player.setVideoOutput(self._video)
        self._player.positionChanged.connect(self.position_changed.emit)
        self._player.durationChanged.connect(self.duration_changed.emit)
        self._player.playbackStateChanged.connect(lambda *_: self.state_changed.emit())
        self._player.errorOccurred.connect(lambda _e, message: self.error_occurred.emit(message))

    @property
    def position_ms(self) -> int:
        return int(self._player.position())

    @property
    def playing(self) -> bool:
        return bool(self._player.playbackState() == QMediaPlayer.PlaybackState.PlayingState)

    @property
    def source(self) -> QUrl:
        return self._player.source()

    def set_source(self, url: QUrl) -> None:
        self._player.setSource(url)

    def set_position(self, ms: int) -> None:
        self._player.setPosition(ms)

    def play(self) -> None:
        self._player.play()

    def pause(self) -> None:
        self._player.pause()

    def video_widget(self) -> QWidget:
        return self._video


class VideoPlayerPanel(QWidget):
    playback_failed = pyqtSignal(str)
    activity = pyqtSignal()  # 재생 버튼이나 탐색 슬라이더를 조작했다

    def __init__(
        self,
        parent: QWidget | None = None,
        backend_factory: Callable[[], MediaBackend] | None = None,
    ) -> None:
        super().__init__(parent)
        self._backend: MediaBackend = (backend_factory or QtMediaBackend)()
        self.video_widget = self._backend.video_widget()

        self.play_button = QPushButton("재생")
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.position_label = QLabel(format_time(0))
        self.duration_label = QLabel(format_time(0))

        controls = QHBoxLayout()
        for widget in (self.play_button, self.position_label, self.slider, self.duration_label):
            controls.addWidget(widget)
        controls.setStretch(2, 1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.video_widget, 1)
        layout.addLayout(controls)

        self.play_button.clicked.connect(self._toggle)
        self.slider.sliderMoved.connect(self._on_slider_moved)
        self._backend.position_changed.connect(self.set_position_ms)
        self._backend.duration_changed.connect(self.set_duration_ms)
        self._backend.state_changed.connect(self._sync_button)
        self._backend.error_occurred.connect(self.playback_failed.emit)

    # ----- 제어 -----

    def load(self, path: Path) -> None:
        self._backend.set_source(QUrl.fromLocalFile(str(path)))

    def seek(self, seconds: float) -> None:
        """해당 시점으로 이동하고 일시정지한다(자동 재생 금지)."""
        self._backend.set_position(max(0, round(seconds * 1000)))
        self._backend.pause()

    def pause(self) -> None:
        self._backend.pause()

    def current_seconds(self) -> float:
        return self._backend.position_ms / 1000

    def is_playing(self) -> bool:
        return self._backend.playing

    def source_url(self) -> str:
        return str(self._backend.source.toString())

    # ----- 표시 -----

    def set_duration_ms(self, ms: int) -> None:
        duration = max(0, ms)
        self.slider.setRange(0, duration)
        self.duration_label.setText(format_time(duration / 1000))

    def set_position_ms(self, ms: int) -> None:
        if not self.slider.isSliderDown():
            self.slider.setValue(ms)
        self.position_label.setText(format_time(ms / 1000))

    def _on_slider_moved(self, ms: int) -> None:
        self.activity.emit()
        self._backend.set_position(ms)

    def _toggle(self) -> None:
        self.activity.emit()
        if self.is_playing():
            self._backend.pause()
        else:
            self._backend.play()

    def _sync_button(self) -> None:
        self.play_button.setText("일시정지" if self.is_playing() else "재생")
