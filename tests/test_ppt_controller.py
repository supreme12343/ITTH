import json
import time
import threading
import queue
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import pptcontroller as controller


@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setattr(controller, "ANNOTATION_FILE_PATH", str(tmp_path / "annotations.json"))
    monkeypatch.setattr(controller.hardware_controller, "use_com", False)
    monkeypatch.setattr(controller.hardware_controller, "refresh_slideshow_position", lambda: None)
    monkeypatch.setattr(controller, "_render_slideshow_annotations", lambda *args, **kwargs: False)
    with controller.ANNOTATION_LOCK:
        controller.slide_annotations.clear()
        if hasattr(controller, "annotation_redo"):
            controller.annotation_redo.clear()
    with TestClient(controller.server_app) as client:
        yield client, tmp_path / "annotations.json"


def stroke_payload(stroke_id="one", slide_index=0):
    return {
        "stroke_id": stroke_id,
        "tool": "pen",
        "color": "#FF3B30",
        "width": 3,
        "points": [
            {"x": 10.0, "y": 20.0, "pressure": 0.5},
            {"x": 15.0, "y": 25.0, "pressure": 1.0},
        ],
        "slide_index": slide_index,
    }


def test_socket_payload_persists_and_can_be_loaded(api):
    client, path = api
    response = client.post("/annotations", json=stroke_payload())
    assert response.status_code == 200
    assert [s["id"] for s in response.json()["strokes"]] == ["one"]
    assert [s["id"] for s in client.get("/annotations/0").json()["strokes"]] == ["one"]
    deadline = time.monotonic() + 2
    while not path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["0"][0]["id"] == "one"


def _projection_stroke(**overrides):
    stroke = {
        "id": "projection-test",
        "tool": "pen",
        "color": "#12ABEF",
        "width": 3,
        "points": [{"x": 0.1, "y": 0.2}, {"x": 0.9, "y": 0.2}],
        "ppt_points": [{"x": 0.1, "y": 0.2}, {"x": 0.9, "y": 0.2}],
        "canvas_width": 400,
        "canvas_height": 225,
        "slide_aspect": 16 / 9,
    }
    stroke.update(overrides)
    return stroke


class _FakeColor:
    def __init__(self, events):
        self.events = events
        self.value = 0

    @property
    def RGB(self):
        return self.value

    @RGB.setter
    def RGB(self, value):
        self.value = value
        self.events.append(("color", value))


class _FakeSlideShowView:
    def __init__(self):
        self.events = []
        self.PointerColor = _FakeColor(self.events)
        self._pointer_type = 1

    @property
    def PointerType(self):
        return self._pointer_type

    @PointerType.setter
    def PointerType(self, value):
        self._pointer_type = value
        self.events.append(("pointer", value))

    def DrawLine(self, x1, y1, x2, y2):
        self.events.append(("line", self._pointer_type, self.PointerColor.RGB))


def test_projection_sets_selected_color_before_switching_to_pen():
    view = _FakeSlideShowView()

    drawn = controller._draw_stroke_in_slideshow(view, _projection_stroke(), 960, 540)

    assert drawn > 0
    assert view.events[:2] == [("color", 0x00EFAB12), ("pointer", 2)]
    assert all(event[1:] == (2, 0x00EFAB12) for event in view.events[2:])


def test_projection_keeps_the_requested_maximum_width():
    view = _FakeSlideShowView()

    drawn = controller._draw_stroke_in_slideshow(
        view, _projection_stroke(width=20), 960, 540
    )

    # Legacy slideshow pen projection approximates this as 24 parallel lines.
    assert drawn == 24


class _FakeShapeLine:
    def __init__(self):
        self.ForeColor = type("Color", (), {"RGB": 0})()
        self.Weight = None
        self.Transparency = 0


class _FakeShape:
    def __init__(self, points=None):
        self.points = points
        self.Line = _FakeShapeLine()
        self.AlternativeText = ""
        self.deleted = False
        self.TextFrame = type("TextFrame", (), {
            "MarginLeft": 0, "MarginRight": 0, "MarginTop": 0, "MarginBottom": 0,
            "WordWrap": True,
            "TextRange": type("TextRange", (), {
                "Text": "",
                "Font": type("Font", (), {"Name": "", "Size": 0, "Color": type("Color", (), {"RGB": 0})()})(),
            })(),
        })()
        self.Fill = type("Fill", (), {"Visible": 1})()

    def Delete(self):
        self.deleted = True


class _FakeShapes:
    def __init__(self):
        self.items = []

    @property
    def Count(self):
        return len([shape for shape in self.items if not shape.deleted])

    def Item(self, index):
        return [shape for shape in self.items if not shape.deleted][index - 1]

    def AddPolyline(self, points):
        shape = _FakeShape(getattr(points, "value", points))
        self.items.append(shape)
        return shape

    def AddTextbox(self, orientation, left, top, width, height):
        shape = _FakeShape()
        shape.box = (orientation, left, top, width, height)
        shape.Line = type("Line", (), {"Visible": 1})()
        self.items.append(shape)
        return shape


def test_native_shape_projection_uses_selected_color_and_exact_width():
    shapes = _FakeShapes()
    stroke = _projection_stroke(width=20)

    shape = controller._draw_controller_annotation(shapes, stroke, 960, 540)

    assert shape.Line.ForeColor.RGB == 0x00EFAB12
    assert shape.Line.Weight == 48
    assert shape.AlternativeText == "PPTControllerAnnotation:projection-test"
    assert len(shape.points) == 2


def test_native_highlighter_is_translucent_and_cancel_keeps_user_shapes():
    shapes = _FakeShapes()
    user_shape = _FakeShape()
    user_shape.AlternativeText = "user shape"
    shapes.items.append(user_shape)
    stroke = _projection_stroke(tool="highlighter", color="#00FF00")
    preview = controller._draw_controller_annotation(
        shapes, stroke, 960, 540, preview=True, chunk=0
    )

    assert preview.Line.Transparency == 0.55
    assert controller._delete_controller_shapes(
        shapes, controller._annotation_shape_tag(stroke["id"], preview=True)
    ) == 1
    assert not user_shape.deleted


def test_native_text_annotation_uses_textbox_with_requested_color():
    shapes = _FakeShapes()
    stroke = _projection_stroke(
        tool="text", color="#00AAFF", width=7, text="TEST", font_size=56,
        ppt_points=[{"x": 0.2, "y": 0.3}, {"x": 0.2, "y": 0.3}],
    )

    shape = controller._draw_controller_annotation(shapes, stroke, 960, 540)

    assert shape.TextFrame.TextRange.Text == "TEST"
    assert shape.TextFrame.TextRange.Font.Color.RGB == 0x00FFAA00
    assert shape.AlternativeText == "PPTControllerAnnotation:projection-test"
    assert shape.box[0] == 1


def test_startup_reuses_service_that_becomes_healthy_after_port_probe(monkeypatch):
    health_results = iter((False, True))
    monkeypatch.setattr(controller, "services_started", False)
    monkeypatch.setattr(controller, "services_attached_to_existing", False)
    monkeypatch.setattr(controller, "_existing_api_has_incompatible_build", lambda: False)
    monkeypatch.setattr(controller, "_existing_node_has_incompatible_build", lambda: False)
    monkeypatch.setattr(controller, "_existing_api_is_healthy", lambda: False)
    monkeypatch.setattr(controller, "_existing_node_is_healthy", lambda: False)
    monkeypatch.setattr(controller, "_existing_controller_is_healthy", lambda: next(health_results))

    def port_conflict():
        raise RuntimeError("Python API port 9005 is already in use")

    monkeypatch.setattr(controller, "_check_service_ports_available", port_conflict)

    assert controller.start_services() == "connected-existing"
    assert controller.services_started
    assert controller.services_attached_to_existing


@pytest.mark.parametrize(("api_version", "expected"), [(2, True), (1, False)])
def test_existing_api_health_requires_matching_build_version(
    monkeypatch, api_version, expected
):
    class FakeResponse:
        def __init__(self, body):
            self.body = body
            self.ok = True

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    def fake_get(url, **_kwargs):
        if url.endswith("/controller/health"):
            return FakeResponse({"status": "ok", "api_version": api_version})
        return FakeResponse({"paths": {
            "/annotations/{slide_index}", "/annotations/preview",
            "/annotations/preview/cancel", "/decks/sync",
        }})

    monkeypatch.setattr(controller.requests, "get", fake_get)

    assert controller._existing_api_is_healthy(retries=1, retry_delay=0) is expected


def test_startup_refuses_to_reuse_outdated_python_api(monkeypatch):
    monkeypatch.setattr(controller, "services_started", False)
    monkeypatch.setattr(controller, "_existing_api_has_incompatible_build", lambda: True)

    with pytest.raises(RuntimeError, match="outdated Python API"):
        controller.start_services()


def test_loopback_only_node_is_not_phone_reachable(monkeypatch):
    class FakeResponse:
        def __init__(self, body, text="", status_code=200):
            self.body = body
            self.text = text
            self.status_code = status_code
            self.ok = status_code < 400

        def raise_for_status(self):
            if not self.ok:
                raise controller.requests.HTTPError()

        def json(self):
            return self.body

    def fake_get(url, **_kwargs):
        if url.endswith("/health"):
            return FakeResponse({
                "status": "ok",
                "controller_version": controller.PPT_CONTROLLER_NODE_VERSION,
                "bind_host": "127.0.0.1",
            })
        return FakeResponse({}, "Smart PPT Controller")

    monkeypatch.setattr(controller.requests, "get", fake_get)

    assert not controller._existing_node_is_healthy()
    assert controller._existing_node_has_incompatible_build()


def test_lan_bound_node_health_passes_phone_reachability_check(monkeypatch):
    class FakeResponse:
        ok = True
        status_code = 200

        def __init__(self, body, text=""):
            self.body = body
            self.text = text

        def raise_for_status(self):
            return None

        def json(self):
            return self.body

    def fake_get(url, **_kwargs):
        if url.endswith("/health"):
            return FakeResponse({
                "status": "ok",
                "controller_version": controller.PPT_CONTROLLER_NODE_VERSION,
                "bind_host": "0.0.0.0",
            })
        return FakeResponse({}, "Smart PPT Controller")

    monkeypatch.setattr(controller.requests, "get", fake_get)

    assert controller._existing_node_is_healthy()
    assert not controller._existing_node_has_incompatible_build()


def test_startup_waits_for_existing_api_to_become_healthy(monkeypatch):
    health_results = iter((False, True))
    monkeypatch.setattr(
        controller,
        "_existing_api_is_healthy",
        lambda **kwargs: next(health_results),
    )

    def occupied_port(*args):
        raise RuntimeError("Python API port 9005 is already in use")

    monkeypatch.setattr(controller, "_check_service_port_available", occupied_port)

    assert controller._wait_for_api_port_resolution(timeout=1) == "healthy"


def test_startup_recovers_node_when_existing_python_api_is_healthy(monkeypatch):
    launches = []
    monkeypatch.setattr(controller, "services_started", False)
    monkeypatch.setattr(controller, "services_attached_to_existing", False)
    monkeypatch.setattr(controller, "_existing_api_has_incompatible_build", lambda: False)
    monkeypatch.setattr(controller, "_existing_node_has_incompatible_build", lambda: False)
    monkeypatch.setattr(controller, "_existing_api_is_healthy", lambda: True)
    monkeypatch.setattr(controller, "_existing_node_is_healthy", lambda: False)
    monkeypatch.setattr(controller, "_check_service_port_available", lambda *args: None)
    monkeypatch.setattr(controller, "_launch_node_frontend", lambda: launches.append("node") or True)
    monkeypatch.setattr(controller, "_probe_powerpoint_after_node_start", lambda: None)

    assert controller.start_services() == "connected-existing"
    assert launches == ["node"]
    assert controller.services_started
    assert controller.services_attached_to_existing


def test_text_payload_preserves_color_width_and_font_size(api):
    client, _ = api
    payload = stroke_payload()
    payload.update({
        "stroke_id": "text-one",
        "tool": "text",
        "color": "#00AAFF",
        "width": 7,
        "text": "TEST",
        "font_size": 56,
        "ppt_points": [{"x": 0.2, "y": 0.3}, {"x": 0.2, "y": 0.3}],
        "canvas_width": 400,
        "canvas_height": 225,
        "slide_aspect": 16 / 9,
    })

    response = client.post("/annotations", json=payload)

    assert response.status_code == 200
    stored = response.json()["strokes"][0]
    assert (stored["tool"], stored["color"], stored["width"]) == ("text", "#00AAFF", 7)
    assert (stored["text"], stored["font_size"]) == ("TEST", 56)


def test_preview_route_does_not_persist_unfinished_strokes(api):
    client, path = api
    payload = stroke_payload()
    payload.update({"stroke_id": "preview-one", "point_offset": 0})

    response = client.post("/annotations/preview", json=payload)
    cancelled = client.post(
        "/annotations/preview/cancel",
        json={"stroke_id": "preview-one", "slide_index": 0},
    )

    assert response.status_code == 200
    assert response.json()["projected"] is False
    assert cancelled.status_code == 200
    assert client.get("/annotations/0").json()["strokes"] == []
    assert not path.exists()


def test_controller_health_exposes_api_build_version(api):
    client, _ = api

    response = client.get("/controller/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "api_version": controller.PPT_CONTROLLER_API_VERSION,
    }


def test_eraser_splits_a_crossed_pen_stroke():
    pen = _projection_stroke(
        tool="pen",
        ppt_points=[{"x": 0.1, "y": 0.5}, {"x": 0.9, "y": 0.5}],
        points=[{"x": 40, "y": 112.5}, {"x": 360, "y": 112.5}],
    )
    eraser = _projection_stroke(
        id="eraser-test",
        tool="eraser",
        color="#1e1e2f",
        width=27,
        ppt_points=[{"x": 0.5, "y": 0.3}, {"x": 0.5, "y": 0.7}],
        points=[{"x": 200, "y": 67.5}, {"x": 200, "y": 157.5}],
    )

    effective = controller._effective_annotation_strokes([pen, eraser])

    assert len(effective) == 2
    assert effective[0]["ppt_points"][-1]["x"] < 0.5
    assert effective[1]["ppt_points"][0]["x"] > 0.5


@pytest.mark.parametrize(
    "change",
    [
        {"tool": "unknown"},
        {"color": "red"},
        {"width": 0},
        {"slide_index": -1},
        {"points": [{"x": -1, "y": 2}, {"x": 3, "y": 4}]},
        {"points": [{"x": 1, "y": 2, "pressure": 2}, {"x": 3, "y": 4}]},
        {"points": [{"x": 1, "y": 2}] * 10001},
    ],
)
def test_invalid_stroke_does_not_change_state(api, change):
    client, path = api
    payload = stroke_payload()
    payload.update(change)
    response = client.post("/annotations", json=payload)
    assert response.status_code == 422
    assert client.get("/annotations/0").json()["strokes"] == []
    assert not path.exists()


def test_undo_redo_and_new_stroke_invalidate_redo(api):
    client, _ = api
    assert client.post("/annotations", json=stroke_payload("one")).status_code == 200
    assert client.post("/annotations", json=stroke_payload("two")).status_code == 200
    undone = client.post("/annotations/undo", json={"slide_index": 0})
    assert [s["id"] for s in undone.json()["strokes"]] == ["one"]
    redone = client.post("/annotations/redo", json={"slide_index": 0})
    assert redone.status_code == 200
    assert [s["id"] for s in redone.json()["strokes"]] == ["one", "two"]
    client.post("/annotations/undo", json={"slide_index": 0})
    client.post("/annotations", json=stroke_payload("three"))
    no_redo = client.post("/annotations/redo", json={"slide_index": 0})
    assert [s["id"] for s in no_redo.json()["strokes"]] == ["one", "three"]
    cleared = client.delete("/annotations/0")
    assert cleared.json()["strokes"] == []
    assert client.post("/annotations/redo", json={"slide_index": 0}).json()["strokes"] == []


def test_thumbnail_export_is_cached_until_presentation_changes(tmp_path, monkeypatch):
    exports = []

    class Slide:
        def Export(self, path, kind):
            exports.append((path, kind))
            Path(path).write_bytes(b"png")

    class Slides(list):
        @property
        def Count(self):
            return len(self)

    class Presentation:
        Name = "test.pptx"
        FullName = "C:/test.pptx"
        LastSavedTime = "2026-09-29T10:00:00"

        def __init__(self):
            self.Slides = Slides([Slide(), Slide()])

    monkeypatch.setattr(controller, "NODE_THUMBNAILS_DIR", str(tmp_path))
    presentation = Presentation()
    subject = controller.PresentationController()
    monkeypatch.setattr(subject, "sync_state_to_node", lambda: None)
    subject.export_deck_thumbnails(presentation)
    subject.export_deck_thumbnails(presentation)
    assert len(exports) == 2
    presentation.LastSavedTime = "2026-09-29T10:01:00"
    subject.export_deck_thumbnails(presentation)
    assert len(exports) == 4
    subject.export_deck_thumbnails(presentation, force=True)
    assert len(exports) == 6


def test_state_sync_returns_before_a_stalled_node_request(monkeypatch):
    entered = threading.Event()
    release = threading.Event()

    def delayed_post(*args, **kwargs):
        entered.set()
        release.wait(0.7)
        return type("Response", (), {"status_code": 200})()

    subject = controller.PresentationController()
    monkeypatch.setattr(subject._sync_session, "post", delayed_post)
    started = time.perf_counter()
    subject.sync_state_to_node()
    elapsed = time.perf_counter() - started
    assert entered.wait(1)
    release.set()
    assert elapsed < 0.1


def test_executor_marks_failed_command_done(monkeypatch):
    commands = queue.Queue()
    stop = threading.Event()
    monkeypatch.setattr(controller, "action_queue", commands)
    monkeypatch.setattr(controller, "stop_event", stop)

    def fail():
        raise RuntimeError("PowerPoint unavailable")

    monkeypatch.setattr(controller.hardware_controller, "move_next", fail)
    commands.put({"command": "NEXT", "target": None, "origin": "test"})
    worker = threading.Thread(target=controller.centralized_presentation_executor, daemon=True)
    worker.start()
    deadline = time.monotonic() + 1
    while commands.unfinished_tasks and time.monotonic() < deadline:
        time.sleep(0.01)
    stop.set()
    worker.join(timeout=1)
    assert commands.unfinished_tasks == 0
