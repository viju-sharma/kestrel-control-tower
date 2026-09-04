"""Boot the Streamlit app headlessly and make sure nothing raises."""
import pytest

from kestrel import config

pytestmark = pytest.mark.skipif(not config.DB_PATH.exists(), reason="kestrel_ops.db not available")


def test_app_renders_without_exceptions():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file("../app.py", default_timeout=180).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.metric[0].label.startswith("Fill rate")


def test_regional_view_and_eaches():
    from streamlit.testing.v1 import AppTest
    at = AppTest.from_file("../app.py", default_timeout=180).run()
    at.sidebar.selectbox[0].select(at.sidebar.selectbox[0].options[1]).run()
    at.sidebar.radio[0].set_value("eaches").run()
    assert not at.exception, [e.value for e in at.exception]
    assert "Fill rate (eaches)" == at.metric[0].label
