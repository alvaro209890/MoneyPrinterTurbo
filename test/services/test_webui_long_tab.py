from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from app.config import config
from app.services import long_video, voice


ROOT_DIR = Path(__file__).parent.parent.parent
WEBUI_MAIN = ROOT_DIR / "webui" / "Main.py"


def _by_key(elements, key):
    return next(
        item
        for item in elements
        if str(getattr(item, "key", "")) == key
        or str(getattr(item, "key", "")).startswith(f"{key}_")
    )


def test_long_tab_accepts_supplied_script_and_preserves_explicit_controls():
    test_app_config = dict(config.app, video_source="pexels")
    test_ui_config = dict(config.ui, language="en", voice_name="en-US-JennyNeural-Female")
    with (
        patch.object(config, "app", test_app_config),
        patch.object(config, "ui", test_ui_config),
        patch.object(config, "try_save_config", return_value=True),
        patch.object(
            voice,
            "get_all_azure_voices",
            return_value=["en-US-JennyNeural-Female"],
        ),
        patch("app.services.webui_task.submit_generation") as submit_generation,
    ):
        app = AppTest.from_file(str(WEBUI_MAIN), default_timeout=60)
        app.session_state["ui_language"] = "en"
        app.run()
        assert not app.exception

        _by_key(app.text_area, "long_video_script").set_value(
            "A supplied sentence. A second supplied sentence. A final sentence."
        ).run()
        _by_key(app.selectbox, "long_video_aspect").set_value("9:16").run()
        _by_key(app.button, "long_generate_button").click().run()

        assert submit_generation.call_count == 1
        submitted = submit_generation.call_args.args[1]
        assert submitted.video_subject == ""
        assert submitted.video_script.startswith("A supplied sentence")
        assert "video_aspect" in submitted.model_fields_set
        assert "n_threads" not in submitted.model_fields_set

        resolved = long_video.apply_long_video_defaults(submitted)
        assert resolved.video_aspect == "9:16"
        assert resolved.n_threads >= 4
        assert not app.exception
