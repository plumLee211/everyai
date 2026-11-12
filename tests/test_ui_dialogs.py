"""설정 다이얼로그(SCR-04)와 자막 영역 확인 다이얼로그 테스트."""

from __future__ import annotations

import pytest
from pytestqt.qtbot import QtBot

from barrierfree.analysis.subtitle_region import RegionEstimate, region_rect
from barrierfree.engines.device import (
    SUPPORTED_MODELS,
    DeviceInfo,
    ModelRecommendation,
    bundled_models,
)
from barrierfree.pipeline import RegionAction
from barrierfree.settings import MAX_REGION_RATIO, MIN_REGION_RATIO, AppSettings
from barrierfree.ui.region_dialog import RegionDialog
from barrierfree.ui.settings_dialog import SettingsDialog

DEVICE = DeviceInfo(cpu_name="Intel i5", cpu_count=8, ram_gb=16.0, cuda_available=False)
RECOMMENDED = ModelRecommendation("small", "cpu", "int8")


def settings_dialog(
    qtbot: QtBot,
    current: AppSettings | None = None,
    recommendation: ModelRecommendation = RECOMMENDED,
    device: DeviceInfo = DEVICE,
) -> SettingsDialog:
    dialog = SettingsDialog(device, recommendation, current or AppSettings())
    qtbot.addWidget(dialog)
    return dialog


# ---------- 설정 다이얼로그 ----------


def test_shows_device_recommendation_and_current_model(qtbot: QtBot) -> None:
    dialog = settings_dialog(qtbot)
    assert "Intel i5" in dialog.device_label.text()
    assert "16GB" in dialog.device_label.text()
    assert "사용 불가" in dialog.device_label.text()
    assert "small" in dialog.recommendation_label.text()
    assert "small" in dialog.current_model_label.text()


def test_cuda_availability_is_shown(qtbot: QtBot) -> None:
    gpu = DeviceInfo("Xeon", 16, 64.0, True)
    rec = ModelRecommendation("medium", "cuda", "float16")
    dialog = settings_dialog(qtbot, device=gpu, recommendation=rec)
    assert "사용 가능" in dialog.device_label.text()


def test_low_memory_warning_is_shown(qtbot: QtBot) -> None:
    warning = "메모리가 적어 작은 모델을 권장합니다."
    rec = ModelRecommendation("base", "cpu", "int8", warning=warning)
    low_memory = settings_dialog(qtbot, recommendation=rec)
    assert "메모리가 적어" in low_memory.warning_label.text()
    assert not low_memory.warning_label.isHidden()
    assert settings_dialog(qtbot).warning_label.isHidden()


def test_advanced_settings_are_always_shown_like_the_design(qtbot: QtBot) -> None:
    dialog = settings_dialog(qtbot)
    dialog.show()
    assert dialog.advanced_box.isVisible()
    assert dialog.model_combo.isVisible() and dialog.model_dir_edit.isVisible()


def test_close_button_and_cancel_reject_the_dialog(qtbot: QtBot) -> None:
    for name in ("close_button", "cancel_button"):
        dialog = settings_dialog(qtbot)
        getattr(dialog, name).click()
        assert dialog.result() == 0


def test_region_preview_follows_the_slider(qtbot: QtBot) -> None:
    dialog = settings_dialog(qtbot)
    dialog.ratio_slider.setValue(40)
    assert dialog.preview._ratio == 0.4  # noqa: SLF001 - 미리보기 값을 확인한다


def test_untouched_dialog_keeps_recommended_model_and_defaults(qtbot: QtBot) -> None:
    assert settings_dialog(qtbot).settings() == AppSettings()


def test_model_choices_include_recommended_plus_every_supported_model(qtbot: QtBot) -> None:
    dialog = settings_dialog(qtbot)  # 대화상자를 붙들어 둔다(없으면 콤보박스가 먼저 삭제된다)
    combo = dialog.model_combo
    assert combo.count() == 1 + len(SUPPORTED_MODELS)
    assert combo.itemData(0) is None  # 권장 모델 사용
    assert [combo.itemData(i) for i in range(1, combo.count())] == list(SUPPORTED_MODELS)


def test_choosing_a_model_changes_settings_and_shows_the_trade_off_notice(qtbot: QtBot) -> None:
    dialog = settings_dialog(qtbot)
    assert dialog.model_notice.isHidden()
    dialog.model_combo.setCurrentIndex(dialog.model_combo.findData("medium"))
    assert dialog.settings().whisper_model == "medium"
    assert not dialog.model_notice.isHidden()
    assert "처리 시간" in dialog.model_notice.text()


def test_existing_manual_model_is_preselected(qtbot: QtBot) -> None:
    dialog = settings_dialog(qtbot, AppSettings(whisper_model="base"))
    assert dialog.settings().whisper_model == "base"
    assert "base" in dialog.current_model_label.text()


def test_region_ratio_slider_is_bounded_and_labelled(qtbot: QtBot) -> None:
    dialog = settings_dialog(qtbot)
    slider = dialog.ratio_slider
    assert slider.minimum() == round(MIN_REGION_RATIO * 100)
    assert slider.maximum() == round(MAX_REGION_RATIO * 100)
    assert slider.value() == 25 and "25%" in dialog.ratio_label.text()
    slider.setValue(40)
    assert "40%" in dialog.ratio_label.text()
    assert dialog.settings().subtitle_region_ratio == pytest.approx(0.40)
    slider.setValue(99)  # 범위를 넘는 값은 최대값으로 제한된다
    assert dialog.settings().subtitle_region_ratio == pytest.approx(MAX_REGION_RATIO)


def test_model_dir_is_editable_and_empty_means_default(qtbot: QtBot) -> None:
    dialog = settings_dialog(qtbot, AppSettings(model_dir="D:/모델"))
    assert dialog.model_dir_edit.text() == "D:/모델"
    dialog.model_dir_edit.setText("   ")
    assert dialog.settings().model_dir is None


def test_restore_recommended_resets_model_and_region_but_keeps_model_dir(qtbot: QtBot) -> None:
    dialog = settings_dialog(qtbot, AppSettings("medium", "D:/모델", 0.4))
    dialog.restore_button.click()
    restored = dialog.settings()
    assert restored.whisper_model is None
    assert restored.subtitle_region_ratio == pytest.approx(0.25)
    assert restored.model_dir == "D:/모델"  # 경로는 사용자 환경 값이라 지우지 않는다


def test_cancel_does_not_expose_changes_as_accepted(qtbot: QtBot) -> None:
    dialog = settings_dialog(qtbot)
    dialog.ratio_slider.setValue(45)
    dialog.reject()
    assert dialog.result() == 0  # Rejected: 호출자는 설정을 적용하지 않는다


# ---------- 자막 영역 확인 다이얼로그 ----------


def estimate(
    is_subtitle: bool = True, ratio: float = 0.25, samples: tuple[str, ...] = ()
) -> RegionEstimate:
    score = 0.8 if is_subtitle else 0.2
    return RegionEstimate(is_subtitle, score, region_rect(ratio), 10, 12.0, 0.9, 0.7, samples)


def region_dialog(qtbot: QtBot, est: RegionEstimate) -> RegionDialog:
    dialog = RegionDialog(est)
    qtbot.addWidget(dialog)
    return dialog


def test_subtitle_like_estimate_preselects_confirm_and_returns_its_rect(qtbot: QtBot) -> None:
    est = estimate(True)
    dialog = region_dialog(qtbot, est)
    assert dialog.confirm_radio.isChecked()
    decision = dialog.decision()
    assert decision.action is RegionAction.CONFIRMED and decision.rect == est.rect


def test_non_subtitle_estimate_preselects_no_subtitle(qtbot: QtBot) -> None:
    dialog = region_dialog(qtbot, estimate(False))
    assert dialog.none_radio.isChecked()
    decision = dialog.decision()
    assert decision.action is RegionAction.NONE and decision.rect is None


def test_adjusting_returns_the_slider_region(qtbot: QtBot) -> None:
    dialog = region_dialog(qtbot, estimate(True))
    dialog.adjust_radio.setChecked(True)
    dialog.ratio_slider.setValue(35)
    decision = dialog.decision()
    assert decision.action is RegionAction.ADJUSTED
    assert decision.rect == region_rect(0.35)


def test_slider_is_enabled_only_when_adjusting(qtbot: QtBot) -> None:
    dialog = region_dialog(qtbot, estimate(True))
    assert not dialog.ratio_slider.isEnabled()
    dialog.adjust_radio.setChecked(True)
    assert dialog.ratio_slider.isEnabled()
    dialog.none_radio.setChecked(True)
    assert not dialog.ratio_slider.isEnabled()


def test_slider_starts_at_the_estimated_region(qtbot: QtBot) -> None:
    dialog = region_dialog(qtbot, estimate(True, ratio=0.3))
    assert dialog.ratio_slider.value() == 30
    assert "30%" in dialog.ratio_label.text()


def test_sample_texts_are_shown_so_the_editor_can_judge(qtbot: QtBot) -> None:
    samples = ("신청은 홈페이지에서", "문의는 02-1234-5678")
    dialog = region_dialog(qtbot, estimate(True, samples=samples))  # 변수에 담아야 해제되지 않는다
    shown = dialog.samples_label.text()
    assert "신청은 홈페이지에서" in shown and "02-1234-5678" in shown


def test_without_samples_the_dialog_says_nothing_was_read(qtbot: QtBot) -> None:
    dialog = region_dialog(qtbot, estimate(False, samples=()))
    assert "읽힌 글자가 없습니다" in dialog.samples_label.text()


def test_summary_describes_the_estimate_without_claiming_certainty(qtbot: QtBot) -> None:
    dialog = region_dialog(qtbot, estimate(True))
    assert "추정" in dialog.summary_label.text()


def test_models_not_bundled_are_labelled_and_warn_about_download(qtbot: QtBot) -> None:
    dialog = settings_dialog(qtbot)
    combo = dialog.model_combo
    for model in SUPPORTED_MODELS:
        text = combo.itemText(combo.findData(model))
        assert ("내려받기 필요" in text) == (model not in bundled_models())
    dialog.model_combo.setCurrentIndex(combo.findData("large-v3"))
    assert "포함되어 있지 않습니다" in dialog.model_notice.text()


def test_bundled_model_keeps_the_trade_off_notice(qtbot: QtBot) -> None:
    dialog = settings_dialog(qtbot)
    dialog.model_combo.setCurrentIndex(dialog.model_combo.findData("small"))
    assert "처리 시간" in dialog.model_notice.text()
