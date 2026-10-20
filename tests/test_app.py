from pathlib import Path

from streamlit.testing.v1 import AppTest

APP = Path(__file__).resolve().parents[1] / "streamlit_app.py"


def test_purchase_order_demo_renders_assignment():
    app = AppTest.from_file(APP, default_timeout=30)
    app.run()
    assert not app.exception
    app.sidebar.button(key="demo_po").click().run()
    assert not app.exception
    body = _text(app)
    assert "150210" in body
    assert "Eva Huber" in body
    assert "1.428,00" in body


def test_collective_demo_renders_without_a_suggestion():
    app = AppTest.from_file(APP, default_timeout=30)
    app.run()
    app.sidebar.button(key="demo_collective").click().run()
    assert not app.exception
    body = _text(app)
    assert "Sammelrechnung" in body
    assert "150210" not in body
    assert "Eva Huber" not in body


def _text(app: AppTest) -> str:
    parts = [element.value for element in app.markdown]
    parts.extend(element.value for element in app.caption)
    parts.extend(element.value for element in app.info)
    return "\n".join(part for part in parts if isinstance(part, str))
